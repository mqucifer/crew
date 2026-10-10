"""The crew's own model calls: a refusal's reason reaches the retry (crew#583, ADR 0025).

The proxy is stood in for by `send`, which answers in turn and keeps what it was sent.
The real call, through LiteLLM, is in the pull request.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel, Field

from crew_org import calls, events
from crew_org.events import EventKind, EventSink
from crew_org.llm import backend_down


class Verdict(BaseModel):
    approved: bool
    why: str = Field(min_length=3)


def reply(content, *, finish="stop", reasoning=0):
    return {
        "id": "resp-1",
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20 + reasoning,
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        },
    }


class Proxy:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent: list[dict] = []

    def __call__(self, body):
        self.sent.append(json.loads(json.dumps(body)))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return reply(answer) if isinstance(answer, str) else answer


@pytest.fixture
def seen():
    found: list = []
    sink = EventSink(None)
    sink.subscribe(found.append)
    events.reset_bridge()
    events._TARGETS.append((sink, None))
    yield found
    events.reset_bridge()


GOOD = '{"approved": true, "why": "All criteria pass"}'
PARTS = [calls.Part("stories", "## The stories\n\nS1"), calls.Part("task", "Judge them.")]


def ask(proxy, **kw):
    return calls.ask(
        "qa_engineer",
        PARTS,
        Verdict,
        step="criteria_check",
        send=proxy,
        expected="A verdict.",
        **kw,
    )


def test_a_valid_answer_is_returned_in_its_form(seen):
    assert ask(Proxy(GOOD)) == Verdict(approved=True, why="All criteria pass")


def test_the_messages_are_the_roles_text_and_the_steps_parts():
    proxy = Proxy(GOOD)
    ask(proxy)
    system, user = proxy.sent[0]["messages"]
    assert system["content"].startswith("You are QA Engineer. You read criteria as the tests")
    assert "Your personal goal is: Before stories are created" in system["content"]
    assert user["content"] == "## The stories\n\nS1\n\nJudge them.\n\nYour answer: A verdict."


def test_the_role_settings_and_the_schema_go_with_the_call():
    proxy = Proxy(GOOD)
    ask(proxy)
    body = proxy.sent[0]
    assert body["model"] == "crew-code-think" and body["max_tokens"] == 32768
    schema = body["response_format"]["json_schema"]
    assert schema["name"] == "Verdict"
    assert schema["schema"]["required"] == ["approved", "why"]
    assert schema["schema"]["additionalProperties"] is False


def test_crewais_framing_is_gone():
    proxy = Proxy(GOOD)
    ask(proxy)
    user = proxy.sent[0]["messages"][1]["content"]
    assert "Current Task" not in user and "OpenAPI schema" not in user
    assert "Preserve the original content" not in user


def test_a_refused_answer_is_asked_for_again_with_the_reason(seen):
    proxy = Proxy('{"approved": true, "why": ""}', GOOD)
    assert ask(proxy).approved
    retry = proxy.sent[1]["messages"]
    assert retry[2] == {"role": "assistant", "content": '{"approved": true, "why": ""}'}
    assert retry[3]["role"] == "user"
    assert retry[3]["content"].startswith("That answer was refused: why: String should have")
    [refused] = [e for e in seen if e.kind is EventKind.LLM_CALL_REFUSED]
    assert refused.detail["try"] == 1 and "why" in refused.detail["reason"]


def test_an_empty_answer_is_a_refusal_with_that_reason():
    proxy = Proxy("", GOOD)
    ask(proxy)
    retry = proxy.sent[1]["messages"]
    assert len(retry) == 3, "nothing of an empty answer to send back"
    assert "the answer was empty" in retry[2]["content"]


def test_the_steps_own_rule_refuses_too_and_its_reason_is_told():
    def check(verdict):
        if verdict.approved:
            raise ValueError("a split with an open question is never approved")

    proxy = Proxy(GOOD, '{"approved": false, "why": "Q1 is open"}')
    assert not ask(proxy, check=check).approved
    assert "never approved" in proxy.sent[1]["messages"][3]["content"]


def test_every_attempt_refused_ends_with_the_last_reason():
    with pytest.raises(calls.Refused, match="refused 3 times; the last time: the answer was empty"):
        ask(Proxy("", "", ""))


def test_each_attempt_is_recorded_with_its_tokens_and_the_parts_sizes(seen):
    proxy = Proxy(reply("", reasoning=900), GOOD)
    ask(proxy)
    started = [e for e in seen if e.kind is EventKind.LLM_CALL_STARTED]
    finished = [e for e in seen if e.kind is EventKind.LLM_CALL_FINISHED]
    assert [e.detail["try"] for e in started] == [1, 2]
    assert started[0].detail["parts"] == {"stories": 18, "task": 11}
    assert finished[0].detail["reasoning_tokens"] == 900
    assert finished[0].detail["response_id"] == "resp-1"
    assert finished[0].role == "QA Engineer"
    assert started[0].detail["prompt_hash"] == started[1].detail["prompt_hash"]
    assert finished[0].detail["output_schema"].startswith("Verdict:")


def test_a_proxy_that_is_down_is_infrastructure_not_a_refusal(seen):
    down = calls.ServiceUnavailableError("Connection error: refused")
    with pytest.raises(calls.ServiceUnavailableError) as raised:
        ask(Proxy(down))
    assert backend_down(raised.value)
    assert [e.kind for e in seen if e.kind.value.startswith("llm.")] == [
        EventKind.LLM_CALL_STARTED,
        EventKind.LLM_CALL_FAILED,
    ]


def test_a_connection_refused_by_the_proxy_reads_as_down(monkeypatch):
    def refuse(*_a, **_k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(calls.httpx, "post", refuse)
    with pytest.raises(calls.ServiceUnavailableError) as raised:
        calls.post({"model": "crew-local"})
    assert backend_down(raised.value)


def test_a_refusal_is_counted_as_its_kind_of_failure():
    from crew_org.rules import Kind, kind_of

    assert kind_of("llm.refused", "", {"empty": True}) is Kind.EMPTY
    assert kind_of("llm.refused", "", {"empty": False}) is Kind.FORM_REFUSED


# --- tools in rounds, then the answer in its form (ADR 0024, ADR 0025) -----------------------


def called(*calls):
    return {
        "id": "resp-t",
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"c{i}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                        for i, (name, args) in enumerate(calls)
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


FILES = {"src/metrics.py": "def first_attempt_count():\n    return 1\n"}


def reader() -> calls.Tool:
    """A read tool over a dict, standing in for the repository's (step C4)."""
    return calls.Tool(
        name="read_file",
        description="Read a file",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        run=lambda arguments: FILES.get(arguments.get("path", ""), "no such file"),
    )


