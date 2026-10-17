"""The crew's own model calls (crew#583, ADR 0025).

One module makes a step's model call. It builds the messages from the role's
entry in `agents.yaml` and the step's named parts, sends them to LiteLLM with the
answer's JSON schema, and validates the answer. A refused answer is asked for
again with the refusal's reason appended, after the answer it refuses; an empty
answer is a refusal with that reason. Every attempt is recorded, with its reason
and the size of each part the step was shown.

This is structured output with schema validation and a re-ask carrying the error,
the pattern the Instructor library made common, over the OpenAI-compatible API
the proxy serves. CrewAI, which the crew used for this, resends identical
messages after a refusal, so the model never saw why: 97 of 609 calls in Sprints
17 to 20 were refused, a third of the Developer's, and sprint-metrics#529 six
times over one criterion. With tools present it drops the answer's schema, so a
read tool and a structured answer can't both be had (ADR 0024).

Staged (ADR 0025): the criteria check is the first step to use it. The others
move only after it is proven on a real epic, a few per pull request.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any, NoReturn

import httpx
from pydantic import BaseModel, ValidationError

from crew_org import rulebook
from crew_org.constitution import excerpt
from crew_org.cut_text import check_not_cut_at_a_quote
from crew_org.events import EventKind, record
from crew_org.llm import DEFAULT_MAX_TOKENS, DEFAULT_TIMEOUT, api_key, base_url
from crew_org.permissions import load_agents

# Three attempts, as CrewAI gave: the first, and two that are told why.
ATTEMPTS = 3
# Empty answers in a row before the call gives up early. An empty answer is the
# model thinking itself out on a prompt or an answer too big to finish (crew#312),
# and the same prompt again rarely finishes: mqucifer/sprint-metrics#567 answered
# empty ten times in two hours at 82k prompt tokens (2026-10-11). The caller has
# a smaller way to ask, such as the Developer's attempt in steps.
EMPTY_LIMIT = 2
# Of a refused answer, how much is sent back with its reason: enough to see what
# was wrong with it, not a second copy of a long answer.
ECHO_CHARS = 4000
# Whitespace in a row, in an answer's JSON, that means it has run away. The server
# holds the answer to the schema, which allows whitespace between any two tokens:
# a model that closes a string early wants a token the schema now forbids, and
# writes whitespace instead, to the token limit. mqucifer/sprint-metrics#567 did,
# 2026-10-11: 8,918 characters of answer, then 84,000 lines of whitespace over 40
# minutes. A real answer has none: line breaks inside its strings are escaped.
RUNAWAY_CHARS = 2000
# How much of the answer before the run is shown to the model with the reason.
RUNAWAY_TAIL = 300

# An answer whose code comes back in blocks (ADR 0026): the JSON holds the structure,
# and each code field names a block written after it, exactly as the code reads.
_BLOCK_START = re.compile(r"^@@@ ([A-Za-z0-9_-]+) @@@[ \t]*$")
_BLOCK_END = "@@@ end @@@"
_FENCE = re.compile(r"^```[\w+-]*[ \t]*$")
BLOCKS_FORMAT = (
    "Write your answer in two parts. First the JSON, in the form below, every field "
    'present, each as its description says. A field marked "code, as a block" holds '
    'the name of a block, such as "b1", not the code: an empty one may stay "". '
    "Then, after the JSON, write each block once:\n\n"
    "@@@ b1 @@@\n"
    "the code or text exactly as it should read, nothing escaped, no ``` around it\n"
    "@@@ end @@@\n\n"
    "Every block the JSON names is written after it, and every block written is named."
)


def split_blocks(text: str) -> tuple[str, dict[str, str]]:
    """An answer in two parts: its JSON, and its blocks by name (ADR 0026).

    A block runs from its `@@@ name @@@` line to the next `@@@ end @@@` line, its text
    exactly as written. A block wrapped whole in a ``` fence has the fence taken off:
    the instruction says not to, and a habit is not the code.
    """
    lines = text.splitlines()
    first = next((i for i, x in enumerate(lines) if _BLOCK_START.match(x)), len(lines))
    head = "\n".join(lines[:first]).strip()
    if head.startswith("```"):
        head = "\n".join(x for x in head.splitlines() if not _FENCE.match(x)).strip()
    blocks: dict[str, str] = {}
    name: str | None = None
    body: list[str] = []
    for line in lines[first:]:
        if name is None:
            started = _BLOCK_START.match(line)
            if started:
                name, body = started.group(1), []
            elif line.strip():
                rulebook.refuse("call.split_blocks", "outside", line=repr(line.strip()[:80]))
            continue
        if line.strip() == _BLOCK_END:
            if body and _FENCE.match(body[0]) and len(body) > 1 and body[-1].strip() == "```":
                body = body[1:-1]
            if name in blocks:
                rulebook.refuse("call.split_blocks", "twice", name=repr(name))
            blocks[name] = "\n".join(body) + ("\n" if body else "")
            name = None
            continue
        body.append(line)
    if name is not None:
        rulebook.refuse("call.split_blocks", "unclosed", name=repr(name))
    return head, blocks


