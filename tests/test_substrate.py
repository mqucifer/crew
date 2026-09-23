"""The Phase 0 probes are what decides whether the architecture is viable, so
their verdicts are tested against simulated servers — especially the failure
verdicts, which are the ones that matter."""

from __future__ import annotations

import json

import httpx
import pytest

from crew_org import substrate as sub
from crew_org.substrate import Status

BASE = "http://spark:30000/v1"
MODEL = "Qwen3.8-27B"


REQUEST = httpx.Request("POST", f"{BASE}/chat/completions")


def chat_response(message: dict) -> httpx.Response:
    # A Response needs its request attached or raise_for_status() errors.
    return httpx.Response(200, json={"choices": [{"message": message}]}, request=REQUEST)


@pytest.fixture
def server(monkeypatch):
    """Install a fake OpenAI-compatible server. Handlers keyed by endpoint."""
    state: dict[str, object] = {}

    def fake_get(url, **_kw):
        if state.get("unreachable"):
            raise httpx.ConnectError("refused")
        return httpx.Response(
            200,
            json={"data": [{"id": MODEL}]},
            request=httpx.Request("GET", f"{BASE}/models"),
        )

    monkeypatch.setattr(sub.httpx, "get", fake_get)
    return state


def install_post(monkeypatch, handler):
    def fake_post(url, *, json=None, **_kw):
        return handler(json)

    monkeypatch.setattr(sub.httpx, "post", fake_post)


# --- reachability --------------------------------------------------------


def test_unreachable_endpoint_fails_and_skips_the_rest(monkeypatch):
    monkeypatch.setattr(
        sub.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("refused"))
    )
    results = sub.run_all(BASE)
    assert results[0].status is Status.FAIL
    assert all(r.status is Status.SKIP for r in results[1:])
    assert len(results) == 6


def test_served_model_name_is_discovered(server, monkeypatch):
    install_post(monkeypatch, lambda _b: chat_response({"content": "ready"}))
    result, served = sub.probe_models(BASE)
    assert result.status is Status.PASS
    assert served == MODEL


# --- tool calling (0c) ---------------------------------------------------


def test_prose_instead_of_tool_calls_is_a_failure_with_the_parser_hint(server, monkeypatch):
    install_post(
        monkeypatch, lambda _b: chat_response({"content": "Sure, I'll move card 42 for you."})
    )
    r = sub.probe_tool_calling(BASE, MODEL)
    assert r.status is Status.FAIL
    assert "tool-call-parser" in (r.hint or "")


def test_unparseable_tool_arguments_are_a_failure(server, monkeypatch):
    install_post(
        monkeypatch,
        lambda _b: chat_response(
            {"tool_calls": [{"function": {"name": "set_card_status", "arguments": "{not json"}}]}
        ),
    )
    assert sub.probe_tool_calling(BASE, MODEL).status is Status.FAIL


def test_incomplete_tool_arguments_warn_rather_than_fail(server, monkeypatch):
    install_post(
        monkeypatch,
        lambda _b: chat_response(
            {
                "tool_calls": [
                    {"function": {"name": "set_card_status", "arguments": json.dumps({"card": 42})}}
                ]
            }
        ),
    )
    assert sub.probe_tool_calling(BASE, MODEL).status is Status.WARN


def test_well_formed_tool_call_passes(server, monkeypatch):
    install_post(
        monkeypatch,
        lambda _b: chat_response(
            {
                "tool_calls": [
                    {
                        "function": {
                            "name": "set_card_status",
                            "arguments": json.dumps({"card": 42, "status": "QAing"}),
                        }
                    }
                ]
            }
        ),
    )
    assert sub.probe_tool_calling(BASE, MODEL).status is Status.PASS


# --- constrained JSON (0d) ----------------------------------------------

VALID_STORY = {"title": "Report cycle time", "points": 3, "acceptance_criteria": ["given…"]}


def test_consistently_schema_valid_output_passes(server, monkeypatch):
    install_post(monkeypatch, lambda _b: chat_response({"content": json.dumps(VALID_STORY)}))
    r = sub.probe_structured_output(BASE, MODEL, trials=5)
    assert r.status is Status.PASS
    assert "5/5" in r.detail


def test_never_valid_output_fails_with_the_grammar_backend_hint(server, monkeypatch):
    install_post(monkeypatch, lambda _b: chat_response({"content": "Here is a story: ..."}))
    r = sub.probe_structured_output(BASE, MODEL, trials=5)
    assert r.status is Status.FAIL
    assert "grammar" in (r.hint or "").lower()


def test_intermittently_valid_output_warns_because_one_pass_proves_nothing(server, monkeypatch):
    calls = {"n": 0}

    def handler(_body):
        calls["n"] += 1
        content = json.dumps(VALID_STORY) if calls["n"] % 2 else "not json"
        return chat_response({"content": content})

    install_post(monkeypatch, handler)
    r = sub.probe_structured_output(BASE, MODEL, trials=5)
    assert r.status is Status.WARN


def test_missing_required_keys_is_not_counted_as_valid(server, monkeypatch):
    install_post(monkeypatch, lambda _b: chat_response({"content": json.dumps({"title": "x"})}))
    assert sub.probe_structured_output(BASE, MODEL, trials=3).status is Status.FAIL


# --- reasoning models ----------------------------------------------------


