"""The bridge, driven by CrewAI's real event bus (#117).

Every earlier test of the bridge faked the bus, and its forwarder was marked
`# pragma: no cover - needs a live crew`. So it could be broken without
anything failing, and it was: it registered one handler on `BaseEvent`, the bus
dispatches on an event's exact type, and no tick ever recorded a model call.
These emit real CrewAI events on the real bus and read what reached the sink.
"""

from __future__ import annotations

import pytest
from crewai.events import crewai_event_bus
from crewai.events.types.llm_events import (
    LLMCallCompletedEvent,
    LLMCallStartedEvent,
    LLMCallType,
)
from crewai.events.types.tool_usage_events import ToolUsageStartedEvent

from crew_org.events import EventKind, EventSink, bridge_crewai, flush_bridge, reset_bridge


@pytest.fixture
def seen():
    reset_bridge()
    sink, got = EventSink(None), []
    sink.subscribe(got.append)
    bridge_crewai(sink, card=31)
    yield got
    reset_bridge()


def emit(event):
    crewai_event_bus.emit(None, event)
    flush_bridge(timeout=10)


def test_a_model_call_is_recorded(seen):
    """Criterion 1: started and finished, with the alias."""
    emit(LLMCallStartedEvent(model="crew-local", call_id="c1", messages=[]))
    emit(
        LLMCallCompletedEvent(
            model="crew-local",
            call_id="c1",
            messages=[],
            response="ok",
            call_type=LLMCallType.LLM_CALL,
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            finish_reason="stop",
        )
    )
    kinds = [e.kind for e in seen]
    assert kinds == [EventKind.LLM_CALL_STARTED, EventKind.LLM_CALL_FINISHED]
    assert all(e.card == 31 for e in seen)
    finished = seen[-1].detail
    assert finished["model"] == "crew-local" and finished["finish_reason"] == "stop"
    assert finished["total_tokens"] == 15


def test_a_generation_that_spent_its_budget_reasoning_is_visible(seen):
    """#67's failure: reasoning_tokens ~16k, no text, finish=length. It has to be
    readable in the log, not only in a total."""
    emit(
        LLMCallCompletedEvent(
            model="crew-local",
            call_id="c2",
            messages=[],
            response="",
            call_type=LLMCallType.LLM_CALL,
            usage={
                "completion_tokens": 16386,
                "completion_tokens_details": {"reasoning_tokens": 16386},
            },
            finish_reason="length",
        )
    )
    detail = seen[-1].detail
    assert detail["finish_reason"] == "length" and detail["reasoning_tokens"] == 16386


def test_a_tool_call_is_recorded(seen):
    """Criterion 2."""
    emit(ToolUsageStartedEvent(tool_name="read_file", tool_args={"path": "a.py"}))
    assert [e.kind for e in seen] == [EventKind.TOOL_STARTED]
    assert seen[-1].detail["tool"] == "read_file"


def test_a_failed_call_says_why(seen):
    """#223: three design notes failed at ten minutes each and the log said only "failed"."""
    from crewai.events.types.llm_events import LLMCallFailedEvent

    emit(
        LLMCallFailedEvent(model="crew-local", call_id="c3", error="Invalid response from LLM call")
    )
    failed = seen[-1]
    assert failed.kind is EventKind.LLM_CALL_FAILED
    assert failed.detail["error"] == "Invalid response from LLM call"