def join_blocks(
    data: Any,
    blocks: dict[str, str],
    fields: frozenset[str],
    loose: list[dict[str, Any]] | None = None,
) -> Any:
    """The answer with each block named in a code field put in its place (ADR 0026).

    A name no block answers, or a block no field names, is refused with that reason.
    A field holding exactly the name of a block written under a name other than
    b1, b2 and so on takes that block, and is added to `loose` (crew#734).
    """
    used: set[str] = set()

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                named = _block_named(item) if key in fields else None
                bare = str(item).strip().strip("@`'\" ").strip() if key in fields else ""
                if named is None and bare in blocks:
                    # sprint-metrics#626 wrote blocks f1 to f8, and crew-presentation#73
                    # b_url_for: each name went in as the code, and was refused.
                    named = bare
                    if loose is not None:
                        loose.append({"fallback": "block.name", "field": key, "name": bare})
                if named is not None and named in blocks:
                    used.add(named)
                    out[key] = blocks[named]
                elif named is not None:
                    rulebook.refuse(
                        "call.join_blocks", "unwritten", key=key, name=repr(named), bare=named
                    )
                elif key in fields and _NOT_A_BLOCK_NAME.match(str(item)):
                    written = ", ".join(blocks) or "none"
                    rulebook.refuse(
                        "call.join_blocks",
                        "not_a_name",
                        key=key,
                        value=repr(str(item).strip()),
                        written=written,
                    )
                else:
                    out[key] = walk(item)
            return out
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    # A block no field names is left out, not refused: answers that only asked to read
    # files wrote an example block and were refused for it (2026-10-11). Code meant for
    # a field and left in a stray block still fails that field's own check.
    return walk(data)


# How a code field names its block. Read loosely: the first real answer wrote "@@b1",
# and a field holding only a block's name, with marks around it, can't be code. Aider
# measured permissive parsing at a ninth of the errors of strict (ways-of-working).
_BLOCK_NAME = re.compile(r"^[\s@`'\"]*(?:block\s+)?(b\d+)[\s@`'\"]*$", re.IGNORECASE)


# A code field that points at a block by some other name, such as "@@criteria_test_1@@"
# (mqucifer/sprint-metrics#569, 2026-10-11). Taken as code it was refused for not
# defining its test, which didn't say what was wrong.
_NOT_A_BLOCK_NAME = re.compile(r"^\s*@{2,3}\s*[\w.-]+\s*@{0,3}\s*$")


def _block_named(value: Any) -> str | None:
    """The block a code field names, or None when it holds anything else."""
    if not isinstance(value, str):
        return None
    found = _BLOCK_NAME.match(value)
    return found.group(1).lower() if found else None


# The grammar a two-part answer is held to while it's written (ADR 0026): JSON, then
# blocks of plain lines. Without one, mqucifer/sprint-metrics#567's Developer went on
# reading after its reading rounds, writing tool calls as text, and three answers in
# four came back empty (2026-10-11). The JSON holds no code, and its whitespace is
# bounded, so nothing in it can run away; a block's lines are any text but a line
# starting `@@@`. At most 60 blocks: without a bound, two answers wrote the same empty
# block 4,994 and 6,464 times, a runaway of blocks instead of whitespace.
BLOCKS_GRAMMAR = r"""root ::= head "\n"+ block{0,60}
head ::= object
block ::= "@@@ " name " @@@\n" line* "@@@ end @@@\n"
name ::= "b" [0-9]+
line ::= ([^@\n] [^\n]* | "@" [^@\n] [^\n]* | "@@" [^@\n] [^\n]*)? "\n"
object ::= "{" ws (member (ws "," ws member)*)? ws "}"
member ::= string ws ":" ws value
value ::= object | array | string | number | "true" | "false" | "null"
array ::= "[" ws (value (ws "," ws value)*)? ws "]"
string ::= "\"" ([^"\\\x00-\x1f] | escape)* "\""
escape ::= "\\" (["\\/bfnrt] | "u" hex hex hex hex)
hex ::= [0-9a-fA-F]
number ::= "-"? ("0" | [1-9] [0-9]*) ("." [0-9]+)? ([eE] [-+]? [0-9]+)?
nonempty ::= "\"" ([^"\\\x00-\x1f] | escape)+ "\""
ws ::= [ \t\n]{0,40}"""


