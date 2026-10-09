"""One levelled stream for the crew's records (crew#449, ADR 0014).

The crew had no logging: everything went through the event sink, with no level, no
shared context, and decisions such as a hold written only to the console summary at
the end of a tick, which Rich buffers until exit. This is the shared library ADR 0014
decides: standard `logging`, a logger per module (`logging.getLogger(__name__)` under
`crew_org`), a level on every record, and a common context from context variables.

**Three outputs, set up once per command (`setup`):**
- `var/logs/crew.jsonl`: every record at DEBUG and above, as JSON lines, message
  included. The crew's own record; nothing leaves this machine from it.
- `var/telemetry/logs.jsonl`: an allow-list projection (ADR 0010). Level, logger,
  event name, the context and allow-listed attributes, **no message**. The Sponsor's
  collector already tails `var/telemetry` into Grafana.
- The console, on stderr: for people. `INFO` and above when stderr isn't a terminal
  (a tick redirected to a file can be watched live), `WARNING` and above when it is,
  so the live board view isn't interleaved with progress lines.

**Named events** (`event`) are the records code reads; their schemas come in a later
part of crew#449. The sink's existing events are mirrored into this stream as
`events.<kind>`, locally only: they already reach Grafana through their own
projection (`telemetry.py`), and sending them twice would count everything twice.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import subprocess
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

ROOT = "crew_org"
SCHEMA_VERSION = 1
# The common context every record carries (ADR 0014), beyond what `working_on` sets.
_SCOPE: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("crew_log_scope")
# Libraries that talk too much at INFO: warnings and above from the start (ADR 0014).
QUIET = ("crewai", "litellm", "LiteLLM", "httpx", "httpcore", "opentelemetry", "urllib3")
# What the context may carry out to Grafana: identifiers and names, never text.
_CONTEXT_OUT = frozenset(
    {
        "tick",
        "pass",
        "phase",
        "repo",
        "card",
        "role",
        "sprint",
        "attempt",
        "for",
        # One role's run on one card, and the code the tick ran (crew#449, discussion
        # 552): ids that join a record to the others of its run and to its commit.
        "run",
        "commit",
        "dirty",
        # The span the record was written in, so Grafana joins a log line to its trace.
        "trace_id",
        "span_id",
    }
)
_EVENTS = "events."

_configured = False


def scope() -> dict[str, Any]:
    """The context a record written now carries: the tick's, the card's (`working_on`), and
    the trace's, when a span is open (crew#283), so a log line and its trace join up."""
    from opentelemetry import trace  # noqa: PLC0415

    from crew_org.events import working  # noqa: PLC0415

    found = {**working(), **_SCOPE.get({})}
    span = trace.get_current_span().get_span_context()
    if span.is_valid:
        found["trace_id"] = format(span.trace_id, "032x")
        found["span_id"] = format(span.span_id, "016x")
    return found


@contextlib.contextmanager
def scoped(**context: Any) -> Iterator[None]:
    """Add to the context of every record written inside: `tick`, `pass_`, `phase`, `role`.

    `pass_` is written as `pass`, which Python can't take as a keyword.
    """
    given = {("pass" if k == "pass_" else k): v for k, v in context.items() if v is not None}
    token = _SCOPE.set({**_SCOPE.get({}), **given})
    try:
        yield
    finally:
        _SCOPE.reset(token)


def enter(**context: Any) -> contextvars.Token[dict[str, Any]]:
    """`scoped` for a block that can't be indented under a `with`; end it with `leave`."""
    given = {("pass" if k == "pass_" else k): v for k, v in context.items() if v is not None}
    return _SCOPE.set({**_SCOPE.get({}), **given})


def leave(token: contextvars.Token[dict[str, Any]]) -> None:
    _SCOPE.reset(token)


def new_id() -> str:
    """An id to join records by: a tick's, or a role's run's. Unique across commands."""
    return uuid.uuid4().hex[:16]


