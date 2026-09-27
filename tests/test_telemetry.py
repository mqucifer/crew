"""Telemetry for the Sponsor's Alloy: attributes, never content (#283)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.telemetry import backfill, path_for, view

EXPORTER = Path(__file__).parents[1] / "deploy" / "telemetry" / "exporter.py"
_spec = importlib.util.spec_from_file_location("exporter", EXPORTER)
exporter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(exporter)

PROMPT = "## What this project is for: its onboarding record\n\nThe Sponsor's answers"


def llm_call(**detail) -> CrewEvent:
    return CrewEvent(
        kind=EventKind.LLM_CALL_FINISHED,
        role="Developer",
        card=147,
        summary=PROMPT,  # the bridge puts the task's description here
        detail={
            "model": "crew-code-think",
            "finish_reason": "stop",
            "prompt_tokens": 45000,
            "completion_tokens": 2500,
            "reasoning_tokens": 1500,
            "duration_s": 61.2,
            "repo": "sprint-metrics",
            "sprint": "Sprint 8",
            "attempt": 1,
            "for": "deliver",
            **detail,
        },
    )


# --- what may leave ---------------------------------------------------------------------


def test_a_model_call_leaves_as_its_attributes_and_nothing_it_said():
    out = view(llm_call())
    assert out["prompt_tokens"] == 45000 and out["for"] == "deliver" and out["card"] == 147
    assert PROMPT not in json.dumps(out) and "summary" not in out


def test_free_text_stays_local_whatever_the_event():
    failure = CrewEvent(
        kind=EventKind.ESCALATION_DECIDED,
        role="Developer",
        card=145,
        summary="VERIFY — retry_local",
        detail={
            "failure_class": "VERIFY",
            "reason": "the model's own words",
            "error": "Traceback … the code it wrote",
            "output": "E   assert 1 == 2",
            "first_error": "tests/test_x.py:3",
            "unproven": ["Given … When … Then …"],
        },
    )
    out = view(failure)
    assert out == {
        "at": out["at"],
        "kind": "escalation.decided",
        "role": "Developer",
        "card": 145,
        "failure_class": "VERIFY",
        "disposition": "retry_local",
    }


def test_a_field_added_to_an_event_later_stays_local_until_allowed():
    """An allow-list, not a deny-list."""
    assert "diff" not in view(llm_call(diff="+ secret code"))


def test_why_a_story_went_back_is_a_name_and_leaves():
    returned = CrewEvent(
        kind=EventKind.STORY_RETURNED,
        card=145,
        detail={"repo": "sprint-metrics", "epic": 59, "reason": "gate round trips"},
    )
    assert view(returned)["reason"] == "gate round trips"


# --- written beside the event log -------------------------------------------------------


def test_every_event_is_also_written_to_the_telemetry_log(tmp_path):
    sink = EventSink(tmp_path / "events" / "tick.jsonl")
    sink.emit(llm_call())
    (full,) = (tmp_path / "events" / "tick.jsonl").read_text().splitlines()
    (kept,) = (tmp_path / "telemetry" / "tick.jsonl").read_text().splitlines()
    assert PROMPT in json.loads(full)["summary"], "the crew's own record keeps everything"
    assert PROMPT not in kept


def test_telemetry_that_cannot_be_written_never_stops_the_work(tmp_path):
    sink = EventSink(tmp_path / "events" / "tick.jsonl")
    (tmp_path / "telemetry").write_text("a file where the folder should be")
    sink.emit(llm_call())
    assert (tmp_path / "events" / "tick.jsonl").read_text()


def test_only_the_event_logs_get_a_telemetry_log(tmp_path):
    assert path_for(tmp_path / "events" / "tick.jsonl") == tmp_path / "telemetry" / "tick.jsonl"
    assert path_for(tmp_path / "elsewhere" / "x.jsonl") is None
    assert path_for(None) is None


def test_history_is_backfilled_and_twice_is_the_same_as_once(tmp_path):
    events = tmp_path / "events"
    events.mkdir()
    (events / "tick.jsonl").write_text(llm_call().model_dump_json() + "\n")
    assert backfill(events, tmp_path / "telemetry") == 1
    backfill(events, tmp_path / "telemetry")
    (kept,) = (tmp_path / "telemetry" / "tick.jsonl").read_text().splitlines()
    assert PROMPT not in kept


# --- the exporter -----------------------------------------------------------------------


def test_the_exporter_counts_calls_tokens_moves_and_retries_with_no_card_label(tmp_path):
    sink = EventSink(tmp_path / "events" / "tick.jsonl")
    sink.emit(llm_call())
    sink.emit(llm_call(finish_reason="length"))
    sink.emit(
        CrewEvent(kind=EventKind.CARD_MOVED, role="Developer", card=147, detail={"to": "Reviewing"})
    )
    sink.emit(
        CrewEvent(
            kind=EventKind.ESCALATION_DECIDED,
            card=147,
            summary="VERIFY — retry_local",
            detail={"failure_class": "VERIFY"},
        )
    )
    sink.emit(
        CrewEvent(
            kind=EventKind.TICK_STARTED,
            detail={"tick": 1, "counts": {"Ready": 2, "Done": 66}},
        )
    )
    text = exporter.render(tmp_path / "telemetry")
    assert (
        'crew_llm_calls_total{role="Developer",model="crew-code-think",finish_reason="stop"} 1'
        in text
    )
    assert 'finish_reason="length"} 1' in text
    assert (
        'crew_llm_tokens_total{role="Developer",model="crew-code-think",type="prompt"} 90000'
        in text
    )
    assert 'crew_card_moves_total{to="Reviewing"} 1' in text
    assert 'crew_retries_total{failure_class="VERIFY",disposition="retry_local"} 1' in text
    assert 'crew_board_cards{column="Done"} 66' in text
    assert "crew_ticks_total 1" in text
    assert "card=" not in text, "a label per card grows without bound"


def test_the_exporter_shows_github_s_budget_and_throttles(tmp_path):
    """#293."""
    sink = EventSink(tmp_path / "events" / "tick.jsonl")
    sink.emit(CrewEvent(kind=EventKind.TICK_STARTED, detail={"tick": 1, "github_remaining": 4321}))
    sink.emit(CrewEvent(kind=EventKind.GITHUB_THROTTLED, detail={"wait_s": 30.0, "status": 403}))
    text = exporter.render(tmp_path / "telemetry")
    assert "crew_github_requests_remaining 4321" in text
    assert 'crew_events_total{kind="github.throttled",role=""} 1' in text


def test_the_exporter_reads_an_empty_or_missing_log(tmp_path):
    assert "crew_ticks_total 0" in exporter.render(tmp_path / "nothing-here")