def blocks_grammar(schema: dict[str, Any]) -> str:
    """BLOCKS_GRAMMAR with the JSON opening on the form's first field, holding a value.

    The bare grammar let an empty object through: answers came back as `{}` after
    thousands of tokens of thinking, refused as "summary: Field required", in replays
    and in the live tick (mqucifer/sprint-metrics#585, 2026-10-11). Every form the
    blocks serve opens with its `summary`; a text first field can't be empty.
    """
    fields = list((schema.get("properties") or {}).items())
    if not fields:
        return BLOCKS_GRAMMAR
    name, field = fields[0]
    first = "nonempty" if field.get("type") == "string" else "value"
    head = f'head ::= "{{" ws "\\"{name}\\"" ws ":" ws {first} (ws "," ws member)* ws "}}"'
    return BLOCKS_GRAMMAR.replace("head ::= object", head)


@dataclass(frozen=True)
class Part:
    """One named part of what a step is shown. Its size is recorded with each call."""

    name: str
    text: str


# How many rounds of tool calls a step may make before it answers: enough to read the
# few files a step finds it needs, not a crawl of the repository. Four was one short for
# mqucifer/sprint-metrics#567 (2026-10-11): twelve reads, then it asked for a 11k-character
# file it could have read, and an answer that asks isn't applied. A round costs seconds.
TOOL_ROUNDS = 6
# Asked once the tool rounds end, when the answer itself is wanted in its form.
ANSWER_NOW = "You have what you need. Give your answer now, in full and in its form."
# Said before the tool rounds. Without it a model that needs nothing more writes its
# whole answer as text, and then again in its form: twice the longest part of a call.
READ_FIRST = (
    "Before you answer, use the tools to read anything you need. When you have what "
    'you need, reply only "Ready.": your answer is asked for next, in its form.'
)


