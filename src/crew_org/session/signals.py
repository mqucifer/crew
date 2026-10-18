"""What's recorded on every call of a session, and the two ceilings that stop one (crew#778).

The Sponsor, reviewing the replay (2026-10-18): no nudges, every signal recorded on
every call, and ceilings high to start, brought down from real stories. Each ceiling
is about three times the largest value a successful replay reached; reasoning and
edits without a run have none, since the 3-hour backstop and the gate at `done` cover
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

from crew_org.session.tools import Reply, edited

# Model calls in a row with no edit applied and no run, before the session stops.
READING_CALLS = 60
# Views of one file with no edit to it between, before the session stops.
VIEWS_OF_ONE_FILE = 50


@dataclass
class Signals:
    # The ceilings: the Developer's, unless a session sets its own, as refinement's
    # reading steps do (crew#807).
    reading_calls: int = field(default_factory=lambda: READING_CALLS)
    views_of_one_file: int = field(default_factory=lambda: VIEWS_OF_ONE_FILE)
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
            changed = edited(name, reply)
            if changed and reply.kind == "ok":
                acted = True
                self.edits_since_run += 1
                for each in changed:
                    self.views.pop(each, None)
            elif name == "run" and reply.kind in ("passed", "failed"):
                acted = True
                self.edits_since_run = 0
            elif name == "view" and reply.kind == "ok" and path:
                self.views[path] = self.views.get(path, 0) + 1
        self.since_action = 0 if acted else self.since_action + 1
        self.empty_lookups += empty
        self.reasoning_tokens = int(usage.get("reasoning_tokens") or 0)
        return {**self.now(), "empty_lookups": empty}

    def now(self) -> dict[str, Any]:
        """Each signal as it stands: `empty_lookups_total` is the session's so far."""
        most = max(self.views.items(), key=lambda kv: kv[1], default=("", 0))
        return {
            "since_action": self.since_action,
            "most_views": most[1],
            "most_viewed": most[0],
            "reasoning_tokens": self.reasoning_tokens,
            "edits_since_run": self.edits_since_run,
            "empty_lookups_total": self.empty_lookups,
        }

    def ceiling(self) -> dict[str, Any] | None:
        """The ceiling reached, with its value, or None."""
        if self.since_action >= self.reading_calls:
            return {"ceiling": "reading", "calls": self.since_action}
        path, views = max(self.views.items(), key=lambda kv: kv[1], default=("", 0))
        if views >= self.views_of_one_file:
            return {"ceiling": "re_viewing", "file": path, "views": views}
        return None
