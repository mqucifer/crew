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
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class EventKind(StrEnum):
    """Every kind of event, each with its meaning under it (crew#870)."""

    TICK_STARTED = "tick.started"
    """A tick began: the board read, and the column counts at its start."""
    TICK_FINISHED = "tick.finished"
    """A tick ended: what each phase did, and the holds and skips it decided."""

    CARD_CLAIMED = "card.claimed"
    """A role took a card to work on."""
    CARD_MOVED = "card.moved"
    """The crew moved a card between columns, and by which rule where one decided."""
    CARD_BLOCKED = "card.blocked"
    """The crew moved a card to Blocked, for a person or the Sponsor."""
    # A move the crew didn't make, seen at the start of a pass (crew#521): a
    # person's, or the board workflow's. Its own kind, so nothing that reads
    # `card.moved` as the crew's own takes it for one.
    CARD_SEEN_MOVED = "card.seen_moved"
    """A move the crew didn't make, seen at a pass's start: a person's, or the workflow's."""

    AGENT_STARTED = "agent.started"
    """A role began a step's work on a card or pull request."""
    AGENT_FINISHED = "agent.finished"
    """A role finished a step: its verdict, as flags and counts."""
    AGENT_FAILED = "agent.failed"
    """A role's step failed before it finished: the call, the form, or the code around it."""

    TASK_STARTED = "task.started"
    """A step began, as CrewAI's bus recorded it before the crew's own calls (ADR 0025)."""
    TASK_COMPLETED = "task.completed"
    """A step completed, as CrewAI's bus recorded it before the crew's own calls."""
    TASK_FAILED = "task.failed"
    """A step failed, as CrewAI's bus recorded it before the crew's own calls."""

    LLM_CALL_STARTED = "llm.started"
    """A model call was sent: which model, which prompt and form, and the sizes of its parts."""
    LLM_CALL_FINISHED = "llm.finished"
    """A model call answered: why it stopped, its tokens and its time."""
    LLM_CALL_FAILED = "llm.failed"
    """A model call didn't return: a connection, a timeout, a server error."""
    # A call that answered nothing, typically after thinking (#312). CrewAI
    # retries it silently, so without this it's invisible in the log.
    LLM_CALL_EMPTY = "llm.empty"
    """A model call answered nothing, typically after thinking (#312)."""
    # A model's answer its form refused, with the reason the next attempt is told
    # (crew#583, ADR 0025). Recorded by the crew's own call layer, `calls.py`.
    LLM_CALL_REFUSED = "llm.refused"
    """A model's answer its form refused, with the reason the next attempt is told (ADR 0025)."""

    TOOL_STARTED = "tool.started"
    """A role's tool call began."""
    TOOL_FINISHED = "tool.finished"
    """A role's tool call finished: what it was, its sizes, and how it ended."""
    TOOL_FAILED = "tool.failed"
    """A role's tool call raised, and what the session was told."""

    # The Developer's session (crew#778, P5): what it started with, its signals after
    # each call, its compaction, and how it ended. Each tool call is a `tool.finished`.
    SESSION_STARTED = "session.started"
    """A Developer session began: its start's sizes and its limits."""
    SESSION_TURN = "session.turn"
    """One model call of a session, with its signals after it."""
    SESSION_COMPACTED = "session.compacted"
    """A session's history compacted to fit, and what it left."""
    SESSION_ENDED = "session.ended"
    """A session ended: how, why, and its totals."""
    # The gates' verdict on a session's branch at `done`: whether it passed, and the
    # rules of what sent it back.
    GATE_CHECKED = "gate.checked"
    """The gates' verdict on a session's branch at `done`, and the rules of what sent it back."""

    # A merged change being undone. Each carries the pull request reverted,
    # the card it belonged to and why, so the audit trail can answer all three.
    REVERT_OPENED = "revert.opened"
    """A revert pull request was opened for a merged change."""
    REVERT_REFUSED = "revert.refused"
    """A revert couldn't be made or landed, and why."""
    REVERT_LANDED = "revert.landed"
    """A revert landed: its card goes back to Needs Refinement."""

    # The retro, recorded as an issue, and each defect it filed (#50).
    RETRO_RECORDED = "retro.recorded"
    """The sprint's retro was recorded as an issue, with the defects it filed."""
    STANDUP_WRITTEN = "standup.written"
    """A standup was written for a project's sprint."""
    DEFECT_FILED = "defect.filed"
    """The retro filed a defect, in the repository of what it found."""

    # The one-call Developer's retries and escalations, written until it retired
    # (crew#778). Nothing writes them now; past logs still hold them, and still read.
    ESCALATION_DECIDED = "escalation.decided"
    """The one-call Developer's retry or escalation decision (crew#778)."""
    ESCALATED = "escalation.sent"
    """The one-call Developer's work escalated to the subscription (crew#778)."""

    # Breaking a loop, step by step (#189, #242): a story sent back because its
    # failures were the story's, the Product Owner deciding or asking, and the
    # epic split again with that to read. Each its own kind, so the chain can be
    # found, counted and replayed.
    STORY_RETURNED = "story.returned"
    """A story sent back to refinement, by its reason from a fixed list."""
    PRODUCT_ANSWERED = "product.answered"
    """The Product Owner answered a story problem, as rows of the record."""
    PRODUCT_ASKED = "product.asked"
    """The Product Owner asked the Sponsor one question instead of deciding."""
    EPIC_RESPLIT = "epic.resplit"
    """An epic split again: the stories it replaced and kept, why, and its round."""
    # An epic re-split as often as `refinement.resplit_stop` allows: its next problem
    # goes to the Sponsor as one question (crew#859).
    EPIC_RESPLIT_STOPPED = "epic.resplit_stopped"
    """An epic re-split as often as its stop allows: its next problem goes to the Sponsor."""
    # Any step's finished work sent back to be done again, in one shape (crew#862): from
    # which step, to which, by which rule, with its round and counts. `sendbacks.py`
    # declares each; the events above still record their own parts beside it.
    WORK_SENT_BACK = "work.sent_back"
    """Finished work sent back to its own step or an earlier one: see Send-backs above."""
    # A Goal the Sponsor sent back: its unstarted epics superseded, decomposed again.
    GOAL_REWORKED = "goal.reworked"
    """A Goal the Sponsor sent back: its unstarted epics superseded, decomposed again."""

    # GitHub asked the crew to slow down (#293): waited out, or the tick stopped.
    GITHUB_THROTTLED = "github.throttled"
    """GitHub asked the crew to slow down: waited out, or the tick stopped."""

    # What a role was shown, and the files it asked to see (#231, crew#449). They
    # were notes with no role or card, matched to their run by time (discussion 553).
    FILES_SHOWN = "files.shown"
    """The files a role was shown for a step (#231)."""
    FILES_ASKED = "files.asked"
    """The files a role asked to see (#231)."""
    # A quote or a block name matched by a fallback the strict rule would refuse
    # (crew#734). Written by the one-call Developer's edits until they retired
    # (crew#778); past logs still hold them.
    EDIT_LOOSE_MATCH = "edit.loose_match"
    """An edit matched by a fallback the strict rule would refuse (crew#734)."""
    # What the project's own fixers changed before VERIFY (crew#758): the files,
    # the changes outside the attempt undone, and each allowed unsafe fix.
    AUTOFIX_APPLIED = "autofix.applied"
    """What the project's own fixers changed before the checks, and what was undone."""

    # A crew issue and the cards it's about (crew#449, discussion 552): the link
    # from a run's trouble to the issue it led to, as a field, whoever filed it.
    ISSUE_CITES = "issue.cites"
    """A crew issue and the cards it cites, whoever filed it."""
    # A bare number left unlinked because no such issue exists, and a comment
    # from an account outside `trust` that was ignored (crew#456).
    REFERENCE_UNRESOLVED = "reference.unresolved"
    """A bare number left unlinked because no such issue exists."""
    COMMENT_IGNORED = "comment.ignored"
    """A comment from an account outside `trust`, ignored."""

    # A role's own Task (ADR 0034, crew#839): opened by an event or a finding, and
    # held because its role has no session yet. With the send-backs, they measure
    # the board's queue.
    ROLE_TASK_OPENED = "role_task.opened"
    """A role's own Task, opened by an event or a finding (ADR 0034)."""
    ROLE_TASK_HELD = "role_task.held"
    """A role's Task held because its role has no session yet."""
    # A role's finding outside the story it was working on, filed as a Task or a bug.
    FINDING_FILED = "finding.filed"
    """A role's finding outside its story, filed as a Task or a bug."""

    NOTE = "note"
    """Anything else a flow records, by the rule it names where it names one."""


