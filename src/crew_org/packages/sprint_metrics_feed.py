"""The crew's history as sprint-metrics' events, and pushing them to the service (crew#280).

sprint-metrics keeps and computes, the crew adds and packages (ADR 0022). So the
feed is built from delivery-history, the crew's allow-listed record, not from the
raw events: what may leave the crew is already decided there (ADR 0020), and the
feed takes only measures, names and states from it.

Stories and bugs only (ADR 0027): a Goal or an Epic moving to Done isn't delivered work. Every event
goes to the sprint whose days it falls in; an event between sprints, or on a card
the history doesn't know, is left out and counted, never guessed.

What a story's events become:
- moved to In Progress: `started`;
- blocked by the crew, or moved to Blocked: `blocked` (once, when both happen);
- moved out of Blocked: `unblocked`;
- an attempt that didn't land: `attempt_failed`, with its class, its role when one
  returned it, and its number when the log has one;
- escalated: `escalated`;
- moved to Done: `finished`, with the class and role of its first miss: why it
  didn't land first time.

**The feed logs what happened and sends no counts** (the Sponsor, 2026-10-30,
crew#963): sprint-metrics keeps and computes (ADR 0022), so it decides what first
try means, from the returns it's sent. `finished` carries no `attempts`.

A story delivered in Developer sessions (crew#778) misses each time its handed-on
work comes back, whoever sent it back and why: a review or QA verdict (`REVIEW`,
`QA`), CI failing before review or at merge (`CI`), a conflict with main (`CONFLICT`),
changes requested at merge (`REVIEW`), or its merged change reverted (`REVERT`). Each
send-back writes its own event beside the verdict (crew#862), so a verdict and a
send-back from the same step moments apart are one return. The gate's returns before
review aren't misses (the Sponsor, 2026-10-18), nor is the Developer's own stop: the
crew counts those for its own blocks. Stories from before sessions are sent as they
always were: escalation's attempts were their misses.

A story closed as not planned was superseded, not finished: its move to Done is
sent as `superseded` (sprint-metrics 1.3.0), with `because`, the reason its epic
was split again. One with no reason recorded is left out and counted, never
guessed. `replaced_by` isn't sent: the crew doesn't record which stories replaced
it.

**Every card also sends one `filed` event** (sprint-metrics 4.3.0, crew#985): who
filed it, what opened it and the project it's for, as facts from `Card.filed`. This
is every card, not only stories: a Goal or a QA Task isn't delivered work, but it
was filed. Its time is when the card was created, and its sprint the one whose days
that falls in, wherever the card was later planned. A card filed outside every sprint
is left out and counted, and what isn't known is left off the event: never guessed.

**Every session goes as a `session` event** (sprint-metrics 4.5.0, crew#1001): one
role's one go at one card or none, its start and end, and its model calls' time and
tokens. sprint-metrics adds them up, and tells planning from building by the story's
first `started`: the crew sends no totals (the Sponsor, 2026-11-01). What makes a session,
and which calls are its, is `session_shape`'s; a session that can't be placed, or has no
recorded finish, is left out and counted, never sent as zero. Each event of a story
carries its `epic`, so an epic's sessions add up from its stories'.

Pushing is safe to repeat: the service stores an event once by card, type and
time, a session once by card, role, start and end, and updates a sprint registered
again in place.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, get_args
from zoneinfo import ZoneInfo

from crew_org import columns, sendbacks
from crew_org.flows.attempts import QA, REVIEW
from crew_org.packages import session_shape
from crew_org.packages.delivery_history import (
    REVIEWERS,
    Card,
    CardRef,
    DeliveryHistory,
    FiledActivity,
    ModelCall,
    PeriodEvents,
    SentBack,
)
from crew_org.sendbacks import Step
from crew_org.work_types import DELIVERED

API_VERSION = "1"
# Seen at the same card within this many seconds, a block and a move to Blocked
# are one block: the crew blocks a card by moving it there.
SAME_BLOCK_SECONDS = 120
# Within this many seconds at the same card, a verdict and a send-back of its class are
# one return: the step writes both as it sends the card back. Two send-backs are two.
SAME_RETURN_SECONDS = 120

# The steps that send a story's handed-on work back: a return from any of them is a
# miss. The gate's come before review, and the build step's are the Developer's own.
RETURNING = {Step.CI, Step.REVIEW, Step.DEPLOY_REVIEW, Step.QA, Step.MERGE, Step.REVERT}
# A return's class is its step's name, except where the rule says more.
_CLASS_BY_RULE = {
    str(sendbacks.MERGE_CI_FAILED.rule): "CI",
    str(sendbacks.MERGE_CONFLICT.rule): "CONFLICT",
    str(sendbacks.MERGE_CHANGES_REQUESTED.rule): REVIEW,
    str(sendbacks.DEPLOY_REVIEW_CHANGES.rule): REVIEW,
}
# Who sent it back, where one role always does; at merge and in CI, none does.
_ROLE_BY_STEP = {
    Step.REVIEW: "Code Reviewer",
    Step.DEPLOY_REVIEW: "DevOps Engineer",
    Step.QA: "QA Engineer",
}
# What opened a card, in sprint-metrics' three kinds: a role's finding, a split of filed
# work, and the crew's own process for the rest.
_OPENER: dict[FiledActivity, str] = {
    "finding": "found",
    "split": "split",
    "task": "process",
    "technical_epic": "process",
    "proposed_goal": "process",
    "skeleton_goal": "process",
    "record_change": "process",
    "retro": "process",
    "standup": "process",
}
# The roles whose work is a session. A group's work event, a "Refinement panel" or a
# "Goal look", spans its members' sessions, which carry their own roles.
SESSION_ROLES = frozenset(get_args(session_shape.Role))


@dataclass
class Feed:
    """What goes to the service: sprints first, then events, oldest first."""

    sprints: list[dict[str, Any]]
    events: list[dict[str, Any]]
    left_out: Counter[str] = field(default_factory=Counter)


def feed(history: DeliveryHistory, periods: Iterable[PeriodEvents]) -> Feed:
    """The history as sprint-metrics' sprint definitions and board events."""
    periods = list(periods)
    cards = {(c.repo, c.number): c for c in history.cards}
    epics = _epics(history.cards)
    in_sessions = {
        (e.card.repo, e.card.number)
        for period in periods
        for e in period.events
        if e.kind in ("session_started", "session_ended")
    }
    left_out: Counter[str] = Counter()
    sprints = [
        {
            "name": s.name,
            "start_date": s.start.isoformat(),
            "end_date": s.end.isoformat(),
            "timezone": history.timezone,
        }
        for s in history.sprints
    ]
    stories: list[tuple[str, Any]] = []
    for period in periods:
        for event in period.events:
            ref = getattr(event, "card", None)
            if event.kind in ("model_call", "work_started", "pull_merged"):
                continue
            if event.kind == "work_finished" and not _review_return(event):
                continue
            if period.sprint is None:
                left_out["between sprints"] += 1
                continue
            card = cards.get((ref.repo, ref.number)) if ref is not None else None
            if card is None:
                left_out["card not in the history"] += 1
                continue
            if card.type not in DELIVERED:
                continue
            if card.created is None:
                left_out["story with no created date"] += 1
                continue
            stories.append((period.sprint, event))
    stories.sort(key=lambda pair: pair[1].at)

    events: list[dict[str, Any]] = []
    misses: dict[tuple[str, int], list[Any]] = {}
    blocks: dict[tuple[str, int], list[Any]] = {}
    for sprint, event in stories:
        key = (event.card.repo, event.card.number)
        card = cards[key]
        sessions = key in in_sessions
        kind = _kind(event, sessions)
        if kind is None:
            continue
        if (
            kind == "attempt_failed"
            and sessions
            and any(
                m.kind != event.kind
                and _failure_class(m) == _failure_class(event)
                and abs((event.at - m.at).total_seconds()) <= SAME_RETURN_SECONDS
                for m in misses.get(key, [])
            )
        ):
            continue
        if kind == "blocked":
            if any(
                abs((event.at - b).total_seconds()) <= SAME_BLOCK_SECONDS
                for b in blocks.get(key, [])
            ):
                continue
            blocks.setdefault(key, []).append(event.at)
        if kind == "finished" and card.closed_as == "not_planned":
            if card.superseded_because is None:
                left_out["superseded with no reason recorded"] += 1
                continue
            kind = "superseded"
        body = _event(card, kind, sprint, event.at.isoformat(), epics.get(key))
        if kind == "superseded":
            body["because"] = card.superseded_because
        if kind == "attempt_failed":
            misses.setdefault(key, []).append(event)
            if event.kind == "attempt" and event.number:
                body["attempt_number"] = event.number
            body["failure_class"] = _failure_class(event)
            if _failure_role(event):
                body["failure_role"] = _failure_role(event)
        if kind == "finished":
            missed = misses.get(key, [])
            if missed:
                body["card"]["failure_class"] = _failure_class(missed[0])
                if _failure_role(missed[0]):
                    body["card"]["failure_role"] = _failure_role(missed[0])
        events.append(body)
    events = sorted(
        [*_filed_events(history, left_out, epics), *events, *_sessions(history, periods, left_out)],
        key=lambda e: datetime.fromisoformat(e["timestamp"]),
    )
    return Feed(sprints=sprints, events=events, left_out=left_out)


