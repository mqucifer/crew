"""Event sink feeding the live view and the audit trail.

The crew's own event model is the source of truth. A bridge translates CrewAI's
internal bus onto it, so the TUI and the JSONL record never depend on CrewAI
internals — those event payloads vary across versions, and a live view that
breaks on upgrade is worse than no live view.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class EventKind(StrEnum):
    TICK_STARTED = "tick.started"
    TICK_FINISHED = "tick.finished"

    CARD_CLAIMED = "card.claimed"
    CARD_MOVED = "card.moved"
    CARD_BLOCKED = "card.blocked"

    AGENT_STARTED = "agent.started"
    AGENT_FINISHED = "agent.finished"
    AGENT_FAILED = "agent.failed"

    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"

    LLM_CALL_STARTED = "llm.started"
    LLM_CALL_FINISHED = "llm.finished"
    LLM_CALL_FAILED = "llm.failed"

    TOOL_STARTED = "tool.started"
    TOOL_FINISHED = "tool.finished"
    TOOL_FAILED = "tool.failed"

    # A merged change being undone. Each carries the pull request reverted,
    # the card it belonged to and why, so the audit trail can answer all three.
    REVERT_OPENED = "revert.opened"
    REVERT_REFUSED = "revert.refused"
    REVERT_LANDED = "revert.landed"

    ESCALATION_DECIDED = "escalation.decided"
    ESCALATED = "escalation.sent"

    NOTE = "note"


class CrewEvent(BaseModel):
    """One observable thing that happened. Rendered live, persisted for replay."""

    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: EventKind
    role: str | None = None
    card: int | None = None
    summary: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


Subscriber = Callable[[CrewEvent], None]


class EventSink:
    """Fan-out to live subscribers plus an append-only JSONL record.

    Thread-safe: CrewAI may emit from worker threads.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> None:
        with self._lock:
            self._subscribers.append(fn)

    def emit(self, event: CrewEvent) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(event.model_dump_json() + "\n")
        for fn in subscribers:
            # A broken view must never take down the run that feeds it.
            with contextlib.suppress(Exception):
                fn(event)

    def note(self, kind: EventKind, summary: str, **detail: Any) -> None:
        self.emit(CrewEvent(kind=kind, summary=summary, detail=detail))

    def replay(self) -> list[CrewEvent]:
        """Read back this sink's own log.

        The docstring used to say "used by the standup and by tests". There is
        no standup: the Scrum Master holds `write_standup` and no flow calls
        it. Saying so here was the only record that it was meant to exist.
        `replay_dir` is what reads history across commands.
        """
        if self.path is None or not self.path.exists():
            return []
        out: list[CrewEvent] = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(CrewEvent.model_validate(json.loads(line)))
        return out