# The detail fields each kind carries, by name (crew#870). Generated into the records
# reference; a test holds every field a site writes by name to its kind's list here.
FIELDS: dict[EventKind, tuple[str, ...]] = {
    EventKind.TICK_STARTED: ("tick", "sprint", "counts", "github_remaining"),
    EventKind.TICK_FINISHED: ("held",),
    EventKind.CARD_CLAIMED: (),
    EventKind.CARD_MOVED: ("from", "to", "repo", "rule", "finding", "rebuilt"),
    EventKind.CARD_BLOCKED: ("from", "to", "repo", "rule"),
    EventKind.CARD_SEEN_MOVED: ("from", "to", "repo", "by"),
    EventKind.AGENT_STARTED: (
        "repo",
        "pr",
        "stories",
        "evidence",
        "trigger",
        "matched",
        "devops_task",
        "task_role",
    ),
    EventKind.AGENT_FINISHED: (
        "repo",
        "pr",
        "rule",
        "task_role",
        "approved",
        "accepted",
        "notes",
        "findings",
        "unproven",
        "unproven_numbers",
        "url",
    ),
    EventKind.AGENT_FAILED: ("repo", "pr", "rule"),
    EventKind.TASK_STARTED: (),
    EventKind.TASK_COMPLETED: (),
    EventKind.TASK_FAILED: ("error",),
    EventKind.LLM_CALL_STARTED: (
        "model",
        "call_id",
        "try",
        "round",
        "prompt_hash",
        "output_schema",
        "given_rules",
        "given_hash",
        "parts",
    ),
    EventKind.LLM_CALL_FINISHED: (
        "model",
        "call_id",
        "try",
        "round",
        "prompt_hash",
        "output_schema",
        "finish_reason",
        "response_id",
        "duration_s",
        "tool_calls",
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "total_tokens",
    ),
    EventKind.LLM_CALL_FAILED: (
        "model",
        "call_id",
        "try",
        "round",
        "prompt_hash",
        "output_schema",
        "error",
        "duration_s",
    ),
    EventKind.LLM_CALL_EMPTY: (),
    EventKind.LLM_CALL_REFUSED: (
        "model",
        "call_id",
        "try",
        "reason",
        "empty",
        "rules",
        "finish_reason",
        "response_id",
        "duration_s",
    ),
    EventKind.TOOL_STARTED: ("tool",),
    EventKind.TOOL_FINISHED: (
        "tool",
        "round",
        "turn",
        "chars",
        "result",
        "rule",
        "rules",
        "counts",
        "step",
        "args_chars",
        "reply_chars",
        "ran_s",
        "found_nothing",
    ),
    EventKind.TOOL_FAILED: ("tool", "step", "turn", "error"),
    EventKind.SESSION_STARTED: (
        "step",
        "head",
        "round",
        "base",
        "story_parts",
        "start_chars",
        "limits",
        # Whether `skill` was offered, for the count of who reads one (crew#839).
        "skill_offered",
    ),
    EventKind.SESSION_TURN: (
        "turn",
        "tool_calls",
        "model_s",
        "since_action",
        "most_views",
        "edits_since_run",
        "empty_lookups",
        "reasoning_kept_chars",
        "reasoning_kept_tokens",
    ),
    EventKind.SESSION_COMPACTED: (
        "compaction",
        "chars_after",
        "estimated_tokens",
        "estimated_after",
        "turns_masked",
        "turns_newly_masked",
        "reasoning_chars_cleared",
        "reasoning_tokens_cleared",
    ),
    EventKind.SESSION_ENDED: (
        "how",
        "why",
        "stop",
        "said",
        "evidence",
        "model_calls",
        "model_s",
        "edits_applied",
        "test_runs",
        "tools_used",
        "compactions",
        "troubles",
        "after_run_errors",
        "resetups",
        "resetups_failed",
    ),
    EventKind.GATE_CHECKED: (
        "repo",
        "passed",
        "rules",
        "already_done",
        "resetups",
        "resetups_failed",
        "turned_on",
        "hang_rounds",
        "hung",
    ),
    EventKind.REVERT_OPENED: ("repo", "pr", "reverted", "card", "reason"),
    EventKind.REVERT_REFUSED: ("repo", "pr", "reverted", "card", "reason", "why", "conflict"),
    EventKind.REVERT_LANDED: ("repo", "pr", "reverted", "card"),
    EventKind.RETRO_RECORDED: ("repo", "sprint", "filed", "failed", "explained"),
    EventKind.STANDUP_WRITTEN: ("repo", "sprint", "quiet"),
    EventKind.DEFECT_FILED: ("repo", "sprint", "about", "cards", "cause", "failure_class"),
    EventKind.ESCALATION_DECIDED: ("failure_class", "attempt", "error"),
    EventKind.ESCALATED: (),
    EventKind.STORY_RETURNED: ("repo", "epic", "reason", "with", "kind", "stop"),
    EventKind.PRODUCT_ANSWERED: ("repo", "answer", "based_on", "story", "rule"),
    EventKind.PRODUCT_ASKED: ("repo", "question", "step", "rule", "proposed_goal"),
    EventKind.EPIC_RESPLIT: (
        "repo",
        "superseded",
        "kept",
        "named",
        "positions",
        "of",
        "because",
        "reads_decision",
        "round",
        "rule",
    ),
    EventKind.EPIC_RESPLIT_STOPPED: ("repo", "round", "stop", "rule"),
    EventKind.GOAL_REWORKED: ("repo", "superseded", "because"),
    EventKind.GITHUB_THROTTLED: ("wait_s", "stopped", "status", "resource", "path"),
    EventKind.FILES_SHOWN: (),
    EventKind.FILES_ASKED: (),
    EventKind.EDIT_LOOSE_MATCH: (),
    EventKind.AUTOFIX_APPLIED: ("fixed", "reverted", "rules", "unsafe"),
    EventKind.ISSUE_CITES: ("repo", "cites", "labels", "state", "state_reason", "opened", "closed"),
    EventKind.REFERENCE_UNRESOLVED: ("repo", "number"),
    EventKind.COMMENT_IGNORED: ("repo", "login"),
    EventKind.ROLE_TASK_OPENED: ("repo", "task_role", "task_kind", "parent", "event", "rule"),
    EventKind.ROLE_TASK_HELD: ("repo", "task_role", "waits_for", "rule"),
    EventKind.FINDING_FILED: ("repo", "owner", "filed", "round", "error", "counts"),
    EventKind.WORK_SENT_BACK: ("repo", "sent_from", "sent_to", "rule", "round", "counts"),
    EventKind.NOTE: ("rule", "repo", "card"),
}


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
        # The card is the event's, as `emit` takes it: kept only in the detail, a count
        # by card missed every note (crew#756). The detail keeps it too, for old readers.
        card = detail.get("card")
        self.emit(
            CrewEvent(
                kind=kind,
                card=card if isinstance(card, int) else None,
                summary=summary,
                detail=detail,
            )
        )

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


