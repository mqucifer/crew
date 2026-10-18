"""The crew's own model calls: a refusal's reason reaches the retry (crew#583, ADR 0025).

The proxy is stood in for by `send`, which answers in turn and keeps what it was sent.
The real call, through LiteLLM, is in the pull request.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel, Field

from crew_org import calls, events, rule_list
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
    assert user["content"].startswith(
        "## The stories\n\nS1\n\nJudge them.\n\nYour answer: A verdict.\n\n"
        "Write your answer as JSON in this form"
    )


def test_the_forms_field_descriptions_are_in_the_prompt():
    """The server holds an answer to the schema's shape; only the prompt says what a
    field means. Without it the Developer wrote a whole test function in `test`."""

    class Named(BaseModel):
        test: str = Field(description="The test function's name, e.g. test_range_as_json")

    proxy = Proxy('{"test": "test_x"}')
    calls.ask("qa_engineer", PARTS, Named, send=proxy)
    assert (
        "The test function's name, e.g. test_range_as_json"
        in (proxy.sent[0]["messages"][1]["content"])
    )


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
    short = '{"approved": true, "why": "ok"}'
    with pytest.raises(calls.Refused, match="refused 3 times; the last time: why") as refused:
        ask(Proxy(short, short, short))
    assert not refused.value.empty
    # Each refused answer, with why, for the caller to keep (crew#690).
    assert len(refused.value.answers) == 3
    assert refused.value.answers[0].startswith("Refused: why")
    assert refused.value.answers[0].endswith(short)


def test_two_empty_answers_in_a_row_end_the_call_early():
    # mqucifer/sprint-metrics#567: the same prompt again answered empty ten times.
    proxy = Proxy("", "", GOOD)
    with pytest.raises(calls.Refused, match="refused 2 times; the last time: the answer was empty"):
        ask(proxy)
    assert len(proxy.sent) == 2


def test_the_roles_own_timeout_goes_with_the_call(monkeypatch):
    seen_timeouts: list[float] = []

    def post(body, *, timeout):
        seen_timeouts.append(timeout)
        return reply(GOOD)

    monkeypatch.setattr(calls, "stream", post)
    calls.ask("developer", PARTS, Verdict)
    assert seen_timeouts == [3600.0]


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
    assert first["messages"][1]["content"].endswith(calls.READ_FIRST)
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


# --- an answer that runs away as whitespace (mqucifer/sprint-metrics#567) ------------------


def sse(*pieces, usage=None, finish="stop"):
    """A streamed answer as the proxy sends it: one chunk per piece, then usage."""
    chunks = [
        {"id": "s1", "choices": [{"delta": {"content": p}, "finish_reason": None}]} for p in pieces
    ]
    chunks.append({"id": "s1", "choices": [{"delta": {}, "finish_reason": finish}]})
    if usage:
        chunks.append({"id": "s1", "choices": [], "usage": usage})
    lines = [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]
    return httpx.MockTransport(
        lambda request: httpx.Response(200, text="\n\n".join(lines) + "\n\n")
    )


def test_a_streamed_answer_is_assembled_with_its_usage():
    usage = {"prompt_tokens": 100, "completion_tokens": 20}
    data = calls.stream({"model": "m"}, transport=sse('{"approved": ', "true}", usage=usage))
    choice = data["choices"][0]
    assert choice["message"]["content"] == '{"approved": true}'
    assert choice["finish_reason"] == "stop" and data["usage"] == usage and data["id"] == "s1"


def test_whitespace_running_on_is_cut_off_as_a_runaway():
    pieces = ['{"approved": true, "why": "ok"}', *(["\n   "] * (calls.RUNAWAY_CHARS // 4 + 1))]
    data = calls.stream({"model": "m"}, transport=sse(*pieces, "never read"))
    choice = data["choices"][0]
    assert choice["finish_reason"] == "runaway"
    assert "never read" not in choice["message"]["content"]


def test_a_runaway_answer_is_refused_saying_where_it_went_wrong(seen):
    ran = {
        "choices": [
            {
                "message": {"content": '{"approved": true, "why": "x"}],"' + " " * 3000},
                "finish_reason": "runaway",
            }
        ]
    }
    proxy = Proxy(ran, GOOD)
    assert ask(proxy).approved
    told = proxy.sent[1]["messages"][-1]["content"]
    assert "ran on as whitespace after this" in told and '"why": "x"}],"' in told
    assert "EOF" not in told


def test_the_form_is_written_plainly_each_field_with_its_type_and_meaning():
    """Lean prompts, L3: the Developer's form was 13.5k characters of JSON Schema.

    Raised from 60% to 65% of the schema by the Sponsor's decision on crew#756: each rule
    the form checks is told in it, once, and that took the form to 64.4%."""
    from crew_org.crews.qa_crew import QAVerdict  # noqa: PLC0415

    schema = QAVerdict.model_json_schema()
    text = calls.form_text(schema)
    assert len(text) < len(json.dumps(schema, indent=2)) * 0.65
    assert "- `criteria` (list of CriterionVerdict): One verdict per criterion" in text
    assert "- `proven` (boolean): Is this criterion proven" in text
    assert "**CriterionVerdict**" in text
    assert '"$ref"' not in text and '"title"' not in text


def test_a_refusal_by_a_declared_rule_is_recorded_with_its_name(seen):
    """The event names the rule (ADR 0031), so a count needn't match its text."""
    ask(Proxy("", GOOD))
    (refused,) = [e for e in seen if e.kind == EventKind.LLM_CALL_REFUSED]
    assert refused.detail["rules"] == ["call.the_answer_was_empty_you"]


