"""The stack the crew runs on is checked each tick (#335, DevOps phase 1).

The proxy and model already stop a tick when they're down. The telemetry
exporter and collector could be down for days, seen only as an empty dashboard.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from crew_org.flows.standup import write_standup
from crew_org.tools import stack
from crew_org.tools.stack import Check, problems, stack_health
from tests.test_standup import run

AT = datetime(2026, 9, 29, tzinfo=UTC)


def answers(monkeypatch, codes: dict, model=(True, "proxy up")):
    def fake_get(url, timeout):
        code = codes[url]
        if isinstance(code, Exception):
            raise code
        return httpx.Response(code)

    monkeypatch.setattr(stack.httpx, "get", fake_get)
    monkeypatch.setattr("crew_org.llm.health", lambda: model)


def test_everything_up_reports_no_problems(monkeypatch):
    answers(monkeypatch, {stack.EXPORTER: 200, stack.COLLECTOR: 200})
    assert problems(stack_health()) == []


def test_a_telemetry_component_down_is_named_with_how_to_start_it(monkeypatch):
    answers(monkeypatch, {stack.EXPORTER: httpx.ConnectError("refused"), stack.COLLECTOR: 503})
    lines = problems(stack_health())
    assert any("telemetry exporter" in line and "ConnectError" in line for line in lines)
    assert any("telemetry collector" in line and "answered 503" in line for line in lines)
    assert all("deploy/telemetry" in line for line in lines)


def test_the_model_is_marked_required(monkeypatch):
    answers(monkeypatch, {stack.EXPORTER: 200, stack.COLLECTOR: 200}, model=(False, "model down"))
    [line] = problems(stack_health())
    assert line.startswith("- **proxy and model** (required)")


def test_the_standup_shows_what_isnt_healthy():
    lines = problems([Check("telemetry collector", False, "not answering")])
    standup = write_standup(run(), sprint="Sprint 9", at=AT, waiting=[], aging=[], stack=lines)
    assert "**Stack health**" in standup.text and "telemetry collector" in standup.text


def test_a_healthy_stack_adds_nothing():
    standup = write_standup(run(), sprint="Sprint 9", at=AT, waiting=[], aging=[])
    assert "Stack health" not in standup.text