def _filed_events(
    history: DeliveryHistory, left_out: Counter[str], epics: dict[tuple[str, int], str]
) -> list[dict[str, Any]]:
    """One event per card: how it was filed, in the sprint whose days it was filed in."""
    zone = ZoneInfo(history.timezone)
    out = []
    for card in history.cards:
        if card.created is None:
            left_out["card with no created date"] += 1
            continue
        day = card.created.astimezone(zone).date()
        sprint = next((s.name for s in history.sprints if s.start <= day <= s.end), None)
        if sprint is None:
            left_out["card filed outside every sprint"] += 1
            continue
        body = _event(
            card, "filed", sprint, card.created.isoformat(), epics.get((card.repo, card.number))
        )
        if card.filed.by != "unknown":
            body["filer"] = card.filed.by
        if card.filed.activity is not None:
            body["opener"] = _OPENER[card.filed.activity]
        if card.filed.for_project is not None:
            body["project"] = card.filed.for_project
        out.append(body)
    return out


def _review_return(event: Any) -> bool:
    return bool(
        event.kind == "work_finished" and event.verdict in ("changes_requested", "returned")
    )


def _failure_class(event: Any) -> str:
    """A miss's class: escalation's, or for a session's story, the step that returned it."""
    if event.kind == "attempt":
        return str(event.failure_class)
    if isinstance(event, SentBack):
        return _CLASS_BY_RULE.get(event.rule, event.sent_from.value.upper())
    return REVIEW if event.role in REVIEWERS else QA


