"""Who moved each card: the crew, the board's own workflow, or a person (#89).

The crew logs its own moves, so a card a person moved by hand used to be
invisible and intervention could only ever be a floor. GitHub records every
Status change with an actor, which is most of the answer and not all of it:
the actor is the credential, not the decision. Three things act as the crew's
App — the crew's code, `board.yml` borrowing its token, and a person running
something with the crew's credentials — and sprint-metrics #33's hand move to
Ready on 2026-09-19 is recorded as `mqucifer-crew`.

So a move is attributed by reconciliation, not by actor:

- **crew** — it matches a move in the crew's own event log.
- **platform** — made with the crew's identity while `board.yml` was running.
  The board moving the cards no role moves (#32). Any repository's run counts
  for any card: the workflow's sweep moves every closed card on the board, so
  a run in crew moved sprint-metrics cards on 2026-09-23.
- **person** — everything else. Named by actor, and flagged when the actor is
  the crew's identity, because that is someone acting *as* the crew.

A limitation worth stating: the event log is local. A move the crew made on
another machine has no log entry here and reads as a person's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from crew_org.events import CrewEvent
from crew_org.tools.github_project import BoardMove

# How far apart the crew's log and GitHub's record of the same move may be.
# They are written a moment apart by the same call, so this is generous; it
# only has to be smaller than the gap between two real moves of one card to
# the same column, which is minutes at the very least.
SLACK = timedelta(seconds=120)

BOARD_WORKFLOW = "board.yml"


class Source(StrEnum):
    CREW = "crew"
    PLATFORM = "platform"
    PERSON = "person"


@dataclass(frozen=True)
class RunWindow:
    """When one run of the board's workflow was acting. `repo` is where it ran."""

    repo: str
    started: datetime
    finished: datetime


@dataclass(frozen=True)
class AttributedMove:
    move: BoardMove
    source: Source
    # A person's move made with the crew's credentials. Counted as a person's —
    # nothing in the crew decided it — and marked, because it reads as the crew
    # everywhere else.
    as_crew: bool = False

    @property
    def who(self) -> str:
        if self.source is Source.PERSON:
            return f"{self.move.actor} (as the crew)" if self.as_crew else str(self.move.actor)
        return self.source.value


def run_windows(runs: list[dict[str, Any]], repo: str) -> list[RunWindow]:
    """The workflow's runs as time windows. A run still going ends now."""
    windows = []
    for run in runs:
        start = run.get("run_started_at") or run.get("created_at")
        end = run.get("updated_at") or start
        if start:
            windows.append(
                RunWindow(
                    repo=repo,
                    started=datetime.fromisoformat(start),
                    finished=datetime.fromisoformat(end),
                )
            )
    return windows


def attribute(
    moves: list[BoardMove],
    events: list[CrewEvent],
    runs: list[RunWindow],
    *,
    crew_logins: set[str],
) -> list[AttributedMove]:
    """Say who made each move GitHub recorded.

    Each logged crew move accounts for one GitHub move at most, the nearest in
    time, so two quick moves of one card to the same column are not both
    claimed by a single log entry.
    """
    logged = [e for e in events if e.card is not None and (e.detail or {}).get("to") is not None]
    used: set[int] = set()
    out: list[AttributedMove] = []

    for move in moves:
        match = _nearest_logged(move, logged, used)
        if match is not None:
            used.add(match)
            out.append(AttributedMove(move, Source.CREW))
            continue
        as_crew = move.actor in crew_logins
        if as_crew and _in_a_run(move, runs):
            out.append(AttributedMove(move, Source.PLATFORM))
            continue
        out.append(AttributedMove(move, Source.PERSON, as_crew=as_crew))
    return out


def _nearest_logged(move: BoardMove, logged: list[CrewEvent], used: set[int]) -> int | None:
    best: tuple[timedelta, int] | None = None
    for i, event in enumerate(logged):
        if i in used or event.card != move.number:
            continue
        detail = event.detail or {}
        if detail.get("to") != move.to:
            continue
        # A crew move that did not say where the card came from still matches;
        # one that did must agree.
        if detail.get("from") is not None and detail.get("from") != move.frm:
            continue
        gap = abs(event.at - move.at)
        if gap <= SLACK and (best is None or gap < best[0]):
            best = (gap, i)
    return best[1] if best else None


def _in_a_run(move: BoardMove, runs: list[RunWindow]) -> bool:
    return any(run.started - SLACK <= move.at <= run.finished + SLACK for run in runs)
