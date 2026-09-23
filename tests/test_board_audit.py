"""The board, read against itself.

A closed card sitting in `Sprint Backlog` is a fact the board contradicts about
itself, and `crew capability` reads that column as occupied. Thirty-three of
them accumulated before anyone counted — because the board's built-in workflow
that would have moved them was off, and nothing said so.
"""

from __future__ import annotations

from crew_org.flows.board_audit import audit
from crew_org.tools.github_project import Card


def card(number: int, *, state: str, status: str, repo: str = "crew") -> Card:
    return Card(
        item_id=f"C{number}",
        number=number,
        title="A story",
        state=state,
        status=status,
        work_type="Story",
        repo=repo,
    )


def test_a_closed_card_outside_done_is_reported():
    a = audit([card(7, state="CLOSED", status="Sprint Backlog")])
    assert [c.number for c in a.finished_but_waiting] == [7]
    assert not a.ok


def test_a_closed_card_in_done_is_the_normal_case():
    assert audit([card(7, state="CLOSED", status="Done")]).ok


def test_an_open_card_in_done_is_reported_too():
    """The inverse, and the one no close-triggered workflow can catch:
    reopening an issue moves nothing, so a card sits in Done with its issue
    open and nothing ever says so."""
    a = audit([card(7, state="OPEN", status="Done")])
    assert [c.number for c in a.waiting_but_finished] == [7]


def test_an_open_card_mid_flow_is_neither():
    assert audit([card(7, state="OPEN", status="In Progress")]).ok


def test_both_directions_are_counted_together():
    a = audit(
        [
            card(7, state="CLOSED", status="Ready"),
            card(8, state="OPEN", status="Done"),
            card(9, state="OPEN", status="QAing"),
        ]
    )
    assert a.total == 2


def test_findings_are_ordered_by_repository_then_number():
    """A list a person reads should not be in whatever order the board
    returned — the same reason `ready_to_land` is sorted."""
    a = audit(
        [
            card(32, state="CLOSED", status="Ready", repo="sprint-metrics"),
            card(12, state="CLOSED", status="Ready"),
            card(7, state="CLOSED", status="Ready"),
        ]
    )
    assert [(c.repo, c.number) for c in a.finished_but_waiting] == [
        ("crew", 7),
        ("crew", 12),
        ("sprint-metrics", 32),
    ]


def test_an_empty_board_holds_the_invariant():
    assert audit([]).ok