def _failure_role(event: Any) -> str | None:
    """The role that returned it, where the record says or its step has only one."""
    if isinstance(event, SentBack):
        return _ROLE_BY_STEP.get(event.sent_from)
    return str(event.role) if event.role else None


def _kind(event: Any, sessions: bool = False) -> str | None:
    """The sprint-metrics event type a delivery-history event becomes, if any.

    A story delivered in sessions misses on review's and QA's returns, never on
    escalation's attempts.
    """
    if event.kind == "attempt":
        return None if sessions else "attempt_failed"
    if event.kind == "work_finished":
        return "attempt_failed" if sessions and _review_return(event) else None
    if event.kind == "sent_back":
        return "attempt_failed" if sessions and event.sent_from in RETURNING else None
    if event.kind == "escalated":
        return "escalated"
    if event.kind == "blocked":
        return "blocked"
    if event.kind != "card_moved" or event.from_column == event.to_column:
        return None
    if event.to_column == columns.BLOCKED:
        return "blocked"
    if event.from_column == columns.BLOCKED:
        return "unblocked"
    if event.to_column == columns.IN_PROGRESS:
        return "started"
    if event.to_column == columns.DONE:
        return "finished"
    return None


def _epics(cards: list[Card]) -> dict[tuple[str, int], str]:
    """Each card whose parent is an epic, and that epic as `repo#number`."""
    found = {(c.repo, c.number): c for c in cards}
    return {
        (c.repo, c.number): f"{c.repo}#{c.parent}"
        for c in cards
        if c.parent is not None and getattr(found.get((c.repo, c.parent)), "type", None) == "Epic"
    }