@contextlib.contextmanager
def run(
    role: str,
    *,
    card: int | None = None,
    repo: str | None = None,
    sprint: str | None = None,
    purpose: str | None = None,
) -> Iterator[str]:
    """One role's run on one card: every record and model call inside carries its id.

    A run used to be rebuilt from the `agent.started` and `agent.finished` around it,
    which is a guess when two runs on a card overlap (discussion 552). Inside, model
    calls are attributed to the card as `working_on` does, and sit in the run's span.
    """
    from crew_org import tracing  # noqa: PLC0415
    from crew_org.events import working_on  # noqa: PLC0415

    run_id = new_id()
    with (
        working_on(card=card, repo=repo, sprint=sprint, purpose=purpose),
        scoped(run=run_id, role=role),
        tracing.span(
            f"{role} #{card}" if card is not None else role,
            **{"crew.run": run_id, "crew.role": role, "crew.card": card, "crew.repo": repo},
        ),
    ):
        yield run_id


def in_run() -> bool:
    """Whether a record written now belongs to a role's run."""
    return "run" in _SCOPE.get({})


class _Context(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.crew_ctx = scope()
        return True


def _now(record: logging.LogRecord) -> str:
    return datetime.fromtimestamp(record.created, UTC).isoformat()


def _event_name(record: logging.LogRecord) -> str | None:
    return getattr(record, "event_name", None)


class JsonLines(logging.Formatter):
    """The full record, for the crew's own use."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "at": _now(record),
            "level": record.levelname,
            "logger": record.name,
            "event": _event_name(record),
            "message": record.getMessage(),
            "ctx": getattr(record, "crew_ctx", {}),
            "attrs": getattr(record, "attrs", {}),
            "schema_version": SCHEMA_VERSION,
        }
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


class Telemetry(logging.Formatter):
    """What may leave for Grafana: names and numbers, never the message (ADR 0010)."""

    def format(self, record: logging.LogRecord) -> str:
        from crew_org.telemetry import DETAIL  # noqa: PLC0415

        ctx = getattr(record, "crew_ctx", {})
        attrs = getattr(record, "attrs", {})
        out: dict[str, Any] = {
            "at": _now(record),
            # The collector copies `kind` into the record's body.
            "kind": _event_name(record) or "log",
            "level": record.levelname,
            "logger": record.name,
            **{k: v for k, v in ctx.items() if k in _CONTEXT_OUT},
            # A phase's counts are numbers by construction (`count_<name>`).
            **{k: v for k, v in attrs.items() if k in DETAIL or k.startswith("count_")},
            "schema_version": SCHEMA_VERSION,
        }
        return json.dumps(out, default=str)


class _NotMirrored(logging.Filter):
    """The sink's own events already reach Grafana through their own projection."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not (_event_name(record) or "").startswith(_EVENTS)


class Console(logging.Formatter):
    """One readable line per record."""

    def format(self, record: logging.LogRecord) -> str:
        return line(
            {
                "at": _now(record),
                "level": record.levelname,
                "message": record.getMessage(),
                "ctx": getattr(record, "crew_ctx", {}),
            }
        )


def line(record: dict[str, Any]) -> str:
    """A record, as one line a person reads: time, level, phase, card, message."""
    ctx = record.get("ctx") or {}
    where = " ".join(
        part
        for part in (
            (ctx.get("phase") or ctx.get("for")) and f"[{ctx.get('phase') or ctx.get('for')}]",
            ctx.get("card") and f"#{ctx['card']}",
        )
        if part
    )
    at = str(record.get("at", ""))[11:19]
    level = f"{record.get('level', ''):<7}"
    return f"{at} {level} {where + ' ' if where else ''}{record.get('message', '')}"


def _to_collector(
    endpoint: str | None, exporter: Any, resource: dict[str, Any] | None = None
) -> logging.Handler | None:
    """The stream, over OTLP to the Sponsor's collector, content-free (ADR 0010).

    The SDK's handler takes a record's message as its body and every field on it as an
    attribute, so this one sends the event name as the body and only the allow-listed
    names and numbers as attributes. The trace context is the SDK's own, so in Grafana
    a log record sits in its trace natively (crew#283).
    """
    if not endpoint and exporter is None:
        return None
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler  # noqa: PLC0415
    from opentelemetry.sdk._logs.export import (  # noqa: PLC0415
        BatchLogRecordProcessor,
        SimpleLogRecordProcessor,
    )
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415

    from crew_org.telemetry import DETAIL  # noqa: PLC0415

    class Content(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            return _event_name(record) or "log"

    class ToCollector(LoggingHandler):
        @staticmethod
        def _get_attributes(record: logging.LogRecord) -> Any:
            ctx = getattr(record, "crew_ctx", {})
            attrs = getattr(record, "attrs", {})
            return {
                "logger": record.name,
                "event.name": _event_name(record) or "log",
                "schema_version": SCHEMA_VERSION,
                **{
                    f"crew.{k}": v
                    for k, v in ctx.items()
                    if k in _CONTEXT_OUT and k not in ("trace_id", "span_id")
                },
                **{
                    f"crew.{k}": v
                    for k, v in attrs.items()
                    if (k in DETAIL or k.startswith("count_"))
                    and isinstance(v, str | int | float | bool)
                },
            }

    global _provider
    _provider = LoggerProvider(
        resource=Resource.create({"service.name": "crew", **(resource or {})})
    )
    if exporter is not None:
        _provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    else:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import (  # noqa: PLC0415
            OTLPLogExporter,
        )

        target = f"{str(endpoint).rstrip('/')}/v1/logs"
        _provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=target, timeout=5))
        )
    handler = ToCollector(level=logging.INFO, logger_provider=_provider)
    handler.setFormatter(Content())
    return handler


