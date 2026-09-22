"""What the crew can do, as opposed to what it worked on.

`crew capability` counts cards, which is an investment ledger: five
Implementation cards may mean a lot was built or a lot needed fixing, and
Release reads as zero while release demonstrably works. A capability is one
question — can the crew do this unattended, reliably, without the card sitting
there — and time in column is what answers it. `Reviewing: median 18s` beside
`Merging: median 4 days` says where the organisation is weak in a way no count
can.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from crew_org.columns import BLOCKED, IN_PROGRESS, MERGING, QAING, REVIEWING, SPRINT_BACKLOG
from crew_org.events import CrewEvent, EventKind
from crew_org.flows.capability import board_settled_at, measure
from crew_org.tools.github_project import Card

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def at(hours: float) -> datetime:
    """`hours` before now."""
    return NOW - timedelta(hours=hours)


def move(card: int, frm: str | None, to: str, when: datetime, role: str | None = None) -> CrewEvent:
    return CrewEvent(
        at=when,
        kind=EventKind.CARD_MOVED,
        card=card,
        role=role,
        summary="",
        detail={"from": frm, "to": to},
    )


def card(number: int, status: str, labels=frozenset()) -> Card:
    return Card(
        item_id=f"C{number}",
        number=number,
        title="A story",
        status=status,
        state="OPEN",
        work_type="Story",
        repo="crew",
        labels=labels,
    )


def stat(m, column: str):
    return next(c for c in m.columns if c.column == column)


# --- time in column ------------------------------------------------------


def test_how_long_a_card_waited_is_the_gap_between_its_moves():
    m = measure(
        [move(1, IN_PROGRESS, REVIEWING, at(5)), move(1, REVIEWING, QAING, at(3))],
        [],
        now=NOW,
    )
    assert stat(m, REVIEWING).median == timedelta(hours=2)


def test_the_worst_wait_is_reported_beside_the_median():
    """A median alone hides the card that sat for days."""
    events = [
        move(1, IN_PROGRESS, REVIEWING, at(10)),
        move(1, REVIEWING, QAING, at(9)),
        move(2, IN_PROGRESS, REVIEWING, at(8)),
        move(2, REVIEWING, QAING, at(2)),
    ]
    m = measure(events, [], now=NOW)
    assert stat(m, REVIEWING).median == timedelta(hours=3.5)
    assert stat(m, REVIEWING).worst == timedelta(hours=6)


def test_a_card_still_sitting_there_counts():
    """Two cards sat in Merging across a whole tick (#40). A measure over
    finished waits alone reports that column as perfectly healthy."""
    m = measure([move(1, QAING, MERGING, at(30))], [], now=NOW)
    assert stat(m, MERGING).worst == timedelta(hours=30)
    assert stat(m, MERGING).still_waiting == 1


def test_a_card_created_straight_into_a_column_starts_waiting_there():
    """A card entering the board has no prior column, which is not missing
    data — 23 of the log's moves are exactly this."""
    m = measure([move(1, None, SPRINT_BACKLOG, at(4))], [], now=NOW)
    assert stat(m, SPRINT_BACKLOG).still_waiting == 1


# --- exercised, and when -------------------------------------------------


def test_a_column_nothing_entered_is_never_exercised():
    """Distinct from a phase that runs and fails. "No cards" could mean absent,
    working but uncarded, or never tried."""
    m = measure([move(1, IN_PROGRESS, REVIEWING, at(5))], [], now=NOW)
    assert stat(m, REVIEWING).exercised is True
    assert stat(m, MERGING).exercised is False


def test_when_a_column_last_did_anything_is_recorded():
    events = [move(1, IN_PROGRESS, REVIEWING, at(9)), move(2, IN_PROGRESS, REVIEWING, at(4))]
    assert stat(measure(events, [], now=NOW), REVIEWING).last_entered == at(4)


# --- intervention --------------------------------------------------------


def test_a_blocked_card_is_charged_to_the_column_it_was_in():
    m = measure([move(1, IN_PROGRESS, BLOCKED, at(3))], [], now=NOW)
    assert m.intervention == {"Implementation": 1}


def test_a_card_flagged_for_a_person_is_an_intervention_where_it_stands():
    m = measure([], [card(1, QAING, labels=frozenset({"needs:human"}))], now=NOW)
    assert m.intervention == {"Acceptance": 1}


def test_a_closed_card_is_not_still_asking_for_a_person():
    stale = card(1, QAING, labels=frozenset({"needs:human"}))
    stale.state = "CLOSED"
    assert measure([], [stale], now=NOW).intervention == {}


# --- rework, charged to what produced it ---------------------------------


def test_a_rejected_review_is_charged_to_implementation():
    """Never to the gate that caught it. Charging the gate makes a working gate
    look like a failing one, and the crew learns to stop rejecting things."""
    m = measure([move(1, REVIEWING, IN_PROGRESS, at(2), role="Code Reviewer")], [], now=NOW)
    assert m.rework == {"Implementation": 1}


def test_unproven_criteria_are_charged_to_implementation_too():
    m = measure([move(1, QAING, IN_PROGRESS, at(2), role="QA Engineer")], [], now=NOW)
    assert m.rework == {"Implementation": 1}


def test_a_dry_run_putting_a_card_back_is_not_rework():
    """Measured against the real log, a bare "moved backwards" rule charged
    three dry-run restores as rework — and a dry run by definition changes
    nothing."""
    m = measure(
        [move(1, IN_PROGRESS, SPRINT_BACKLOG, at(2), role="Developer")],
        [],
        now=NOW,
    )
    assert m.rework == {}


def test_reconciliation_healing_a_dead_run_is_not_rework():
    m = measure([move(1, IN_PROGRESS, SPRINT_BACKLOG, at(2), role=None)], [], now=NOW)
    assert m.rework == {}


def test_a_decomposition_sent_back_is_charged_to_refinement():
    """`rework_gate` removes the label and moves no card, so this case leaves
    no backward move to find."""
    m = measure([], [card(9, "Needs Refinement", labels=frozenset({"needs:rework"}))], now=NOW)
    assert m.rework == {"Refinement": 1}


# --- the window ----------------------------------------------------------


def test_history_from_a_board_that_no_longer_exists_is_not_measured():
    """The log holds `Awaiting QA` and `Awaiting Approval`, and in that era
    review had no column at all — so measuring across the boundary would report
    Review as barely exercised."""
    events = [
        move(1, IN_PROGRESS, "Awaiting QA", at(40)),
        move(1, "Awaiting QA", "Awaiting Approval", at(38)),
        move(2, IN_PROGRESS, REVIEWING, at(5)),
        move(2, REVIEWING, QAING, at(4)),
    ]
    m = measure(events, [], now=NOW)
    assert m.excluded == 2
    assert stat(m, REVIEWING).median == timedelta(hours=1)


def test_the_window_starts_after_the_last_dead_column():
    events = [move(1, IN_PROGRESS, "Awaiting QA", at(40)), move(2, IN_PROGRESS, REVIEWING, at(5))]
    assert board_settled_at(events) == at(40)
    assert measure(events, [], now=NOW).window_start == at(40)


def test_a_board_that_never_changed_measures_everything():
    events = [move(1, IN_PROGRESS, REVIEWING, at(5)), move(1, REVIEWING, QAING, at(4))]
    assert board_settled_at(events) is None
    assert measure(events, [], now=NOW).excluded == 0


def test_the_window_is_reported_so_a_median_is_readable():
    m = measure([move(1, IN_PROGRESS, REVIEWING, at(48))], [], now=NOW)
    assert m.window_days > 0
