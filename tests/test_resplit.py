"""A re-split replaces only the stories a story problem affects (crew#859).

sprint-metrics#685's problem named #702 and #703 of four; its re-split closed all
four and wrote four more, and the next problem did the same again.
"""

from __future__ import annotations

from crew_org.flows.resplit import affected, named_in

OWNER, REPO = "mqucifer", "sprint-metrics"
FOUR = {
    702: "story\n\nFollows the epic's R6.",
    703: "story\n\nFollows the epic's R2, R3, R5.",
    704: "story\n\nFollows the epic's R1, R2, R4, R5.",
    705: "story\n\nFollows the epic's R3.",
}


def test_a_problem_on_one_story_replaces_it_and_the_stories_after_it_only():
    plan = affected(FOUR, named={704}, replaced_rows=set(), started=set())
    assert plan.replaced == (704, 705)
    assert plan.kept == (702, 703)
    assert plan.positions == {704: 3}


def test_a_problem_on_the_first_story_replaces_them_all():
    """The cost of order as the only link between an epic's stories."""
    plan = affected(FOUR, named={702, 703}, replaced_rows=set(), started=set())
    assert plan.replaced == (702, 703, 704, 705) and plan.kept == ()
    assert plan.positions == {702: 1, 703: 2}


def test_a_story_following_a_replaced_row_is_affected_too():
    plan = affected(FOUR, named={705}, replaced_rows={"R3"}, started=set())
    assert plan.replaced == (703, 704, 705) and plan.kept == (702,)


def test_a_problem_that_names_none_of_them_affects_them_all():
    plan = affected(FOUR, named=set(), replaced_rows=set(), started=set())
    assert plan.replaced == tuple(FOUR) and plan.kept == ()


def test_work_started_on_an_affected_story_is_named_and_a_kept_one_isnt():
    plan = affected(FOUR, named={704}, replaced_rows=set(), started={702, 705})
    assert plan.started == (705,)


def test_an_epic_with_no_open_stories_replaces_nothing():
    plan = affected({}, named=set(), replaced_rows=set(), started=set())
    assert plan.replaced == () and plan.kept == ()


def test_the_stories_a_problem_names_are_its_references_to_them():
    problem = (
        "**No design note: [mqucifer/sprint-metrics#702](https://github.com/mqucifer/"
        "sprint-metrics/issues/702), sprint-metrics#703 and #705 can't be designed.** "
        "See mqucifer/crew#704 and the panel's note on #9."
    )
    assert named_in(problem, owner=OWNER, repo=REPO, among=FOUR) == {702, 703, 705}


def test_a_full_reference_to_this_repository_names_the_story():
    found = named_in("mqucifer/sprint-metrics#703 broke", owner=OWNER, repo=REPO, among=FOUR)
    assert found == {703}
