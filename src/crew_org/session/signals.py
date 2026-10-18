"""What's recorded on every call of a session, and the two ceilings that stop one (crew#778).

The Sponsor, reviewing the replay (2026-10-18): no nudges, every signal recorded on
every call, and ceilings high to start, brought down from real stories. Each ceiling
is about three times the largest value a successful replay reached; reasoning and
edits without a run have none, since the 2-hour backstop and the gate at `done` cover
them.

- calls since the last edit or run (reading without acting: crew-presentation#124
  read for 44 calls, the successful replays at most 19);
- views of one file since its last edit (circling: at most 5 in pass 3);
- the call's reasoning tokens;
- edits since the last run;
- lookups that found nothing: no such file or definition, no hits, no tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from crew_org.session.tools import EDITS, Reply

# Model calls in a row with no edit applied and no run, before the session stops.
READING_CALLS = 60
# Views of one file with no edit to it between, before the session stops.
VIEWS_OF_ONE_FILE = 50


@dataclass
class Signals:
    since_action: int = 0
    views: dict[str, int] = field(default_factory=dict)
    edits_since_run: int = 0
    empty_lookups: int = 0
    reasoning_tokens: int = 0

    def after(self, replies: list[tuple[str, Reply]], usage: dict[str, int]) -> dict[str, Any]:
        """Each signal after one call's tools have run, as its record carries them."""
        acted = False
        empty = 0
        for name, reply in replies:
            empty += reply.empty
            path = str(reply.detail.get("path", ""))
            if name in EDITS and reply.kind == "ok":
                acted = True
                self.edits_since_run += 1
                self.views.pop(path, None)
            elif name == "run" and reply.kind in ("passed", "failed"):
                acted = True
                self.edits_since_run = 0
            elif name == "view" and reply.kind == "ok" and path:
                self.views[path] = self.views.get(path, 0) + 1
        self.since_action = 0 if acted else self.since_action + 1
        self.empty_lookups += empty
        self.reasoning_tokens = int(usage.get("reasoning_tokens") or 0)
        most = max(self.views.items(), key=lambda kv: kv[1], default=("", 0))
        return {
            "since_action": self.since_action,
            "most_views": most[1],
            "most_viewed": most[0],
            "reasoning_tokens": self.reasoning_tokens,
            "edits_since_run": self.edits_since_run,
            "empty_lookups": empty,
            "empty_lookups_total": self.empty_lookups,
        }

    def ceiling(self) -> dict[str, Any] | None:
        """The ceiling reached, with its value, or None."""
        if self.since_action >= READING_CALLS:
            return {"stop": "reading", "calls": self.since_action}
        path, views = max(self.views.items(), key=lambda kv: kv[1], default=("", 0))
        if views >= VIEWS_OF_ONE_FILE:
            return {"stop": "re-viewing", "file": path, "views": views}
        return None
