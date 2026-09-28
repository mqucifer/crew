"""An empty answer is recorded, and nothing else changes (#312).

The model can think for thousands of tokens and answer nothing. CrewAI resends
the same prompt, and the crew's log showed only a call finishing and another
starting: sprint-metrics#268 spent 24 of 35 minutes on three empty answers.
Measured first; making the call weaker isn't an acceptable fix (Sponsor,
2026-09-28: correctness first).
"""

from __future__ import annotations

import pytest

from crew_org import events
from crew_org.events import EventKind, EventSink
from crew_org.llm import _recording


class Client:
    model = "crew-code-think"

    def __init__(self, answers):
        self._answers = list(answers)
        self.calls = 0

    def call(self, messages, *args, **kwargs):
        self.calls += 1
        return self._answers.pop(0)


@pytest.fixture
def seen():
    sink = EventSink(None)
    got = []
    sink.subscribe(got.append)
    events._TARGETS.append((sink, 268))
    yield got
    events._TARGETS.remove((sink, 268))


def wrapped(answers):
    client = Client(answers)
    client.__class__ = _recording(Client)
    return client


@pytest.mark.parametrize("empty", ["", "   \n", None])
def test_an_empty_answer_is_recorded_and_returned_unchanged(seen, empty):
    client = wrapped([empty])
    assert client.call([{"role": "user", "content": "x"}], response_model=object) == empty
    assert client.calls == 1, "no retry, no fallback: behaviour is unchanged"
    [event] = [e for e in seen if e.kind is EventKind.LLM_CALL_EMPTY]
    assert event.card == 268
    assert event.detail["model"] == "crew-code-think"
    assert event.detail["structured"] is True


def test_an_answer_is_not_recorded(seen):
    assert wrapped(["{}"]).call([]) == "{}"
    assert [e for e in seen if e.kind is EventKind.LLM_CALL_EMPTY] == []


def test_a_structured_answer_is_not_mistaken_for_empty(seen):
    parsed = object()
    assert wrapped([parsed]).call([]) is parsed
    assert seen == []


def test_the_wrapper_is_made_once_per_client_class():
    assert _recording(Client) is _recording(Client)
