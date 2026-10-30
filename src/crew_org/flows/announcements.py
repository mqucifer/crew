"""A producer's announced new major opens an epic to adopt it in each project that uses it
(crew#960, crew#969).

A producer announces a breaking change and ships it a week later (ADR 0008 in
mqucifer/project-print). The crew's first, `delivery-history` 2.0.0 (crew#959), opened a
Task that only ruled whether crew-presentation could still read it, and the ruling named
the work and stopped: mqucifer/crew-presentation#210 said adopting it "is the follow-up
story/Task, not this one", and nothing could file one. As in Renovate or Dependabot, the
notice is the work item now, and the week is the time to do it.

**Read from what the producer publishes,** never a list in the crew: `announced.json`
beside the contract files a project's record names, on the producer's default branch.
Each version in it says the major it replaces, the date, where its contract is, which
section of which file is its changelog, and its mock's files. A producer that publishes
none files nothing, and the pass says so.

**One technical epic per project, producer and version,** in Needs Refinement, past the
Sponsor's gate (crew#192): the producer set the date. It's never filed again, even once
closed, and a ruling Task crew#960 filed counts as filed. Only a project that pins the
major being replaced gets one: what it pins is read from the producer's `versions.json`
at the pinned release, leaving out what was only announced there.

**Its refinement does the ruling.** A split with nothing to change says so in
`already_delivered`, with where the code reads it, and that closes the epic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from crew_org.events import EventKind, EventSink
from crew_org.flows import role_tasks as rt
from crew_org.flows.board_flow import TECHNICAL
from crew_org.flows.revisit import file_technical_epic, place_technical_epic
from crew_org.llm import reraise_if_down
from crew_org.project import RECORD_PATH, ProjectRecordError, Use, parse
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient
from crew_org.usage import (
    Announcement,
    Read,
    Unreadable,
    announcing,
    producer_file,
    section,
)

# What crew#960's ruling Task said it was: mqucifer/crew-presentation#207 is one.
RULING = "announced"
_ADOPTION = re.compile(r"<!-- crew:adoption key=(\S+) -->")


def adoption_key(body: str | None) -> str | None:
    """Which announcement an epic adopts, by what its body says, or None."""
    found = _ADOPTION.search(body or "")
    return found.group(1) if found else None


def _filed_key(issue: dict[str, Any]) -> str | None:
    """Each announcement is filed once per project: the one this issue is filed for, or None."""
    key = adoption_key(issue.get("body"))
    if key is not None:
        return key
    ruled = rt.marked(issue.get("body"))
    return ruled.key if ruled is not None and ruled.kind == RULING else None


@dataclass(frozen=True)
class Item:
    """What a pass files in a project for one announced version."""

    repo: str
    key: str
    title: str
    body: str
    version: str
    # What's already filed for it, open or closed, or None.
    filed: int | None = None
    # An open epic of this announcement, which a board that refused it may still lack.
    epic: dict[str, Any] | None = None


@dataclass
class Plan:
    repo: str
    items: list[Item] = field(default_factory=list)
    # Producers the project uses that publish no announcements.
    unannounced: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


@dataclass
class Announced:
    opened: list[tuple[str, int]] = field(default_factory=list)
    unannounced: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def _body(use: Use, *, key: str, found: Announcement, changelog: str, tag: str) -> str:
    where = f"https://github.com/{use.repo}/tree/HEAD/{found.contract}"
    package, version = found.package, found.version
    return (
        f"<!-- crew:adoption key={key} -->\n\n"
        f"**The work:** this project reads `{package}` {version} by **{found.on}**, when it "
        f"replaces {found.replaces} whether the project has moved or not (ADR 0008 in "
        "mqucifer/project-print).\n\n"
        f"**What the project does now:** it pins `{tag}` in `{use.pin}`, which follows "
        f"{found.pinned}.\n\n"
        f"**The new contract**, its schemas and mock: {where}\n\n"
        "**What refinement decides:** whether anything this project reads leaves or changes "
        f"shape, against the new contract's schemas and mock (ADR 0021). If nothing it reads "
        "changes, this epic closes with that said. Staying on "
        f"{found.pinned} and losing what changed is the project's to decide (ADR 0008), written "
        "on this epic as a decision, never the default when nobody acts.\n\n"
        f"**{use.repo}'s changelog for {version}**, from its `{found.changelog_file}`, "
        f'"{found.changelog_section}":\n\n{changelog}\n\n'
        "Technical work: the producer's announcement set the date, so it goes straight to "
        "refinement rather than to the Sponsor's gate (mqucifer/crew#192)."
    )


def _items(repo: str, use: Use, pin: str | None, read: Read, ref: str) -> list[Item] | None:
    """What the project files for `use`'s announcements; None when it publishes none."""
    found = announcing(use, pin, read, ref)
    if found is None:
        return None
    items = []
    for each in found:
        text = read(use.repo, each.changelog_file, ref)
        changelog = section(text, each.changelog_section) if text is not None else None
        if changelog is None:
            raise Unreadable(
                f"{use.repo}'s `{each.changelog_file}` has no section "
                f'"{each.changelog_section}" for {each.version}'
            )
        key = f"{use.repo}:{each.package}@{each.version}"
        items.append(
            Item(
                repo=repo,
                key=key,
                title=f"Adopt {each.package} {each.version} before it replaces "
                f"{each.replaces} on {each.on}",
                body=_body(use, key=key, found=each, changelog=changelog, tag=str(pin)),
                version=each.version,
            )
        )
    return items


