"""Every card movement on the record, and who made it (#89).

The cases are the real ones from the board's history on 2026-09-23: a hand
move made with the crew's credentials, the board workflow's sweep moving
another repository's cards, and a person dragging a card themselves.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from crew_org.columns import (
    BLOCKED,
    DONE,
    IN_PROGRESS,
    INBOX,
    MERGING,
    NEEDS_REFINEMENT,
    READY,
    SPRINT_BACKLOG,
)
from crew_org.events import CrewEvent, EventKind
from crew_org.flows.board_moves import (
    AttributedMove,
    RunWindow,
    Source,
    attribute,
    run_windows,
)
from crew_org.flows.capability import measure
from crew_org.tools.github_project import BoardMove, ProjectClient

T0 = datetime(2026, 9, 19, 19, 55, tzinfo=UTC)
CREW = "mqucifer-crew"
LOGINS = {CREW}


def gh(number, frm, to, when, actor=CREW, repo="sprint-metrics") -> BoardMove:
    return BoardMove(repo=repo, number=number, at=when, frm=frm, to=to, actor=actor)


def logged(number, frm, to, when) -> CrewEvent:
    return CrewEvent(
        at=when, kind=EventKind.CARD_MOVED, card=number, detail={"from": frm, "to": to}
    )


def secs(n: float) -> timedelta:
    return timedelta(seconds=n)


def who(moves, events=(), runs=()):
    return [a.who for a in attribute(list(moves), list(events), list(runs), crew_logins=LOGINS)]


# --- attribution ------------------------------------------------------------


def test_a_move_in_the_crews_log_is_the_crews():
    assert who(
        [gh(33, READY, SPRINT_BACKLOG, T0 + secs(30))],
        [logged(33, READY, SPRINT_BACKLOG, T0 + secs(29))],
    ) == ["crew"]


def test_a_person_dragging_a_card_is_named():
    """Criterion 1: who moved it, and between which columns."""
    [move] = attribute(
        [gh(12, BLOCKED, SPRINT_BACKLOG, T0, actor="mquarters")], [], [], crew_logins=LOGINS
    )
    assert move.source is Source.PERSON and move.who == "mquarters"
    assert (move.move.frm, move.move.to) == (BLOCKED, SPRINT_BACKLOG)


def test_a_hand_move_with_the_crews_credentials_is_a_person_acting_as_the_crew():
    """sprint-metrics #33, 2026-09-19 19:55:10: moved to Ready by hand, and
    recorded by GitHub as the crew. Nothing in the crew's log made it."""
    [move] = attribute([gh(33, NEEDS_REFINEMENT, READY, T0 + secs(10))], [], [], crew_logins=LOGINS)
    assert move.source is Source.PERSON and move.as_crew
    assert move.who == "mqucifer-crew (as the crew)"


def test_the_board_workflow_is_the_platform_not_a_person():
    run = RunWindow(repo="sprint-metrics", started=T0, finished=T0 + secs(20))
    assert who([gh(5, SPRINT_BACKLOG, DONE, T0 + secs(8))], runs=[run]) == ["platform"]


def test_the_sweep_run_in_one_repository_moves_cards_in_another():
    """2026-09-23 01:20: the sweep ran in crew and moved sprint-metrics cards."""
    run = RunWindow(repo="crew", started=T0, finished=T0 + secs(25))
    assert who([gh(17, INBOX, DONE, T0 + secs(12))], runs=[run]) == ["platform"]


def test_a_person_moving_a_card_while_the_workflow_runs_is_still_a_person():
    """The window only excuses the crew's identity. A person's own login is a
    person whatever else is happening."""
    run = RunWindow(repo="crew", started=T0, finished=T0 + secs(25))
    assert who([gh(9, BLOCKED, READY, T0 + secs(5), actor="mquarters")], runs=[run]) == [
        "mquarters"
    ]


def test_a_crew_identity_move_outside_any_run_is_not_the_platform():
    run = RunWindow(repo="crew", started=T0, finished=T0 + secs(10))
    assert who([gh(9, MERGING, SPRINT_BACKLOG, T0 + timedelta(minutes=30))], runs=[run]) == [
        "mqucifer-crew (as the crew)"
    ]


def test_one_log_entry_accounts_for_one_move():
    """A card moved to a column, out, and back within the slack is two moves by
    whoever made them — not both claimed by the crew's one entry."""
    moves = [gh(4, IN_PROGRESS, BLOCKED, T0), gh(4, SPRINT_BACKLOG, BLOCKED, T0 + secs(40))]
    # No from-column, so it could match either: only being used once stops it.
    assert who(moves, [logged(4, None, BLOCKED, T0 + secs(1))]) == [
        "crew",
        "mqucifer-crew (as the crew)",
    ]


def test_a_move_and_move_back_are_both_seen():
    """What a snapshot diff cannot do: the card ends where it started."""
    moves = [
        gh(8, READY, SPRINT_BACKLOG, T0, actor="mquarters"),
        gh(8, SPRINT_BACKLOG, READY, T0 + secs(20), actor="mquarters"),
    ]
    assert who(moves) == ["mquarters", "mquarters"]


