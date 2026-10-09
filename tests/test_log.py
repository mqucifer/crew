"""One levelled stream for the crew's records (crew#449, ADR 0014)."""

from __future__ import annotations

import io
import json
import logging
import subprocess

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


def test_a_record_written_in_a_span_carries_the_traces_ids(tmp_path):
    from opentelemetry import trace
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

    configured(tmp_path)
    span = NonRecordingSpan(
        SpanContext(trace_id=0xABC, span_id=0xDEF, is_remote=False, trace_flags=TraceFlags(1))
    )
    with trace.use_span(span):
        logging.getLogger("crew_org.x").info("inside the tick's span")
    logging.getLogger("crew_org.x").info("outside any span")
    inside, outside = read(tmp_path / "logs" / "crew.jsonl")
    assert (
        inside["ctx"]["trace_id"] == f"{0xABC:032x}" and inside["ctx"]["span_id"] == f"{0xDEF:016x}"
    )
    assert "trace_id" not in outside["ctx"]
    [projected, _] = read(tmp_path / "telemetry" / "logs.jsonl")
    assert projected["trace_id"] == f"{0xABC:032x}"


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


def test_an_untracked_file_does_not_make_the_code_dirty(tmp_path, monkeypatch):
    """Only a change to tracked files means the code that ran wasn't the commit's."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "pkg" / "code.py").write_text("x = 1\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "first"], check=True)
    monkeypatch.setattr(log, "__file__", str(repo / "pkg" / "log.py"))

    (repo / "package.json").write_text("{}")
    assert log.code_version()["dirty"] is False
    (repo / "pkg" / "code.py").write_text("x = 2\n")
    assert log.code_version()["dirty"] is True


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


def test_a_line_names_its_card_once():
    """A run puts the card in the context, and most summaries already start with it."""
    named = {
        **RECORD,
        "message": "#529 context: 242,562 chars",
        "ctx": {"phase": "deliver", "card": 529},
    }
    assert log.line(named) == "12:58:03 INFO    [deliver] #529 context: 242,562 chars"


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


# --- over OTLP to the Sponsor's collector ---------------------------------------------------------


def exporting(tmp_path):
    from opentelemetry.sdk._logs.export import InMemoryLogExporter

    memory = InMemoryLogExporter()
    log.setup(var=tmp_path, console=io.StringIO(), exporter=memory)
    return memory


def test_with_an_endpoint_the_stream_goes_over_otlp_content_free(tmp_path):
    memory = exporting(tmp_path)
    logger = logging.getLogger("crew_org.flows.refine")
    with working_on(card=406, repo="sprint-metrics"), log.scoped(phase="refine"):
        log.event(logger, "phase.held", "held: secret prompt text", count=2, answer="free text")
    [sent] = memory.get_finished_logs()
    record = sent.log_record
    assert record.body == "phase.held" and record.severity_text == "INFO"
    attributes = dict(record.attributes)
    assert attributes["event.name"] == "phase.held" and attributes["crew.card"] == 406
    assert attributes["crew.phase"] == "refine" and attributes["crew.count"] == 2
    assert "secret prompt text" not in json.dumps(attributes, default=str)
    assert not any(k.endswith("answer") or k.startswith("code.") for k in attributes)


def test_otlp_records_sit_in_their_trace_natively(tmp_path):
    from opentelemetry import trace
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

    memory = exporting(tmp_path)
    span = NonRecordingSpan(
        SpanContext(trace_id=0xABC, span_id=0xDEF, is_remote=False, trace_flags=TraceFlags(1))
    )
    with trace.use_span(span):
        logging.getLogger("crew_org.x").info("inside a span")
    [sent] = memory.get_finished_logs()
    assert sent.log_record.trace_id == 0xABC and sent.log_record.span_id == 0xDEF
    assert "crew.trace_id" not in dict(sent.log_record.attributes)


def test_otlp_sends_no_debug_no_mirrored_events_and_writes_no_projection_file(tmp_path):
    memory = exporting(tmp_path)
    logging.getLogger("crew_org.x").debug("noise")
    EventSink(None).emit(CrewEvent(kind=EventKind.CARD_MOVED, card=406, summary="moved"))
    assert memory.get_finished_logs() == ()
    assert not (tmp_path / "telemetry" / "logs.jsonl").exists()


# --- a record whose parts join up (crew#449 part 2, discussion 552) -------------------------------


def test_a_runs_records_share_its_id_and_a_run_inside_another_has_its_own():
    with log.run("Code Reviewer", card=455, repo="sprint-metrics") as review:
        assert log.scope()["run"] == review and log.scope()["role"] == "Code Reviewer"
        assert log.scope()["card"] == 455 and log.in_run()
        with log.run("DevOps Engineer", card=455, repo="sprint-metrics") as deploy:
            assert deploy != review and log.scope()["role"] == "DevOps Engineer"
        assert log.scope()["run"] == review
    assert not log.in_run() and "run" not in log.scope()


def test_an_event_is_stamped_with_the_context_it_was_written_in(tmp_path):
    sink = EventSink(tmp_path / "events" / "tick.jsonl")
    with (
        log.scoped(tick="t1", commit="abc", dirty=False),
        log.run("Developer", card=502, repo="sprint-metrics", sprint="Sprint 20") as run,
    ):
        sink.note(EventKind.NOTE, "a note with no card of its own")
    [written] = read(tmp_path / "events" / "tick.jsonl")
    assert written["card"] is None, "the envelope's own fields are left as they were"
    assert written["ctx"]["run"] == run and written["ctx"]["role"] == "Developer"
    assert written["ctx"]["card"] == 502 and written["ctx"]["repo"] == "sprint-metrics"
    assert written["ctx"]["sprint"] == "Sprint 20" and written["ctx"]["tick"] == "t1"
    assert written["ctx"]["commit"] == "abc"
    [line] = read(tmp_path / "telemetry" / "tick.jsonl")
    assert line["run"] == run and line["commit"] == "abc" and line["repo"] == "sprint-metrics"


def test_an_event_from_before_the_context_still_reads():
    old = '{"at":"2026-09-18T15:56:40Z","kind":"note","role":null,"card":null,"summary":"x"}'
    assert CrewEvent.model_validate_json(old).ctx == {}


def test_a_model_call_outside_any_run_is_a_run_of_its_own():
    from crew_org.events import attributed

    seen = []
    attributed(lambda: seen.append(log.scope().get("run")), card=1, repo="crew")()
    attributed(lambda: seen.append(log.scope().get("run")), card=1, repo="crew")()
    assert all(seen) and seen[0] != seen[1]
    with log.run("Developer", card=1) as run:
        attributed(lambda: seen.append(log.scope().get("run")), card=1)()
    assert seen[-1] == run


def test_what_a_role_was_shown_to_choose_from_stays_local(tmp_path):
    sink = EventSink(tmp_path / "events" / "tick.jsonl")
    sink.emit(
        CrewEvent(
            kind=EventKind.FILES_SHOWN,
            role="Developer",
            card=502,
            summary="#502 context: 64,189 chars, 2 files in full",
            detail={"shown": ["a.py"], "selection_text": "the story's secret text"},
        )
    )
    assert "secret" in (tmp_path / "events" / "tick.jsonl").read_text()
    assert "secret" not in (tmp_path / "telemetry" / "tick.jsonl").read_text()


def test_the_ticks_code_and_id_are_on_every_exported_record(tmp_path):
    from opentelemetry.sdk._logs.export import InMemoryLogExporter

    memory = InMemoryLogExporter()
    log.setup(
        var=tmp_path,
        console=io.StringIO(),
        exporter=memory,
        resource={"service.version": "abc123", "service.instance.id": "t1"},
    )
    with log.run("QA Engineer", card=7, repo="crew") as run:
        logging.getLogger("crew_org.x").info("judged")
    [sent] = memory.get_finished_logs()
    resource = dict(sent.resource.attributes)
    assert resource["service.version"] == "abc123" and resource["service.instance.id"] == "t1"
    assert dict(sent.log_record.attributes)["crew.run"] == run
