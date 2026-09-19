"""Where the crew's effort has gone, read off the board.

An investment ledger. It counts cards, which measures *what was worked on* and
not *what works* — five Implementation cards may mean a lot was built or a lot
needed fixing, and Release reads as zero while release demonstrably works,
because the capability predates anyone carding it.

Measuring capability properly means asking whether the crew can do a thing
unattended, reliably, without the card sitting there: time in each column, when
each phase was last exercised, and how often a person had to intervene. The
move log already carries the columns, timestamps and acting role the first two
need. That is a separate piece of work and it is not this.

Two ladders, kept apart (section 18): a card in the crew's own repository
advances a *capability* and carries the field; a card in a delivery repository
advances a *product* and carries none. Counting them together hides the only
ratio worth watching — how much of a sprint went on the orchestrator versus on
the work it was exercising itself with.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from crew_org.columns import DONE
from crew_org.tools.github_project import Card

# In the order a card climbs them, so the scorecard reads like the loop.
LADDER = (
    "Refinement",
    "Planning",
    "Implementation",
    "Review",
    "Acceptance",
    "Release",
    "Flow metrics",
    "Retrospective",
    "Self-diagnosis",
    "Audit trail",
)

STORY_TYPES = frozenset({"Story", "Spike", "Bug", "Task"})


@dataclass
class Row:
    capability: str
    done: int = 0
    open: int = 0
    points_done: float = 0.0
    points_open: float = 0.0

    @property
    def total(self) -> int:
        return self.done + self.open


@dataclass
class Scorecard:
    rows: list[Row] = field(default_factory=list)
    # Crew cards carrying no capability. Named rather than counted, because an
    # unattributed card is the one thing that makes this whole picture a lie.
    unattributed: list[int] = field(default_factory=list)
    # The other ladder. Not broken down: it is one number, and its only job is
    # to sit beside the crew's so the ratio is visible.
    product_done: int = 0
    product_open: int = 0

    @property
    def crew_points(self) -> float:
        return sum(r.points_done + r.points_open for r in self.rows)


def scorecard(cards: list[Card], *, crew_repo: str) -> Scorecard:
    """The board, read as a picture of what the crew can and cannot do."""
    out = Scorecard(rows=[Row(name) for name in LADDER])
    by_name = {r.capability: r for r in out.rows}

    for card in cards:
        if card.work_type not in STORY_TYPES:
            continue
        finished = card.status == DONE or card.state == "CLOSED"

        if card.repo != crew_repo:
            if finished:
                out.product_done += 1
            else:
                out.product_open += 1
            continue

        row = by_name.get(card.capability or "")
        if row is None:
            if not finished:
                out.unattributed.append(card.number or 0)
            continue
        if finished:
            row.done += 1
            row.points_done += card.points or 0
        else:
            row.open += 1
            row.points_open += card.points or 0

    out.unattributed.sort()
    return out
