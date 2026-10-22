"""A re-split replaces only the stories a story problem affects (crew#859).

The rework gate closed every unstarted story of an epic and split it again from
scratch, and most of Sprint 31's discarded stories went that way. Which stories a
problem affects is one rule, from the link the crew already records between an
epic's stories, its order: the stories the problem names, as references in its
comment, and every later story of the epic. A story waits for every earlier sibling
(`siblings.py`), so every later one builds on it. A problem that names none of them
affects them all. No field records which sibling a story needs, and none is
invented: the Sponsor wants no logic for "this dependency hell" (crew#859).

The rest stay as they are. The replacements are numbered after every kept story,
so the order delivery reads stays true.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows.conclusion import parse_follows
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card
from crew_org.work_types import TASK


@dataclass(frozen=True)
class Plan:
    """Which of an epic's open stories a re-split replaces, and which it keeps."""

    named: tuple[int, ...]
    replaced: tuple[int, ...]
    kept: tuple[int, ...]
    # Affected, but work on them has started: the re-split waits, discarding nothing.
    started: tuple[int, ...]
    # Each named story's place among the epic's open stories, 1 first: the evidence
    # for whether links between an epic's stories are worth recording.
    positions: Mapping[int, int]


def named_in(text: str, *, owner: str, repo: str, among: Iterable[int]) -> set[int]:
    """The stories of `among` that `text` references: `#12`, `repo#12`, `owner/repo#12`
    or a link. Another repository's `crew#12` is not one of them."""
    here = rf"(?<![\w/-])(?:{re.escape(owner)}/)?{re.escape(repo)}#"
    link = rf"/{re.escape(owner)}/{re.escape(repo)}/issues/"
    found = re.findall(rf"(?:(?<![\w/])#|{here}|{link})(\d+)\b", text)
    return {int(n) for n in found} & set(among)


def affected(stories: Iterable[int], *, named: set[int], started: set[int]) -> Plan:
    """What a re-split replaces of `stories`, the epic's open stories by number.

    A problem that names none of them affects them all: nothing says which it's about.
    """
    order = sorted(stories)
    first = min(named, default=None)
    replaced = tuple(n for n in order if first is None or n >= first)
    return Plan(
        named=tuple(sorted(named)),
        replaced=replaced,
        kept=tuple(n for n in order if n not in replaced),
        started=tuple(n for n in replaced if n in started),
        positions={n: order.index(n) + 1 for n in sorted(named)},
    )


@dataclass(frozen=True)
class Open:
    """One of the epic's open stories, as the re-split reads it."""

    card: Card
    body: str


@dataclass(frozen=True)
class Resplit:
    """An epic sent back: what its re-split replaces, its open stories, and why."""

    plan: Plan
    stories: dict[int, Open]
    because: str  # "story problem" or "sponsor"


def plan_for(
    issues: IssueClient, cards: list[Card], epic: Card, repo: str, *, problem: str
) -> tuple[Plan, dict[int, Open]]:
    """What a re-split of `epic` replaces, read from GitHub, with the epic's open stories.

    A role's Task under the epic isn't the split's: DevOps's CI and release stays and
    waits for the new stories (crew#839).
    """
    from crew_org.flows.board_flow import started  # noqa: PLC0415

    number = epic.number or 0
    by_key = {c.key: c for c in cards}
    try:
        children = [child["number"] for child in issues.sub_issues(repo, number)]
    except Exception:  # noqa: BLE001
        children = []
    live = [
        card
        for child in children
        if (card := by_key.get((repo, child))) is not None
        and card.state != "CLOSED"
        and card.work_type != TASK
    ]
    stories = {card.number or 0: Open(card, _body(issues, repo, card.number or 0)) for card in live}
    plan = affected(
        stories,
        named=named_in(problem, owner=issues.owner, repo=repo, among=stories),
        started=started(issues, repo, live),
    )
    return plan, stories


def _body(issues: IssueClient, repo: str, number: int) -> str:
    try:
        return issues.get(repo, number).get("body") or ""
    except Exception:  # noqa: BLE001
        return ""


def shown(stories: dict[int, Open], numbers: Iterable[int]) -> str:
    """Stories whole, as the split is shown them: what it keeps, and what it replaces."""
    return "\n\n".join(
        f"### #{n} {stories[n].card.title}\n\n{stories[n].body.strip()}" for n in numbers
    )


def covered(stories: dict[int, Open], kept: Iterable[int]) -> tuple[set[str], str]:
    """The rows the kept stories follow, and their Existing tests lines: the record's
    checks count them, so the new stories needn't follow them again."""
    from crew_org.flows.board_flow import EXISTING_TESTS  # noqa: PLC0415

    rows: set[str] = set()
    tests: list[str] = []
    for n in kept:
        rows |= set(parse_follows(stories[n].body))
        tests += [ln for ln in stories[n].body.splitlines() if ln.startswith(EXISTING_TESTS)]
    return rows, "\n".join(tests)


def render(plan: Plan) -> str:
    """What a re-split did to the epic's stories, for whoever reads the epic later."""
    lines = ["", "", "### Split again: only what the problem affects", ""]
    lines.append(
        "Replaced, closed as not planned: " + ", ".join(f"#{n}" for n in plan.replaced) + "."
        if plan.replaced
        else "Nothing replaced."
    )
    if plan.kept:
        lines.append("Kept as they are: " + ", ".join(f"#{n}" for n in plan.kept) + ".")
    return "\n".join(lines)


def emit(sink: EventSink, repo: str, epic: int, again: Resplit, *, reads_decision: bool) -> None:
    """One event per round, so the stop can be lowered from evidence (crew#859)."""
    plan = again.plan
    sink.emit(
        CrewEvent(
            kind=EventKind.EPIC_RESPLIT,
            card=epic,
            summary=f"epic #{epic} split again"
            + (
                " after a story problem"
                if again.because == "story problem"
                else " at the Sponsor's request"
            )
            + f": {len(plan.replaced)} replaced, {len(plan.kept)} kept",
            detail={
                "repo": repo,
                "superseded": list(plan.replaced),
                "kept": list(plan.kept),
                "named": list(plan.named),
                # Each named story's place among the epic's open stories, and how many.
                "positions": [plan.positions[n] for n in plan.named],
                "of": len(plan.replaced) + len(plan.kept),
                "because": again.because,
                "reads_decision": reads_decision,
                "rule": "decision.resplit.affected",
            },
        )
    )
