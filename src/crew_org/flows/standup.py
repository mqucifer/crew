"""The standup: what a tick did, recorded where the retro can read it (#79).

§12 assumed a standup and nothing wrote one: the Scrum Master held
`write_standup` and there was no function behind it.

Written mechanically, from the tick's own result. Every fact in it is already
structured — what moved, what was blocked, what is waiting and why — so a model
call would add cost and a chance to get a fact wrong, and nothing else. The
judgment happens once per sprint instead: the retro reads the sprint's
standups, and the model analyses how the sprint *went* rather than only how it
ended.

One issue per sprint, on the crew repository, labelled `standup`; each tick
adds a comment. Ticks ran 32 times on 2026-09-19 and sprints are one day, so an
issue per tick would bury the one thing worth finding. A tick in which nothing
happened says so, once: a run of quiet ticks is one comment, not thirty.

The standup is a record, never an input to what the crew does next. The board
is the source of truth, and a second one the crew acted on could disagree
with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from crew_org.columns import BLOCKED, INBOX
from crew_org.events import CrewEvent, EventKind, EventSink, blocked_since, replay_dir
from crew_org.flows.artifacts import link_references, signed
from crew_org.process import ProcessRules
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, many_repos

ROLE = "Scrum Master"
EPIC = "Epic"
STANDUP_LABEL = "standup"
QUIET_MARKER = "<!-- crew:standup-quiet -->"

# The retro reads the sprint's standups; this bounds how much. Whole standups
# only, newest kept, and the number dropped is said — a retro should not reason
# from half a standup.
MAX_STANDUP_CHARS = 24_000


def marker(sprint: str) -> str:
    return f"<!-- crew:standup sprint={sprint} -->"


@dataclass
class Standup:
    text: str
    quiet: bool


def write_standup(
    result,
    *,
    sprint: str,
    at: datetime,
    waiting: list[str],
    aging: list[tuple[str, int]],
    awaiting: list[str] = (),
) -> Standup:
    """The Scrum Master's standup for one tick, from the tick's own result."""
    from crew_org.flows.loop import PHASES  # noqa: PLC0415

    names = [name for name, _run in PHASES]
    moved = [
        f"- **{name}**: "
        + (", ".join(f"{v} {k}" for k, v in result.totals(name).items() if v) or "moved")
        for name in names
        if result.moved_in(name)
    ]
    blocked = [f"- **{name}**: {line}" for name in names for line in result.blocked(name)]
    held = [f"- **{name}**: {line}" for name in names for line in result.held(name)]
    failed = [f"- **{o.name}**: {o.error}" for o in result.failed]
    breaches = [
        f"- {column}: {count} against a limit of {limit}"
        for column, (count, limit) in sorted(result.over_limit.items())
    ]
    quiet = not (moved or blocked or failed)

    passes = f"{result.passes} pass" + ("es" if result.passes != 1 else "")
    state = "settled" if result.settled else "stopped at the pass cap"
    lines = [f"**Tick at {at:%H:%M} UTC** — {passes}, {state}."]
    if quiet:
        lines += ["", "Nothing moved."]
    sections = [
        ("Moved", moved),
        ("Blocked this tick", blocked),
        ("Failed", failed),
        ("Waiting", held),
        ("Waiting on a person", [f"- {name}" for name in waiting]),
        (
            "Awaiting your approval (epics at the gate; they move when you decide)",
            [f"- {name}" for name in awaiting],
        ),
        (
            "Blocked past the threshold",
            [f"- {name}: blocked {days} days" for name, days in aging],
        ),
        ("Over a WIP limit", breaches),
    ]
    for title, items in sections:
        if items:
            lines += ["", f"**{title}**", *items]
    return Standup(text="\n".join(lines), quiet=quiet)


def _at_the_gate(card: Card) -> bool:
    """An epic waiting for the Sponsor's approval: the one job the Sponsor has."""
    return card.status == INBOX and card.work_type == EPIC


