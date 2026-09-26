"""Refinement is shown what the project has already delivered (#220).

Epic sprint-metrics#60 was split into seven stories; six repeated Sprint 6's
(#73, #70, #75, #76, #91, #92). The Business Analyst saw the code, not what was
delivered, and a CLI flag doesn't read as "this criterion is met".
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.crews.refinement_crew import AlreadyDelivered, StoryProposal, check_delivered
from crew_org.events import EventSink
from crew_org.flows.board_flow import tick
from crew_org.flows.delivered import delivered
from crew_org.tools.github_project import Card
from tests.test_board_flow import PROPOSAL, FakeBoard, FakeIssues, epic_card, make_story

REPO = "sprint-metrics"

RANGE_BODY = """As a **lead**, I want **a range**, so that **trends show**.

## Acceptance criteria

1. **Given** sprints 1 to 3
   **When** I pass `--sprint-range 1..3`
   **Then** the table has one row per sprint

2. **Given** a bad range
   **When** I pass `--sprint-range 3..1`
   **Then** it exits 2 naming the range
"""


def story_card(number: int) -> Card:
    return Card(item_id=f"S{number}", number=number, repo=REPO, work_type="Story", state="CLOSED")


class History(FakeIssues):
    def closed_since(self, repo, since):
        return [
            {
                "number": 73,
                "title": "Report a range",
                "body": RANGE_BODY,
                "state_reason": "completed",
            },
            {
                "number": 149,
                "title": "Duplicate",
                "body": RANGE_BODY,
                "state_reason": "not_planned",
            },
            {"number": 44, "title": "Goal: ranges", "body": "", "state_reason": "completed"},
        ]


def test_only_completed_stories_count_as_delivered():
    found = delivered(History(), [story_card(73), story_card(149)], REPO)
    assert found.numbers == {73}, (
        "a duplicate closed not planned delivered nothing; a Goal isn't a story"
    )
    (story,) = found.stories
    assert story.outcomes == ("the table has one row per sprint", "it exits 2 naming the range")


def test_the_block_names_each_story_and_what_it_promised():
    text = delivered(History(), [story_card(73)], REPO).render()
    assert text.splitlines() == [
        "- #73 Report a range",
        "  - then the table has one row per sprint",
        "  - then it exits 2 naming the range",
    ]


def test_a_split_citing_a_story_that_isnt_delivered_is_refused():
    proposal = StoryProposal(
        epic_title="Ranges",
        already_delivered=[AlreadyDelivered(title="Range table", by=999, why="it does")],
    )
    with pytest.raises(ValueError, match="#999, which the project has not delivered"):
        check_delivered(proposal, {73})
    check_delivered(proposal, None)  # nothing known: nothing to check against


def test_an_epic_entirely_delivered_splits_into_no_new_stories():
    proposal = StoryProposal(
        epic_title="Ranges",
        already_delivered=[AlreadyDelivered(title="Range table", by=73, why="its first outcome")],
    )
    assert proposal.stories == []
    with pytest.raises(ValidationError, match="at least one story"):
        StoryProposal(epic_title="Ranges")


def test_the_business_analyst_is_shown_it_and_what_it_leaves_out_is_recorded(monkeypatch):
    shown = {}
    split = StoryProposal(
        epic_title="Ranges",
        stories=[make_story("Reject a reversed range", 2)],
        already_delivered=[
            AlreadyDelivered(title="Range as a table", by=73, why="its first outcome is this")
        ],
    )

    def business_analyst(title, context="", **kw):
        shown.update(kw)
        return split

    monkeypatch.setattr("crew_org.flows.board_flow.propose_epics", lambda g, **kw: PROPOSAL)
    monkeypatch.setattr("crew_org.flows.board_flow.split_epic", business_analyst)
    issues = History()
    board = FakeBoard([epic_card(60), story_card(73)])
    tick(board, issues, EventSink(None), default_repo=REPO)

    assert "#73 Report a range" in shown["delivered"] and shown["delivered_numbers"] == {73}
    assert [i["title"] for i in issues.created] == ["Reject a reversed range"], "not written again"
    (comment,) = [body for n, body in issues.posted if n == 60 and "## Stories" in body]
    assert "Already delivered, not written again" in comment
    assert "Range as a table — delivered by #73" in comment


def test_an_epic_already_wholly_delivered_is_closed(monkeypatch):
    split = StoryProposal(
        epic_title="Ranges",
        already_delivered=[AlreadyDelivered(title="Range as a table", by=73, why="all of it")],
    )
    closed = []

    class Closing(History):
        def close(self, repo, number, *, reason="completed"):
            closed.append((number, reason))

    monkeypatch.setattr("crew_org.flows.board_flow.propose_epics", lambda g, **kw: PROPOSAL)
    monkeypatch.setattr("crew_org.flows.board_flow.split_epic", lambda t, c="", **kw: split)
    issues = Closing()
    tick(FakeBoard([epic_card(60), story_card(73)]), issues, EventSink(None), default_repo=REPO)
    assert issues.created == [] and closed == [(60, "completed")]
