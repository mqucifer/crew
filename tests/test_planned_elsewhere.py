"""Splitting an epic sees the stories its sibling epics already hold (#295).

Goal sprint-metrics#87's epics #175 and #176 shared a foundation. Split a pass
apart, #176 wrote #195 and #196 again, duplicating #175's #189 and #190 while
those were still in Ready.
"""

from __future__ import annotations

import contextlib

import pytest

from crew_org.crews import refinement_crew
from crew_org.crews.refinement_crew import AlreadyDelivered, StoryProposal, check_delivered
from crew_org.events import EventSink
from crew_org.flows.board_flow import planned_elsewhere, render_story_body, tick
from crew_org.flows.delivery import held_by_what_it_builds_on
from crew_org.tools.github_project import Card
from tests import asked
from tests.test_board_flow import SPLIT, FakeBoard, FakeIssues, epic_card, make_story


def story(number, parent, status="Ready", state="OPEN", repo="sprint-metrics", title=None):
    return Card(
        item_id=f"S{number}",
        number=number,
        title=title or f"Story {number}",
        status=status,
        state=state,
        work_type="Story",
        repo=repo,
        parent=parent,
    )


def test_the_stories_other_epics_hold_are_what_a_split_must_not_write_again():
    cards = [
        story(189, 175, title="Extend the Card model"),
        story(190, 175, title="Calculate the rate"),
        story(197, 176),  # this epic's own
        story(150, 60, status="Done"),  # delivered: #220 shows those
        story(151, 60, state="CLOSED"),
        story(300, 175, repo="crew"),  # another repository
    ]
    split_now = [("sprint-metrics", 210, "Just split, this pass", 177)]
    assert planned_elsewhere(cards, split_now, repo="sprint-metrics", epic=176) == [
        (189, "Extend the Card model", 175),
        (190, "Calculate the rate", 175),
        (210, "Just split, this pass", 177),
    ]


def test_a_split_in_the_same_pass_sees_what_the_one_before_it_made(monkeypatch):
    seen: list[dict] = []

    def split(title, context="", **kw):
        seen.append(kw)
        return SPLIT

    monkeypatch.setattr("crew_org.flows.board_flow.split_epic", split)
    board, issues = FakeBoard([epic_card(3), epic_card(4)]), FakeIssues()
    tick(board, issues, EventSink(None), default_repo="sprint-metrics")
    assert len(seen) == 2
    assert seen[0]["planned"] == "", "the first split has nothing planned elsewhere"
    assert "(epic #3)" in seen[1]["planned"] and seen[1]["planned_numbers"]


def test_a_planned_story_may_be_named_as_covering_one():
    proposal = StoryProposal(
        epic_title="E",
        stories=[make_story("the range table")],
        already_delivered=[AlreadyDelivered(title="the Card fields", by=189, why="it adds them")],
    )
    check_delivered(proposal, {150}, planned={189})
    with pytest.raises(ValueError, match="neither delivered nor planned"):
        check_delivered(proposal, {150}, planned=set())


def test_the_business_analyst_is_shown_what_other_epics_plan(monkeypatch):
    seen = asked.capture(monkeypatch)
    with contextlib.suppress(asked.Stop):
        refinement_crew.split_epic("E", planned="- #189 Extend the Card model (epic #175)")
    assert "## Stories other epics already plan, not yet built" in seen["description"]
    assert "names it in `builds_on`" in seen["description"]


# --- delivery waits for what a story builds on ------------------------------------------


def test_a_story_s_body_says_what_it_builds_on():
    built = make_story("the range table").model_copy(update={"builds_on": [190, 189]})
    assert "**Builds on** — #189, #190" in render_story_body(built, 176, "Ranges")


def test_a_story_waits_until_what_it_builds_on_has_landed():
    body = "…\n**Builds on** — #189, #190\n…"
    mine = story(197, 176, status="Sprint Backlog")
    waiting = [mine, story(189, 175, status="Done"), story(190, 175, status="Reviewing")]
    assert held_by_what_it_builds_on(waiting, mine, body).number == 190
    landed = [mine, story(189, 175, status="Done"), story(190, 175, state="CLOSED")]
    assert held_by_what_it_builds_on(landed, mine, body) is None
    assert held_by_what_it_builds_on(waiting, mine, "no builds-on line") is None


# --- the split on the crew's call layer (crew#583, steps D1b and C4) ---------------------


def test_the_split_reads_inside_the_call_within_what_its_prompt_leaves(monkeypatch, tmp_path):
    from crew_org.tools.repo_context import READING_CHAR_CEILING  # noqa: PLC0415
    from crew_org.tools.repo_read import Reader  # noqa: PLC0415

    reader = Reader(tmp_path, budget=0)
    reader.read = ["left from an earlier call"]
    seen = asked.capture(monkeypatch)
    with contextlib.suppress(asked.Stop):
        refinement_crew.split_epic("E", "body", repository="r" * 1000, reader=reader)
    assert [t.name for t in seen["tools"]] == ["read_file"]
    assert reader.read == [], "each call starts with nothing read"
    assert (
        0
        < reader.left
        == READING_CHAR_CEILING - len(seen["description"].split("\n\nYour answer:")[0])
    )


def test_a_split_that_drops_a_superseded_story_is_refused_inside_the_call(monkeypatch):
    seen = asked.capture(monkeypatch)
    with contextlib.suppress(asked.Stop):
        refinement_crew.split_epic("E", "body", superseded=[(41, "Old story")])
    assert seen["tools"] is None, "no reader, no tool"
    dropped = StoryProposal(epic_title="E", stories=[make_story("the range table")])
    with pytest.raises(ValueError, match="doesn't say what became of #41"):
        seen["check"](dropped)


def test_with_no_room_to_read_the_split_is_offered_no_tool(monkeypatch, tmp_path):
    """sprint-metrics#561's first split with the tool had nothing left: it thought 17k
    tokens before its first read, and every read was refused."""
    from crew_org.tools.repo_context import READING_CHAR_CEILING  # noqa: PLC0415
    from crew_org.tools.repo_read import Reader  # noqa: PLC0415

    seen = asked.capture(monkeypatch)
    with contextlib.suppress(asked.Stop):
        refinement_crew.split_epic(
            "E", "body", repository="r" * READING_CHAR_CEILING, reader=Reader(tmp_path, budget=0)
        )
    assert seen["tools"] is None


def test_the_code_a_split_is_shown_leaves_room_to_read_beside_the_rest():
    from crew_org.tools import repo_context  # noqa: PLC0415

    assert repo_context.shown_ceiling(0) == repo_context.READ_FIRST_CHAR_CEILING
    beside = 100_000
    shown = repo_context.shown_ceiling(beside)
    assert beside + shown + repo_context.READ_ROOM_CHARS == repo_context.READING_CHAR_CEILING
    assert repo_context.shown_ceiling(10**6) == 0