def test_the_log_must_agree_on_where_the_card_came_from():
    assert who([gh(3, BLOCKED, READY, T0)], [logged(3, NEEDS_REFINEMENT, READY, T0)]) == [
        "mqucifer-crew (as the crew)"
    ]


def test_a_log_entry_that_did_not_say_where_from_still_matches():
    """Card creation is logged with no from-column."""
    assert who([gh(3, None, READY, T0)], [logged(3, None, READY, T0 + secs(2))]) == ["crew"]


def test_a_log_entry_too_far_away_in_time_does_not_match():
    assert who(
        [gh(3, READY, SPRINT_BACKLOG, T0)],
        [logged(3, READY, SPRINT_BACKLOG, T0 + timedelta(minutes=10))],
    ) == ["mqucifer-crew (as the crew)"]


def test_workflow_runs_become_windows():
    [window] = run_windows(
        [{"run_started_at": "2026-09-23T01:19:58Z", "updated_at": "2026-09-23T01:20:23Z"}],
        "crew",
    )
    assert window.finished - window.started == secs(25)


# --- the count ------------------------------------------------------------------

NOW = T0 + timedelta(days=1)


def person(frm, to, when=T0, actor="mquarters") -> AttributedMove:
    return AttributedMove(gh(1, frm, to, when, actor=actor), Source.PERSON)


def counted(*moves, events=None):
    events = events if events is not None else [logged(99, None, READY, T0 - timedelta(hours=1))]
    return measure(events, [], now=NOW, moves=list(moves))


def test_intervention_is_a_count_of_what_people_moved():
    """Criterion 2: a count, from the board's history — not the crew's declarations."""
    m = counted(person(MERGING, DONE), person(IN_PROGRESS, SPRINT_BACKLOG))
    assert m.counted
    assert m.intervention == {"Release": 1, "Implementation": 1}
    assert m.intervened_by == {"mquarters": 2}


def test_crew_and_platform_moves_are_not_intervention():
    """Criterion 3: distinguishable, and only a person's move counts."""
    crew = AttributedMove(gh(1, MERGING, DONE, T0), Source.CREW)
    platform = AttributedMove(gh(2, MERGING, DONE, T0), Source.PLATFORM)
    assert counted(crew, platform).intervention == {}


def test_the_sponsors_gate_is_not_intervention():
    """Approving an epic out of Inbox, or sending one back, is the Sponsor's job."""
    m = counted(person(INBOX, NEEDS_REFINEMENT), person(NEEDS_REFINEMENT, INBOX))
    assert m.intervention == {}


def test_filing_a_card_is_not_intervention():
    assert counted(person(None, READY)).intervention == {}


def test_unblocking_is_charged_where_the_card_goes_back_to():
    assert counted(person(BLOCKED, SPRINT_BACKLOG)).intervention == {"Planning": 1}


def test_a_move_before_the_log_begins_is_not_counted():
    """Before the crew's log starts, its own moves have nothing to match and
    would all read as a person's."""
    early = person(MERGING, DONE, when=T0 - timedelta(hours=2), actor=CREW)
    assert counted(early).intervention == {}


def test_what_the_crew_asked_for_is_reported_apart():
    m = counted(events=[logged(1, IN_PROGRESS, BLOCKED, T0)])
    assert m.intervention == {} and m.asked == {"Implementation": 1}


def test_without_the_boards_history_intervention_is_the_floor_it_was():
    m = measure([logged(1, IN_PROGRESS, BLOCKED, T0)], [], now=NOW)
    assert not m.counted
    assert m.intervention == {"Implementation": 1}


# --- reading the history -----------------------------------------------------


def test_the_history_is_read_for_this_board_only(monkeypatch):
    board = ProjectClient("t", "mqucifer", 1)
    page = {
        "items": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [
                {
                    "content": {
                        "number": 33,
                        "repository": {"name": "sprint-metrics"},
                        "timelineItems": {
                            "nodes": [
                                {
                                    "createdAt": "2026-09-19T19:55:10Z",
                                    "previousStatus": "Needs Refinement",
                                    "status": "Ready",
                                    "actor": {"login": CREW},
                                    "project": {"number": 1},
                                },
                                {
                                    "createdAt": "2026-09-19T19:50:00Z",
                                    "previousStatus": "",
                                    "status": "Todo",
                                    "actor": {"login": CREW},
                                    "project": {"number": 7},
                                },
                                {},
                            ]
                        },
                    }
                },
                # A draft item has no issue behind it.
                {"content": {}},
            ],
        }
    }
    monkeypatch.setattr(board, "_project", lambda query, **kw: page)
    [move] = board.status_history()
    assert (move.repo, move.number, move.frm, move.to, move.actor) == (
        "sprint-metrics",
        33,
        "Needs Refinement",
        "Ready",
        CREW,
    )
