"""What waits in a project's changelog with no epic to release it gets a release
(crew#961).

A release came only from an epic's release Task (DevOps's, one per epic). A bug has no
epic, so its fix's changelog entry never shipped: crew-presentation's eight fixes from
its UX review (mqucifer/crew-presentation#197 to mqucifer/crew-presentation#204) sat
behind v0.1.2, and its site deploys only a release.

**What waits** is the changelog fragments in `changes/`, towncrier's, one per story or
fix (crew#1010), and any entries still under `## [Unreleased]` from before them.

**The standard:** Semantic Versioning, with releases cut in the style of Release Please
or semantic-release: what waits is released without waiting for an epic, a PATCH when
it holds only fixes (Keep a Changelog's `Fixed` and `Security`).

**The rule with an epic:** an epic's release ships everything that waits (ADR 0019), so
while an epic is in flight its release carries the fixes, and none is opened for them:
two releases a day apart would ship the same lines twice. In flight means split and not
yet released: a release Task open in the repository, or an open epic with stories under
it whose release Task isn't open yet. An approved epic not yet split holds nothing,
since it may wait weeks behind another Goal's.

**The same path as an epic's release:** DevOps's release Task, kind `release`, so its
session, the Code Reviewer's release review and the Sponsor's approval are the same.
One per version it follows, keyed by that version, never opened twice for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from crew_org.events import EventKind, EventSink
from crew_org.flows import role_tasks as rt
from crew_org.flows.declared_changes import RELEASE_DONE, RELEASE_STEP
from crew_org.llm import reraise_if_down
from crew_org.tools import changelog
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient

RULE = "decision.unreleased.unreleased_releases"
KEY_PREFIX = "unreleased-"

PATCH, MINOR, MAJOR = "PATCH", "MINOR", "MAJOR"
# Keep a Changelog's sections, by the SemVer part each needs. A section it doesn't name
# is read as a MINOR: a change, whatever it's called.
_FIXES = frozenset({"fixed", "security"})
_BREAKING = frozenset({"removed"})

_HEADING = re.compile(r"^##\s+\[([^\]]+)\]")
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclass
class Waiting:
    """What waits to be released, by its Keep a Changelog section."""

    entries: list[tuple[str, str]] = field(default_factory=list)
    # The newest version released, or "" for none.
    released: str = ""

    @property
    def bump(self) -> str:
        sections = {s.lower() for s, _ in self.entries}
        if sections & _BREAKING or any("BREAKING" in e for _, e in self.entries):
            return MAJOR
        return PATCH if sections <= _FIXES else MINOR


def waiting(
    text: str | None,
    fragments: list[changelog.Fragment] | tuple[()] = (),
    cfg: changelog.Config | None = None,
) -> Waiting:
    """Every entry that waits: each fragment's, and each under a `## [Unreleased]`
    heading, however many merges left; and the newest version released."""
    found = Waiting(entries=changelog.unreleased(text))
    for line in (text or "").splitlines():
        heading = _HEADING.match(line)
        if heading and _VERSION.match(heading.group(1).strip()):
            found.released = heading.group(1).strip()
            break
    issue_format = (cfg or changelog.Config()).issue_format
    found.entries += [
        (changelog.KINDS[f.kind], changelog.entry(f, issue_format)) for f in fragments
    ]
    return found


def fragments_at(
    issues: IssueClient, repo: str, ref: str, cfg: changelog.Config
) -> list[changelog.Fragment]:
    """The fragments in `repo` at `ref`, each with its text."""
    found = []
    for name in sorted(issues.list_dir(repo, cfg.directory, ref)):
        named = changelog.parse(f"{cfg.directory}/{name}", cfg.directory)
        if named is not None:
            text = issues.file_at(repo, named.path, ref) or ""
            found.append(changelog.Fragment(named.path, named.issue, named.kind, text))
    return found


def next_version(released: str, bump: str) -> str:
    """The version a PATCH or a MINOR moves to. A MAJOR is the Product Owner's call, as
    an epic's is where its record makes one: ""."""
    parts = _VERSION.match(released)
    if parts is None or bump == MAJOR:
        return ""
    major, minor, patch = (int(p) for p in parts.groups())
    return f"{major}.{minor}.{patch + 1}" if bump == PATCH else f"{major}.{minor + 1}.0"


def unreleased_title(repo: str, version: str = "") -> str:
    """A release Task's title, and its pull request's, when no epic is behind it.
    `repo` is owner/name, so the reference links wherever the title is read."""
    return f"Release {version + ' ' if version else ''}of {repo}'s unreleased changes"


