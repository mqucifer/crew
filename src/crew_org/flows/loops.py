"""Loops the crew broke in a sprint, read back from the event log (#253).

A story the gates kept returning goes back to refinement (#233, #243), the
Product Owner decides or asks (#234), and its epic is split again. Each step
is an event (#246). The retro reads them here, because the board no longer
can: sending a story back clears its Sprint field, and a retro that lists the
sprint's stories by that field lost sprint-metrics#145 entirely — six
deliveries, three reviews and two QA returns, reported as nothing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BACK_TO_REFINEMENT = "back to refinement with epic #"
_EPIC = re.compile(r"back to refinement with epic #(\d+)")

# The moves of one hand-back land within seconds of each other; a story
# returned with its siblings is one return, not four.
SAME_RETURN = timedelta(seconds=60)


@dataclass
class Chain:
    """One loop broken: the return, the decision, the split that followed."""

    card: int
    epic: int | None
    at: str
    reason: str = ""
    repo: str | None = None
    # Siblings sent back with it.
    with_: list[int] = field(default_factory=list)
    decided: str = ""
    asked: str = ""
    superseded: list[int] | None = None


def sprint_window(first: date, last: date, tz: str = "UTC") -> tuple[str, str]:
    """The sprint's first and last day as UTC timestamps the event log compares with."""
    zone = ZoneInfo(tz)
    start = datetime.combine(first, time.min, zone)
    end = datetime.combine(last + timedelta(days=1), time.min, zone)
    return _utc(start), _utc(end)


def _utc(moment: datetime) -> str:
    return moment.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S")


