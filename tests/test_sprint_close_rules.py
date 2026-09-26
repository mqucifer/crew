"""A sprint ends when its dates do, or when it's closed early on purpose (#193).

Sprint 6 was closed in the afternoon to try the retro's new layout, and that
evening's ticks admitted fifteen more stories into it. Its retro never saw them,
twice in one day.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from crew_org import clock
from crew_org.crews.retro_crew import Retro
from crew_org.flows import close as close_mod
from crew_org.flows.close import close_sprint
from crew_org.flows.loop import _closed
from crew_org.flows.retro import RETRO_LABEL, marker
from crew_org.tools.github_project import BoardField
from tests.test_retro import CREW, PRODUCT, SPRINT, CloseBoard, FakeIssues, process

ITERATIONS = BoardField(
    id="F",
    name="Sprint",
    data_type="ITERATION",
    iterations=[
        {"title": "Sprint 6", "startDate": "2026-09-25", "duration": 1},
        {"title": "Sprint 7", "startDate": "2026-09-26", "duration": 1},
        {"title": "Sprint 8", "startDate": "2026-09-27", "duration": 1},
    ],
)


def test_the_sprint_clock_is_the_one_org_yaml_names():
    assert clock.timezone({"sprint": {"timezone": "America/Chicago"}}) == "America/Chicago"
    assert clock.timezone({}) == "UTC"
    assert isinstance(clock.sprint_today({"sprint": {"timezone": "America/Chicago"}}), date)


def test_a_sprints_dates_and_the_one_after_it():
    assert ITERATIONS.iteration_dates("Sprint 6") == (date(2026, 9, 25), date(2026, 9, 25))
    assert ITERATIONS.next_iteration("Sprint 6") == ("Sprint 7", date(2026, 9, 26))
    assert ITERATIONS.next_iteration("Sprint 8") is None
    assert ITERATIONS.iteration_dates("Sprint 99") is None


def crew_for(sprint: str, issues) -> SimpleNamespace:
    board = SimpleNamespace(schema=SimpleNamespace(field=lambda name: ITERATIONS))
    return SimpleNamespace(crew_repo=CREW, issues=issues, sprint=sprint, board=board)


def test_a_sprint_with_its_retro_recorded_admits_nothing_and_says_when_the_next_starts():
    issues = FakeIssues(
        existing=[{"number": 198, "labels": [RETRO_LABEL], "body": marker("Sprint 6")}]
    )
    why = _closed(crew_for("Sprint 6", issues))
    assert "Sprint 6 is closed (retro #198)" in why
    assert "Sprint 7 starts on 2026-09-26" in why


def test_a_sprint_without_a_retro_is_open():
    assert _closed(crew_for("Sprint 7", FakeIssues())) == ""


def test_a_preview_records_nothing_even_for_a_sprint_that_has_a_retro(monkeypatch, tmp_path):
    from crew_org.escalation import EscalationLedger

    monkeypatch.setattr(
        close_mod,
        "write_retro",
        lambda *a, **k: Retro(summary="Delivered the split.", defects=[process()]),
    )
    issues = FakeIssues(existing=[{"number": 12, "labels": [RETRO_LABEL], "body": marker(SPRINT)}])
    result = close_sprint(
        CloseBoard(),
        issues,
        close_mod.EventSink(None),
        EscalationLedger(tmp_path / "ledger.jsonl"),
        sprint=SPRINT,
        repo=PRODUCT,
        crew_repo=CREW,
        delivery_repos=[PRODUCT],
        preview=True,
    )
    assert issues.created == [], "no retro issue, no defects"
    assert result.retro_record is None and not result.retro_already
    assert result.preview is not None
    assert "## Delivered" in result.preview and "Delivered the split." in result.preview
    assert "- would file:" in result.preview