def waiting_on_a_person(cards: list[Card]) -> list[str]:
    """Open cards stuck on a person: Blocked, or flagged `needs:human`.

    Not an epic at the Sponsor's gate. It carries `needs:human` too, and was
    listed here until three standups in a row showed 17 of them "waiting on a
    person" and the retro concluded they were stuck (#122). Waiting for a
    decision is what the gate is for.
    """
    qualify = many_repos(cards)
    return sorted(
        card.name(qualify=qualify)
        for card in cards
        if card.state != "CLOSED"
        and not _at_the_gate(card)
        and (card.status == BLOCKED or card.needs_human)
    )


def awaiting_approval(cards: list[Card]) -> list[str]:
    """Epics at the Sponsor's gate, by name: a queue, not a problem."""
    qualify = many_repos(cards)
    return sorted(
        card.name(qualify=qualify)
        for card in cards
        if card.state != "CLOSED" and _at_the_gate(card)
    )


def aging_blocked(
    cards: list[Card], rules: ProcessRules, events_dir: Path, now: datetime
) -> list[tuple[str, int]]:
    """Cards blocked past `blocked_aging_days`, named, as §12 asks every standup to."""
    blocked = blocked_since(replay_dir(events_dir), blocked_column=rules.blocked_column)
    aging = rules.aging_blocked(blocked, now=now)
    qualify = many_repos(cards)
    by_number = {c.number: c for c in cards}
    return sorted(
        (by_number[n].name(qualify=qualify) if n in by_number else f"#{n}", days)
        for n, days in aging.items()
    )


def find_standup(issues: IssueClient, crew_repo: str, sprint: str) -> int | None:
    for issue in issues.labelled(crew_repo, STANDUP_LABEL):
        if marker(sprint) in (issue.get("body") or ""):
            return issue["number"]
    return None


def record_standup(
    issues: IssueClient,
    sink: EventSink,
    standup: Standup,
    *,
    sprint: str,
    crew_repo: str,
    delivery_repos: list[str] | tuple[str, ...] = (),
) -> tuple[int, bool]:
    """Add this tick's standup to the sprint's issue. (issue, whether it commented)."""
    number = find_standup(issues, crew_repo, sprint)
    if number is None:
        issues.ensure_label(
            crew_repo,
            STANDUP_LABEL,
            color="0e8a16",
            description="A sprint's standups, one comment per tick",
        )
        number = issues.create(
            crew_repo,
            f"Standup: {sprint}",
            "\n".join(
                [
                    marker(sprint),
                    f"The standups for **{sprint}**: one comment per tick, written by the "
                    "Scrum Master from what the tick did. The retro reads them at sprint "
                    "close, and closes this issue.",
                ]
            ),
            labels=[STANDUP_LABEL],
        )["number"]
    elif standup.quiet:
        comments = issues.comments(crew_repo, number)
        if comments and QUIET_MARKER in (comments[-1].get("body") or ""):
            return number, False

    # Written on the crew repository about delivery cards: a bare #31 here
    # would link to crew#31 (#118).
    text = link_references(
        standup.text, owner=issues.owner, home=crew_repo, delivery=list(delivery_repos)
    )
    body = f"{QUIET_MARKER}\n{text}" if standup.quiet else text
    issues.comment(crew_repo, number, signed(body, ROLE))
    sink.emit(
        CrewEvent(
            kind=EventKind.STANDUP_WRITTEN,
            role=ROLE,
            card=number,
            summary=f"standup for {sprint}" + (" — nothing moved" if standup.quiet else ""),
            detail={"repo": crew_repo, "sprint": sprint, "quiet": standup.quiet},
        )
    )
    return number, True


def standups_for_retro(issues: IssueClient, crew_repo: str, sprint: str) -> tuple[int | None, str]:
    """The sprint's standup issue, and its standups as text for the retro to read."""
    number = find_standup(issues, crew_repo, sprint)
    if number is None:
        return None, ""
    bodies = [
        (c.get("body") or "").replace(QUIET_MARKER, "").strip()
        for c in issues.comments(crew_repo, number)
    ]
    kept: list[str] = []
    spent = 0
    for body in reversed([b for b in bodies if b]):
        if spent + len(body) > MAX_STANDUP_CHARS and kept:
            break
        kept.append(body)
        spent += len(body)
    dropped = len([b for b in bodies if b]) - len(kept)
    text = "\n\n---\n\n".join(reversed(kept))
    if dropped:
        text = f"_{dropped} earlier standup(s) omitted for length._\n\n{text}"
    return number, text
