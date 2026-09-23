"""Two cards with the same number are two cards.

The sprint close on 2026-09-19 reported:

    6 stories did not reach QA: #12, #19, #20, #31, #32, #32

and the retro described a single card two ways — a 3-point scrape-endpoint
story and a 0-point card about the board's own automations. Those are
sprint-metrics#32 and crew#32. Different cards, different repositories, one
number.

Not a display problem: `Card.number` was a dictionary key and a comparison in
three places where a collision silently picks the wrong card.
"""

from __future__ import annotations

from crew_org.flows.close import board_summary
from crew_org.flows.delivery import held_by_a_sibling
from crew_org.flows.sprint import plan_sprint
from crew_org.tools.github_project import Card, many_repos

SPRINT = "Sprint 3"


def card(number: int, repo: str, **kw) -> Card:
    return Card(
        item_id=f"{repo}-{number}",
        number=number,
        title=kw.pop("title", f"Card {number}"),
        state="OPEN",
        repo=repo,
        **kw,
    )


# --- identity ------------------------------------------------------------


def test_the_same_number_in_two_repositories_is_two_cards():
    assert card(32, "sprint-metrics").key != card(32, "crew").key


def test_a_card_is_named_plainly_on_a_single_repository_board():
    assert card(32, "sprint-metrics").name() == "#32"


def test_a_card_carries_its_repository_where_it_has_to():
    assert card(32, "sprint-metrics").name(qualify=True) == "sprint-metrics#32"


def test_one_repository_on_the_board_needs_no_qualifying():
    assert many_repos([card(1, "crew"), card(2, "crew")]) is False


def test_two_repositories_do():
    assert many_repos([card(32, "crew"), card(32, "sprint-metrics")]) is True


# --- the collisions that mattered ----------------------------------------


def test_a_story_is_not_attributed_to_an_epic_in_another_repository():
    """`parents` was keyed by story number alone, so a story in one repository
    could be planned into an epic slice belonging to another."""
    cards = [
        card(3, "crew", work_type="Epic", status="Needs Refinement", priority="P1"),
        card(6, "sprint-metrics", work_type="Story", status="Ready", points=3),
    ]
    # The epic's child is crew#6, which does not exist; sprint-metrics#6 does.
    plan = plan_sprint(cards, {("crew", 6): ("crew", 3)}, sprint=SPRINT, capacity=20)
    # Admitted, but as a story with no epic, not into crew#3's slice.
    assert [(p.number, [c.repo for c in p.admitted]) for p in plan.slices] == [
        (None, ["sprint-metrics"])
    ]


def test_a_story_is_not_held_back_by_a_sibling_in_another_repository():
    """`held_by_a_sibling` compared two plain parent numbers, so a story could
    wait on a story it has nothing to do with."""
    story = card(31, "sprint-metrics", work_type="Story", status="Sprint Backlog", parent=30)
    stranger = card(29, "crew", work_type="Story", status="In Progress", parent=30)
    assert held_by_a_sibling([stranger, story], story) is None


def test_a_real_sibling_still_holds_it_back():
    story = card(31, "sprint-metrics", work_type="Story", status="Sprint Backlog", parent=30)
    sibling = card(29, "sprint-metrics", work_type="Story", status="In Progress", parent=30)
    assert held_by_a_sibling([sibling, story], story) is sibling


# --- and the report the retro read ---------------------------------------


def test_a_two_repository_sprint_names_every_card_once():
    cards = [
        card(
            32,
            "sprint-metrics",
            work_type="Story",
            status="Sprint Backlog",
            sprint=SPRINT,
            points=3,
        ),
        card(32, "crew", work_type="Story", status="Sprint Backlog", sprint=SPRINT, points=0),
    ]
    summary = board_summary(cards, SPRINT)
    assert "sprint-metrics#32" in summary
    assert "crew#32" in summary


def test_a_single_repository_sprint_is_not_cluttered_with_it():
    cards = [card(32, "crew", work_type="Story", status="Sprint Backlog", sprint=SPRINT, points=3)]
    assert board_summary(cards, SPRINT).startswith("#32")
