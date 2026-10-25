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
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from crew_org.columns import BLOCKED, DONE, IN_PROGRESS
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows import artifacts
from crew_org.flows.moves import move_card
from crew_org.flows.release_check import changelog_section
from crew_org.flows.role_tasks import (
    REVIEW,
    UX,
    file_findings,
    marked,
    not_opened,
    open_task,
    render,
    role_of,
)
from crew_org.llm import reraise_if_down
from crew_org.project import RECORD_PATH, ProjectRecordError, parse
from crew_org.session import role_tools
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient
from crew_org.tools.look import Fetched, NotLooked

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


# --- the review, worked in a session ---------------------------------------------------------

# Its corrections are the review's whole work, so a release files more than a story's
# one per owner: started high, to be lowered from what reviews file (crew#830).
CORRECTIONS_PER_OWNER = 10
BY = UX.name


def is_review(card: Card) -> bool:
    """Is this the UX Designer's Task, worked by its review session, not delivered?

    Its review of a release, or a finding another role filed for it about what a reader
    sees: either is a look at the published pages, filed as corrections. Its role
    changes no file, so the Developer's path has nothing for it.
    """
    return role_of(card) is UX


def shot_name(version: str, page: str, width: int) -> str:
    """A screenshot's name on the Release: one per page and width, so a second look
    replaces nothing."""
    return f"ux-{version}-{re.sub(r'[^A-Za-z0-9]+', '-', page).strip('-')}-{width}.png"


def screenshot(issues: Any, release: dict[str, Any], version: str, c: Any, shot: Any) -> str:
    """The correction's screenshot on the reviewed Release, as Markdown, or why not."""
    name = shot_name(version, c.page, c.width)
    try:
        there = next((a for a in release.get("assets") or [] if a.get("name") == name), None)
        if there is None:
            there = issues.upload_release_asset(release, shot, name=name, content_type="image/png")
            release.setdefault("assets", []).append(there)
    except Exception as exc:  # noqa: BLE001 - the correction is filed without it
        reraise_if_down(exc)
        return f"(The screenshot wasn't uploaded: {str(exc)[:120]})"
    return f"![`{c.page}` at {c.width}px]({there['browser_download_url']})"


@dataclass(frozen=True)
class Found:
    """A correction, as `file_findings` files a finding."""

    what: str
    where: str
    evidence: str
    owner: str


def as_found(site: str, c: Any, image: str) -> Found:
    return Found(
        what=f"`{c.page}` at {c.width}px: {c.saw}",
        where=f"{site.rstrip('/')}/{c.page} at {c.width}px",
        evidence=f"{c.saw}\n\n**The reader should meet:** {c.instead}\n\n"
        f"**Standard:** {c.standard}\n\n{image}",
        owner=c.owner,
    )


