"""The crew's work pushed to sprint-metrics as it happens (crew#280, criterion 3).

After each pass of a tick, the sprint under way goes to the service again, with
any earlier sprint that hasn't gone through cleanly. The service stores an event
once by card, type and time, so sending one again adds nothing. A pass whose push
fails changes nothing here, so the next pass sends the same again: what the crew
recorded in `var/events` is retried until it lands.

A sprint is marked sent once it has ended and a push of it had no refusal. The
sprint under way is sent on every pass, because its events are still being
recorded.

The service's address is `sprint_metrics.url` in org.yaml. Without it, nothing is
pushed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from crew_org.clock import sprint_today
from crew_org.events import EventKind
from crew_org.packages import sprint_metrics_feed as smf
from crew_org.packages.gather import history_now

if TYPE_CHECKING:
    from crew_org.flows.loop import Crew

STATE = "sprint-metrics-sent.json"


def url(org: dict[str, Any]) -> str | None:
    """The service's address, as org.yaml names it, or None to push nothing."""
    return (org.get("sprint_metrics") or {}).get("url") or None


def push_after_pass(crew: Crew, *, var: Path) -> smf.Pushed | None:
    """Send the sprint under way, and every sprint not yet sent cleanly. None if unconfigured."""
    address = url(crew.org)
    if address is None:
        return None
    state = var / "sprint-metrics" / STATE
    sent = set(json.loads(state.read_text())["sprints"]) if state.exists() else set()
    history, periods, _ = history_now(
        crew.board,
        crew.issues,
        org=crew.org,
        crew_repo=crew.crew_repo or "crew",
        events_dir=var / "events",
    )
    today = sprint_today(crew.org)
    wanted = [s.name for s in history.sprints if s.name not in sent and s.start <= today]
    found = smf.only(smf.feed(history, periods.values()), wanted)
    pushed = smf.push_to(address, found)
    if not pushed.reasons:
        sent |= {s.name for s in history.sprints if s.name in wanted and s.end < today}
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"sprints": sorted(sent)}, indent=1) + "\n")
    crew.sink.note(
        EventKind.NOTE,
        f"pushed to sprint-metrics: {len(found.sprints)} sprints, {len(found.events)} events"
        + (f"; refused: {'; '.join(pushed.reasons.values())}"[:300] if pushed.reasons else ""),
        sprints=wanted,
        events=len(found.events),
        statuses={str(k): v for k, v in pushed.events.items()},
    )
    return pushed