def _pin(issues: Any, repo: str, use: Use, branch: str) -> str | None:
    text = issues.file_at(repo, use.pin, branch) if use.pin else None
    return next((line.strip() for line in (text or "").splitlines() if line.strip()), None)


def _already_filed(issues: Any, repo: str) -> dict[str, dict[str, Any]]:
    """What's filed for each announcement, open or closed: its adoption epic, or the ruling
    Task crew#960 filed before there were epics."""
    filed: dict[str, dict[str, Any]] = {}
    for label in (rt.ARCHITECT.label, TECHNICAL):
        for issue in issues.labelled(repo, label):
            key = _filed_key(issue)
            if key is not None:
                filed[key] = issue
    return filed


def plan(issues: Any, repo: str, *, read: Read = producer_file, ref: str = "HEAD") -> Plan:
    """What a pass would file in `repo` for its producers' announcements, opening nothing.

    `ref` is the producers' ref to read the announcements at: a branch shows what an
    announcement not yet merged would file.
    """
    result = Plan(repo)
    branch = issues.default_branch(repo)
    text = issues.file_at(repo, RECORD_PATH, branch)
    if not text:
        return result
    try:
        uses = parse(text).intent.scope.uses
    except (ProjectRecordError, ValueError) as exc:
        result.failed.append(f"its record can't be read: {exc}"[:200])
        return result
    filed = None
    for use in uses:
        # A producer's own announcement is its own work, not a reader's.
        if use.repo == f"{issues.owner}/{repo}":
            continue
        try:
            found = _items(repo, use, _pin(issues, repo, use, branch), read, ref)
        except (Unreadable, httpx.HTTPError) as exc:
            result.failed.append(f"{use.repo}'s announcements unread: {exc}"[:200])
            continue
        if found is None:
            result.unannounced.append(use.repo)
            continue
        if found and filed is None:
            filed = _already_filed(issues, repo)
        for item in found:
            issue = (filed or {}).get(item.key)
            result.items.append(
                replace(
                    item,
                    filed=int(issue["number"]) if issue else None,
                    epic=issue
                    if issue and adoption_key(issue.get("body")) and issue.get("state") == "open"
                    else None,
                )
            )
    return result


def _placed_late(board: Any, sink: EventSink, *, repo: str, item: Item, cards: list[Card]) -> bool:
    """An epic filed by a pass the board refused goes on the board now, not filed again."""
    if item.epic is None or item.filed is None:
        return False
    if any(c.repo == repo and c.number == item.filed for c in cards):
        return False
    place_technical_epic(board, sink, repo=repo, issue=item.epic, summary=_summary(item))
    return True


def _summary(item: Item) -> str:
    return f"technical epic — {item.title[:50]}"


def _said(sink: EventSink, *, repo: str, number: int, item: Item) -> None:
    sink.note(
        EventKind.NOTE,
        f"{repo}#{number}: {item.title}"[:120],
        repo=repo,
        card=number,
        rule="decision.announcements.announced_epics",
    )


def announced_epics(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    repos: set[str],
    cards: list[Card],
    read: Read = producer_file,
) -> Announced:
    """Each project gets one technical epic for each announced major that replaces the one
    it pins, once. A producer that publishes none is noted; one that can't be read, or an
    epic GitHub or the board refused, is held and tried again next pass."""
    result = Announced()
    for repo in sorted(repos):
        try:
            planned = plan(issues, repo, read=read)
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            result.failed.append((repo, f"announcements unread: {exc}"[:200]))
            continue
        result.failed += [(repo, why) for why in planned.failed]
        for producer in planned.unannounced:
            result.unannounced.append((repo, producer))
            sink.note(
                EventKind.NOTE,
                f"{repo}: {producer} publishes no announcements",
                repo=repo,
                producer=producer,
                rule="decision.announcements.none_published",
            )
        for item in planned.items:
            try:
                if item.filed is not None:
                    if not _placed_late(board, sink, repo=repo, item=item, cards=cards):
                        continue
                    number = item.filed
                else:
                    number = int(
                        file_technical_epic(
                            issues,
                            board,
                            sink,
                            repo=repo,
                            title=item.title,
                            body=item.body,
                            summary=_summary(item),
                        )["number"]
                    )
            except Exception as exc:  # noqa: BLE001
                reraise_if_down(exc)
                why = f"{repo}'s epic for {item.key} not filed or not placed: {exc}"[:200]
                sink.note(
                    EventKind.NOTE, why, repo=repo, rule="decision.announcements.announced_epics"
                )
                result.failed.append((repo, why))
                continue
            _said(sink, repo=repo, number=number, item=item)
            result.opened.append((repo, number))
    return result


def render_plan(found: Plan) -> str:
    """The dry run, as `crew announced` prints it."""
    lines: list[str] = []
    for item in found.items:
        lines += [
            f"Already filed as {found.repo}#{item.filed}: {item.title}"
            if item.filed is not None
            else f"Would file in {found.repo}: {item.title}",
            "",
            item.body,
            "",
        ]
    if not found.items:
        lines.append(f"Nothing to file in {found.repo}.")
    lines += [f"{producer} publishes no announcements." for producer in found.unannounced]
    lines += [f"Held: {why}" for why in found.failed]
    return "\n".join(lines).rstrip()
