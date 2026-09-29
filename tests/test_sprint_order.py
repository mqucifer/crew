"""Planning takes technical work first, reads what a story builds on, and frees superseded points.

Sprint 10 (2026-09-29) admitted the docs work ahead of the image work 1.0.0
depends on: technical epics have no Priority and sorted last. It admitted
sprint-metrics#293 (cut 1.0.0) and #297, held all sprint on #303–#306, because
planning never read "Builds on" (crew#358). And a story closed as not planned,
superseded by a re-split, kept holding its points (crew#327).
"""

from __future__ import annotations

from crew_org.columns import DONE, IN_PROGRESS, READY
from crew_org.flows.sprint import committed_points, plan_sprint
from tests.test_sprint import REPO, epic, story


def card(number, points, *, parent, status=READY, sprint=None):
    c = story(number, points, status=status)
    c.parent, c.sprint = parent, sprint
    return c


def technical(number):
    e = epic(number, priority=None)
    e.labels = frozenset({"technical"})
    return e


def plan(cards, capacity, builds_on=None):
    stories = [c for c in cards if c.work_type == "Story"]
    parents = {(REPO, c.number): (REPO, c.parent) for c in stories if c.parent}
    return plan_sprint(cards, parents, sprint="Sprint 10", capacity=capacity, builds_on=builds_on)


def admitted(result):
    return [c.number for c in result.admitted]


# --- technical work first ----------------------------------------------------------------------


def test_technical_epics_are_planned_before_prioritised_product_ones():
    cards = [epic(179, "P0"), card(283, 5, parent=179), technical(303), card(307, 3, parent=303)]
    assert admitted(plan(cards, capacity=4)) == [307]


# --- what a story builds on --------------------------------------------------------------------


def test_a_story_waits_for_a_story_it_builds_on_that_is_not_coming():
    cards = [epic(181), card(292, 1, parent=181), epic(186), card(297, 1, parent=186)]
    result = plan(cards, capacity=1, builds_on={(REPO, 297): {292}})
    assert admitted(result) == [292], "#297 waits: #292 took the room"


def test_it_goes_in_with_what_it_builds_on():
    cards = [epic(181), card(292, 1, parent=181), epic(186), card(297, 1, parent=186)]
    assert admitted(plan(cards, capacity=5, builds_on={(REPO, 297): {292}})) == [292, 297]


def test_what_is_done_or_under_way_does_not_hold_it():
    cards = [
        epic(186),
        card(217, 2, parent=None, status=DONE),
        card(291, 2, parent=None, status=IN_PROGRESS, sprint="Sprint 10"),
        card(293, 2, parent=186),
    ]
    assert admitted(plan(cards, capacity=5, builds_on={(REPO, 293): {217, 291}})) == [293]


def test_an_epic_it_builds_on_lands_only_when_all_its_open_stories_do():
    """#293 builds on #303-#306: 1.0.0 waits for all the image work."""
    cards = [
        technical(303),
        card(307, 2, parent=303),
        card(309, 3, parent=303),
        epic(181),
        card(293, 1, parent=181),
    ]
    held = plan(cards, capacity=3, builds_on={(REPO, 293): {303}})
    assert admitted(held) == [307], "#309 didn't fit, so #303 won't land this sprint"
    whole = plan(cards, capacity=10, builds_on={(REPO, 293): {303}})
    assert admitted(whole) == [307, 309, 293]


def test_an_epic_not_yet_split_holds_what_builds_on_it():
    cards = [technical(305), epic(181), card(293, 1, parent=181)]
    assert admitted(plan(cards, capacity=10, builds_on={(REPO, 293): {305}})) == []


# --- superseded stories (#327) -------------------------------------------------------------------


def test_a_superseded_story_holds_no_points_and_a_delivered_one_still_does():
    superseded = card(207, 5, parent=178, sprint="Sprint 10")
    superseded.state, superseded.state_reason = "CLOSED", "NOT_PLANNED"
    delivered = card(208, 3, parent=178, sprint="Sprint 10", status=DONE)
    delivered.state, delivered.state_reason = "CLOSED", "COMPLETED"
    assert committed_points([superseded, delivered], "Sprint 10") == 3
