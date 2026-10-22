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


# An epic sent back by a problem its first split raised: not a re-split, nor a round.
FIRST = "first split"


@dataclass(frozen=True)
class Resplit:
    """An epic sent back: what its re-split replaces, its open stories, and why."""

    plan: Plan
    stories: dict[int, Open]
    because: str  # "story problem", "sponsor", or FIRST


def plan_for(issues: IssueClient, stories: dict[int, Open], repo: str, *, problem: str) -> Plan:
    """What a re-split replaces of the epic's open stories, given what sent it back."""
    from crew_org.flows.board_flow import started  # noqa: PLC0415

    return affected(
        stories,
        named=named_in(problem, owner=issues.owner, repo=repo, among=stories),
        started=started(issues, repo, [s.card for s in stories.values()]),
    )


def open_stories(issues: IssueClient, cards: list[Card], epic: Card, repo: str) -> dict[int, Open]:
    """The epic's open stories, by number, with their bodies.

    A role's Task under the epic isn't the split's: DevOps's CI and release stays and
    waits for the new stories (crew#839).
    """
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
    return {card.number or 0: Open(card, _body(issues, repo, card.number or 0)) for card in live}


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


# Marks the split comment of a round: a re-split after a story problem. Counted on
# GitHub, so the count survives between ticks (crew#859).
ROUND_MARKER = "<!-- crew:resplit round={round} -->"
_ROUND = re.compile(r"<!-- crew:resplit round=(\d+) -->")


# On each story a re-split writes: what the round replaces, and its round ("-" for the
# Sponsor's own rework). A pass that finds a round's stories with no split comment
# naming them finishes that round rather than starting another (crew#859).
WRITTEN_MARKER = "<!-- crew:resplit-of {replaced} round={round} -->"
_WRITTEN = re.compile(r"<!-- crew:resplit-of ([\d,]*) round=(\d+|-) -->")
# On every re-split's comment: a `needs:rework` still on after it is spent, not new.
DONE_MARKER = "<!-- crew:resplit-done -->"


def written(plan: Plan, round_: int | None) -> str:
    """The marker each story a re-split writes carries."""
    return WRITTEN_MARKER.format(
        replaced=",".join(str(n) for n in plan.replaced),
        round="-" if round_ is None else round_,
    )


@dataclass(frozen=True)
class Unfinished:
    """A round whose stories exist, but whose last writes didn't happen."""

    new: tuple[int, ...]
    replaced: tuple[int, ...]
    round: int | None


def unfinished(stories: Mapping[int, Open], splits: list[str]) -> Unfinished | None:
    """The round an earlier pass wrote stories for, if no split comment names them."""
    new: list[int] = []
    replaced: set[int] = set()
    round_: int | None = None
    for n in sorted(stories):
        found = _WRITTEN.search(stories[n].body)
        if not found or any(re.search(rf"#{n}\b", body) for body in splits):
            continue
        new.append(n)
        replaced |= {int(x) for x in found.group(1).split(",") if x}
        round_ = None if found.group(2) == "-" else int(found.group(2))
    return Unfinished(tuple(new), tuple(sorted(replaced)), round_) if new else None


def rounds(bodies: Iterable[str]) -> int:
    """How many rounds an epic has had: re-splits after a story problem."""
    return sum(len(_ROUND.findall(body)) for body in bodies)


def render(plan: Plan, round_: int | None = None) -> str:
    """What a re-split did to the epic's stories, for whoever reads the epic later.

    `round_` is its round, when a story problem sent the epic back.
    """
    lines = ["", "", DONE_MARKER, "### Split again: only what the problem affects", ""]
    if round_ is not None:
        lines = [*lines[:-1], ROUND_MARKER.format(round=round_), f"Round {round_}.", ""]
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


def stop_question(bodies: list[str], *, epic: int, problem: str, stop: int) -> str:
    """The Sponsor's one question when an epic's rounds reach the stop (crew#859).

    Two lines first, what keeps failing and the question, since the crew's issues are
    hard to read; each round below, folded.
    """
    from crew_org.flows.board_flow import (  # noqa: PLC0415
        PRODUCT_QUESTION_MARKER,
        STORY_PROBLEM_MARKER,
    )

    def first(body: str) -> str:
        line = next((ln for ln in body.splitlines() if ln.strip() and "<!--" not in ln), "")
        return line.strip().strip("*").strip()[:300]

    detail, said, decided = [], "", ""
    for body in bodies:
        if STORY_PROBLEM_MARKER in body:
            said, decided = first(body), ""
        elif "**Decided:**" in body:
            decided = body.split("**Decided:**", 1)[1].strip().splitlines()[0][:300]
        elif found := _ROUND.search(body):
            replaced = next((ln for ln in body.splitlines() if ln.startswith("Replaced")), "")
            detail += [
                f"**Round {found.group(1)}.** The problem: {said or '(not found)'}",
                f"- The Product Owner decided: {decided or '(the Sponsor answered)'}",
                f"- {replaced or 'Nothing replaced.'}",
                "",
            ]
    return "\n".join(
        [
            PRODUCT_QUESTION_MARKER,
            f"**Epic #{epic} has been split again {stop} times, and another story problem "
            f"came back:** {first(problem)}",
            "**Your call:** reply with how it should go (your reply becomes a binding row, "
            "and only the stories it affects are split again), or close the epic.",
            "",
            "<details><summary>Each round so far</summary>",
            "",
            *detail,
            "</details>",
        ]
    )


def stopped(sink: EventSink, repo: str, epic: int, *, done: int, stop: int) -> None:
    """The stop, recorded, so it can be lowered from evidence."""
    sink.emit(
        CrewEvent(
            kind=EventKind.EPIC_RESPLIT_STOPPED,
            role="Product Owner",
            card=epic,
            summary=f"epic #{epic}: {done} re-splits, the next problem goes to the Sponsor",
            detail={"repo": repo, "round": done, "stop": stop, "rule": "decision.resplit.stop"},
        )
    )
