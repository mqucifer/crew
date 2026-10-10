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
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from crew_org.constitution import excerpt
from crew_org.events import EventKind, record
from crew_org.llm import DEFAULT_MAX_TOKENS, DEFAULT_TIMEOUT, api_key, base_url
from crew_org.permissions import load_agents

# Three attempts, as CrewAI gave: the first, and two that are told why.
ATTEMPTS = 3
# Of a refused answer, how much is sent back with its reason: enough to see what
# was wrong with it, not a second copy of a long answer.
ECHO_CHARS = 4000


@dataclass(frozen=True)
class Part:
    """One named part of what a step is shown. Its size is recorded with each call."""

    name: str
    text: str


class Refused(ValueError):
    """Every attempt was refused. The last reason is the message."""

    def __init__(self, reason: str, attempts: int) -> None:
        super().__init__(f"refused {attempts} times; the last time: {reason}")
        self.reason = reason
        self.attempts = attempts


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
    rules = excerpt(*spec.get("constitution", []))
    return (
        f"You are {spec['role']}. {spec['backstory'].strip()}\n\n{rules}\n"
        f"Your personal goal is: {spec['goal'].strip()}"
    )


def task_text(parts: list[Part], expected: str) -> str:
    """What the step is shown, part by part, and what its answer should be."""
    shown = "\n\n".join(p.text.strip() for p in parts if p.text.strip())
    return f"{shown}\n\nYour answer: {expected}" if expected else shown


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
    usage = data.get("usage") or {}
    found = {
        k: usage[k]
        for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        if isinstance(usage.get(k), int)
    }
    details = usage.get("completion_tokens_details") or {}
    if isinstance(details.get("reasoning_tokens"), int):
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


def ask[Answer: BaseModel](
    role: str,
    parts: list[Part],
    answer: type[Answer],
    *,
    expected: str = "",
    step: str | None = None,
    check: Callable[[Answer], None] | None = None,
    attempts: int = ATTEMPTS,
    send: Callable[[dict[str, Any]], dict[str, Any]] = post,
) -> Answer:
    """The role's answer, in its form, asked for again with the reason when refused.

    `check` is the step's own refusal rules, beyond the schema: it raises
    `ValueError` with the reason, and the model is told that reason.
    """
    spec = role_spec(role, step)
    params = spec.get("llm_params") or {}
    system = system_text(spec)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": task_text(parts, expected)},
    ]
    form = response_format(answer)
    versions = {
        "prompt_hash": _fingerprint(system),
        "output_schema": f"{answer.__name__}:{_fingerprint(json.dumps(form, sort_keys=True))}",
    }
    sizes = {p.name: len(p.text) for p in parts}
    reason = ""
    for attempt in range(1, attempts + 1):
        body = {
            "model": spec.get("llm", "crew-local"),
            "messages": messages,
            "max_tokens": params.get("max_tokens", DEFAULT_MAX_TOKENS),
            "response_format": form,
        }
        call = str(uuid.uuid4())
        detail = {"model": body["model"], "call_id": call, "try": attempt, **versions}
        record(EventKind.LLM_CALL_STARTED, spec["role"], role=spec["role"], parts=sizes, **detail)
        started = time.monotonic()
        began = datetime.now(UTC)
        try:
            data = send(body)
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
            if not text.strip():
                raise ValueError(
                    "the answer was empty: you stopped before writing it. Think less and "
                    "write the whole answer"
                )
            found = answer.model_validate_json(text)
            if check is not None:
                check(found)
            return found
        except (ValidationError, ValueError) as exc:
            reason = _reason(exc)
            record(
                EventKind.LLM_CALL_REFUSED,
                f"{spec['role']}: {reason}"[:120],
                role=spec["role"],
                reason=reason[:600],
                empty=not text.strip(),
                **detail,
            )
            if text.strip():
                messages.append({"role": "assistant", "content": text[:ECHO_CHARS]})
            messages.append(
                {
                    "role": "user",
                    "content": f"That answer was refused: {reason}\n\n"
                    "Answer again, in full and in the same form, with that fixed.",
                }
            )
    raise Refused(reason, attempts)


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
