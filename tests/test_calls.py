"""The crew's own model calls: a refusal's reason reaches the retry (crew#583, ADR 0025).

The proxy is stood in for by `send`, which answers in turn and keeps what it was sent.
The real call, through LiteLLM, is in the pull request.
"""

from __future__ import annotations

import json
import re

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
    """Lean prompts, L3: the Developer's form was 13.5k characters of JSON Schema."""
    from crew_org.crews.delivery_crew import FirstOrDone  # noqa: PLC0415

    schema = FirstOrDone.model_json_schema()
    text = calls.form_text(schema)
    assert len(text) < len(json.dumps(schema, indent=2)) * 0.6
    assert "- `edits` (list of FileEdit): Changes to Python files that already exist" in text
    assert "- `operation` (one of `replace`, `add`, `add_method`, `add_import`, `delete`)" in text
    assert "**FileEdit**: One change to an existing file" in text
    assert '"$ref"' not in text and '"title"' not in text


# --- code in blocks after the JSON (ADR 0026) ------------------------------------------


class Write(BaseModel):
    path: str
    source: str = ""


class Change(BaseModel):
    summary: str
    writes: list[Write]


CODE = (
    "def test_json(run_command):\n    data = run_command()\n"
    '    assert data["superseded_breakdown"] == {"sponsor": 0}}],"\n'
)
TWO_PARTS = (
    '{"summary": "add it", "writes": [{"path": "tests/test_x.py", "source": "b1"}, '
    '{"path": "src/x.py", "source": ""}]}\n\n'
    "@@@ b1 @@@\n" + CODE + "@@@ end @@@\n"
)


def test_code_in_a_block_comes_back_exactly_as_written():
    """sprint-metrics#567's slip, `{\\"sponsor\\": 0}}],"`, inside a block is just code."""
    head, blocks = calls.split_blocks(TWO_PARTS)
    joined = calls.join_blocks(json.loads(head), blocks, frozenset({"source"}))
    assert joined["writes"][0]["source"] == CODE
    assert joined["writes"][1]["source"] == "", "an empty code field stays empty"


def test_a_block_in_a_fence_or_json_in_a_fence_is_unwrapped():
    fenced = (
        '```json\n{"summary": "s", "writes": [{"path": "p", "source": "b1"}]}\n```\n'
        "@@@ b1 @@@\n```python\nx = 1\n```\n@@@ end @@@\n"
    )
    head, blocks = calls.split_blocks(fenced)
    assert json.loads(head)["summary"] == "s" and blocks == {"b1": "x = 1\n"}


@pytest.mark.parametrize(
    ("text", "said"),
    [
        (
            '{"summary": "s", "writes": [{"path": "p", "source": "b2"}]}',
            "names block 'b2', which isn't written",
        ),
        ('{"summary": "s", "writes": []}\n@@@ b1 @@@\nx', "block 'b1' has no @@@ end @@@ line"),
        (
            '{"summary": "s", "writes": [{"path": "p", "source": "b1"}]}\n'
            "@@@ b1 @@@\nx\n@@@ end @@@\nstray words\n",
            "text after the JSON outside any block",
        ),
    ],
)
def test_a_missing_unused_or_unclosed_block_is_refused_with_why(text, said):
    with pytest.raises(ValueError, match=re.escape(said)):
        head, blocks = calls.split_blocks(text)
        calls.join_blocks(json.loads(head), blocks, frozenset({"source"}))


def test_an_answer_with_blocks_is_asked_without_the_grammar_and_joined():
    proxy = Proxy(TWO_PARTS)
    found = calls.ask("developer", PARTS, Change, send=proxy, blocks=frozenset({"source"}))
    assert found.writes[0].source == CODE
    sent = proxy.sent[0]
    assert "response_format" not in sent, "a JSON schema would forbid the blocks"
    assert sent["extra_body"]["ebnf"] == calls.blocks_grammar(Change.model_json_schema())
    prompt = sent["messages"][1]["content"]
    assert "@@@ b1 @@@" in prompt and "- `source` (code, as a block)" in prompt


def test_a_bad_json_part_is_refused_and_asked_again():
    proxy = Proxy('{"summary": "s", "writes": [}\n', TWO_PARTS)
    found = calls.ask("developer", PARTS, Change, send=proxy, blocks=frozenset({"source"}))
    assert found.summary == "add it"
    assert "isn't valid JSON" in proxy.sent[1]["messages"][-1]["content"]


