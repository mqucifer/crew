"""The proxy is the crew's single point of dependency, so `health` is the first
thing that speaks when anything is wrong with it. Its verdicts are tested
because a misdiagnosis here is expensive: it sends you to fix the wrong thing.
"""

from __future__ import annotations

import httpx
import pytest

from crew_org import llm

URL = "http://localhost:4000/v1"
REQUEST = httpx.Request("GET", f"{URL}/models")


@pytest.fixture
def proxy(monkeypatch):
    """Install a fake proxy. Set `state` to choose how it answers."""
    state: dict[str, object] = {"key": None}

    def fake_get(url, **kwargs):
        if state.get("unreachable"):
            raise httpx.ConnectError("refused")
        required = state.get("key")
        sent = kwargs.get("headers", {}).get("Authorization", "")
        if required and sent != f"Bearer {required}":
            return httpx.Response(401, json={"error": "no api key passed in"}, request=REQUEST)
        aliases = state.get("aliases", ["crew-local", "crew-code"])
        return httpx.Response(200, json={"data": [{"id": a} for a in aliases]}, request=REQUEST)

    def fake_post(url, **kwargs):
        # The pre-flight's one minimal completion (#153).
        if state.get("backend_down"):
            return httpx.Response(
                500,
                json={"error": "litellm.InternalServerError: Connection error."},
                request=httpx.Request("POST", url),
            )
        body = {"choices": [{"message": {"content": "ready"}}]}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("CREW_LLM_BASE_URL", URL)
    monkeypatch.setenv("CREW_LLM_API_KEY", "sk-not-used")
    return state


def test_an_unreachable_proxy_says_how_to_start_it(proxy):
    proxy["unreachable"] = True
    ok, message = llm.health()
    assert not ok
    assert "not answering" in message
    assert "docker compose" in message
    # Without --env-file the compose guards refuse to start, so a start command
    # that omits it is not a start command.
    assert "--env-file" in message


def test_a_proxy_that_rejects_the_key_is_not_reported_as_down(proxy, monkeypatch):
    """The expensive misdiagnosis: a proxy started with LITELLM_MASTER_KEY
    answers 401, and calling that "not answering" sends you to restart a
    container that is already healthy."""
    proxy["key"] = "sk-the-real-one"
    ok, message = llm.health()
    assert not ok
    assert "not answering" not in message
    assert "rejected the credential" in message
    assert "CREW_LLM_API_KEY" in message


def test_the_configured_key_is_sent(proxy, monkeypatch):
    proxy["key"] = "sk-the-real-one"
    monkeypatch.setenv("CREW_LLM_API_KEY", "sk-the-real-one")
    ok, message = llm.health()
    assert ok, message


def test_a_proxy_missing_the_workhorse_alias_is_not_healthy(proxy):
    proxy["aliases"] = ["something-else"]
    ok, message = llm.health()
    assert not ok
    assert "crew-local" in message


def test_settings_fall_back_to_the_env_file(monkeypatch, tmp_path):
    """`load_env` does not export, so os.environ alone misses `.env` — which is
    exactly where the credential lives."""
    monkeypatch.delenv("CREW_LLM_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("CREW_LLM_API_KEY=sk-from-env\n")
    assert llm.api_key() == "sk-from-env"


def test_an_absent_setting_falls_back_to_the_placeholder(monkeypatch, tmp_path):
    monkeypatch.delenv("CREW_LLM_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    assert llm.api_key() == llm.PLACEHOLDER_KEY


def _sent(role: str) -> dict:
    """The body the crew's call layer sends for `role` (ADR 0025)."""
    from pydantic import BaseModel  # noqa: PLC0415

    from crew_org import calls  # noqa: PLC0415

    class Said(BaseModel):
        text: str

    sent: list[dict] = []
    answer = {"choices": [{"message": {"content": '{"text": "ok"}'}, "finish_reason": "stop"}]}
    calls.ask(role, [calls.Part("task", "t")], Said, send=lambda body: sent.append(body) or answer)
    return sent[0]


def test_a_role_can_ask_for_more_headroom_than_the_default():
    """The Business Analyst's input grows without bound — a large epic spent
    the shared 16,384-token budget on reasoning alone and returned nothing."""
    from crew_org.permissions import load_agents  # noqa: PLC0415

    roomy = [k for k, v in load_agents().items() if (v.get("llm_params") or {}).get("max_tokens")]
    assert roomy
    for role in roomy:
        assert _sent(role)["max_tokens"] == load_agents()[role]["llm_params"]["max_tokens"]


def test_a_role_that_asks_for_nothing_gets_the_defaults(monkeypatch):
    from crew_org import calls  # noqa: PLC0415

    plain = {"role": "Plain", "goal": "g", "backstory": "b"}
    monkeypatch.setattr(calls, "load_agents", lambda: {"plain": plain})
    body = _sent("plain")
    assert body["max_tokens"] == llm.DEFAULT_MAX_TOKENS
    assert body["model"] == "crew-local"
