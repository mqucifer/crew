"""Planning admits what delivery can finish, and a sprint's leftovers are planned again (#353).

Sprint 9 admitted sprint-metrics#284 when its earlier sibling #283 didn't fit
the points left. Delivery holds a story until every earlier sibling has
landed, so #284 waited all sprint. At close it stayed in Sprint Backlog marked
Sprint 9, which Sprint 10 would neither plan nor deliver.
"""

from __future__ import annotations

from crew_org.columns import IN_PROGRESS, READY, SPRINT_BACKLOG
from crew_org.config import load_org
from crew_org.events import EventSink
from crew_org.flows.sprint import left_over, plan_sprint, start_sprint
from crew_org.process import ProcessRules
from tests.test_sprint import REPO, epic, parents, story


def sibling(number, points, status=READY, sprint=None, parent=179):
    card = story(number, points, status=status)
    card.parent = parent
    card.sprint = sprint
    return card


def plan(cards, capacity, sprint="Sprint 10"):
    stories = [c for c in cards if c.work_type == "Story"]
    return plan_sprint(
        cards, parents({c.number: 179 for c in stories}), sprint=sprint, capacity=capacity
    )


def test_a_story_is_not_admitted_ahead_of_an_earlier_sibling_that_did_not_fit():
    """#283 was 5 points with 3 left; #284, 2 points, went in without it."""
    result = plan([epic(179), sibling(283, 5), sibling(284, 2)], capacity=3)
    assert result.admitted == []
    assert [c.number for c in result.slices[0].deferred] == [283, 284]


def test_siblings_that_fit_together_are_admitted_together():
    result = plan([epic(179), sibling(283, 5), sibling(284, 2)], capacity=8)
    assert [c.number for c in result.admitted] == [283, 284]


def test_an_earlier_sibling_already_in_the_sprints_flow_does_not_hold_it():
    cards = [
        epic(179),
        sibling(282, 3, status=IN_PROGRESS, sprint="Sprint 10"),
        sibling(284, 2),
    ]
    assert [c.number for c in plan(cards, capacity=5).admitted] == [284]


def test_a_story_with_no_epic_is_not_held():
    loose = sibling(284, 2, parent=None)
    result = plan_sprint([loose], {}, sprint="Sprint 10", capacity=5)
    assert [c.number for c in result.admitted] == [284]


# --- a sprint's leftovers --------------------------------------------------------------------


def test_a_story_an_earlier_sprint_never_started_is_left_over():
    cards = [
        sibling(284, 2, status=SPRINT_BACKLOG, sprint="Sprint 9"),
        sibling(285, 2, status=SPRINT_BACKLOG, sprint="Sprint 10"),
        sibling(286, 2, status=IN_PROGRESS, sprint="Sprint 9"),
    ]
    assert [c.number for c in left_over(cards, "Sprint 10")] == [284]


class Board:
    def __init__(self, cards):
        self._cards = cards
        self.moves, self.cleared, self.sprints = [], [], []

    def cards(self):
        return self._cards

    def counts(self, cards):
        return {}

    def set_status(self, item, to):
        self.moves.append((item, to))

    def set_owner_agent(self, item, by):
        pass

    def clear_field(self, item, field):
        self.cleared.append((item, field))

    def set_iteration(self, item, field, title):
        self.sprints.append((item, title))


class Issues:
    def sub_issues(self, repo, number):
        return [{"number": 283}, {"number": 284}]

    def labelled(self, repo, label):
        return []

    def comments(self, repo, number):
        return []


def test_a_leftover_goes_back_to_ready_and_is_planned_with_its_sibling(monkeypatch):
    monkeypatch.setattr("crew_org.flows.design_notes.awaiting_design", lambda *a: set())
    board = Board(
        [
            epic(179),
            sibling(283, 5),
            sibling(284, 2, status=SPRINT_BACKLOG, sprint="Sprint 9"),
        ]
    )
    result = start_sprint(
        board,
        Issues(),
        EventSink(None),
        ProcessRules.from_config(load_org()),
        sprint="Sprint 10",
        capacity=10,
        default_repo=REPO,
    )
    assert board.moves[0] == ("S284", READY) and ("S284", "Sprint") in board.cleared
    assert [c.number for c in result.admitted] == [283, 284]
    assert ("S284", "Sprint 10") in board.sprints
