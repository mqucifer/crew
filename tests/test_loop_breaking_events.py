"""Each step of breaking a loop is its own event, to find, count and replay.

Asked for by the Sponsor on 2026-09-26: "make sure these re-works and
re-discussions are properly tagged or flagged in the events to showcase this
loop breaking and how it works."
"""

from __future__ import annotations

from crew_org.crews.refinement_crew import ProductAnswer
from crew_org.events import EventKind, EventSink
from crew_org.flows import board_flow
from crew_org.flows.board_flow import TickResult, product_step
from crew_org.flows.story_problem import return_to_refinement
from tests.test_product_answers import EVIDENCE, Issues, epic
from tests.test_story_problem import Board, story
from tests.test_story_problem import Issues as StoryIssues


def watch() -> tuple[EventSink, list]:
    sink, seen = EventSink(None), []
    sink.subscribe(seen.append)
    return sink, seen


def test_a_story_sent_back_says_why_and_with_what():
    sink, seen = watch()
    cards = [story(97), story(98, status="Sprint Backlog")]
    return_to_refinement(
        Board(),
        StoryIssues(),
        sink,
        cards[0],
        repo="sprint-metrics",
        cards=cards,
        comment="evidence",
        reason="gate round trips",
    )
    (event,) = [e for e in seen if e.kind is EventKind.STORY_RETURNED]
    assert event.card == 97
    assert event.detail == {
        "repo": "sprint-metrics",
        "epic": 54,
        "reason": "gate round trips",
        "with": [98],
    }


def test_the_product_owners_decision_and_question_are_events(monkeypatch):
    for reply, kind in (
        (
            ProductAnswer(answer="stdout stays empty", based_on=["#91", "#150"]),
            EventKind.PRODUCT_ANSWERED,
        ),
        (ProductAnswer(question="stdout or stderr?"), EventKind.PRODUCT_ASKED),
    ):
        monkeypatch.setattr(board_flow, "answer_story_problem", lambda _r=reply, **kw: _r)
        sink, seen = watch()
        product_step(
            Issues(f"{board_flow.STORY_SPLIT_MARKER}", EVIDENCE),
            sink,
            TickResult(),
            epic(),
            "sprint-metrics",
            goal="g",
            project="p",
            delivered="d",
        )
        (event,) = [e for e in seen if e.kind is kind]
        assert event.card == 54 and event.role == "Product Owner"
    assert event.detail["question"] == "stdout or stderr?"