_provider: Any = None


def setup(
    *,
    var: Path = Path("var"),
    console: TextIO | None = None,
    console_level: int | None = None,
    otlp_endpoint: str | None = None,
    exporter: Any = None,
    resource: dict[str, Any] | None = None,
) -> None:
    """Send the crew's records to its file, to Grafana and to the console. Once per process.

    With `otlp_endpoint` (org.yaml's `telemetry.otlp_endpoint`) the stream goes to the
    Sponsor's collector over OTLP; without it, an allow-list projection is written to
    `var/telemetry/logs.jsonl` for the collector to tail. Never both, so nothing is
    counted twice. `exporter` replaces the OTLP one, for tests. `resource` is what every
    exported record says about the process: for a tick, the crew's commit as
    `service.version` and the tick's id as `service.instance.id`.
    """
    global _configured
    if _configured:
        return
    root = logging.getLogger(ROOT)
    root.setLevel(logging.DEBUG)
    root.propagate = False
    context = _Context()

    (var / "logs").mkdir(parents=True, exist_ok=True)
    full = logging.FileHandler(var / "logs" / "crew.jsonl", encoding="utf-8")
    full.setLevel(logging.DEBUG)
    full.setFormatter(JsonLines())
    full.addFilter(context)
    root.addHandler(full)

    out = _to_collector(otlp_endpoint, exporter, resource)
    if out is None:
        (var / "telemetry").mkdir(parents=True, exist_ok=True)
        out = logging.FileHandler(var / "telemetry" / "logs.jsonl", encoding="utf-8")
        out.setLevel(logging.INFO)
        out.setFormatter(Telemetry())
    out.addFilter(context)
    out.addFilter(_NotMirrored())
    root.addHandler(out)

    stream = console if console is not None else sys.stderr
    people = logging.StreamHandler(stream)
    if console_level is None:
        console_level = logging.WARNING if stream.isatty() else logging.INFO
    people.setLevel(console_level)
    people.setFormatter(Console())
    people.addFilter(context)
    root.addHandler(people)

    for name in QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)
    _configured = True