def review(
    card: Card,
    *,
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    ws: Any,
    repo: str,
) -> str:
    """Work the review Task in a session, file its corrections, and close it: "done", or
    "blocked" with why on the Task. Never blocks anything but itself.

    The session reads the default branch, and looks at the published site, where each
    width's screenshot is kept for the correction that cites it.
    """
    from crew_org.crews import ux_review_crew  # noqa: PLC0415
    from crew_org.session import steps  # noqa: PLC0415

    number = card.number or 0
    body = issues.get(repo, number).get("body") or ""
    found_as = marked(body)
    if found_as is not None and found_as.kind == REVIEW:
        version, round_key = found_as.key, found_as.key
    else:
        # A finding is looked at on the version readers meet now.
        now = live(issues, repo)
        version, round_key = (f"v{now.version}", f"t{number}") if now else ("", "")
    sink.emit(
        CrewEvent(
            kind=EventKind.AGENT_STARTED,
            role=BY,
            card=number,
            summary=f"release review of {version}",
            detail={"repo": repo},
        )
    )
    shots = Path(tempfile.mkdtemp(prefix="ux-review-"))
    fetched = Fetched()
    try:
        release = issues.release_for_tag(repo, version) if version else None
        if release is None:
            return _blocked(
                board,
                issues,
                sink,
                card,
                repo=repo,
                why=f"There's no live release with a GitHub Release to review ({version!r}).",
            )
        site = str((issues.pages(repo) or {}).get("html_url") or "")
        reading = steps.Reading.at(ws.at_main(), repo, None, issues)
        seeing = role_tools.Context(reading.root, repo, issues, looked=fetched, keep=shots)
        # Fetched first: a session that can't see the published site can file nothing,
        # and its proof spent 62 calls finding that out (crew#830).
        unseen = _unfetchable(fetched, issues, repo)
        if unseen:
            return _blocked(board, issues, sink, card, repo=repo, why=unseen)

        def seen(answer: Any) -> None:
            ux_review_crew.check_seen(answer, seeing.shots)

        answer = steps.run(
            ux_review_crew.STEP,
            {"task": ux_review_crew.section(version, body)},
            reading,
            about=body,
            check=seen,
            context=seeing,
        )
        found = [
            as_found(
                site, c, screenshot(issues, release, version, c, seeing.shots[(c.page, c.width)])
            )
            for c in answer.corrections
        ]
        filed = file_findings(
            issues,
            board,
            sink,
            repo=repo,
            story=card,
            found=found,
            by=BY,
            round_key=round_key,
            cards=board.cards(),
            cap=CORRECTIONS_PER_OWNER,
            found_while=f"The {BY} found this reviewing `{version}` as its readers meet it "
            f"(#{number}).",
        )
    except Exception as exc:  # noqa: BLE001
        reraise_if_down(exc)
        return _blocked(board, issues, sink, card, repo=repo, why=f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(shots, ignore_errors=True)
        fetched.close()
    said = (
        f"**The {BY}'s review of `{version}`.**\n\n{answer.readers_meet.strip()}\n\n"
        + (
            "**Corrections filed:** " + ", ".join(f"#{n}" for n in filed)
            if filed
            else "**No corrections filed.**"
        )
        + (
            f"\n\n{len(found) - len(filed)} more weren't filed: the events say why."
            if len(found) > len(filed)
            else ""
        )
    )
    artifacts.comment(issues, sink, repo=repo, number=number, body=said, by=BY)
    issues.close(repo, number, reason="completed")
    move_card(
        board,
        sink,
        item_id=card.item_id,
        to=DONE,
        by=BY,
        card=number,
        frm=IN_PROGRESS,
        summary=f"{version} reviewed: {len(filed)} corrections filed",
        rule="decision.ux_review.reviewed",
    )
    sink.emit(
        CrewEvent(
            kind=EventKind.AGENT_FINISHED,
            role=BY,
            card=number,
            summary=f"{version} reviewed: {len(filed)} corrections filed",
            detail={"repo": repo},
        )
    )
    return "done"


def _unfetchable(fetched: Fetched, github: Any, repo: str) -> str:
    """Why the published site can't be fetched for `look`, or "" when it can."""
    try:
        fetched.site(github, repo)
    except NotLooked as exc:
        return f"The published site can't be looked at, so no session was started: {exc}"
    return ""


def _blocked(
    board: ProjectClient, issues: IssueClient, sink: EventSink, card: Card, *, repo: str, why: str
) -> str:
    """A review that couldn't be worked goes to a person, as a Task does: it isn't split."""
    number = card.number or 0
    move_card(
        board,
        sink,
        item_id=card.item_id,
        to=BLOCKED,
        by=BY,
        card=number,
        frm=IN_PROGRESS,
        summary=why[:80],
        kind=EventKind.CARD_BLOCKED,
        rule="decision.ux_review.blocked",
    )
    artifacts.label(issues, sink, repo=repo, number=number, by=BY, add=["blocked"])
    artifacts.comment(
        issues, sink, repo=repo, number=number, body=f"**Blocked.** {why[:400]}", by=BY
    )
    return "blocked"
