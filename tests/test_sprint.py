"""Sprint planning is mechanical because approving an epic was the scope
decision. These tests pin the mechanics, and the reporting level: the Sponsor
sees epics, never a list of stories."""

from __future__ import annotations

from crew_org.flows.sprint import INBOX, NO_EPIC, READY, approved_epics, plan_sprint
from crew_org.tools.github_project import Card

REPO = "sprint-metrics"


def epic(number: int, priority: str = "P1", status: str = "Needs Refinement", repo=REPO) -> Card:
    return Card(
        item_id=f"E{number}",
        number=number,
        title=f"Epic {number}",
        status=status,
        state="OPEN",
        work_type="Epic",
        priority=priority,
        repo=repo,
    )


def story(
    number: int,
    points: int | None,
    status: str = READY,
    repo=REPO,
    priority: str | None = None,
) -> Card:
    return Card(
        item_id=f"S{number}",
        number=number,
        title=f"Story {number}",
        status=status,
        state="OPEN",
        work_type="Story",
        points=points,
        repo=repo,
        priority=priority,
    )


def parents(mapping: dict[int, int], repo: str = REPO) -> dict:
    """Story key -> epic key, for a single-repository plan."""
    return {(repo, child): (repo, parent) for child, parent in mapping.items()}


# --- which epics count ---------------------------------------------------


def test_epics_still_at_the_gate_are_not_in_scope():
    cards = [epic(3), epic(5, status=INBOX)]
    assert [e.number for e in approved_epics(cards)] == [3]


def test_closed_epics_are_not_in_scope():
    rejected = epic(9)
    rejected.state = "CLOSED"
    assert approved_epics([epic(3), rejected]) == [approved_epics([epic(3)])[0]]


def test_epics_are_taken_in_priority_order():
    cards = [epic(3, "P2"), epic(4, "P0"), epic(5, "P1")]
    assert [e.number for e in approved_epics(cards)] == [4, 5, 3]


def test_unprioritised_epics_sort_last():
    """Unprioritised work is not urgent — it is unconsidered."""
    unset = epic(7)
    unset.priority = None
    assert [e.number for e in approved_epics([unset, epic(3, "P3")])] == [3, 7]


# --- filling the sprint --------------------------------------------------


def test_a_sprint_that_fits_takes_everything():
    cards = [epic(3), story(6, 3), story(7, 2)]
    plan = plan_sprint(cards, parents({6: 3, 7: 3}), sprint="S1", capacity=20)
    assert [c.number for c in plan.admitted] == [6, 7]
    assert plan.points == 5
    assert plan.slices[0].complete


def test_capacity_stops_the_fill():
    cards = [epic(3), story(6, 8), story(7, 8), story(8, 8)]
    plan = plan_sprint(cards, parents({6: 3, 7: 3, 8: 3}), sprint="S1", capacity=20)
    assert [c.number for c in plan.admitted] == [6, 7]
    assert [c.number for c in plan.slices[0].deferred] == [8]
    assert not plan.slices[0].complete


def test_a_story_is_never_split_to_fit():
    """A partially admitted story is not deliverable; shaving scope by halves is
    how a sprint rots."""
    cards = [epic(3), story(6, 8), story(7, 5)]
    plan = plan_sprint(cards, parents({6: 3, 7: 3}), sprint="S1", capacity=10)
    assert [c.number for c in plan.admitted] == [6]
    assert plan.points == 8


def test_a_later_smaller_story_still_fits_after_a_deferral():
    """Capacity is a budget, not a cursor: passing on one story does not close
    the sprint to everything after it."""
    cards = [epic(3), story(6, 8), story(7, 1)]
    plan = plan_sprint(cards, parents({6: 3, 7: 3}), sprint="S1", capacity=8)
    assert [c.number for c in plan.admitted] == [6]
    plan2 = plan_sprint(cards, parents({6: 3, 7: 3}), sprint="S1", capacity=9)
    assert [c.number for c in plan2.admitted] == [6, 7]


def test_a_higher_priority_epic_is_filled_first():
    cards = [epic(3, "P2"), epic(4, "P0"), story(6, 8), story(9, 8)]
    plan = plan_sprint(cards, parents({6: 3, 9: 4}), sprint="S1", capacity=8)
    assert [c.number for c in plan.admitted] == [9]


