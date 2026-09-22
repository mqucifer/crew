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

    monkeypatch.setattr(httpx, "get", fake_get)
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


def test_a_role_can_ask_for_more_headroom_than_the_default(monkeypatch):
    """The Business Analyst's input grows without bound — a large epic spent
    the shared 16,384-token budget on reasoning alone and returned nothing."""
    import crew_org.agents as agents_mod

    captured = {}

    def fake_build_llm(alias="crew-local", **overrides):
        captured["alias"] = alias
        captured.update(overrides)
        return object()

    monkeypatch.setattr(agents_mod, "build_llm", fake_build_llm)
    monkeypatch.setattr(agents_mod, "Agent", lambda **kw: kw)

    agents_mod.build_agent(
        "business_analyst",
        {
            "role": "Business Analyst",
            "goal": "split epics",
            "backstory": "you are ruthless about size",
            "llm": "crew-analysis",
            "llm_params": {"max_tokens": 32768},
        },
    )
    assert captured["alias"] == "crew-analysis"
    assert captured["max_tokens"] == 32768


def test_a_role_that_asks_for_nothing_gets_the_defaults(monkeypatch):
    import crew_org.agents as agents_mod

    captured = {}

    def fake_build_llm(alias="crew-local", **overrides):
        captured["alias"] = alias
        captured["overrides"] = overrides
        return object()

    monkeypatch.setattr(agents_mod, "build_llm", fake_build_llm)
    monkeypatch.setattr(agents_mod, "Agent", lambda **kw: kw)

    agents_mod.build_agent(
        "developer",
        {"role": "Developer", "goal": "build", "backstory": "you write code", "llm": "crew-code"},
    )
    assert captured["overrides"] == {}


def test_the_analyst_is_configured_off_the_shared_alias():
    """The change that matters is in agents.yaml, not in a call site."""
    from crew_org.permissions import load_agents

    spec = load_agents()["business_analyst"]
    assert spec["llm"] == "crew-analysis"
    assert spec["llm_params"]["max_tokens"] > 16384
