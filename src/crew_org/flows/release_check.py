"""Check what a release actually published (crew#335, phase 1).

A release workflow can succeed and still publish less than the design asked
for: sprint-metrics#262's approved image push set `provenance: true`, which is
BuildKit's provenance, not the GitHub artifact attestation its criterion names,
and added no source label. The post-merge watcher sees a green run; only
reading what was published shows the gap. This does that, once the release
run for the branch's head has finished, and files a technical epic for the
gaps, one per version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from crew_org.events import EventKind, EventSink
from crew_org.flows.main_watch import file_technical_epic
from crew_org.flows.revisit import TECHNICAL
from crew_org.project import RECORD_PATH, ProjectRecordError, parse
from crew_org.tools.registry import ImageUnreadable, Registry

RELEASE_WORKFLOW = ".github/workflows/release.yml"
RELEASE_MARKER = "<!-- crew:release-check version={version} -->"
PLATFORMS = ("linux/amd64", "linux/arm64")
SOURCE_LABEL = "org.opencontainers.image.source"
# A gap only the Sponsor can close. A package's visibility is set on its settings
# page; no API or workflow token changes it, so no story can (crew#386).
SPONSOR_STEP = "For the Sponsor, not the crew:"
# Neither a gap nor a step: the image is private because the Sponsor chose it (crew#398).
PRIVATE_NOTE = "By the Sponsor's choice:"
_VERSION = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


@dataclass
class ReleaseCheck:
    verified: list[tuple[str, str]] = field(default_factory=list)
    filed: list[tuple[str, int]] = field(default_factory=list)
    waiting: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    # (repo, version) whose only gap is the Sponsor's to close.
    sponsor: list[tuple[str, str]] = field(default_factory=list)


def changelog_section(text: str, version: str) -> list[str]:
    """The non-empty lines under `## [version]`, up to the next `## [` heading."""
    lines: list[str] = []
    inside = False
    for line in text.splitlines():
        if line.startswith("## ["):
            if inside:
                break
            inside = line.startswith(f"## [{version}]")
            continue
        if inside and line.strip():
            lines.append(line.strip())
    return lines


def _already_checked(issues: Any, repo: str, version: str) -> bool:
    marker = RELEASE_MARKER.format(version=version)
    return any(marker in (i.get("body") or "") for i in issues.labelled(repo, TECHNICAL))


def private_by_choice(issues: Any, repo: str, branch: str) -> bool:
    """The project's record says its published image is private."""
    text = issues.file_at(repo, RECORD_PATH, branch)
    if not text:
        return False
    try:
        return parse(text).intent.release.image == "private"
    except (ProjectRecordError, ValueError):
        return False  # unreadable: an unreadable image stays the Sponsor's step, as before


def gaps(
    issues: Any, registry: Registry, repo: str, version: str, *, private: bool = False
) -> list[str]:
    """What release `version` of `repo` didn't publish, each as a line a Developer can act on."""
    found: list[str] = []
    tag = f"v{version}"
    if not issues.tag_exists(repo, tag):
        # Without the tag there's no Release on it and no changelog at it to read:
        # one gap, not three (sprint-metrics#350, crew#384).
        found.append(
            f"The tag `{tag}` doesn't exist, so there's no GitHub Release on it "
            f"and `CHANGELOG.md` at it can't be read."
        )
    else:
        release = issues.release_for_tag(repo, tag)
        if release is None:
            found.append(f"There's no GitHub Release on `{tag}`.")
        changelog = issues.file_at(repo, "CHANGELOG.md", tag) or ""
        section = changelog_section(changelog, version)
        if not section:
            found.append(f"`CHANGELOG.md` at `{tag}` has no `## [{version}]` section with entries.")
        elif release is not None:
            body = " ".join((release.get("body") or "").split())
            # The entries, not the `### Added` headings (sprint-metrics#261's criterion).
            entries = [line for line in section if line.startswith(("-", "*"))]
            missing = [line for line in entries if " ".join(line.split()) not in body]
            if missing:
                found.append(
                    "The Release notes leave out lines of the CHANGELOG section: "
                    + "; ".join(f"`{m[:80]}`" for m in missing[:5])
                )

    name = f"{issues.owner}/{repo}".lower()
    try:
        image = registry.image(name, version)
    except ImageUnreadable as exc:
        if private:
            found.append(
                f"{PRIVATE_NOTE} `ghcr.io/{name}` is private, as the project's record says, "
                "so its image checks are skipped."
            )
            return found
        found.append(
            f"{SPONSOR_STEP} {exc}. Making the package public is a setting only its owner "
            "can change, on the package's settings page (Change visibility). No API or "
            "workflow token can do it, so no story should try. The image checks run once "
            "the image can be read."
        )
        return found
    if image is None:
        found.append(f"There's no image `ghcr.io/{name}:{version}`.")
        return found
    absent = [p for p in PLATFORMS if p not in image.platforms]
    if absent:
        found.append(
            f"`ghcr.io/{name}:{version}` has no {', '.join(absent)} image "
            f"(it has {', '.join(image.platforms) or 'none'})."
        )
    short = ".".join(version.split(".")[:2])
    for other in (short, "latest"):
        if registry.digest(name, other) != image.digest:
            found.append(f"`ghcr.io/{name}:{other}` doesn't point at the `{version}` image.")
    if not issues.attestations(repo, image.digest):
        found.append(
            f"There's no GitHub artifact attestation for `{image.digest}` "
            f"(`gh attestation verify oci://ghcr.io/{name}:{version} --repo "
            f"{issues.owner}/{repo}` finds none). BuildKit's `provenance: true` isn't one; "
            "`actions/attest-build-provenance` makes one."
        )
    expected = f"https://github.com/{issues.owner}/{repo}"
    if image.labels.get(SOURCE_LABEL) != expected:
        found.append(
            f"The image's `{SOURCE_LABEL}` label is "
            f"`{image.labels.get(SOURCE_LABEL) or 'missing'}`, not `{expected}`, so GHCR "
            "doesn't link the package to the repository."
        )
    return found


