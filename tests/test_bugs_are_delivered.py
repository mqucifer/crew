"""A bug is delivered work: admitted, built, proven and merged like a story (crew#407, ADR 0027)."""

from __future__ import annotations

from crew_org.columns import DONE, IN_PROGRESS, INBOX, MERGING, QAING, READY, SPRINT_BACKLOG
from crew_org.flows.acceptance import awaiting_qa
from crew_org.flows.close import board_summary, sprint_cards
from crew_org.flows.delivery import sprint_stories
from crew_org.flows.merge import done_but_open, ready_to_land
from crew_org.flows.sprint import NO_EPIC, committed_points, plan_sprint, under_a_parked_epic
from crew_org.tools.github_project import Card
from crew_org.work_types import BUG, DELIVERED, STORY

REPO = "sm"


def _card(number: int, status: str, work_type: str = BUG, points: int = 2) -> Card:
    return Card(
        item_id=f"I{number}",
        number=number,
        title=f"Card {number}",
        status=status,
        state="OPEN",
        work_type=work_type,
        points=points,
        repo=REPO,
        sprint="S1",
    )


def test_a_story_and_a_bug_are_delivered_and_nothing_else():
    assert {STORY, BUG} == DELIVERED


def test_a_bug_in_ready_is_admitted_as_work_with_no_epic():
    plan = plan_sprint([_card(593, READY)], {}, sprint="S1", capacity=10)
    assert [c.number for c in plan.admitted] == [593]
    assert plan.slices[-1].title == NO_EPIC


def test_a_bugs_points_count_against_the_sprint():
    cards = [_card(593, SPRINT_BACKLOG), _card(1, SPRINT_BACKLOG, work_type="Epic")]
    assert committed_points(cards, "S1") == 2


def test_a_bug_moves_through_delivery_qa_and_merge():
    assert [c.number for c in sprint_stories([_card(593, SPRINT_BACKLOG)], "S1")] == [593]
    assert [c.number for c in awaiting_qa([_card(593, QAING)])] == [593]
    assert [c.number for c in ready_to_land([_card(593, MERGING)])] == [593]
    assert [c.number for c in done_but_open([_card(593, DONE)])] == [593]


def test_a_bug_is_in_the_sprints_cards_for_its_review():
    cards = [_card(593, DONE), _card(7, DONE, work_type=STORY), _card(1, DONE, work_type="Epic")]
    assert sorted(c.number for c in sprint_cards(cards, "S1")) == [7, 593]


def test_the_review_tells_a_bug_fixed_from_a_story_delivered():
    summary = board_summary([_card(593, DONE), _card(7, DONE, work_type=STORY)], "S1")
    assert "#7 [2pt] Done: Card 7" in summary
    assert "#593 [2pt] Done: Bug: Card 593" in summary


def _story(number: int, status: str, parent: int | None) -> Card:
    return _card(number, status, work_type=STORY).model_copy(update={"parent": parent})


def test_a_parked_epics_ready_story_waits_with_it():
    # sprint-metrics#599 was parked in Inbox after its split; its stories were
    # admitted as stories with no epic (crew#673).
    cards = [_card(599, INBOX, work_type="Epic"), _story(609, READY, 599), _card(593, READY)]
    plan = plan_sprint(cards, {}, sprint="S1", capacity=10)
    assert [c.number for c in plan.admitted] == [593], "work with no epic is still taken"
    assert [c.number for c in plan.parked] == [609]


def test_a_parked_epics_admitted_story_goes_back_to_ready():
    epic = _card(599, INBOX, work_type="Epic")
    started = _story(610, IN_PROGRESS, 599)
    approved = _card(598, READY, work_type="Epic")
    cards = [
        epic,
        _story(609, SPRINT_BACKLOG, 599),
        started,
        approved,
        _story(625, SPRINT_BACKLOG, 598),
    ]
    assert [c.number for c in under_a_parked_epic(cards)] == [609], "started work is left alone"
