"""LLM construction for agents.

Agents name a LiteLLM alias (`crew-local`, `crew-draft`, `crew-mechanical`),
never a model. The proxy owns the mapping to a backend, so re-pointing the crew
at different hardware is a config change rather than a code change.

Thinking is a cost lever, not a fixed tax: Qwen3.8 spends reasoning tokens
before answering, which is worth paying on judgment-heavy roles and is waste on
mechanical ones. `crew-mechanical` disables it — verified through the proxy at
2 completion tokens against 25.
"""

from __future__ import annotations

import os
from typing import Any

from crewai import LLM

DEFAULT_BASE_URL = "http://localhost:4000/v1"

# Used when the proxy is running without a master key, where the backend
# ignores the value entirely. A proxy started WITH LITELLM_MASTER_KEY rejects
# it — set CREW_LLM_API_KEY to that key in .env.
PLACEHOLDER_KEY = "sk-not-used"

# Reasoning models need headroom ABOVE their thinking, and real refinement work
# thinks hard: a single "propose epics for this goal" task was measured spending
# 4,097 reasoning tokens and emitting 0 text tokens against a 4,096 ceiling —
# the whole budget consumed before the answer began. CrewAI retries, so it
# surfaces only as a slow task and a logged parse error, which is a genuinely
# confusing way to discover a token budget problem.
#
# The context window is 262,144, so headroom is cheap. Spend it.
DEFAULT_MAX_TOKENS = 16384

# Long enough to carry DEFAULT_MAX_TOKENS at the measured throughput (~36 tok/s
# gives ~460s for a full implementation), with headroom for reasoning tokens.
# A timeout shorter than the generation it carries does not fail fast — it
# fails slowly and repeatedly, because the client retries into the same wall.
DEFAULT_TIMEOUT = 1800


def _setting(name: str, default: str) -> str:
    """A proxy setting, from the environment or `.env`.

    `load_env` returns a dict rather than exporting, so reading os.environ
    alone silently misses everything configured in `.env` and falls back to a
    default. That was survivable while the default happened to be right; it is
    not survivable for a credential, where being wrong is a 401.
    """
    from crew_org.config import load_env  # noqa: PLC0415

    return os.environ.get(name) or load_env().get(name) or default


def base_url() -> str:
    return _setting("CREW_LLM_BASE_URL", DEFAULT_BASE_URL)


def api_key() -> str:
    return _setting("CREW_LLM_API_KEY", PLACEHOLDER_KEY)


def build_llm(alias: str = "crew-local", **overrides: Any) -> LLM:
    """Build a CrewAI LLM bound to a proxy alias.

    The `openai/` prefix tells LiteLLM the backend speaks the OpenAI protocol;
    it is not a statement about the provider.
    """
    params: dict[str, Any] = {
        "model": f"openai/{alias}",
        "base_url": base_url(),
        "api_key": api_key(),
        "max_tokens": DEFAULT_MAX_TOKENS,
        "timeout": DEFAULT_TIMEOUT,
    }
    params.update(overrides)
    return LLM(**params)


def health() -> tuple[bool, str]:
    """Is the proxy up?

    The proxy is deliberately project-scoped — it runs in Docker for the crew
    and is not general infrastructure — so it will not always be running. A tick
    that fails on a dead proxy should say so plainly rather than surfacing a
    connection error from somewhere deep inside an agent.

    "Down" and "up but rejecting the credential" need different answers, and
    conflating them is expensive: a proxy started with LITELLM_MASTER_KEY
    answers 401 to an unauthenticated probe, and reporting that as "not
    answering" sends you to restart a container that is already healthy.
    """
    import httpx  # noqa: PLC0415

    url = base_url()
    try:
        r = httpx.get(
            f"{url}/models",
            headers={"Authorization": f"Bearer {api_key()}"},
            timeout=5.0,
        )
    except Exception:  # noqa: BLE001
        return False, (
            f"LiteLLM proxy is not answering at {url}.\n"
            "  Start it with:  cd deploy/litellm && "
            "docker compose --env-file ../../.env up -d"
        )
    if r.status_code in (401, 403):
        return False, (
            f"LiteLLM proxy is up at {url} but rejected the credential.\n"
            "  It was started with LITELLM_MASTER_KEY, so the crew needs that key:\n"
            "  set CREW_LLM_API_KEY in .env to the proxy's master key."
        )
    try:
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return False, f"LiteLLM proxy at {url} answered {r.status_code}: {exc}"
    aliases = [m["id"] for m in r.json().get("data", [])]
    if "crew-local" not in aliases:
        return False, f"Proxy is up but has no 'crew-local' alias. Serving: {aliases}"
    # The model list comes from the proxy's own config, whether or not the model
    # server behind it is up. On 2026-09-25 SGLang on the Spark was stopped, this
    # passed, and `crew design` failed inside its first model call with a
    # traceback (#153). One minimal completion, thinking off, proves the path.
    try:
        answer = httpx.post(
            f"{url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key()}"},
            json={
                "model": "crew-local",
                "messages": [{"role": "user", "content": "Reply with the word ready."}],
                "max_tokens": 8,
                "chat_template_kwargs": {"enable_thinking": False},
            },
            timeout=PROBE_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001
        return False, down(url, f"{type(exc).__name__}: {exc}")
    if answer.status_code >= 400:
        return False, down(url, f"it answered {answer.status_code}: {answer.text[:160]}")
    return True, f"proxy up — {', '.join(aliases)}; crew-local answering"


# Long enough for a cold model's first token, short enough to fail a pre-flight
# rather than hang it.
PROBE_TIMEOUT = 60.0


def down(url: str, detail: str) -> str:
    """The one line that says the proxy is up and the model behind it isn't."""
    return (
        f"The LiteLLM proxy at {url} is up, but the model behind 'crew-local' isn't "
        f"answering ({detail.strip()[:200]}). Check the model server, e.g. SGLang on the Spark."
    )


# How a request fails when the proxy can't reach the model server: a connection
# error, or the proxy's 5xx for one. Not a timeout: a slow generation isn't a
# dead backend, and treating it as one would stop a tick that was working.
_DOWN = ("APIConnectionError", "InternalServerError", "ServiceUnavailableError")


def backend_down(exc: BaseException) -> bool:
    """Is this a model backend that can't be reached, anywhere in the chain?

    A card's model call fails for the card's reasons (bad output, a failed
    check) or for the infrastructure's. Only the first is the card's: a dead
    backend recorded as a card failure is retried, then blocked as a prompt
    defect, and the card is blamed for a server that was switched off (#153).
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ in _DOWN or "Connection error" in str(current):
            return True
        current = current.__cause__ or current.__context__
    return False


class ModelUnavailable(RuntimeError):
    """The model backend can't be reached. The command stops; no card is blamed."""


def reraise_if_down(exc: BaseException) -> None:
    """Called first in a catch around a model call: infrastructure isn't a card's failure."""
    if backend_down(exc):
        raise ModelUnavailable(str(exc)) from exc