def _event(card: Card, kind: str, sprint: str, at: str, epic: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"created": card.created.date().isoformat() if card.created else ""}
    if card.created:
        # The time as well as the day: sprint-metrics 1.3.0's minutes need it (crew#683).
        body["created_at"] = card.created.isoformat()
    # Not on `filed`: the service reads a superseded card's points from its first row.
    if card.points is not None and kind != "filed":
        body["points"] = int(card.points)
    event = {
        "api_version": API_VERSION,
        "card_id": f"{card.repo}#{card.number}",
        "type": kind,
        "timestamp": at,
        "sprint": sprint,
        "card": body,
    }
    if epic is not None:
        # sprint-metrics reads a story's epic from any of its rows (4.5.0's epic answers).
        event["epic"] = epic
    return event


@dataclass
class _Span:
    """One session as it's put together: whose, on what, when, and the calls it holds."""

    role: str
    card: CardRef | None
    start: datetime
    end: datetime | None = None
    # Until when an unfinished one holds calls: the next session of its role on its card.
    until: datetime | None = None
    calls: list[ModelCall] = field(default_factory=list)
    step: bool = False


def _sessions(
    history: DeliveryHistory, periods: list[PeriodEvents], left_out: Counter[str]
) -> list[dict[str, Any]]:
    """Each session the record holds, as sprint-metrics' `session` event (crew#1001)."""
    events = sorted((e for p in periods for e in p.events), key=lambda e: e.at)
    spans: list[_Span] = []
    work: dict[tuple[Any, ...], _Span] = {}
    steps: dict[tuple[Any, ...], _Span] = {}
    ended: dict[tuple[str, int], datetime] = {}
    calls: list[ModelCall] = []
    for event in events:
        if event.kind == "model_call":
            calls.append(event)
        elif event.kind == "session_ended":
            ended[(event.card.repo, event.card.number)] = event.at
        elif event.kind in ("work_started", "work_finished") and event.role in SESSION_ROLES:
            key = (event.role, event.card, event.pull)
            began = work.pop(key, None)
            if began is not None:
                if event.kind == "work_finished":
                    began.end = event.at
                else:
                    # It never finished: it ended at the last session end on its card.
                    began.end = _last_end(began, ended)
            if event.kind == "work_started":
                work[key] = _Span(event.role, event.card, event.at)
                spans.append(work[key])
        elif event.kind in ("step_started", "step_ended") and event.role in SESSION_ROLES:
            named = (event.role, event.card, event.step)
            began = steps.pop(named, None)
            if began is not None and event.kind == "step_ended":
                began.end = event.at
            if event.kind == "step_started":
                steps[named] = _Span(str(event.role), event.card, event.at, step=True)
                spans.append(steps[named])
    for began in work.values():
        began.end = _last_end(began, ended)
    # A step inside its role's work on the same card is part of that work's session.
    spans = [s for s in spans if not (s.step and any(_inside(s, w) for w in spans if not w.step))]
    for s in spans:
        if s.end is None:
            later = [o.start for o in spans if o is not s and _same(o, s) and o.start > s.start]
            s.until = min(later, default=None)
    by_role: dict[str, list[_Span]] = {}
    for s in spans:
        by_role.setdefault(s.role, []).append(s)
    alone: list[ModelCall] = []
    for call in calls:
        held = [s for s in by_role.get(str(call.role), []) if _holds(s, call)]
        if held:
            max(held, key=lambda s: s.start).calls.append(call)
        else:
            alone.append(call)
    # A session with no recorded finish can't be sent: its minutes are unknown. Each of its
    # calls ran for its seconds before it was recorded, so it goes as one no session holds.
    for unfinished in [s for s in spans if s.end is None]:
        left_out["session: no recorded finish"] += 1
        alone += unfinished.calls
        spans.remove(unfinished)
    for call in alone:
        if call.role not in SESSION_ROLES:
            left_out["model call: by no role"] += 1
        elif call.seconds is None:
            left_out["model call: no time, in no session"] += 1
        else:
            start = call.at - timedelta(seconds=call.seconds)
            spans.append(_Span(str(call.role), call.card, start, call.at, calls=[call]))
    cards = {(c.repo, c.number): c for c in history.cards}
    sent = []
    for s in sorted(spans, key=lambda s: s.start):
        body = _session(s, cards, history, left_out)
        if body is not None:
            sent.append(body)
    return sent


def _last_end(span: _Span, ended: dict[tuple[str, int], datetime]) -> datetime | None:
    at = ended.get((span.card.repo, span.card.number)) if span.card is not None else None
    return at if at is not None and at >= span.start else None