@pytest.mark.parametrize("named", ["b1", "@@b1", "@@@ b1 @@@", "block b1", "`b1`", " B1 "])
def test_a_block_is_found_however_its_name_is_marked(named):
    """The first real answer wrote "@@b1"; a field holding only a block's name isn't code."""
    head = json.dumps({"summary": "s", "writes": [{"path": "p", "source": named}]})
    _, blocks = calls.split_blocks(f"{head}\n@@@ b1 @@@\nx = 1\n@@@ end @@@\n")
    joined = calls.join_blocks(json.loads(head), blocks, frozenset({"source"}))
    assert joined["writes"][0]["source"] == "x = 1\n"


def test_code_that_merely_mentions_a_block_name_is_left_as_code():
    data = {"source": "print('b1')"}
    assert calls.join_blocks(data, {}, frozenset({"source"})) == data


def test_a_block_no_field_names_is_left_out():
    """Ask-only answers wrote an example block and were refused for it (2026-10-11)."""
    head = '{"summary": "s", "writes": []}'
    _, blocks = calls.split_blocks(f"{head}\n@@@ b0 @@@\nexample\n@@@ end @@@\n")
    assert calls.join_blocks(json.loads(head), blocks, frozenset({"source"})) == json.loads(head)


def test_the_grammar_bounds_the_number_of_blocks():
    """Unbounded, two answers wrote the same block 4,994 and 6,464 times."""
    assert "block{0,60}" in calls.BLOCKS_GRAMMAR


def test_the_json_opens_with_the_forms_first_field_so_it_cant_be_empty():
    """Answers came back as `{}`, refused as "summary: Field required", in the live tick."""
    grammar = calls.blocks_grammar(Change.model_json_schema())
    assert 'head ::= "{" ws "\\"summary\\"" ws ":" ws nonempty' in grammar
    assert "root ::= head" in grammar and "nonempty ::=" in grammar

    class Count(BaseModel):
        count: int
        source: str = ""

    assert 'ws ":" ws value (' in calls.blocks_grammar(Count.model_json_schema())


def test_a_code_field_pointing_at_no_block_says_which_blocks_are_written():
    """`@@criteria_test_1@@` was taken as code and refused for not defining its test."""
    head = '{"summary": "s", "writes": [{"path": "p", "source": "@@criteria_test_1@@"}]}'
    _, blocks = calls.split_blocks(f"{head}\n@@@ b1 @@@\ndef test_x(): pass\n@@@ end @@@\n")
    with pytest.raises(ValueError, match="isn't a block's name.*written are b1"):
        calls.join_blocks(json.loads(head), blocks, frozenset({"source"}))


def test_code_that_merely_mentions_at_signs_is_still_code():
    head = '{"summary": "s", "writes": [{"path": "p", "source": "@decorator"}]}'
    joined = calls.join_blocks(json.loads(head), {}, frozenset({"source"}))
    assert joined["writes"][0]["source"] == "@decorator"


@pytest.mark.parametrize("named", ["f1", "b_url_for", "@@f1@@"])
def test_a_block_written_under_another_name_is_found_and_recorded(named):
    """sprint-metrics#626 wrote blocks f1 to f8; each name went in as the quote (crew#734)."""
    bare = named.strip("@")
    head = json.dumps({"summary": "s", "writes": [{"path": "p", "source": named}]})
    _, blocks = calls.split_blocks(f"{head}\n@@@ {bare} @@@\nx = 1\n@@@ end @@@\n")
    loose: list[dict] = []
    joined = calls.join_blocks(json.loads(head), blocks, frozenset({"source"}), loose)
    assert joined["writes"][0]["source"] == "x = 1\n"
    assert loose == [{"fallback": "block.name", "field": "source", "name": bare}]


def test_a_block_named_b1_records_no_fallback():
    head = json.dumps({"summary": "s", "writes": [{"path": "p", "source": "b1"}]})
    _, blocks = calls.split_blocks(f"{head}\n@@@ b1 @@@\nx = 1\n@@@ end @@@\n")
    loose: list[dict] = []
    calls.join_blocks(json.loads(head), blocks, frozenset({"source"}), loose)
    assert loose == []