@dataclass(frozen=True)
class Tool:
    """A tool a step's model may call before it answers: its schema, and what runs it.

    `run` takes the call's arguments and returns what the model is told. It never
    raises: what went wrong is the answer, so the model can ask differently.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], str]

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class Refused(ValueError):
    """Every attempt was refused. The last reason is the message.

    `empty` says the last answers were empty: not a wrong answer but none, which
    asking the same way again doesn't fix.
    """

    def __init__(
        self, reason: str, attempts: int, *, empty: bool = False, answers: tuple[str, ...] = ()
    ) -> None:
        super().__init__(f"refused {attempts} times; the last time: {reason}")
        self.reason = reason
        self.attempts = attempts
        self.empty = empty
        # Each refused answer as the model wrote it, with why. Only for the caller to
        # keep locally: answers never go in the log (ADR 0014).
        self.answers = answers


class ServiceUnavailableError(RuntimeError):
    """The proxy, or the model behind it, can't be reached.

    Named as the client libraries name it, so `llm.backend_down` recognises it and
    no card is blamed for a server that is down.
    """


def role_spec(key: str, step: str | None = None) -> dict[str, Any]:
    """A role's entry in `agents.yaml`, with a step's own goal and backstory over it."""
    spec = load_agents().get(key)
    if spec is None:
        raise KeyError(f"no agent named {key!r} in agents.yaml")
    return {**spec, **(spec.get(step) or {})} if step else dict(spec)


def system_text(spec: dict[str, Any]) -> str:
    """Who the role is: its backstory, its rules from the constitution, and its goal.

    The words CrewAI put around the same three, without its framing.
    """
    rules = excerpt(*spec.get("constitution", []), role=spec["role"])
    return (
        f"You are {spec['role']}. {spec['backstory'].strip()}\n\n{rules}\n"
        f"Your personal goal is: {spec['goal'].strip()}"
    )


def task_text(
    parts: list[Part],
    expected: str,
    form: dict[str, Any] | None = None,
    blocks: frozenset[str] = frozenset(),
) -> str:
    """What the step is shown, part by part, what its answer should be, and its form.

    The form is the answer's JSON schema, its field descriptions included. The server
    holds the answer to the schema's shape, but the model reads only the prompt: with
    the schema left out as CrewAI's framing, the Developer put a whole test function
    in `test`, a name field, in every try (crew#583, 2026-10-11). CrewAI sent it the
    same way, after the task.
    """
    shown = "\n\n".join(p.text.strip() for p in parts if p.text.strip())
    text = f"{shown}\n\nYour answer: {expected}" if expected else shown
    if form is not None:
        text += f"\n\n{form_text(form, blocks)}"
    return text


def form_text(schema: dict[str, Any], blocks: frozenset[str] = frozenset()) -> str:
    """An answer's JSON schema as the model reads it: each field, its type and what it is.

    The server holds the answer to the full schema; the prompt's copy says the same in
    about half (lean-prompts plan, L3): the Developer's was 13.5k characters of JSON
    Schema's own wrapping (`title`, `$ref`, `properties`). Every field is written,
    because the server requires each; a list may be empty and a text may be blank where
    its description says so.
    """
    defs = schema.get("$defs") or {}

    def kind(field: dict[str, Any]) -> str:
        if "$ref" in field:
            name = field["$ref"].rsplit("/", 1)[-1]
            target = defs.get(name, {})
            if "enum" in target:
                return "one of " + ", ".join(f"`{v}`" for v in target["enum"])
            return name
        if "anyOf" in field:
            options = [kind(f) for f in field["anyOf"] if f.get("type") != "null"]
            nullable = any(f.get("type") == "null" for f in field["anyOf"])
            return " or ".join(options) + (" or null" if nullable else "")
        if "enum" in field:
            return "one of " + ", ".join(f"`{v}`" for v in field["enum"])
        if field.get("type") == "array":
            return f"list of {kind(field.get('items') or {})}"
        return str(field.get("type", "any"))

    def said(*parts: str) -> str:
        """Sentences joined: a description that doesn't end one is ended first."""
        found = [" ".join(p.split()) for p in parts if p and p.strip()]
        return " ".join(
            p if i == len(found) - 1 or p.endswith((".", ":", "!", "?")) else p + "."
            for i, p in enumerate(found)
        )

    def fields(model: dict[str, Any], told: dict[str, list[str]]) -> list[str]:
        # A field's description, then each rule that checks it, from the rule's own
        # declaration (ADR 0031): a check is told in the form it checks.
        return [
            f"- `{name}` ({'code, as a block' if name in blocks else kind(field)})"
            + (
                f": {text}"
                if (text := said(str(field.get("description") or ""), *told.get(name, [])))
                else ""
            )
            for name, field in (model.get("properties") or {}).items()
        ]

    def heading(name: str, model: dict[str, Any]) -> str:
        told = rulebook.told(name).get("", [])
        # Unchanged where nothing is told: the prompt's prefix stays the same.
        text = (
            said(str(model.get("description") or ""), *told) if told else model.get("description")
        )
        return f"**{name}**" + (f": {text}" if text else "")

    lines = [
        BLOCKS_FORMAT
        if blocks
        else "Write your answer as JSON in this form, every field present, each as its "
        "description says.",
        "",
        heading(str(schema.get("title", "Answer")), {}),
        *fields(schema, rulebook.told(str(schema.get("title", "")))),
    ]
    for name, model in defs.items():
        if "properties" not in model:
            continue
        lines += ["", heading(name, model)]
        lines += fields(model, rulebook.told(name))
    return "\n".join(lines)


def _strict(schema: dict[str, Any]) -> dict[str, Any]:
    """The answer's schema as the server holds it: every field required, nothing extra.

    The same shape CrewAI sent, so an answer form reads the same to the model.
    """
    if schema.get("type") == "object" and "properties" in schema:
        schema["required"] = list(schema["properties"])
        schema["additionalProperties"] = False
    for value in schema.values():
        if isinstance(value, dict):
            _strict(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _strict(item)
    return schema


def response_format(answer: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {"name": answer.__name__, "schema": _strict(answer.model_json_schema())},
    }


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _usage(data: dict[str, Any]) -> dict[str, int]:
    """Token counts off a response, reasoning picked out: a shape nobody anticipated is none."""
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        return {}
    found = {
        k: usage[k]
        for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        if isinstance(usage.get(k), int)
    }
    details = usage.get("completion_tokens_details") or {}
    if isinstance(details, dict) and isinstance(details.get("reasoning_tokens"), int):
        found["reasoning_tokens"] = details["reasoning_tokens"]
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    if isinstance(cached, int):
        found["cached_tokens"] = cached
    return found


def post(body: dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """One request to the proxy. Everything goes through LiteLLM (ADR 0007)."""
    try:
        response = httpx.post(
            f"{base_url()}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {api_key()}"},
            timeout=timeout,
        )
    except httpx.TransportError as exc:
        if isinstance(exc, httpx.TimeoutException):
            raise
        raise ServiceUnavailableError(f"Connection error: {exc}") from exc
    if response.status_code >= 500:
        raise ServiceUnavailableError(f"{response.status_code}: {response.text[:300]}")
    response.raise_for_status()
    result: dict[str, Any] = response.json()
    return result


def stream(
    body: dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT, transport: Any = None
) -> dict[str, Any]:
    """One request to the proxy, read as it streams, and cut off if it runs away.

    Returns what `post` returns, assembled from the stream. An answer cut off for
    running on as whitespace has `finish_reason` "runaway", and closing the stream
    ends the generation instead of letting it run to its token limit.
    """
    parts: list[str] = []
    usage: dict[str, Any] = {}
    finish: str | None = None
    response_id = ""
    run = 0
    try:
        with (
            httpx.Client(transport=transport, timeout=timeout) as client,
            client.stream(
                "POST",
                f"{base_url()}/chat/completions",
                json={**body, "stream": True, "stream_options": {"include_usage": True}},
                headers={"Authorization": f"Bearer {api_key()}"},
            ) as response,
        ):
            if response.status_code >= 500:
                response.read()
                raise ServiceUnavailableError(f"{response.status_code}: {response.text[:300]}")
            if response.status_code >= 400:
                response.read()
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                response_id = chunk.get("id") or response_id
                usage = chunk.get("usage") or usage
                for choice in chunk.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content") or ""
                    if piece:
                        parts.append(piece)
                        kept = piece.rstrip()
                        run = run + len(piece) if not kept else len(piece) - len(kept)
                    finish = choice.get("finish_reason") or finish
                if run >= RUNAWAY_CHARS:
                    finish = "runaway"
                    break
    except httpx.TransportError as exc:
        if isinstance(exc, httpx.TimeoutException):
            raise
        raise ServiceUnavailableError(f"Connection error: {exc}") from exc
    return {
        "id": response_id,
        "choices": [{"message": {"content": "".join(parts)}, "finish_reason": finish}],
        "usage": usage,
    }


def _runaway(text: str) -> NoReturn:
    """Refuse an answer that ran on as whitespace, saying where it went wrong."""
    rulebook.refuse("call.the_answer_ran_on_as", tail=text.rstrip()[-RUNAWAY_TAIL:])


def ask[Answer: BaseModel](
    role: str,
    parts: list[Part],
    answer: type[Answer],
    *,
    expected: str = "",
    step: str | None = None,
    check: Callable[[Answer], None] | None = None,
    attempts: int = ATTEMPTS,
    tools: list[Tool] | None = None,
    rounds: int = TOOL_ROUNDS,
    send: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    blocks: frozenset[str] = frozenset(),
) -> Answer:
    """The role's answer, in its form, asked for again with the reason when refused.

    `check` is the step's own refusal rules, beyond the schema: it raises
    `ValueError` with the reason, and the model is told that reason.

    With `tools`, the model first calls them in rounds, without the answer's schema,
    and is given each result; when it stops, or after `rounds`, it is asked for the
    answer in its form (ADR 0025). A schema constrains every token, so a call held
    to one can't also call a tool: CrewAI dropped the schema instead.

    With `blocks`, the fields named there hold code, and come back as blocks after the
    JSON instead of inside it (ADR 0026): no code is escaped, so none can close a string
    early. The answer is held to BLOCKS_GRAMMAR while it's written, JSON then blocks,
    and to its form when it's joined.
    """
    spec = role_spec(role, step)
    params = spec.get("llm_params") or {}
    # The role's own timeout: a Developer's answer can take most of an hour. The answer
    # is streamed, so one that runs away is stopped; tool rounds return tool calls, not
    # text, and are asked for whole.
    timeout = float(params.get("timeout", DEFAULT_TIMEOUT))
    sender = send or partial(stream, timeout=timeout)
    reader = send or partial(post, timeout=timeout)
    system = system_text(spec)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": task_text(parts, expected, answer.model_json_schema(), blocks),
        },
    ]
    form = response_format(answer)
    grammar = blocks_grammar(answer.model_json_schema()) if blocks else ""
    versions = {
        "prompt_hash": _fingerprint(system),
        "output_schema": f"{answer.__name__}:{_fingerprint(json.dumps(form, sort_keys=True))}",
    }
    sizes = {p.name: len(p.text) for p in parts}
    if tools:
        messages[-1]["content"] += f"\n\n{READ_FIRST}"
        messages = _use_tools(spec, messages, tools, rounds=rounds, send=reader, sizes=sizes)
    reason = ""
    empties = 0
    refused: list[str] = []
    for attempt in range(1, attempts + 1):
        loose: list[dict[str, Any]] = []
        body = {
            "model": spec.get("llm", "crew-local"),
            "messages": messages,
            "max_tokens": params.get("max_tokens", DEFAULT_MAX_TOKENS),
            **({"extra_body": {"ebnf": grammar}} if blocks else {"response_format": form}),
        }
        call = str(uuid.uuid4())
        detail = {"model": body["model"], "call_id": call, "try": attempt, **versions}
        record(EventKind.LLM_CALL_STARTED, spec["role"], role=spec["role"], parts=sizes, **detail)
        started = time.monotonic()
        began = datetime.now(UTC)
        try:
            data = sender(body)
        except Exception as exc:
            record(
                EventKind.LLM_CALL_FAILED,
                spec["role"],
                role=spec["role"],
                error=f"{type(exc).__name__}: {exc}"[:400],
                duration_s=round(time.monotonic() - started, 3),
                **detail,
            )
            raise
        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        detail.update(
            {
                "finish_reason": str(choice.get("finish_reason")),
                "response_id": str(data.get("id", "")),
                "duration_s": round(time.monotonic() - started, 3),
                **_usage(data),
            }
        )
        record(EventKind.LLM_CALL_FINISHED, spec["role"], role=spec["role"], **detail)
        _trace(began, detail, spec["role"])
        try:
            if choice.get("finish_reason") == "runaway":
                _runaway(text)
            if not text.strip():
                rulebook.refuse("call.the_answer_was_empty_you")
            if blocks:
                head, written = split_blocks(text)
                try:
                    data = json.loads(head)
                except json.JSONDecodeError as exc:
                    rulebook.refuse("call.the_json_before_the_blocks", error=exc)
                found = answer.model_validate(join_blocks(data, written, blocks, loose))
            else:
                found = answer.model_validate_json(text)
            # Before the step's own rules: a value cut at a double quote reads as
            # finished to every rule after it (crew#618).
            check_not_cut_at_a_quote(found)
            if check is not None:
                check(found)
            for match in loose:
                record(
                    EventKind.EDIT_LOOSE_MATCH,
                    f"{spec['role']}: block {match['name']!r} named in `{match['field']}`",
                    role=spec["role"],
                    **match,
                    **detail,
                )
            return found
        except (ValidationError, ValueError) as exc:
            reason = _reason(exc)
            record(
                EventKind.LLM_CALL_REFUSED,
                f"{spec['role']}: {reason}"[:120],
                role=spec["role"],
                reason=reason[:600],
                empty=not text.strip(),
                **({"rules": rules} if (rules := _rules(exc)) else {}),
                **detail,
            )
            refused.append(f"Refused: {reason}\n\n{text}")
            empties = 0 if text.strip() else empties + 1
            if empties >= EMPTY_LIMIT:
                raise Refused(reason, attempt, empty=True, answers=tuple(refused)) from exc
            if text.strip():
                messages.append({"role": "assistant", "content": text[:ECHO_CHARS]})
            messages.append(
                {
                    "role": "user",
                    "content": f"That answer was refused: {reason}\n\n"
                    "Answer again, in full and in the same form, with that fixed.",
                }
            )
    raise Refused(reason, attempts, answers=tuple(refused))