def replay_dir(directory: Path) -> list[CrewEvent]:
    """Every event the crew has recorded, across all its logs, oldest first.

    One log per command — `tick.jsonl`, `deliver.jsonl`, `close.jsonl` — so a
    question about the board's history cannot be answered from any single one.
    A card is blocked by `deliver` and read about by `close`.

    Best effort per file: a truncated or hand-edited line is skipped rather
    than losing the whole history to it.
    """
    out: list[CrewEvent] = []
    if not directory.exists():
        return out
    for path in sorted(directory.glob("*.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                with contextlib.suppress(Exception):
                    out.append(CrewEvent.model_validate(json.loads(line)))
    return sorted(out, key=lambda e: e.at)


def blocked_since(events: list[CrewEvent], *, blocked_column: str) -> dict[int, datetime]:
    """When each card that is still blocked entered Blocked, per the log.

    The board says a card *is* blocked and never says since when: an issue's
    `updated` timestamp moves for any comment, so it cannot answer this. The
    move log can — `move_card` records the column a card came from and the one
    it went to.

    A card blocked, freed and blocked again counts from the latest time it was
    blocked, which is what "how long has this been blocked" means to the person
    being asked to deal with it.
    """
    since: dict[int, datetime] = {}
    for event in events:
        card = event.card
        if card is None:
            continue
        detail = event.detail or {}
        if detail.get("to") == blocked_column:
            since[card] = event.at
        elif detail.get("from") == blocked_column and detail.get("to") != blocked_column:
            since.pop(card, None)
    return since


# --- CrewAI bridge -------------------------------------------------------

# CrewAI event payload attributes differ between versions, so every field is
# read defensively and the mapping is data rather than code.
_BRIDGE: dict[str, EventKind] = {
    "TaskStartedEvent": EventKind.TASK_STARTED,
    "TaskCompletedEvent": EventKind.TASK_COMPLETED,
    "TaskFailedEvent": EventKind.TASK_FAILED,
    "LLMCallStartedEvent": EventKind.LLM_CALL_STARTED,
    "LLMCallCompletedEvent": EventKind.LLM_CALL_FINISHED,
    "LLMCallFailedEvent": EventKind.LLM_CALL_FAILED,
    "ToolUsageStartedEvent": EventKind.TOOL_STARTED,
    "ToolUsageFinishedEvent": EventKind.TOOL_FINISHED,
    "ToolUsageErrorEvent": EventKind.TOOL_FAILED,
    "LiteAgentExecutionStartedEvent": EventKind.AGENT_STARTED,
    "LiteAgentExecutionCompletedEvent": EventKind.AGENT_FINISHED,
    "LiteAgentExecutionErrorEvent": EventKind.AGENT_FAILED,
}


def _first_attr(obj: Any, *names: str) -> Any:
    for n in names:
        value = getattr(obj, n, None)
        if value is not None:
            return value
    return None


# What a token count is called, across providers and CrewAI versions. Read
# defensively for the same reason the rest of the bridge is: a live view that
# breaks on upgrade is worse than no live view.
_TOKEN_KEYS = {
    "prompt_tokens": "prompt_tokens",
    "input_tokens": "prompt_tokens",
    "completion_tokens": "completion_tokens",
    "output_tokens": "completion_tokens",
    "total_tokens": "total_tokens",
    "reasoning_tokens": "reasoning_tokens",
    "cached_tokens": "cached_tokens",
}


def _usage(event: Any) -> dict[str, Any]:
    """Token counts off a CrewAI LLM event, normalised and flattened.

    The crew's own event model is the contract, so provider spellings are
    mapped onto one set of names here rather than leaking into the log — and a
    shape nobody anticipated yields nothing rather than an exception.

    `reasoning_tokens` is picked out deliberately: a generation that spends its
    whole budget reasoning and emits no text is the failure mode that hung an
    epic split six times, and it is invisible in a total.
    """
    raw = _first_attr(event, "usage", "token_usage")
    if not isinstance(raw, dict):
        raw = getattr(raw, "__dict__", None) if raw is not None else None
        if not isinstance(raw, dict):
            return {}

    out: dict[str, Any] = {}
    for key, value in raw.items():
        name = _TOKEN_KEYS.get(str(key))
        if name is not None and isinstance(value, int):
            out[name] = value
        elif isinstance(value, dict):
            # Nested details — OpenAI puts reasoning_tokens inside
            # completion_tokens_details, and providers disagree about depth.
            for inner, inner_value in value.items():
                name = _TOKEN_KEYS.get(str(inner))
                if name is not None and isinstance(inner_value, int):
                    out.setdefault(name, inner_value)
    return out


# Where forwarded events go, and whether the handler is on the bus yet. The
# bus is global and a handler registered twice delivers twice, so a command
# that bridges more than one sink must not install a second handler.
_TARGETS: list[tuple[EventSink, int | None]] = []
_INSTALLED = False


def bridged_sinks() -> list[EventSink]:
    """The sinks currently receiving CrewAI's events. For tests."""
    return [sink for sink, _card in _TARGETS]


def reset_bridge() -> None:
    """Forget every target. For tests — the bus handler itself cannot be removed."""
    _TARGETS.clear()


def bridge_crewai(sink: EventSink, *, card: int | None = None) -> None:
    """Forward CrewAI's internal bus onto the crew's sink.

    Nothing called this, so no real run's log has ever held a model call, a
    token count or a tool invocation — `tui.py` defines and renders all four
    kinds and they appeared only in the synthetic demo. The cost of a run was
    unanswerable the moment it ended.

    Best-effort by design: if CrewAI changes its bus, the live view degrades to
    the crew's own events rather than crashing the tick.

    Idempotent. Calling it again adds a target; the handler is installed once,
    because the bus is global and registering twice delivers twice.
    """
    global _INSTALLED  # noqa: PLW0603

    if not any(existing is sink for existing, _ in _TARGETS):
        _TARGETS.append((sink, card))
    if _INSTALLED:
        return

    try:
        from crewai.events import crewai_event_bus  # noqa: PLC0415
        from crewai.events.base_events import BaseEvent  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        sink.note(EventKind.NOTE, "CrewAI event bus unavailable; live view uses crew events only.")
        return

    @crewai_event_bus.on(BaseEvent)
    def _forward(_source: Any, event: Any) -> None:  # pragma: no cover - needs a live crew
        kind = _BRIDGE.get(type(event).__name__)
        if kind is None:
            return
        role = _first_attr(event, "role", "agent_role", "from_agent")
        name = _first_attr(event, "task_name", "tool_name", "model", "description") or ""

        # What the call cost and what it did, not just that it happened. The
        # log had no tokens, no model and no tool names, so the cost of a run
        # was unanswerable after the fact and a generation that burned its
        # budget looked the same as one that answered.
        detail: dict[str, Any] = {}
        for field, value in (
            ("model", _first_attr(event, "model")),
            ("tool", _first_attr(event, "tool_name")),
            ("finish_reason", _first_attr(event, "finish_reason")),
        ):
            if value is not None:
                detail[field] = str(value)[:120]
        detail.update(_usage(event))

        for target, target_card in list(_TARGETS):
            target.emit(
                CrewEvent(
                    kind=kind,
                    role=str(role) if role else None,
                    card=target_card,
                    summary=str(name)[:120],
                    detail=detail,
                )
            )

    _INSTALLED = True
