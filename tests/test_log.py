"""One levelled stream for the crew's records (crew#449, ADR 0014)."""

from __future__ import annotations

import io
import json
import logging

import pytest

from crew_org import log
from crew_org.events import CrewEvent, EventKind, EventSink, working_on


@pytest.fixture(autouse=True)
def _fresh():
    log.reset()
    yield
    log.reset()


class Tty(io.StringIO):
    def isatty(self):
        return True


def read(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def configured(tmp_path, console=None, level=None):
    stream = console if console is not None else io.StringIO()
    log.setup(var=tmp_path, console=stream, console_level=level)
    return stream


# --- the context every record carries ---------------------------------------------------------


def test_the_context_joins_the_ticks_and_the_cards():
    with working_on(card=406, repo="sprint-metrics"), log.scoped(tick=1, pass_=2, phase="refine"):
        assert log.scope() == {
            "card": 406,
            "repo": "sprint-metrics",
            "tick": 1,
            "pass": 2,
            "phase": "refine",
        }
    assert log.scope() == {}


def test_a_scope_nests_and_is_restored():
    with log.scoped(pass_=1):
        token = log.enter(phase="deliver")
        assert log.scope() == {"pass": 1, "phase": "deliver"}
        log.leave(token)
        assert log.scope() == {"pass": 1}


# --- the three outputs ---------------------------------------------------------------------------


def test_every_record_is_kept_whole_with_its_level_and_context(tmp_path):
    configured(tmp_path)
    logger = logging.getLogger("crew_org.flows.refine")
    with log.scoped(phase="refine"):
        log.event(logger, "phase.held", "held: #406 waits", level=logging.INFO, count=2)
        logger.debug("detail for diagnosis")
    held, debug = read(tmp_path / "logs" / "crew.jsonl")
    assert held["level"] == "INFO" and held["event"] == "phase.held"
    assert held["message"] == "held: #406 waits" and held["attrs"] == {"count": 2}
    assert held["ctx"] == {"phase": "refine"} and held["schema_version"] == 1
    assert debug["level"] == "DEBUG" and debug["event"] is None


def test_what_goes_to_grafana_is_names_and_numbers_never_the_message(tmp_path):
    configured(tmp_path)
    logger = logging.getLogger("crew_org.flows.refine")
    with working_on(card=406, repo="sprint-metrics"), log.scoped(phase="refine"):
        log.event(logger, "phase.held", "held: secret prompt text", count=2, answer="free text")
    [out] = read(tmp_path / "telemetry" / "logs.jsonl")
    assert out["kind"] == "phase.held" and out["level"] == "INFO"
    assert out["card"] == 406 and out["phase"] == "refine" and out["count"] == 2
    assert "secret prompt text" not in json.dumps(out)
    assert "answer" not in out and "message" not in out


def test_which_code_ran_and_a_phases_counts_go_to_grafana(tmp_path):
    configured(tmp_path)
    logger = logging.getLogger("crew_org.tick")
    log.event(logger, "tick.started", "tick", commit="abc123", dirty=False, repos="sprint-metrics")
    log.event(logger, "phase.finished", "2 stories", moved=True, count_stories=2)
    started, finished = read(tmp_path / "telemetry" / "logs.jsonl")
    assert started["commit"] == "abc123" and started["dirty"] is False
    assert finished["moved"] is True and finished["count_stories"] == 2


def test_debug_records_stay_local(tmp_path):
    configured(tmp_path)
    logging.getLogger("crew_org.x").debug("noise")
    assert (tmp_path / "telemetry" / "logs.jsonl").read_text() == ""


def test_a_redirected_console_shows_progress_and_a_terminal_only_warnings(tmp_path):
    redirected = configured(tmp_path)
    logging.getLogger("crew_org.x").info("splitting #406")
    assert "splitting #406" in redirected.getvalue()

    log.reset()
    terminal = configured(tmp_path / "tty", console=Tty())
    logging.getLogger("crew_org.x").info("quiet on a terminal")
    logging.getLogger("crew_org.x").warning("but warnings show")
    assert "quiet on a terminal" not in terminal.getvalue()
    assert "but warnings show" in terminal.getvalue()


def test_chatty_libraries_are_held_to_warnings(tmp_path):
    configured(tmp_path)
    assert all(logging.getLogger(name).level == logging.WARNING for name in log.QUIET)


def test_setting_up_twice_adds_nothing(tmp_path):
    def ours():
        # pytest attaches its own capture handlers during a test; count only the crew's.
        return [
            h
            for h in logging.getLogger("crew_org").handlers
            if "LogCapture" not in type(h).__name__
        ]

    configured(tmp_path)
    before = len(ours())
    log.setup(var=tmp_path)
    assert len(ours()) == before == 3


# --- the sink's events, mirrored -----------------------------------------------------------------


def test_events_are_mirrored_only_once_logging_is_set_up(tmp_path):
    sink = EventSink(None)
    sink.emit(CrewEvent(kind=EventKind.CARD_MOVED, card=406, summary="moved"))
    configured(tmp_path)
    sink.emit(CrewEvent(kind=EventKind.CARD_MOVED, card=406, role="Developer", summary="to Done"))
    [record] = read(tmp_path / "logs" / "crew.jsonl")
    assert record["event"] == "events.card.moved" and record["message"] == "to Done"
    assert record["attrs"] == {"card": 406, "role": "Developer"}


def test_a_model_calls_prompt_never_becomes_a_message_and_its_level_follows_its_kind(tmp_path):
    configured(tmp_path)
    sink = EventSink(None)
    sink.emit(
        CrewEvent(
            kind=EventKind.LLM_CALL_STARTED,
            card=406,
            summary="## The Goal ... the whole prompt",
            detail={"model": "crew-analysis", "for": "refine"},
        )
    )
    sink.emit(CrewEvent(kind=EventKind.AGENT_FAILED, card=406, summary="split failed"))
    started, failed = read(tmp_path / "logs" / "crew.jsonl")
    assert started["level"] == "DEBUG" and "prompt" not in started["message"]
    assert started["message"] == "llm.started refine crew-analysis"
    assert failed["level"] == "ERROR"


def test_mirrored_events_are_not_sent_to_grafana_twice(tmp_path):
    configured(tmp_path)
    EventSink(None).emit(CrewEvent(kind=EventKind.CARD_MOVED, card=406, summary="moved"))
    assert (tmp_path / "telemetry" / "logs.jsonl").read_text() == ""


# --- which code ran


def test_the_crews_commit_is_known_and_unknown_is_empty(monkeypatch):
    found = log.code_version()
    assert len(found["commit"]) == 40 and isinstance(found["dirty"], bool)

    def fails(*a, **k):
        raise OSError("no git")

    monkeypatch.setattr(log.subprocess, "run", fails)
    assert log.code_version() == {}


# --- reading it back -----------------------------------------------------------------------------


RECORD = {
    "at": "2026-10-06T12:58:03.123456+00:00",
    "level": "INFO",
    "event": "phase.held",
    "message": "held: #406 waits",
    "ctx": {"phase": "refine", "card": 406},
    "attrs": {},
}


def test_a_record_reads_as_one_line():
    assert log.line(RECORD) == "12:58:03 INFO    [refine] #406 held: #406 waits"
    assert log.line({**RECORD, "ctx": {"for": "deliver"}}).startswith("12:58:03 INFO    [deliver] ")


def test_the_filters_match_level_card_phase_and_event():
    assert log.matches(RECORD)
    assert not log.matches(RECORD, level="WARNING")
    assert log.matches(RECORD, card=406) and not log.matches(RECORD, card=407)
    assert log.matches(RECORD, phase="refine") and not log.matches(RECORD, phase="deliver")
    assert log.matches(RECORD, event="phase.") and not log.matches(RECORD, event="tick.")
    assert log.matches({**RECORD, "ctx": {}, "attrs": {"card": 406}}, card=406)


def test_crew_log_prints_the_matching_records(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from crew_org import cli

    (tmp_path / "logs").mkdir()
    lines = [
        RECORD,
        {**RECORD, "level": "DEBUG", "message": "noise"},
        {**RECORD, "ctx": {"card": 7}},
    ]
    (tmp_path / "logs" / "crew.jsonl").write_text("\n".join(json.dumps(r) for r in lines) + "\n")
    monkeypatch.setattr(cli, "VAR", tmp_path)
    out = CliRunner().invoke(cli.app, ["log", "--card", "406"]).output
    assert "held: #406 waits" in out and "noise" not in out
    assert out.count("held:") == 1


# --- the loop puts every phase's decisions on the record


def test_a_phases_holds_blocks_and_failures_are_logged_as_it_finishes(tmp_path):
    from crew_org.flows.loop import PhaseOutcome, _record

    configured(tmp_path)
    with log.scoped(pass_=1):
        _record(
            PhaseOutcome(
                "refine",
                summary="0 epics",
                held=["#406 — waits: the Architect's technical work lands first: #415, #416"],
                blocked=["#31 — the split failed the same way twice"],
                error="RuntimeError: boom",
                counts={"stories": 0},
            )
        )
    records = read(tmp_path / "logs" / "crew.jsonl")
    assert [(r["event"], r["level"]) for r in records] == [
        ("phase.held", "INFO"),
        ("phase.blocked", "WARNING"),
        ("phase.failed", "ERROR"),
        ("phase.finished", "INFO"),
    ]
    assert all(r["ctx"] == {"pass": 1, "phase": "refine"} for r in records)
    assert "technical work lands first" in records[0]["message"]
    assert records[-1]["attrs"] == {"moved": False, "count_stories": 0}
