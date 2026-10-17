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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
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
    # A move the crew didn't make, seen at the start of a pass (crew#521): a
    # person's, or the board workflow's. Its own kind, so nothing that reads
    # `card.moved` as the crew's own takes it for one.
    CARD_SEEN_MOVED = "card.seen_moved"

    AGENT_STARTED = "agent.started"
    AGENT_FINISHED = "agent.finished"
    AGENT_FAILED = "agent.failed"

    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"

    LLM_CALL_STARTED = "llm.started"
    LLM_CALL_FINISHED = "llm.finished"
    LLM_CALL_FAILED = "llm.failed"
    # A call that answered nothing, typically after thinking (#312). CrewAI
    # retries it silently, so without this it's invisible in the log.
    LLM_CALL_EMPTY = "llm.empty"
    # A model's answer its form refused, with the reason the next attempt is told
    # (crew#583, ADR 0025). Recorded by the crew's own call layer, `calls.py`.
    LLM_CALL_REFUSED = "llm.refused"

    TOOL_STARTED = "tool.started"
    TOOL_FINISHED = "tool.finished"
    TOOL_FAILED = "tool.failed"

    # A merged change being undone. Each carries the pull request reverted,
    # the card it belonged to and why, so the audit trail can answer all three.
    REVERT_OPENED = "revert.opened"
    REVERT_REFUSED = "revert.refused"
    REVERT_LANDED = "revert.landed"

    # The retro, recorded as an issue, and each defect it filed (#50).
    RETRO_RECORDED = "retro.recorded"
    STANDUP_WRITTEN = "standup.written"
    DEFECT_FILED = "defect.filed"

    ESCALATION_DECIDED = "escalation.decided"
    ESCALATED = "escalation.sent"

    # Breaking a loop, step by step (#189, #242): a story sent back because its
    # failures were the story's, the Product Owner deciding or asking, and the
    # epic split again with that to read. Each its own kind, so the chain can be
    # found, counted and replayed.
    STORY_RETURNED = "story.returned"
    PRODUCT_ANSWERED = "product.answered"
    PRODUCT_ASKED = "product.asked"
    EPIC_RESPLIT = "epic.resplit"

    # GitHub asked the crew to slow down (#293): waited out, or the tick stopped.
    GITHUB_THROTTLED = "github.throttled"

    # What a role was shown, and the files it asked to see (#231, crew#449). They
    # were notes with no role or card, matched to their run by time (discussion 553).
    FILES_SHOWN = "files.shown"
    FILES_ASKED = "files.asked"
    # A quote or a block name matched by a fallback the strict rule would refuse
    # (crew#734): which one, and where, so each is counted and reviewed.
    EDIT_LOOSE_MATCH = "edit.loose_match"

    # A crew issue and the cards it's about (crew#449, discussion 552): the link
    # from a run's trouble to the issue it led to, as a field, whoever filed it.
    ISSUE_CITES = "issue.cites"
    # A bare number left unlinked because no such issue exists, and a comment
    # from an account outside `trust` that was ignored (crew#456).
    REFERENCE_UNRESOLVED = "reference.unresolved"
    COMMENT_IGNORED = "comment.ignored"

    NOTE = "note"


class CrewEvent(BaseModel):
    """One observable thing that happened. Rendered live, persisted for replay."""

    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: EventKind
    role: str | None = None
    card: int | None = None
    summary: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    # The common context when it was written (crew#449, discussion 552): tick, commit,
    # pass, phase, run, role, card, repo, sprint, attempt, trace and span. Stamped by
    # the sink, so no call site repeats it, and a line written before it existed
    # still reads, with none.
    ctx: dict[str, Any] = Field(default_factory=dict)


def _named(event: CrewEvent) -> CrewEvent:
    """The event, with its failure's kind and a return's rule as names (crew#449, crew#550).

    Derived here from what the event already says, so every path that writes one
    is covered without each call site restating it. One its site named is kept.
    """
    from crew_org import rules  # noqa: PLC0415

    detail = dict(event.detail or {})
    kind = event.kind.value
    found = rules.kind_of(kind, event.summary, detail)
    if found is not None:
        detail.setdefault("failure_kind", found.value)
    if kind == "story.returned" and (rule := rules.RETURN_RULES.get(str(detail.get("reason")))):
        detail.setdefault("rule", rule.value)
    return event if detail == event.detail else event.model_copy(update={"detail": detail})


def repo_of(event: CrewEvent) -> str | None:
    """The event's repository: where it was written, or where its detail says."""
    return event.ctx.get("repo") or (event.detail or {}).get("repo")


Subscriber = Callable[[CrewEvent], None]