def _use_tools(
    spec: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[Tool],
    *,
    rounds: int,
    send: Callable[[dict[str, Any]], dict[str, Any]],
    sizes: dict[str, int],
) -> list[dict[str, Any]]:
    """The conversation after the tool rounds, ending with the request for the answer."""
    by_name = {t.name: t for t in tools}
    params = spec.get("llm_params") or {}
    for turn in range(1, rounds + 1):
        body = {
            "model": spec.get("llm", "crew-local"),
            "messages": messages,
            "max_tokens": params.get("max_tokens", DEFAULT_MAX_TOKENS),
            "tools": [t.spec() for t in tools],
            "tool_choice": "auto",
        }
        detail = {"model": body["model"], "call_id": str(uuid.uuid4()), "round": turn}
        record(EventKind.LLM_CALL_STARTED, spec["role"], role=spec["role"], parts=sizes, **detail)
        started = time.monotonic()
        data = send(body)
        message = ((data.get("choices") or [{}])[0]).get("message") or {}
        calls_made = message.get("tool_calls") or []
        detail.update(
            {
                "response_id": str(data.get("id", "")),
                "duration_s": round(time.monotonic() - started, 3),
                "tool_calls": len(calls_made),
                **_usage(data),
            }
        )
        record(EventKind.LLM_CALL_FINISHED, spec["role"], role=spec["role"], **detail)
        if not calls_made:
            if (message.get("content") or "").strip():
                messages = [*messages, {"role": "assistant", "content": message["content"]}]
            break
        messages = [
            *messages,
            {
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": calls_made,
            },
        ]
        for made in calls_made:
            function = made.get("function") or {}
            name = str(function.get("name") or "")
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = None
            tool = by_name.get(name)
            if tool is None:
                said = f"There is no tool named {name!r}: use one of {', '.join(by_name)}."
            elif not isinstance(arguments, dict):
                said = "Those arguments aren't a JSON object: call the tool again."
            else:
                said = tool.run(arguments)
            record(
                EventKind.TOOL_FINISHED,
                name,
                role=spec["role"],
                tool=name,
                round=turn,
                chars=len(said),
            )
            messages.append({"role": "tool", "tool_call_id": made.get("id", ""), "content": said})
    return [*messages, {"role": "user", "content": ANSWER_NOW}]