def test_only_ready_stories_are_admitted():
    cards = [epic(3), story(6, 3), story(7, 3, status="Needs Refinement")]
    plan = plan_sprint(cards, parents({6: 3, 7: 3}), sprint="S1", capacity=20)
    assert [c.number for c in plan.admitted] == [6]


# --- stories with no epic (#46) -------------------------------------------


def test_a_story_with_no_epic_is_admitted():
    """crew #26, #40 and #42: filed by hand, refined, sized, in Ready — and
    reported as held on every tick because nothing could ever admit them."""
    cards = [epic(3), story(6, 3), story(99, 3)]
    plan = plan_sprint(cards, parents({6: 3}), sprint="S1", capacity=20)
    assert [c.number for c in plan.admitted] == [6, 99]
    assert [(p.number, p.title) for p in plan.slices] == [(3, "Epic 3"), (None, NO_EPIC)]


def test_stories_with_an_epic_come_first():
    """Epic priority orders the rest: a P0 story with no epic still waits for a
    P2 epic's stories."""
    cards = [epic(3, priority="P2"), story(6, 5), story(99, 5, priority="P0")]
    plan = plan_sprint(cards, parents({6: 3}), sprint="S1", capacity=5)
    assert [c.number for c in plan.admitted] == [6]
    assert [c.number for c in plan.slices[-1].deferred] == [99]


def test_stories_with_no_epic_are_taken_in_their_own_priority_order():
    cards = [story(7, 3, priority="P2"), story(8, 3, priority="P0"), story(9, 3)]
    plan = plan_sprint(cards, parents({}), sprint="S1", capacity=6)
    assert [c.number for c in plan.admitted] == [8, 7]
    assert [c.number for c in plan.slices[0].deferred] == [9]


def test_a_story_with_no_epic_and_no_estimate_is_excluded_and_named():
    """Criterion 2: genuinely not ready. An epic's stories are sized when the
    epic is split; a story filed by hand can reach Ready without Points."""
    cards = [story(40, None), story(42, 2)]
    plan = plan_sprint(cards, parents({}), sprint="S1", capacity=20)
    assert [c.number for c in plan.admitted] == [42]
    assert [c.number for c in plan.unestimated] == [40]


def test_no_slice_is_reported_when_every_story_has_an_epic():
    cards = [epic(3), story(6, 3)]
    plan = plan_sprint(cards, parents({6: 3}), sprint="S1", capacity=20)
    assert [p.number for p in plan.slices] == [3]


def test_an_epic_with_no_ready_stories_is_left_out_of_the_report():
    cards = [epic(3), epic(4), story(6, 3)]
    plan = plan_sprint(cards, parents({6: 3}), sprint="S1", capacity=20)
    assert [p.number for p in plan.slices] == [3]


def test_the_plan_reports_per_epic_not_per_story():
    """The Sponsor's unit of attention is the epic."""
    cards = [epic(3), epic(4), story(6, 3), story(9, 2)]
    plan = plan_sprint(cards, parents({6: 3, 9: 4}), sprint="S1", capacity=20)
    assert len(plan.slices) == 2
    assert [(p.number, p.points) for p in plan.slices] == [(3, 3), (4, 2)]


# --- only work the crew can actually deliver ------------------------------

CREW = "crew"


def test_a_story_the_crew_cannot_deliver_is_not_admitted():
    """`plan_sprint` filled from every Ready story while
    `delivery.sprint_stories` filtered — so planning admitted the card,
    delivery declined it as not_ours, and the capacity was gone either way.
    Measured 2026-09-22: five of eight admitted points were undeliverable."""
    cards = [epic(3), story(6, 3), story(7, 5, repo=CREW)]
    plan = plan_sprint(
        cards,
        parents({6: 3, 7: 3}) | parents({7: 3}, CREW),
        sprint="S1",
        capacity=20,
        repos={REPO},
    )
    assert [c.number for c in plan.admitted] == [6]
    assert plan.points == 3