def _events(events_dir: Path, start: str, end: str) -> list[dict[str, Any]]:
    found = []
    for path in sorted(events_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if start <= str(event.get("at") or "") < end:
                found.append(event)
    return sorted(found, key=lambda e: e["at"])


def _moment(at: str) -> datetime:
    return datetime.fromisoformat(at[:19])


def read_loops(events_dir: Path, start: str, end: str) -> list[Chain]:
    """Every return to refinement between `start` and `end`, with what followed it."""
    chains: list[Chain] = []

    def latest(epic: int | None) -> Chain | None:
        return next((c for c in reversed(chains) if c.epic == epic), None)

    for event in _events(events_dir, start, end):
        kind = event.get("kind")
        detail = event.get("detail") or {}
        card = event.get("card")
        if kind == "story.returned" and card is not None:
            epic = detail.get("epic")
            # The moves came first; the event names which story went back and why.
            chain = latest(epic)
            late = chain is not None and _moment(event["at"]) - _moment(chain.at) > SAME_RETURN
            if chain is None or chain.reason or late:
                chain = Chain(card=int(card), epic=epic, at=event["at"])
                chains.append(chain)
            chain.card = int(card)
            chain.reason = str(detail.get("reason") or "")
            chain.repo = detail.get("repo")
            chain.with_ = [int(n) for n in detail.get("with") or [] if n != card]
        elif kind == "card.moved" and card is not None:
            found = _EPIC.search(str(event.get("summary") or ""))
            if not found:
                continue
            epic = int(found.group(1))
            chain = latest(epic)
            if chain is not None and _moment(event["at"]) - _moment(chain.at) <= SAME_RETURN:
                if card != chain.card and card not in chain.with_:
                    chain.with_.append(int(card))
                continue
            # A return from before #246, which only the moves recorded: the
            # first card moved is the story that went back.
            chains.append(Chain(card=int(card), epic=epic, at=event["at"]))
        elif kind == "product.answered":
            # Only with the story whose return it answers (crew#583, step A6). Paired by
            # time, Sprint 20's retro filed an answer about refinement under sm#529.
            chain = latest(card)
            story = detail.get("story")
            if chain is not None and story is not None and story in (chain.card, *chain.with_):
                chain.decided = str(detail.get("answer") or event.get("summary") or "")
        elif kind == "product.asked":
            chain = latest(card)
            if chain is not None:
                chain.asked = str(detail.get("question") or event.get("summary") or "")
        elif kind == "epic.resplit":
            chain = latest(card)
            if chain is not None:
                chain.superseded = [int(n) for n in detail.get("superseded") or []]
    return chains


@dataclass
class Resplit:
    """An epic split again, and the stories it superseded."""

    epic: int
    at: str
    because: str
    superseded: list[int]
    repo: str | None = None


def read_resplits(events_dir: Path, start: str, end: str) -> list[Resplit]:
    """Every re-split between `start` and `end`, whether or not a return came first.

    `read_loops` keeps a re-split only as the end of a return. One the Sponsor
    asked for has none before it, and sprint-metrics#467's left no trace in the
    retro for Sprint 19 (crew#558).
    """
    return [
        Resplit(
            epic=int(event["card"]),
            at=event["at"],
            because=str((event.get("detail") or {}).get("because") or ""),
            superseded=[int(n) for n in (event.get("detail") or {}).get("superseded") or []],
            repo=(event.get("detail") or {}).get("repo"),
        )
        for event in _events(events_dir, start, end)
        if event.get("kind") == "epic.resplit" and event.get("card") is not None
    ]


def superseded_text(
    stories: list[tuple[str | None, int, str, int]], resplits: list[Resplit]
) -> list[str]:
    """The retro's section on stories superseded: each, its points, and what replaced it.

    `stories` is (repo, number, name, points) per superseded story in the sprint. A
    superseded story is a plan that didn't hold, worth its own line rather than
    a quiet absence from the delivered count: the Sponsor, 2026-10-09.
    """
    if not stories:
        return []
    lines = [
        "Admitted to the sprint, then replaced by a new split and never built. "
        "Each is a plan that didn't hold: worth asking why.",
        "",
    ]
    why = {"sponsor": "at the Sponsor's request", "story problem": "after a story problem"}
    # By repository and number: a re-split names its own repository's stories.
    left = {(repo, number): (name, points) for repo, number, name, points in stories}
    for r in resplits:
        mine = [(r.repo, n) for n in r.superseded if (r.repo, n) in left]
        if not mine:
            continue
        names = ", ".join(f"{left[n][0]} ({left[n][1]})" for n in mine)
        total = sum(left[n][1] for n in mine)
        # Named with its repository: the retro is filed in the crew's, where a
        # bare number links to the crew's own issue.
        epic = f"{r.repo}#{r.epic}" if r.repo else f"#{r.epic}"
        lines.append(
            f"- Epic {epic} was split again {why.get(r.because, 'for a reason not recorded')}"
            f" at {r.at[:16].replace('T', ' ')}Z: {names}. {total} points."
        )
        for n in mine:
            del left[n]
    for name, points in left.values():
        lines.append(f"- {name} ({points}): closed as not planned, with no re-split recorded.")
    total = sum(points for _, _, _, points in stories)
    lines += ["", f"{len(stories)} stories, {total} points, superseded."]
    return lines


def loops_text(chains: list[Chain], points: dict[int, int]) -> list[str]:
    """The retro's section on loops broken: each chain, then the points they cost."""
    if not chains:
        return []
    lines = [
        "When the gates keep returning a story, the crew sends it back to be "
        "split again rather than rebuild it once more.",
        "",
    ]
    for c in chains:
        why = c.reason or "why wasn't recorded"
        sent = f"#{c.card} went back to refinement ({why})"
        if c.with_:
            sent += ", taking " + ", ".join(f"#{n}" for n in sorted(c.with_)) + " with it"
        lines.append(f"- {sent}.")
        if c.decided:
            lines.append(f"  - The Product Owner decided: {c.decided}")
        elif c.asked:
            lines.append(f"  - The Product Owner asked you: {c.asked}")
        else:
            lines.append("  - No Product Owner decision recorded.")
        if c.superseded is not None:
            names = ", ".join(f"#{n}" for n in c.superseded) or "nothing"
            lines.append(f"  - Epic #{c.epic} was split again; it superseded {names}.")
        else:
            lines.append(f"  - Epic #{c.epic} not split again yet.")
    spent = {c.card: points.get(c.card, 0) for c in chains}
    total = sum(spent.values())
    if total:
        cards = ", ".join(f"#{n} ({p})" for n, p in spent.items())
        lines += ["", f"{total} points went into stories that went back, not delivered: {cards}."]
    return lines


def from_comments(chains: list[Chain], issues: Any, repo: str) -> None:
    """Fill in what a return's events left out, from the comments on its epic.

    Why the story went back is the story-problem comment the crew wrote then. What
    the Product Owner decided is the epic's record, changed for that story: the
    record-change comment that names it (crew#583, step A6). An answer is never
    paired with a return by time: Sprint 20's retro filed an answer about
    refinement under sm#529 that way.
    """
    from crew_org.flows.board_flow import PRODUCT_QUESTION_MARKER, STORY_PROBLEM_MARKER
    from crew_org.flows.record import has_change

    for chain in chains:
        if chain.epic is None or (chain.reason and (chain.decided or chain.asked)):
            continue
        owner = getattr(issues, "owner", "")
        try:
            comments = issues.comments(chain.repo or repo, chain.epic)
        except Exception:  # noqa: BLE001
            continue
        after = [
            str(c.get("body") or "")
            for c in comments
            if str(c.get("created_at") or "")[:19] >= chain.at[:19]
        ]
        problem = next((b for b in after if STORY_PROBLEM_MARKER in b), "")
        if problem and not chain.reason:
            said = _first_line(problem.replace(STORY_PROBLEM_MARKER, ""))
            chain.reason = said.split(": ", 1)[-1].rstrip(".")
        if chain.decided or chain.asked:
            continue
        mine = f"for {owner}/{chain.repo or repo}#{chain.card}."
        answer = next((b for b in after if has_change([b], "answer") and mine in b), "")
        question = next((b for b in after if PRODUCT_QUESTION_MARKER in b), "")
        if answer:
            chain.decided = "; ".join(
                line.removeprefix("- ").replace("**", "")
                for line in answer.splitlines()
                if line.startswith("- **R")
            )
        elif question:
            chain.asked = _first_line(question.replace(PRODUCT_QUESTION_MARKER, "")).removeprefix(
                "A question for the Sponsor. "
            )


def _first_line(text: str) -> str:
    line = next((x.strip() for x in text.splitlines() if x.strip()), "")
    return line.replace("**", "")
