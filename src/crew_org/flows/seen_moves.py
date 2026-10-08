"""Moves the crew didn't make, recorded when a pass sees them (crew#521).

The crew logs its own moves. Nothing logged anyone else's: `crew moves` read
them from GitHub's issue timelines, which stopped recording status changes:
asked on 2026-10-08, they held 3 of the crew's 91 moves on 10-07 and none
after, the Sponsor's included. So the Sponsor's approvals were in no record,
and the replay in `delivery-history` needs them.

Each pass already reads every card's Status, and the board says when that
Status was last set (not by whom). A card whose Status was set since the last
pass, matching no move the crew logged, was moved by someone else. It is
attributed as `board_moves` attributes GitHub's history: the platform when a
run of `board.yml` was acting, otherwise a person. GitHub's own project
workflows that set a Status are off on this board; only the two that add
items are on.

What this can't see, stated: two moves between passes read as one, from where
the card was to where it is; a card moved and moved back reads as no move.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from crew_org.events import CrewEvent, EventKind
from crew_org.flows.board_moves import SLACK, RunWindow, Source
from crew_org.tools.github_project import Card

# Where the last pass's view of each card's Status is kept, beside the events.
SEEN_FILE = "board-statuses.json"


def _key(card: Card) -> str:
    return f"{card.repo}#{card.number}"


def load_seen(path: Path) -> dict[str, dict[str, Any]] | None:
    """The last pass's statuses, or None when there's no earlier pass to compare."""
    try:
        seen = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return seen if isinstance(seen, dict) else None


def save_seen(path: Path, cards: Iterable[Card]) -> None:
    seen = {
        _key(c): {"status": c.status, "set": c.status_set.isoformat() if c.status_set else None}
        for c in cards
        if c.repo and c.number
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(seen, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def changed(
    cards: Iterable[Card], seen: dict[str, dict[str, Any]]
) -> list[tuple[Card, str | None]]:
    """Each card whose Status was set since the last pass, with the Status it had then."""
    out: list[tuple[Card, str | None]] = []
    for card in cards:
        if not (card.repo and card.number and card.status and card.status_set):
            continue
        before = seen.get(_key(card))
        if before is None:
            out.append((card, None))
            continue
        if before.get("status") == card.status:
            # Moved and moved back, or set to what it was: no move to report.
            continue
        out.append((card, before.get("status")))
    return out


def _the_crews(card: Card, logged: list[CrewEvent]) -> bool:
    """A move the crew logged to this Status, near when the board says it was set."""
    assert card.status_set is not None
    for event in logged:
        # Every move the crew makes goes through `move_card`, which logs a block as
        # `card.blocked` and a move as `card.moved`, both with where it went. A
        # block matched only `card.moved`, so the crew blocking sprint-metrics#475
        # on 2026-10-08 was recorded as a person's move. What this file records
        # itself is never the crew's.
        if event.kind is EventKind.CARD_SEEN_MOVED or event.card != card.number:
            continue
        detail = event.detail or {}
        if detail.get("to") != card.status:
            continue
        # Moves logged before crew#521 name no repository; the number and time decide.
        if detail.get("repo") not in (None, card.repo):
            continue
        if abs(event.at - card.status_set) <= SLACK:
            return True
    return False


def seen_moves(
    cards: Iterable[Card],
    seen: dict[str, dict[str, Any]],
    logged: list[CrewEvent],
    runs: Callable[[datetime], list[RunWindow]],
) -> list[CrewEvent]:
    """The moves since the last pass that the crew didn't make, as events.

    `runs(since)` gives the board workflow's runs from then on; it's asked only
    when there's a move to attribute.
    """
    others = [(c, before) for c, before in changed(cards, seen) if not _the_crews(c, logged)]
    if not others:
        return []
    earliest = min(c.status_set for c, _ in others if c.status_set is not None)
    windows = runs(earliest - SLACK)
    events: list[CrewEvent] = []
    for card, before in others:
        assert card.status_set is not None
        by = (
            Source.PLATFORM
            if any(w.started - SLACK <= card.status_set <= w.finished + SLACK for w in windows)
            else Source.PERSON
        )
        mover = "the board workflow" if by is Source.PLATFORM else "a person"
        events.append(
            CrewEvent(
                at=card.status_set,
                kind=EventKind.CARD_SEEN_MOVED,
                card=card.number,
                summary=f"moved to {card.status} by {mover}",
                detail={"from": before, "to": card.status, "repo": card.repo, "by": by.value},
            )
        )
    return sorted(events, key=lambda e: e.at)