def check_releases(
    issues: Any,
    board: Any,
    sink: EventSink,
    *,
    repos: set[str] | list[str],
    registry: Registry | None = None,
) -> ReleaseCheck:
    """Verify each repo's current release, once its release run for the head has finished."""
    result = ReleaseCheck()
    registry = registry or Registry()
    for repo in sorted(repos):
        try:
            branch = issues.repository(repo)["default_branch"]
            run = issues.latest_runs(repo, branch).get(RELEASE_WORKFLOW)
            if run is None:
                continue  # no release workflow: nothing is published
            text = issues.file_at(repo, "pyproject.toml", branch) or ""
            found = _VERSION.search(text)
            version = found.group(1) if found else ""
            if not version or version == "0.0.0":
                continue  # nothing released yet
            if run.get("head_sha") != issues.head_sha(repo, branch):
                # The release run for the newest push hasn't finished: judging
                # now would report a release still being built.
                result.waiting.append((repo, version))
                continue
            if _already_checked(issues, repo, version):
                continue
            missing = gaps(
                issues, registry, repo, version, private=private_by_choice(issues, repo, branch)
            )
            published = issues.tag_exists(repo, f"v{version}")
        except Exception as exc:  # noqa: BLE001
            result.failed.append((repo, str(exc)[:160]))
            continue
        sponsor = [line for line in missing if line.startswith(SPONSOR_STEP)]
        skipped = [line for line in missing if line.startswith(PRIVATE_NOTE)]
        missing = [line for line in missing if line not in sponsor and line not in skipped]
        if not missing and not sponsor:
            result.verified.append((repo, version))
            sink.note(
                EventKind.NOTE,
                f"{repo} v{version}: release verified"
                + ("; its image is private by choice, so not read" if skipped else ""),
            )
            continue
        if not missing:
            # Nothing for the crew: no epic, and it's read again next tick.
            result.sponsor.append((repo, version))
            sink.note(
                EventKind.NOTE, f"{repo} v{version}: waiting on the Sponsor: {sponsor[0]}"[:160]
            )
            continue
        number = file_technical_epic(
            issues,
            board,
            sink,
            repo=repo,
            title=(
                f"Release v{version} published less than the design asks"
                if published
                else f"Release v{version} was never published"
            ),
            body=(
                f"{RELEASE_MARKER.format(version=version)}\n"
                + (
                    f"**The work:** make the release mechanism publish everything the design "
                    f"asks for, and ship it as the next release. `v{version}` itself can't be "
                    "changed once published.\n\n"
                    if published
                    # Nothing to amend: the version is still free to release as itself.
                    else f"**The work:** make the release mechanism publish everything the "
                    f"design asks for, and publish `{version}`. Nothing was published for it: "
                    f"the tag `v{version}` doesn't exist, so `{version}` can still be released "
                    "as itself.\n\n"
                )
                + f"What `v{version}` is missing, read from GitHub and GHCR after its release "
                "run finished:\n\n"
                + "\n".join(f"- {line}" for line in missing)
                + ("\n\n" + "\n".join(sponsor) if sponsor else "")
                + "\n\nTechnical work: found by the crew, so it goes straight to refinement "
                "(mqucifer/crew#192, #335)."
            ),
        )
        result.filed.append((repo, number))
    return result