def _same(a: _Span, b: _Span) -> bool:
    return a.role == b.role and a.card == b.card


def _inside(step: _Span, work: _Span) -> bool:
    return (
        _same(step, work)
        and work.start <= step.start
        and (work.end is None or step.start <= work.end)
    )


def _holds(span: _Span, call: ModelCall) -> bool:
    """A session's calls are its role's inside its span, on its card or on no card."""
    if call.role != span.role or (call.card is not None and call.card != span.card):
        return False
    if call.at < span.start:
        return False
    if span.end is not None:
        return call.at <= span.end
    return span.until is None or call.at < span.until


def _session(
    span: _Span,
    cards: dict[tuple[str, int], Card],
    history: DeliveryHistory,
    left_out: Counter[str],
) -> dict[str, Any] | None:
    """The session as sent, or None, counted, when it can't be placed."""
    assert span.end is not None, "an unfinished session is never sent"
    zone = ZoneInfo(history.timezone)
    day = span.start.astimezone(zone).date()
    sprint = next((s.name for s in history.sprints if s.start <= day <= s.end), None)
    if sprint is None:
        left_out["session: between sprints"] += 1
        return None
    card = cards.get((span.card.repo, span.card.number)) if span.card is not None else None
    if span.card is not None and card is None:
        left_out["session: card not in the history"] += 1
        return None
    if card is not None and card.created is None:
        left_out["session: card with no created date"] += 1
        return None

    def total(name: str) -> int | None:
        values = [v for c in span.calls if (v := getattr(c, name)) is not None]
        return round(sum(values)) if values else None

    return session_shape.SessionEvent(
        api_version=API_VERSION,  # type: ignore[arg-type]
        type="session",
        card_id=f"{card.repo}#{card.number}" if card is not None else None,
        timestamp=span.start,
        sprint=sprint,
        card=session_shape.SessionCard(created=card.created.date(), created_at=card.created)
        if card is not None and card.created is not None
        else None,
        role=span.role,  # type: ignore[arg-type]
        start=span.start,
        end=span.end,
        model_seconds=total("seconds"),
        input_tokens=total("prompt_tokens"),
        output_tokens=total("completion_tokens"),
    ).sent()


@dataclass
class Pushed:
    """What the service said to each request, by status, with the first reason for each."""

    sprints: Counter[int] = field(default_factory=Counter)
    events: Counter[int] = field(default_factory=Counter)
    reasons: dict[str, str] = field(default_factory=dict)


def push(found: Feed, send: Callable[[str, dict[str, Any]], tuple[int, str]]) -> Pushed:
    """Each sprint, then each event, sent once; nothing stops at a refusal.

    `send(path, body)` returns the status and the response's text. A refusal is
    counted and its first reason kept, so one run shows every kind of problem.
    """
    pushed = Pushed()
    for sprint in found.sprints:
        status, text = send("/sprints", sprint)
        pushed.sprints[status] += 1
        if status >= 300:
            pushed.reasons.setdefault(f"sprint {status}", f"{sprint['name']}: {text[:200]}")
    for event in found.events:
        status, text = send("/events", event)
        pushed.events[status] += 1
        if status >= 300:
            # A session on no card is named by its role.
            name = event.get("card_id") or event.get("role")
            pushed.reasons.setdefault(f"{event['type']} {status}", f"{name}: {text[:200]}")
    return pushed


def only(found: Feed, sprints: Iterable[str]) -> Feed:
    """The feed for some sprints: their definitions and their events.

    The feed is built from the whole history and cut afterwards. A story's
    `finished` names its first miss in any sprint, not only the ones sent.
    """
    names = set(sprints)
    return Feed(
        sprints=[s for s in found.sprints if s["name"] in names],
        events=[e for e in found.events if e["sprint"] in names],
        left_out=found.left_out,
    )


def push_to(url: str, found: Feed, *, timeout: float = 10.0) -> Pushed:
    """`push` over HTTP to the service at `url`. A service that can't be reached raises."""
    import httpx  # noqa: PLC0415

    with httpx.Client(base_url=url, timeout=timeout) as client:

        def send(path: str, body: dict[str, Any]) -> tuple[int, str]:
            response = client.post(path, json=body)
            return response.status_code, response.text

        return push(found, send)
