"""The UX Designer reviews each release as its readers meet it (ADR 0034, crew#830).

The UX Designer wrote criteria before a story was built and never saw what was built.
The site's first live review (2026-10-16) found what no criterion caught: the
phone-width bug mqucifer/crew-presentation#98, filed by hand. So a release going live
opens the UX Designer's review Task, one per repository and version. Its session looks
at the published pages at the reader's widths and files what it finds as corrections:
bugs, or Tasks for the role they belong to. It never blocks a merge (the Sponsor,
2026-10-17).

**Live** is read from GitHub, not from a release workflow: crew-presentation has none,
and publishes to GitHub Pages. A version is live once its tag has a GitHub Release and
a Pages deployment succeeded after that Release was published. Where the pages are is
GitHub's to say (`pages`); who reads them is the record's intent (`serves`), so a
project gets reviews when it has both (the operator, 2026-10-24).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from crew_org.events import EventSink
from crew_org.flows.release_check import changelog_section
from crew_org.flows.role_tasks import REVIEW, UX, not_opened, open_task, render
from crew_org.llm import reraise_if_down
from crew_org.project import RECORD_PATH, ProjectRecordError, parse
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient

REVIEW_POINTS = 2
_PYPROJECT_VERSION = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


@dataclass
class Reviews:
    opened: list[tuple[str, int]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def version_of(issues: Any, repo: str, branch: str) -> str:
    """The version the default branch says it is: `pyproject.toml`'s, else `package.json`'s."""
    found = _PYPROJECT_VERSION.search(issues.file_at(repo, "pyproject.toml", branch) or "")
    if found:
        return found.group(1)
    try:
        package = json.loads(issues.file_at(repo, "package.json", branch) or "{}")
        return str(package.get("version") or "")
    except (ValueError, AttributeError):
        return ""


def readers(issues: Any, repo: str, branch: str) -> list[Any]:
    """Who the project's pages serve, as its record's intent says: none when it has no
    record, or one that can't be read."""
    text = issues.file_at(repo, RECORD_PATH, branch)
    if not text:
        return []
    try:
        return list(parse(text).intent.scope.serves)
    except (ProjectRecordError, ValueError):
        return []


def _when(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


@dataclass(frozen=True)
class Live:
    """A version whose site readers are served now."""

    version: str
    site: str
    readers: list[Any]
    release: dict[str, Any]


def live(issues: Any, repo: str) -> Live | None:
    """The version of `repo` its readers meet now, or None: no reader in the record, no
    Pages site, nothing released, or the release not deployed yet."""
    site = issues.pages(repo)
    if not site:
        return None
    branch = issues.repository(repo)["default_branch"]
    serves = readers(issues, repo, branch)
    if not serves:
        return None
    version = version_of(issues, repo, branch)
    if not version or version == "0.0.0":
        return None
    release = issues.release_for_tag(repo, f"v{version}")
    if release is None or not release.get("published_at"):
        return None
    since = issues.pages_live_since(repo)
    # A deployment before the Release built the version before it.
    if since is None or _when(since) < _when(str(release["published_at"])):
        return None
    return Live(version, str(site.get("html_url") or ""), serves, release)


def review_body(issues: Any, repo: str, found: Live) -> str:
    """The review Task: what went live, for whom, and what the review is."""
    tag = f"v{found.version}"
    entries = changelog_section(issues.file_at(repo, "CHANGELOG.md", tag) or "", found.version)
    who = "\n".join(
        f"- **{r.who}**, who needs: " + "; ".join(r.needs) if r.needs else f"- **{r.who}**"
        for r in found.readers
    )
    return render(
        UX,
        kind=REVIEW,
        key=tag,
        why=f"`{tag}` is live at {found.site}. The UX Designer reviews each release as its "
        "readers meet it, and files what it finds as corrections; it never blocks a merge "
        "(the Sponsor, 2026-10-17).",
        work=f"**Who reads it** (the project's record):\n\n{who}\n\n"
        f"**What `{tag}` changed** (`CHANGELOG.md`):\n\n"
        + ("\n".join(entries) if entries else "(no entries)")
        + "\n\nLook at the published pages as each reader meets them, at the widths the "
        "Goals name (375, 768 and 1280 when they name none). Judge them against what each "
        "reader needs, and the UX criteria, by Nielsen's usability heuristics and WCAG "
        "2.2's checks. Each correction names the page, the width, what you saw and what "
        "the reader should meet instead, and the heuristic or success criterion it breaks.",
        done_when=[
            "Each page a reader meets is looked at, at each width",
            "Each correction is filed with its page, width, standard and screenshot",
        ],
    )


def release_review_tasks(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    repos: set[str],
    cards: list[Card],
) -> Reviews:
    """Each repository whose release went live gets the UX Designer's review Task, once
    per version. One that can't be read, or whose Task GitHub refused, is said as held
    and tried again next pass."""
    result = Reviews()
    for repo in sorted(repos):
        try:
            found = live(issues, repo)
            if found is None:
                continue
            opened = open_task(
                issues,
                board,
                sink,
                repo=repo,
                role=UX,
                kind=REVIEW,
                key=f"v{found.version}",
                title=f"UX review of v{found.version} as its readers meet it",
                body=review_body(issues, repo, found),
                points=REVIEW_POINTS,
                cards=cards,
                event="release.live",
                rule="decision.ux_review.release_review_tasks",
                detail={"version": found.version},
            )
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            result.failed.append(
                (repo, not_opened(sink, repo=repo, role=UX, kind=REVIEW, card=None, why=exc))
            )
            continue
        if opened.new:
            result.opened.append((repo, opened.number))
    return result