def _rules(exc: Exception) -> list[str]:
    """The declared rules a refusal names (ADR 0031): pydantic keeps each validator's
    own error in the context of the error it reports."""
    if isinstance(exc, rulebook.Refusal):
        return [exc.rule]
    if not isinstance(exc, ValidationError):
        return []
    found = [
        error.rule
        for e in exc.errors()
        if isinstance(error := (e.get("ctx") or {}).get("error"), rulebook.Refusal)
    ]
    return list(dict.fromkeys(found))


def _reason(exc: Exception) -> str:
    """A refusal as the model is told it: each error with where it is, not a traceback."""
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'the answer'}: {e['msg']}"
            for e in exc.errors()[:10]
        )
    return str(exc)


def _trace(began: datetime, detail: dict[str, Any], role: str) -> None:
    """The call as a span under whatever the caller was doing (#283). Attributes only."""
    from crew_org import tracing  # noqa: PLC0415
    from crew_org.events import working  # noqa: PLC0415

    try:
        start_ns = int(began.timestamp() * 1e9)
        tracing.model_call(
            start_ns=start_ns,
            end_ns=start_ns + int(float(detail.get("duration_s") or 0) * 1e9),
            failed=False,
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": detail.get("model"),
                "gen_ai.response.id": detail.get("response_id"),
                "gen_ai.response.finish_reasons": detail.get("finish_reason"),
                "gen_ai.usage.input_tokens": detail.get("prompt_tokens"),
                "gen_ai.usage.output_tokens": detail.get("completion_tokens"),
                "crew.reasoning_tokens": detail.get("reasoning_tokens"),
                "crew.role": role,
                **{f"crew.{k}": v for k, v in working().items()},
            },
        )
    except Exception:  # noqa: BLE001, S110 - a span is never a reason to fail a call
        pass