def in_flight(issues: IssueClient, repo: str, cards: list[Card]) -> str:
    """Why an epic's release will ship what waits, or "" when none will."""
    for issue in issues.labelled(repo, rt.DEVOPS.label):
        found = rt.marked(issue.get("body"))
        if issue.get("state") == "open" and found is not None and found.kind == rt.RELEASE:
            return f"waits for DevOps's release Task {issues.owner}/{repo}#{issue['number']}"
    for epic in cards:
        if epic.repo != repo or epic.work_type != "Epic" or epic.state == "CLOSED":
            continue
        under = [c for c in cards if c.repo == repo and c.parent == epic.number]
        if any(c.work_type == "Story" for c in under) and not any(
            rt.role_of(c) is rt.DEVOPS for c in under
        ):
            return f"waits for epic {issues.owner}/{repo}#{epic.number}'s release"
    return ""


def _body(repo: str, found: Waiting, version: str) -> str:
    bump = found.bump
    if bump == PATCH:
        why_version = f"It holds only fixes, so it's a PATCH: {found.released} to {version}."
    elif bump == MINOR:
        why_version = (
            f"It holds more than fixes, so it's a MINOR: {found.released} to {version}, "
            "or the Product Owner's call where a record makes one."
        )
    else:
        why_version = (
            f"It removes or breaks something, so it's a MAJOR after {found.released}: the "
            "Product Owner's call, as the version is where a record makes one."
        )
    return rt.render(
        rt.DEVOPS,
        kind=rt.RELEASE,
        key=f"{KEY_PREFIX}{found.released}",
        why=f"`{repo}` has {len(found.entries)} changelog entries merged since "
        f"{found.released}, and no epic in flight will release them: a bug's fix has no "
        "epic. What's merged and never released reaches nobody (mqucifer/crew#961).\n\n"
        + "\n".join(entry for _, entry in found.entries),
        work="**Release:** release every changelog entry that waits, those above and any "
        f"merged since, as an epic's release does (ADR 0019). {why_version} "
        + rt.CHANGELOG_STEP
        + "\n\n**CI:** CI runs green on the release, with nothing skipped that it should run.\n\n"
        + RELEASE_STEP,
        done_when=[
            rt.CHANGELOG_DONE,
            "The version is bumped where the project keeps it",
            RELEASE_DONE,
        ],
    )


@dataclass
class Unreleased:
    opened: list[tuple[str, int]] = field(default_factory=list)
    # (repo, why) for what waits that this pass releases nothing for.
    waiting: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def unreleased_releases(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    repos: set[str],
    cards: list[Card],
) -> Unreleased:
    """Each project with changelog entries waiting that no epic in flight will release gets
    DevOps's release Task for them, once per version it follows. What waits on an epic,
    or on a first release, is noted; what can't be read or opened is held and tried
    again next pass."""
    result = Unreleased()
    for repo in sorted(repos):
        try:
            branch = issues.default_branch(repo)
            cfg = changelog.config(issues.file_at(repo, "pyproject.toml", branch))
            text = issues.file_at(repo, cfg.filename, branch)
            found = waiting(text, fragments_at(issues, repo, branch, cfg), cfg)
            if not found.entries:
                continue
            why = (
                "no version released yet: its first epic's release cuts one"
                if not found.released
                else in_flight(issues, repo, cards)
            )
            if why:
                result.waiting.append((repo, why))
                sink.note(EventKind.NOTE, f"{repo}: changelog entries {why}", repo=repo, rule=RULE)
                continue
            full = f"{issues.owner}/{repo}"
            version = next_version(found.released, found.bump)
            opened = rt.open_task(
                issues,
                board,
                sink,
                repo=repo,
                role=rt.DEVOPS,
                kind=rt.RELEASE,
                key=f"{KEY_PREFIX}{found.released}",
                title=unreleased_title(full, version),
                body=_body(repo, found, version),
                points=rt.RELEASE_POINTS,
                cards=cards,
                event="changelog.unreleased",
                rule=RULE,
                detail={"released": found.released, "bump": found.bump, "version": version},
            )
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            result.failed.append(
                (
                    repo,
                    rt.not_opened(
                        sink, repo=repo, role=rt.DEVOPS, kind=rt.RELEASE, card=None, why=exc
                    ),
                )
            )
            continue
        if opened.new:
            result.opened.append((repo, opened.number))
    return result
