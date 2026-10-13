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
- an attempt that didn't land: `attempt_failed`, with its number, class and role;
- escalated: `escalated`;
- moved to Done: `finished`, carrying how many attempts it took, and the class and
  role of its first miss: why it didn't land first time.

A story closed as not planned was superseded, not finished: its move to Done is
sent as `superseded` (sprint-metrics 1.3.0), with `because`, the reason its epic
was split again. One with no reason recorded is left out and counted, never
guessed. `replaced_by` isn't sent: the crew doesn't record which stories replaced
it.

Pushing is safe to repeat: the service stores an event once by card, type and
time, and updates a sprint registered again in place.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from crew_org import columns
from crew_org.packages.delivery_history import Card, DeliveryHistory, PeriodEvents
from crew_org.work_types import DELIVERED

API_VERSION = "1"
# Seen at the same card within this many seconds, a block and a move to Blocked
# are one block: the crew blocks a card by moving it there.
SAME_BLOCK_SECONDS = 120


@dataclass
class Feed:
    """What goes to the service: sprints first, then events, oldest first."""

    sprints: list[dict[str, Any]]
    events: list[dict[str, Any]]
    left_out: Counter[str] = field(default_factory=Counter)


def feed(history: DeliveryHistory, periods: Iterable[PeriodEvents]) -> Feed:
    """The history as sprint-metrics' sprint definitions and board events."""
    cards = {(c.repo, c.number): c for c in history.cards}
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
            if event.kind in ("model_call", "work_started", "work_finished", "pull_merged"):
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
        kind = _kind(event)
        if kind is None:
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
        body = _event(card, kind, sprint, event.at.isoformat())
        if kind == "superseded":
            body["because"] = card.superseded_because
        if kind == "attempt_failed":
            misses.setdefault(key, []).append(event)
            body["attempt_number"] = event.number or len(misses[key])
            body["failure_class"] = event.failure_class
            if event.role:
                body["failure_role"] = event.role
        if kind == "finished":
            missed = misses.get(key, [])
            body["card"]["attempts"] = 1 + len(missed)
            if missed:
                body["card"]["failure_class"] = missed[0].failure_class
                if missed[0].role:
                    body["card"]["failure_role"] = missed[0].role
        events.append(body)
    return Feed(sprints=sprints, events=events, left_out=left_out)


def _kind(event: Any) -> str | None:
    """The sprint-metrics event type a delivery-history event becomes, if any."""
    if event.kind == "attempt":
        return "attempt_failed"
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


def _event(card: Card, kind: str, sprint: str, at: str) -> dict[str, Any]:
    body: dict[str, Any] = {"created": card.created.date().isoformat() if card.created else ""}
    if card.created:
        # The time as well as the day: sprint-metrics 1.3.0's minutes need it (crew#683).
        body["created_at"] = card.created.isoformat()
    if card.points is not None:
        body["points"] = int(card.points)
    return {
        "api_version": API_VERSION,
        "card_id": f"{card.repo}#{card.number}",
        "type": kind,
        "timestamp": at,
        "sprint": sprint,
        "card": body,
    }


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
            pushed.reasons.setdefault(
                f"{event['type']} {status}", f"{event['card_id']}: {text[:200]}"
            )
    return pushed


def only(found: Feed, sprints: Iterable[str]) -> Feed:
    """The feed for some sprints: their definitions and their events.

    The feed is built from the whole history and cut afterwards. A story's
    `finished` counts its misses in every sprint, not only the ones sent.
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