# --- the rule ids a prompt gives (crew#778) ---------------------------------


def test_each_call_names_the_rules_its_prompt_gives_and_the_catalog(seen):
    """Given beside refused (crew#772): the first call that gives a set carries its ids,
    by where they sit; every call carries the set's hash and the catalog's version."""
    calls._GIVEN_WRITTEN.clear()
    proxy = Proxy(called(("read_file", {"path": "src/metrics.py"})), reply("Done."), "", GOOD)
    ask(proxy, tools=[reader()])
    ask(Proxy(GOOD))
    started = [e.detail for e in seen if e.kind is EventKind.LLM_CALL_STARTED]
    assert len(started) == 5, "two tool rounds, two tries, then the second ask"
    assert len({d["given"] for d in started}) == 1
    assert all(d["catalog"] == rule_list.catalog_version() for d in started)
    assert [("given_rules" in d) for d in started] == [True, False, False, False, False]
    rules = started[0]["given_rules"]
    assert set(rules) == {"system", "form", "site"}
    assert all(i in {e.id for e in rule_list.entries()} for ids in rules.values() for i in ids)


def test_a_prompt_site_s_line_counts_only_where_the_prompt_says_it():
    rule, shown = next(iter(rule_list._site_lines()))
    said = rule_list.given(role="", sections=[], agents=[], forms=[], prompt=f"a\n{shown}\nb")
    unsaid = rule_list.given(role="", sections=[], agents=[], forms=[], prompt="nothing here")
    assert rule in said["site"] and rule not in unsaid["site"]


def test_a_passage_told_to_one_role_is_given_only_to_it():
    """crew#756's markers: §1's mechanics are told to the Senior Engineer alone."""
    senior = rule_list.given(role="Senior Engineer", sections=[1], agents=[], forms=[], prompt="")
    owner = rule_list.given(role="Product Owner", sections=[1], agents=[], forms=[], prompt="")
    assert "§1 no_agent_may_move_a_card" in senior["system"]
    assert "§1 no_agent_may_move_a_card" not in owner["system"]


def test_the_rules_given_stay_in_the_crews_own_record():
    """Not content, but kept local unless the Sponsor says otherwise (crew#778)."""
    from crew_org import telemetry

    assert not {"given", "given_rules", "catalog"} & telemetry.DETAIL


def test_a_lookup_that_fails_leaves_the_call_unmarked_not_unmade(seen, monkeypatch):
    monkeypatch.setattr(rule_list, "given", lambda **kw: 1 / 0)
    assert ask(Proxy(GOOD)).approved
    started = [e.detail for e in seen if e.kind is EventKind.LLM_CALL_STARTED]
    assert "given" not in started[0]


def test_a_rule_is_told_once_in_a_form_however_many_fields_it_checks(monkeypatch):
    """The Sponsor, 2026-10-17 (crew#756): the path rule was told three times on the
    Developer's first-answer form, the named-test rule twice."""

    class Two(BaseModel):
        first: str
        second: str

    said = "Refused if it leaves the repository."
    told = {"first": [said], "second": [said]}
    monkeypatch.setattr(calls.rulebook, "told", lambda name: told if name == "Two" else {})
    text = " ".join(calls.form_text(Two.model_json_schema()).split())
    assert text.count(said) == 1


def test_a_conversation_call_sends_the_tools_and_records_it_as_any_call(seen):
    """The Developer's session (crew#778): one call of a conversation the caller keeps."""
    proxy = Proxy(reply("", finish="tool_calls", reasoning=7))
    tool = {"type": "function", "function": {"name": "view", "parameters": {}}}
    said = calls.converse(
        "developer", [{"role": "system", "content": "s"}], [tool], send=proxy, turn=4
    )
    body = proxy.sent[0]
    assert (body["model"], body["max_tokens"], body["tool_choice"]) == (
        "crew-code-think",
        65536,
        "auto",
    )
    assert body["tools"] == [tool] and "response_format" not in body
    assert (said.finish_reason, said.usage["reasoning_tokens"]) == ("tool_calls", 7)
    started, finished = (e for e in seen if e.kind.value.startswith("llm."))
    assert started.detail["turn"] == finished.detail["turn"] == 4
    assert finished.detail["tool_calls"] == 0 and finished.detail["completion_tokens"] == 27