def test_a_tool_is_called_in_rounds_and_the_answer_comes_in_its_form(seen):
    proxy = Proxy(called(("read_file", {"path": "src/metrics.py"})), reply("Done reading."), GOOD)
    found = ask(proxy, tools=[reader()])
    assert found.approved
    first, second, final = proxy.sent
    assert "response_format" not in first and first["tools"][0]["function"]["name"] == "read_file"
    told = [m for m in second["messages"] if m["role"] == "tool"]
    assert told[0]["tool_call_id"] == "c0" and "def first_attempt_count" in told[0]["content"]
    assert "tools" not in final and final["response_format"]["json_schema"]["name"] == "Verdict"
    assert final["messages"][-1]["content"] == calls.ANSWER_NOW
    assert [e.detail["tool"] for e in seen if e.kind is EventKind.TOOL_FINISHED] == ["read_file"]


def test_the_rounds_end_at_their_limit():
    reading = called(("read_file", {"path": "src/metrics.py"}))
    proxy = Proxy(reading, reading, GOOD)
    ask(proxy, tools=[reader()], rounds=2)
    assert len(proxy.sent) == 3 and "response_format" in proxy.sent[2]


def test_a_tool_that_does_not_exist_or_bad_arguments_are_told_not_raised():
    bad = called(("write_file", {"path": "x"}))
    proxy = Proxy(bad, reply(""), GOOD)
    ask(proxy, tools=[reader()])
    told = [m for m in proxy.sent[1]["messages"] if m["role"] == "tool"]
    assert "no tool named 'write_file'" in told[0]["content"]