def test_the_excluded_story_is_reported_rather_than_dropped():
    """It is real work with a real estimate, just not the crew's to do. A card
    that vanishes from planning without a word is how this went unnoticed."""
    cards = [epic(3), story(6, 3), story(7, 5, repo=CREW)]
    plan = plan_sprint(
        cards,
        parents({6: 3}) | parents({7: 3}, CREW),
        sprint="S1",
        capacity=20,
        repos={REPO},
    )
    assert [(c.repo, c.number) for c in plan.not_ours] == [(CREW, 7)]


def test_an_excluded_story_is_not_also_planned_as_one_with_no_epic():
    """Two reports of one card reads as two problems."""
    cards = [epic(3), story(9, None, repo=CREW)]
    plan = plan_sprint(cards, parents({}), sprint="S1", capacity=20, repos={REPO})
    assert [c.number for c in plan.not_ours] == [9]
    assert plan.unestimated == []
    assert plan.slices == []


def test_capacity_is_spent_only_on_deliverable_work():
    """The sprint looked two thirds full and was one third full."""
    cards = [epic(3), story(6, 8, repo=CREW), story(7, 8)]
    plan = plan_sprint(
        cards,
        parents({7: 3}) | parents({6: 3}, CREW),
        sprint="S1",
        capacity=8,
        repos={REPO},
    )
    assert [c.number for c in plan.admitted] == [7]
    assert plan.points == 8


def test_no_allow_list_admits_everything():
    """A caller with no delivery configuration gets every repository, which is
    the behaviour that existed before.

    Both epics are numbered 3, in different repositories — the collision #49
    fixed, and the reason each story's parent is looked up by key."""
    cards = [epic(3), epic(3, repo=CREW), story(6, 3), story(7, 5, repo=CREW)]
    plan = plan_sprint(cards, parents({6: 3}) | parents({7: 3}, CREW), sprint="S1", capacity=20)
    assert sorted(c.number for c in plan.admitted) == [6, 7]
    assert plan.not_ours == []


# --- capacity is the sprint's, not the run's (#222) -------------------------------------------


def in_sprint(number: int, points: int, status: str = "Done", sprint: str = "Sprint 6") -> Card:
    card = story(number, points, status=status)
    card.sprint = sprint
    return card


def test_points_already_in_the_sprint_are_counted_first():
    cards = [
        epic(59),
        in_sprint(1, 8, "Done"),
        in_sprint(2, 5, "In Progress"),
        story(10, 5),
        story(11, 5),
    ]
    plan = plan_sprint(cards, parents({10: 59, 11: 59}), sprint="Sprint 6", capacity=20)
    assert plan.committed == 13
    assert [c.number for c in plan.admitted] == [10], "13 + 5 fits; another 5 would not"
    assert plan.total == 18


def test_a_full_sprint_admits_nothing():
    """Sprint 6's real shape: 22 done stories with 63 points, 9 more with 17."""
    done = [in_sprint(100 + n, 3, "Done") for n in range(21)] + [in_sprint(121, 0, "Done")]
    split = [in_sprint(125 + n, 2, "Done") for n in range(8)] + [in_sprint(133, 1, "Done")]
    cards = [epic(59), *done, *split, story(143, 3), story(144, 2)]
    plan = plan_sprint(cards, parents({143: 59, 144: 59}), sprint="Sprint 6", capacity=20)
    assert plan.committed == 63 + 17 and plan.full
    assert plan.admitted == [] and [c.number for c in plan.slices[0].deferred] == [143, 144]


def test_another_sprints_points_are_not_this_ones():
    cards = [epic(59), in_sprint(1, 20, "Done", sprint="Sprint 6"), story(10, 5)]
    plan = plan_sprint(cards, parents({10: 59}), sprint="Sprint 7", capacity=20)
    assert plan.committed == 0 and [c.number for c in plan.admitted] == [10]


def test_another_repositorys_points_are_not_counted_when_the_crew_is_bounded():
    other = in_sprint(1, 20)
    other.repo = "elsewhere"
    cards = [epic(59), other, story(10, 5)]
    plan = plan_sprint(cards, parents({10: 59}), sprint="Sprint 6", capacity=20, repos={REPO})
    assert plan.committed == 0 and [c.number for c in plan.admitted] == [10]
