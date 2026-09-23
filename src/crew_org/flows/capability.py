"""Where the crew's effort has gone, read off the board.

An investment ledger. It counts cards, which measures *what was worked on* and
not *what works* — five Implementation cards may mean a lot was built or a lot
needed fixing, and Release reads as zero while release demonstrably works,
because the capability predates anyone carding it.

Measuring capability properly means asking whether the crew can do a thing
unattended, reliably, without the card sitting there: time in each column, when
each phase was last exercised, and how often a person had to intervene. That is
`measure()` below, computed off the move log rather than off card counts. Both
live here because both are read at once — what a sprint spent, beside what the
crew can actually do — and neither is legible without the other.

Two ladders, kept apart (section 18): a card in the crew's own repository
advances a *capability* and carries the field; a card in a delivery repository
advances a *product* and carries none. Counting them together hides the only
ratio worth watching — how much of a sprint went on the orchestrator versus on
the work it was exercising itself with.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from crew_org.columns import (
    ALL,
    BLOCKED,
    DONE,
    FLOW,
    IN_PROGRESS,
    INBOX,
    MERGING,
    NEEDS_REFINEMENT,
    QAING,
    READY,
    REVIEWING,
    SPRINT_BACKLOG,
)
from crew_org.events import CrewEvent
from crew_org.flows.board_moves import AttributedMove, Source
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


# --- what the crew can do, as opposed to what it worked on ---------------

# Which capability owns the wait in each column. `Inbox (Goals)` is absent on
# purpose: the wait there is the Sponsor deciding, which is goal supply rather
# than anything the crew can be measured on. `Done` and `Blocked` are not waits
# a phase drains — leaving Blocked needs a person, which is what `intervention`
# counts instead.
# The columns a gate judges from. Only a move out of one of these is work
# being *sent back*; everything else that steps backwards is the crew's own
# bookkeeping — a dry run putting a card where it found it, reconciliation
# healing an interrupted run — and charging those as rework made Planning the
# weakest capability on the board for doing nothing wrong.
GATE_COLUMNS = frozenset({REVIEWING, QAING})

# A decomposition the Sponsor sent back. `rework_gate` removes the label and
# moves no card, so this case leaves no backward move to find — it is read off
# the label instead, and undercounts exactly as `needs:human` does.
NEEDS_REWORK = "needs:rework"

COLUMN_CAPABILITY: dict[str, str] = {
    NEEDS_REFINEMENT: "Refinement",
    READY: "Planning",
    SPRINT_BACKLOG: "Planning",
    IN_PROGRESS: "Implementation",
    REVIEWING: "Review",
    QAING: "Acceptance",
    MERGING: "Release",
}


@dataclass
class ColumnStat:
    """How long cards wait in one column, and when it last did anything."""

    column: str
    capability: str
    waits: list[timedelta] = field(default_factory=list)
    # Waits that have not ended: the card is in the column right now. Counted,
    # because a card that never leaves is the signal — two cards sat in Merging
    # across a whole tick (#40), and a measure over finished waits alone would
    # have reported that column as perfectly healthy.
    still_waiting: int = 0
    last_entered: datetime | None = None

    @property
    def exercised(self) -> bool:
        return self.last_entered is not None

    @property
    def median(self) -> timedelta | None:
        return (
            timedelta(seconds=statistics.median(w.total_seconds() for w in self.waits))
            if self.waits
            else None
        )

    @property
    def worst(self) -> timedelta | None:
        return max(self.waits) if self.waits else None


@dataclass
class Measure:
    """What the crew can do, over a window."""

    columns: list[ColumnStat] = field(default_factory=list)
    # Cards a person moved, against the capability whose column they left. A
    # count when the board's own history was read (#89); without it, a floor.
    intervention: dict[str, int] = field(default_factory=dict)
    # Who made those moves. A person using the crew's credentials is named as
    # such, because everywhere else that move reads as the crew's.
    intervened_by: dict[str, int] = field(default_factory=dict)
    # Whether `intervention` came from the board's history, or only from what
    # the crew declared.
    counted: bool = False
    # The crew declaring it cannot proceed: a card it moved to Blocked, or one
    # carrying `needs:human`. What it asked for, as distinct from what a person
    # actually did.
    asked: dict[str, int] = field(default_factory=dict)
    # Work a gate sent back, against the capability that *produced* it.
    rework: dict[str, int] = field(default_factory=dict)
    window_start: datetime | None = None
    window_end: datetime | None = None
    # Events dropped because they predate the board's current shape.
    excluded: int = 0

    @property
    def window_days(self) -> float:
        if self.window_start is None or self.window_end is None:
            return 0.0
        return (self.window_end - self.window_start).total_seconds() / 86400


def board_settled_at(
    events: list[CrewEvent], *, known: frozenset[str] | None = None
) -> datetime | None:
    """When the board last took a shape that matches the one it has now.

    The move log outlives the board. It holds `Awaiting QA` and `Awaiting
    Approval`, columns that no longer exist — and in that era `crew review`
    read GitHub's pull requests without touching the board at all, so Reviewing
    was not a slow column, it was not a column. Measuring across that boundary
    would report Review as barely exercised, which is exactly the kind of
    statement this measure exists to make trustworthy.

    Derived rather than configured, so it stays true the next time a column is
    renamed: the window starts after the last event naming a column the board
    does not have.
    """
    columns = known if known is not None else frozenset(ALL)
    latest: datetime | None = None
    for event in events:
        detail = event.detail or {}
        for name in (detail.get("from"), detail.get("to")):
            if name and name not in columns and (latest is None or event.at > latest):
                latest = event.at
    return latest


def _rework_target(frm: str, to: str) -> str | None:
    """The capability a gate's rejection is charged to.

    The one that *produced* the work, never the one that caught it: a diff sent
    back from Reviewing was produced in In Progress, so Implementation owns it.
    Charging the gate would make a working gate look like a failing one, and
    the crew would learn to stop rejecting things.

    Only a gate's rejection counts. Measured against the real log, a bare
    "moved backwards" rule charged four dry-run restores and one orphan
    reconciliation as rework — none of which is a gate sending work back, and
    three of which are a *dry run*, which by definition changes nothing.
    """
    if frm not in GATE_COLUMNS:
        return None
    order = {name: i for i, name in enumerate(FLOW)}
    if frm not in order or to not in order or order[to] >= order[frm]:
        return None
    return COLUMN_CAPABILITY.get(to)


def _charged_to(move: AttributedMove) -> str | None:
    """The capability a person's move is an intervention in, or None if it is not one.

    Not every move a person makes is the crew needing them. Creating a card is
    supplying work, and anything in or out of Inbox (Goals) is the Sponsor's
    gate, which is the one job the Sponsor is meant to have. Otherwise the move
    is charged to the column the card left; leaving Blocked, which no
    capability owns, to the column it went back to.
    """
    frm, to = move.move.frm, move.move.to
    if frm is None or INBOX in (frm, to):
        return None
    if frm == BLOCKED:
        return COLUMN_CAPABILITY.get(to or "") or "Unblocking"
    return COLUMN_CAPABILITY.get(frm) or COLUMN_CAPABILITY.get(to or "")


def measure(
    events: list[CrewEvent],
    cards: list[Card],
    *,
    now: datetime | None = None,
    known: frozenset[str] | None = None,
    moves: list[AttributedMove] | None = None,
) -> Measure:
    """Can the crew run each part of the process unattended, and how well?

    Reads the move log, not card counts. A card count says what was worked on;
    time in column says where the organisation is weak — `Reviewing: median
    18s` beside `Merging: median 4 days` says it in a way no count can.

    **Intervention is counted from the board's own history** when `moves` is
    given: every Status change GitHub recorded, attributed by
    `board_moves.attribute`, and each one a person made counts. Without it, the
    crew's log holds its own moves only, and intervention falls back to what
    the crew *declared* — a card it moved to Blocked, or one carrying
    `needs:human` — which is a floor and is reported as one.
    """
    now = now or datetime.now(UTC)
    columns = known if known is not None else frozenset(ALL)

    # Everything before the board's current shape is not measured. A hard
    # cutoff, and the window is reported so the numbers are readable.
    cutoff = board_settled_at(events, known=columns)
    in_window = [e for e in events if cutoff is None or e.at > cutoff]
    out = Measure(
        window_start=cutoff or (in_window[0].at if in_window else None),
        window_end=now,
        excluded=len(events) - len(in_window),
    )

    stats = {
        column: ColumnStat(column=column, capability=capability)
        for column, capability in COLUMN_CAPABILITY.items()
    }
    entered: dict[tuple[str, int], datetime] = {}

    for event in in_window:
        detail = event.detail or {}
        frm, to = detail.get("from"), detail.get("to")
        card_id = event.card
        if to is None or card_id is None:
            continue

        # Leaving a column closes its wait.
        if frm in stats and (key := (frm, card_id)) in entered:
            stats[frm].waits.append(event.at - entered.pop(key))

        if to in stats:
            entered[(to, card_id)] = event.at
            stat = stats[to]
            if stat.last_entered is None or event.at > stat.last_entered:
                stat.last_entered = event.at

        if to == BLOCKED and frm in COLUMN_CAPABILITY:
            capability = COLUMN_CAPABILITY[frm]
            out.asked[capability] = out.asked.get(capability, 0) + 1

        if frm is not None and (target := _rework_target(frm, to)) is not None:
            out.rework[target] = out.rework.get(target, 0) + 1

    # Cards still sitting somewhere. Their wait is open and counted as such.
    for (column, _card_id), since in entered.items():
        stats[column].waits.append(now - since)
        stats[column].still_waiting += 1

    for card in cards:
        if card.state == "CLOSED":
            continue
        # A card flagged for a person is an intervention wherever it is
        # standing, whether or not the crew ever moved it to Blocked.
        capability = COLUMN_CAPABILITY.get(card.status or "")
        if capability and card.needs_human:
            out.asked[capability] = out.asked.get(capability, 0) + 1
        # A decomposition sent back is rework against the role that wrote it.
        if NEEDS_REWORK in card.labels:
            out.rework["Refinement"] = out.rework.get("Refinement", 0) + 1

    if moves is None:
        out.intervention = dict(out.asked)
    else:
        out.counted = True
        for attributed in moves:
            if attributed.source is not Source.PERSON:
                continue
            # Before the crew's log begins, its own moves have nothing to be
            # matched against and would all read as a person's.
            if out.window_start is not None and attributed.move.at <= out.window_start:
                continue
            capability = _charged_to(attributed)
            if capability is None:
                continue
            out.intervention[capability] = out.intervention.get(capability, 0) + 1
            out.intervened_by[attributed.who] = out.intervened_by.get(attributed.who, 0) + 1

    out.columns = [stats[name] for name in FLOW if name in stats]
    return out
