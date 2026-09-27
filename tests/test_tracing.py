"""The crew's traces: a tick, its phases, the cards, the model calls (#283)."""

from __future__ import annotations

import pytest
from crewai.events.types.llm_events import LLMCallCompletedEvent, LLMCallStartedEvent, LLMCallType
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from crew_org import tracing
from crew_org.events import EventSink, attributed, bridge_crewai, reset_bridge
from tests.test_bridge_live_bus import emit

EXPORTED = InMemorySpanExporter()


@pytest.fixture(scope="module", autouse=True)
def tracing_on():
    # One provider per process: OpenTelemetry sets the global once.
    assert tracing.start({}, exporter=EXPORTED)


@pytest.fixture(autouse=True)
def fresh():
    EXPORTED.clear()
    reset_bridge()
    yield
    reset_bridge()


def spans():
    return {s.name: s for s in EXPORTED.get_finished_spans()}


def test_a_phase_and_the_card_it_works_are_nested_spans_with_the_attributes():
    def deliver_one():
        return attributed(lambda: "done", card=147, repo="sprint-metrics", attempt=1)()

    assert attributed(deliver_one, sprint="Sprint 8", purpose="deliver")() == "done"
    got = spans()
    phase, card = got["deliver"], got["#147 deliver"]
    assert card.parent.span_id == phase.context.span_id
    assert card.attributes["crew.card"] == 147 and card.attributes["crew.repo"] == "sprint-metrics"
    assert card.attributes["crew.sprint"] == "Sprint 8" and card.attributes["crew.attempt"] == 1


def test_a_model_call_is_a_span_under_its_card_with_genai_attributes_and_no_content():
    bridge_crewai(EventSink(None))

    def call():
        emit(LLMCallStartedEvent(model="crew-code-think", call_id="t1", messages=[]))
        emit(
            LLMCallCompletedEvent(
                model="crew-code-think",
                call_id="t1",
                messages=[{"role": "user", "content": "THE PROMPT"}],
                response="THE RESPONSE",
                call_type=LLMCallType.LLM_CALL,
                usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                finish_reason="stop",
                response_id="abc123",
            )
        )

    attributed(attributed(call, card=147, repo="sprint-metrics"), purpose="deliver")()
    got = spans()
    model, card = got["gen_ai.chat"], got["#147 deliver"]
    assert model.parent.span_id == card.context.span_id, "nested under the card it was for"
    assert model.attributes["gen_ai.request.model"] == "crew-code-think"
    assert model.attributes["gen_ai.usage.input_tokens"] == 100
    assert model.attributes["gen_ai.response.id"] == "abc123"
    assert model.attributes["crew.card"] == 147
    everything = str([dict(s.attributes) for s in EXPORTED.get_finished_spans()])
    assert "THE PROMPT" not in everything and "THE RESPONSE" not in everything


def test_nothing_is_exported_without_an_endpoint():
    """Off unless org.yaml names the collector."""
    assert tracing.start({"telemetry": {}}) is True, "already started in this process"
    fresh_process = {"_PROVIDER": None}
    original = tracing._PROVIDER
    try:
        tracing._PROVIDER = fresh_process["_PROVIDER"]
        assert tracing.start({"telemetry": {}}) is False
    finally:
        tracing._PROVIDER = original