# The holds and skips a tick decides that write no event of their own, named for its
# `tick.finished` as {rule, repo, card} (crew#756): a count by rule finds them, and the
# selection filters, which decide nothing worth counting, stay out.
_HELD: ContextVar[list[dict[str, Any]] | None] = ContextVar("crew_held", default=None)  # noqa: B039


HOLDS_SEEN = "holds-seen.json"


def first_hold(sink: EventSink, *, sprint: str, rule: str, repo: str, card: int | None) -> bool:
    """Whether this hold on this card is new in this sprint, which is then remembered.

    A hold was noted on every pass while it lasted: `hold.sibling` wrote 333 notes
    on 55 cards in a month (crew#756). Noted once, it still counts on every tick's
    `tick.finished`. A sink with no log has nowhere to remember, so each hold is new.
    """
    if sink.path is None:
        return True
    path = sink.path.parent / HOLDS_SEEN
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    seen = state.get("seen") if isinstance(state, dict) and state.get("sprint") == sprint else None
    seen = seen if isinstance(seen, list) else []
    key = f"{rule} {repo}#{card}"
    if key in seen:
        return False
    with contextlib.suppress(OSError):
        path.write_text(json.dumps({"sprint": sprint, "seen": [*seen, key]}), encoding="utf-8")
    return True


def held(rule: str, *, repo: str | None = None, card: int | None = None) -> None:
    """Name a hold or a skip for the tick's end, once per card and rule."""
    found = _HELD.get()
    entry = {"rule": rule, "repo": repo, "card": card}
    if found is not None and entry not in found:
        found.append(entry)


def holding() -> Token[list[dict[str, Any]] | None]:
    """Start collecting the tick's holds; `held_so_far` ends it."""
    return _HELD.set([])


def held_so_far(token: Token[list[dict[str, Any]] | None]) -> list[dict[str, Any]]:
    found = _HELD.get() or []
    _HELD.reset(token)
    return found


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
