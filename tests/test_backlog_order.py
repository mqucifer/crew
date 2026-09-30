"""The Product Owner orders a repository's backlog when new work arrives (#358)."""

from __future__ import annotations

from crew_org.crews.refinement_crew import BacklogOrder, Hold
from crew_org.events import EventSink
from crew_org.flows import backlog_order as bo
from crew_org.flows.board_flow import BUILDS_ON
from crew_org.flows.sprint import approved_epics
from crew_org.tools.github_project import Card

STORY_BODY = "As a reader…\n\n## Acceptance criteria\n\n1. **Given** x\n\n**Estimate** — 2 points"


def epic(number, rank=None, status="Needs Refinement", labels=()):
    return Card(
        item_id=f"E{number}",
        number=number,
        title=f"Epic {number}",
        status=status,
        state="OPEN",
        work_type="Epic",
        repo="sprint-metrics",
        rank=rank,
        labels=frozenset(labels),
        parent=48,
    )


def story(number, parent, status="Ready"):
    return Card(
        item_id=f"S{number}",
        number=number,
        title=f"Story {number}",
        status=status,
        state="OPEN",
        work_type="Story",
        repo="sprint-metrics",
        parent=parent,
    )


class Board:
    def __init__(self):
        self.ranks: dict[str, float] = {}

    def set_number(self, item_id, field, value):
        assert field == "Rank"
        self.ranks[item_id] = value


class Issues:
    def __init__(self):
        self.bodies = {}
        self.edited = []

    def get(self, repo, number):
        return {"body": self.bodies.get(number, STORY_BODY)}

    def edit_issue(self, repo, number, *, body):
        self.bodies[number] = body
        self.edited.append(number)


def run(cards, answers):
    replies = iter(answers)
    calls = []

    def order(**kw):
        calls.append(kw)
        return next(replies)

    board, issues = Board(), Issues()
    result = bo.order_backlogs(
        board, issues, EventSink(None), cards, repos={"sprint-metrics"}, order=order
    )
    return result, board, issues, calls


def test_nothing_is_asked_while_every_open_epic_has_a_rank():
    result, _board, _issues, calls = run([epic(62, rank=1), epic(63, rank=2)], [])
    assert calls == [] and result.ordered == []


def test_an_unranked_epic_orders_the_whole_repository_once():
    cards = [epic(62), epic(63), epic(366, labels=["technical"]), story(375, 62)]
    answer = BacklogOrder(order=[366, 62, 63])
    result, board, _issues, calls = run(cards, [answer])
    assert len(calls) == 1
    assert board.ranks == {"E366": 1.0, "E62": 2.0, "E63": 3.0}
    assert result.ordered == [("sprint-metrics", [366, 62, 63])]


def test_it_is_shown_one_line_per_item_never_bodies():
    cards = [epic(62), story(375, 62)]
    _result, _board, _issues, calls = run(cards, [BacklogOrder(order=[62])])
    backlog = calls[0]["backlog"]
    assert "- #62 epic, Needs Refinement, not yet ranked: Epic 62 (Goal #48)" in backlog
    assert "- #375 story of #62, Ready: Story 375" in backlog
    assert "Acceptance criteria" not in backlog


def test_a_hold_is_written_as_a_builds_on_line_and_only_added():
    cards = [epic(62), epic(304, labels=["technical"]), story(293, 62)]
    answer = BacklogOrder(
        order=[304, 62],
        holds=[Hold(story=293, builds_on=[304], why="the release needs the image user")],
    )
    result, _board, issues, _calls = run(cards, [answer])
    assert f"{BUILDS_ON} #304" in issues.bodies[293]
    assert issues.bodies[293].index(BUILDS_ON) < issues.bodies[293].index("**Estimate**")
    assert result.holds == [("sprint-metrics", 293, [304], "the release needs the image user")]


def test_an_existing_hold_is_kept_when_another_is_added():
    body = f"{STORY_BODY.split('**Estimate**')[0]}{BUILDS_ON} #297\n\n**Estimate** — 2 points"
    assert f"{BUILDS_ON} #297, #304" in bo.with_holds(body, {304})
    assert bo.with_holds(body, {297}) == body


def test_an_order_that_misses_an_epic_is_retried_then_refused():
    cards = [epic(62), epic(63)]
    short = BacklogOrder(order=[62])
    result, board, _issues, calls = run(cards, [short, short])
    assert len(calls) == 2 and "isn't ranked" in calls[1]["feedback"]
    assert board.ranks == {} and result.failed


def test_a_hold_on_work_that_isn_t_listed_is_refused():
    cards = [epic(62), story(375, 62)]
    bad = BacklogOrder(order=[62], holds=[Hold(story=375, builds_on=[999], why="x")])
    result, _board, issues, _calls = run(cards, [bad, bad])
    assert result.failed and issues.edited == []


def test_planning_takes_epics_by_priority_then_rank():
    cards = [epic(65, rank=1), epic(62, rank=3), epic(63), epic(64, rank=2)]
    assert [c.number for c in approved_epics(cards)] == [65, 64, 62, 63]