def test_empty_content_is_a_failure_not_a_pass(server, monkeypatch):
    """A reasoning model that spends its whole budget thinking has not answered."""
    monkeypatch.setattr(
        sub.httpx,
        "post",
        lambda *a, **k: httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                "usage": {"reasoning_tokens": 16},
            },
            request=REQUEST,
        ),
    )
    r = sub.probe_chat(BASE, MODEL)
    assert r.status is Status.FAIL
    assert "max_tokens" in (r.hint or "")


def test_content_after_reasoning_passes_and_reports_the_cost(server, monkeypatch):
    monkeypatch.setattr(
        sub.httpx,
        "post",
        lambda *a, **k: httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "\n\nready"}, "finish_reason": "stop"}],
                "usage": {"reasoning_tokens": 22},
            },
            request=REQUEST,
        ),
    )
    r = sub.probe_chat(BASE, MODEL)
    assert r.status is Status.PASS
    assert "22 reasoning tokens" in r.detail


# --- proxy hops ----------------------------------------------------------


def test_reasoning_tokens_found_at_the_top_of_usage():
    """SGLang reports it here."""
    assert sub._reasoning_tokens({"usage": {"reasoning_tokens": 22}}) == 22


def test_reasoning_tokens_found_nested_under_completion_details():
    """LiteLLM nests it here, so a naive read reports zero through the proxy."""
    payload = {"usage": {"completion_tokens_details": {"reasoning_tokens": 22}}}
    assert sub._reasoning_tokens(payload) == 22


def test_reasoning_tokens_absent_is_zero_not_an_error():
    assert sub._reasoning_tokens({}) == 0
    assert sub._reasoning_tokens({"usage": {}}) == 0


def test_unreported_context_length_names_the_proxy_as_the_likely_cause(server, monkeypatch):
    monkeypatch.setattr(
        sub.httpx,
        "get",
        lambda *a, **k: httpx.Response(
            200,
            json={"data": [{"id": MODEL}]},  # no max_model_len, as LiteLLM returns
            request=httpx.Request("GET", f"{BASE}/models"),
        ),
    )
    result = sub.probe_context(BASE, MODEL)
    assert result.status is Status.WARN
    assert "proxy" in (result.hint or "")


def test_a_short_context_window_warns_about_truncation(server, monkeypatch):
    monkeypatch.setattr(
        sub.httpx,
        "get",
        lambda *a, **k: httpx.Response(
            200,
            json={"data": [{"id": MODEL, "max_model_len": 4096}]},
            request=httpx.Request("GET", f"{BASE}/models"),
        ),
    )
    result = sub.probe_context(BASE, MODEL)
    assert result.status is Status.WARN
    assert "truncation" in (result.hint or "")


def test_deep_is_opt_in_so_the_plain_gate_costs_nothing(server, monkeypatch):
    """probe_crew spends real tokens; it must not run unless asked."""
    install_post(monkeypatch, lambda _b: chat_response({"content": "ready"}))
    monkeypatch.setattr(
        sub, "probe_crew", lambda *a, **k: pytest.fail("deep probe ran without --deep")
    )
    checks = [r.check for r in sub.run_all(BASE)]
    assert "crewai round-trip" not in checks


# --- through the proxy, as every tick is ------------------------------------------


def test_the_window_is_read_from_the_proxys_model_info(monkeypatch):
    """LiteLLM's /models carries no window; its /model/info does, when the alias
    declares max_input_tokens."""

    def fake_get(url, **_kw):
        body = (
            {"data": [{"model_name": MODEL, "model_info": {"max_input_tokens": 262144}}]}
            if url.endswith("/model/info")
            else {"data": [{"id": MODEL}]}
        )
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(sub.httpx, "get", fake_get)
    result = sub.probe_context(BASE, MODEL)
    assert result.status is Status.PASS and "262,144" in result.detail


def test_every_probe_sends_the_proxys_key(monkeypatch):
    """The doctor sent no key, so the proxy answered 401 and it could only be
    pointed around the proxy — at a path no agent takes."""
    monkeypatch.setenv("CREW_LLM_API_KEY", "sk-proxy")
    sent: list[str | None] = []

    def fake_get(url, *, headers=None, **_kw):
        sent.append((headers or {}).get("Authorization"))
        return httpx.Response(
            200, json={"data": [{"id": MODEL}]}, request=httpx.Request("GET", url)
        )

    def fake_post(url, *, json=None, headers=None, **_kw):
        sent.append((headers or {}).get("Authorization"))
        return chat_response({"content": "ready"})

    monkeypatch.setattr(sub.httpx, "get", fake_get)
    monkeypatch.setattr(sub.httpx, "post", fake_post)
    sub.run_all(BASE, MODEL)
    assert sent and set(sent) == {"Bearer sk-proxy"}


def test_the_doctor_probes_the_proxy_by_default(monkeypatch):
    from typer.testing import CliRunner

    from crew_org import cli

    monkeypatch.setenv("CREW_LLM_BASE_URL", "http://localhost:4000/v1")
    seen = {}

    def fake_run_all(base_url, model, *, deep=False):
        seen.update(base_url=base_url, model=model)
        return []

    monkeypatch.setattr(sub, "run_all", fake_run_all)
    result = CliRunner().invoke(cli.app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert seen == {"base_url": "http://localhost:4000/v1", "model": "crew-local"}