def shutdown() -> None:
    """Send what's still batched for the collector. At the end of a command."""
    if _provider is not None:
        _provider.shutdown()


def reset() -> None:
    """Remove what `setup` installed. For tests."""
    global _configured, _provider
    root = logging.getLogger(ROOT)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    if _provider is not None:
        _provider.shutdown()
        _provider = None
    _configured = False


def event(
    logger: logging.Logger,
    name: str,
    message: str = "",
    *,
    level: int = logging.INFO,
    **attrs: Any,
) -> None:
    """A named event: a record code reads, with its attributes (ADR 0014)."""
    logger.log(level, message or name, extra={"event_name": name, "attrs": attrs})


def code_version() -> dict[str, Any]:
    """The crew's commit, and whether its tree had uncommitted changes. Empty if unknown.

    A tick on 2026-10-01 crashed from mixed code versions, and nothing recorded which
    code ran it (crew#449).
    """
    here = Path(__file__).resolve().parent
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True, check=True
        ).stdout.strip()
        changed = subprocess.run(
            ["git", "status", "--porcelain"], cwd=here, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return {}
    return {"commit": commit, "dirty": bool(changed)}


# The level each of the sink's kinds is mirrored at. Anything not named is INFO.
_LEVELS = {
    "llm.started": logging.DEBUG,
    "llm.finished": logging.DEBUG,
    "task.started": logging.DEBUG,
    "task.completed": logging.DEBUG,
    "tool.started": logging.DEBUG,
    "tool.finished": logging.DEBUG,
    "llm.failed": logging.WARNING,
    "llm.empty": logging.WARNING,
    "task.failed": logging.WARNING,
    "tool.failed": logging.WARNING,
    "card.blocked": logging.WARNING,
    "github.throttled": logging.WARNING,
    "agent.failed": logging.ERROR,
}
# Kinds whose summary is model content (the bridge puts prompt text there): not mirrored.
_CONTENT = frozenset(
    {"llm.started", "llm.finished", "llm.failed", "llm.empty", "task.started", "task.completed"}
)
_mirror = logging.getLogger(f"{ROOT}.events")


def from_event(event: Any) -> None:
    """Mirror one of the sink's events into the stream, as `events.<kind>`.

    Only once `setup` has run: a command or a test that doesn't log sees no change.
    """
    if not _configured:
        return
    kind = getattr(event.kind, "value", str(event.kind))
    detail = dict(event.detail or {})
    if kind in _CONTENT:
        message = f"{kind} {detail.get('for') or ''} {detail.get('model') or ''}".strip()
    else:
        message = event.summary or kind
    attrs = {**detail, **({"card": event.card} if event.card is not None else {})}
    if event.role:
        attrs["role"] = event.role
    level = _LEVELS.get(kind, logging.INFO)
    if detail.get("failure_kind") == "form_refused":
        # The repair loop's normal path, not an outage: logged as a WARNING, it
        # read as the model failing (crew#449, crew#550).
        level = logging.INFO
    _mirror.log(
        level,
        message,
        extra={"event_name": _EVENTS + kind, "attrs": attrs},
    )


def matches(
    record: dict[str, Any],
    *,
    level: str = "INFO",
    card: int | None = None,
    phase: str | None = None,
    event: str | None = None,
) -> bool:
    """Whether a record from `var/logs/crew.jsonl` passes `crew log`'s filters."""
    if logging.getLevelName(record.get("level", "INFO")) < logging.getLevelName(level.upper()):
        return False
    ctx = record.get("ctx") or {}
    attrs = record.get("attrs") or {}
    if card is not None and card not in (ctx.get("card"), attrs.get("card")):
        return False
    if phase is not None and phase not in (ctx.get("phase"), ctx.get("for")):
        return False
    return event is None or (record.get("event") or "").startswith(event)
