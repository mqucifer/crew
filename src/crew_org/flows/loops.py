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
            chain = latest(card)
            if chain is not None:
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
    """Fill in what a return from before #246 left only on the epic.

    The event log then recorded the moves and the re-split. Why the story went
    back and what the Product Owner decided are the comments the crew wrote on
    the epic, and the first of each after the return is the one it wrote then.
    """
    from crew_org.flows.board_flow import (
        PRODUCT_ANSWER_MARKER,
        PRODUCT_QUESTION_MARKER,
        STORY_PROBLEM_MARKER,
    )

    for chain in chains:
        if chain.epic is None or (chain.reason and (chain.decided or chain.asked)):
            continue
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
        answer = next((b for b in after if PRODUCT_ANSWER_MARKER in b), "")
        question = next((b for b in after if PRODUCT_QUESTION_MARKER in b), "")
        if answer:
            chain.decided = _first_line(answer.replace(PRODUCT_ANSWER_MARKER, "")).removeprefix(
                "Decided: "
            )
        elif question:
            chain.asked = _first_line(question.replace(PRODUCT_QUESTION_MARKER, "")).removeprefix(
                "A question for the Sponsor. "
            )


def _first_line(text: str) -> str:
    line = next((x.strip() for x in text.splitlines() if x.strip()), "")
    return line.replace("**", "")