class EventSink:
    """Fan-out to live subscribers plus an append-only JSONL record.

    Thread-safe: CrewAI may emit from worker threads.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        # Beside `var/events`, attributes only, for the Sponsor's Alloy (#283).
        from crew_org import telemetry  # noqa: PLC0415

        self.telemetry = telemetry.path_for(path)
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> None:
        with self._lock:
            self._subscribers.append(fn)

    def emit(self, event: CrewEvent) -> None:
        if not event.ctx:
            # Never at the cost of the work: an event without its context still lands.
            with contextlib.suppress(Exception):
                from crew_org import log  # noqa: PLC0415

                event = event.model_copy(update={"ctx": log.scope()})
        with contextlib.suppress(Exception):
            event = _named(event)
        with self._lock:
            subscribers = list(self._subscribers)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(event.model_dump_json() + "\n")
            if self.telemetry is not None:
                # Never at the cost of the work: telemetry that can't be
                # written is skipped, and `var/events` still has it all.
                with contextlib.suppress(Exception):
                    from crew_org import telemetry  # noqa: PLC0415

                    self.telemetry.parent.mkdir(parents=True, exist_ok=True)
                    with self.telemetry.open("a", encoding="utf-8") as fh:
                        fh.write(telemetry.line(event))
        # One levelled stream (crew#449): the event, mirrored into the crew's log.
        with contextlib.suppress(Exception):
            from crew_org import log  # noqa: PLC0415

            log.from_event(event)
        for fn in subscribers:
            # A broken view must never take down the run that feeds it.
            with contextlib.suppress(Exception):
                fn(event)

    def note(self, kind: EventKind, summary: str, **detail: Any) -> None:
        self.emit(CrewEvent(kind=kind, summary=summary, detail=detail))

    def replay(self) -> list[CrewEvent]:
        """Read back this sink's own log.

        The docstring once said "used by the standup and by tests" when there
        was no standup. There is one now (#79), and it reads the tick's result,
        not this log. `replay_dir` is what reads history across commands.
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


# --- What the crew is working on -----------------------------------------

# The card, repository, sprint and attempt a model call is for, or what it's
# for when there's no card (#179). The llm.* events had the role and nothing
# else, so token use couldn't be joined to an outcome. A context variable,
# because CrewAI copies the caller's context onto the thread that delivers its
# events, and the crew makes its calls on one thread.
_WORKING: ContextVar[dict[str, Any]] = ContextVar("crew_working_on", default={})  # noqa: B039
_WORKING_KEYS = ("card", "repo", "sprint", "attempt", "for")


@contextmanager
def working_on(
    *,
    card: int | None = None,
    repo: str | None = None,
    sprint: str | None = None,
    attempt: int | None = None,
    purpose: str | None = None,
) -> Iterator[None]:
    """Attribute every model call made inside this block. Nested blocks add to the outer one."""
    given = {"card": card, "repo": repo, "sprint": sprint, "attempt": attempt, "for": purpose}
    token = _WORKING.set({**_WORKING.get(), **{k: v for k, v in given.items() if v is not None}})
    try:
        yield
    finally:
        _WORKING.reset(token)


def attributed(fn: Callable[..., Any], **what: Any) -> Callable[..., Any]:
    """`fn`, with every model call it makes attributed as `working_on(**what)` says."""

    def call(*args: Any, **kwargs: Any) -> Any:
        from crew_org import tracing  # noqa: PLC0415

        with working_on(**what), _own_run():
            about = working()
            # A span for the phase, or for the card within it (#283): the
            # same attributes the model calls carry, and no content.
            name = (
                f"#{about['card']} {about.get('for', '')}".strip()
                if "card" in what and what["card"] is not None
                else str(about.get("for") or "crew")
            )
            with tracing.span(name, **{f"crew.{k}": v for k, v in about.items()}):
                return fn(*args, **kwargs)

    return call


def _own_run() -> contextlib.AbstractContextManager[Any]:
    """A model call made outside any role's run is a run of its own (crew#449)."""
    from crew_org import log  # noqa: PLC0415

    return contextlib.nullcontext() if log.in_run() else log.scoped(run=log.new_id())


def working() -> dict[str, Any]:
    """What the current model calls are for. For tests, and `record`."""
    return dict(_WORKING.get())


# --- where model calls are recorded --------------------------------------

# The sinks every model call's events go to (`record`), each with the card it stands
# for when the call itself doesn't say. The crew's own call layer writes them (ADR
# 0025); CrewAI's bus, which this once forwarded, left with CrewAI (crew#583).
_TARGETS: list[tuple[EventSink, int | None]] = []


def forward_calls_to(sink: EventSink, *, card: int | None = None) -> None:
    """Send every model call's events to `sink` too. Idempotent per sink."""
    if not any(existing is sink for existing, _ in _TARGETS):
        _TARGETS.append((sink, card))


def bridged_sinks() -> list[EventSink]:
    """The sinks currently receiving model calls' events. For tests."""
    return [sink for sink, _card in _TARGETS]


def reset_bridge() -> None:
    """Forget every target. For tests."""
    _TARGETS.clear()


def record(kind: EventKind, summary: str, *, role: str | None = None, **detail: Any) -> None:
    """An event from outside a flow (the model client), to every sink calls go to."""
    about = working()
    detail.update({k: about[k] for k in _WORKING_KEYS if k in about and k != "card"})
    for target, target_card in list(_TARGETS):
        target.emit(
            CrewEvent(
                kind=kind,
                role=role or about.get("role"),
                card=about.get("card", target_card),
                summary=summary[:120],
                detail=detail,
            )
        )
