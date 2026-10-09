"""What leaves the crew's own record for observability: attributes, never content (#283).

The Sponsor's Alloy tails `var/telemetry/*.jsonl` into Loki, and the exporter
in `deploy/telemetry` serves Prometheus metrics computed from it. So this file
is the boundary: an **allow-list** of fields, each a number, a name from a
fixed vocabulary, or a card or sprint identifier. Nothing free-text crosses
it: not an event's summary (the model bridge puts prompt snippets there), not
an error, test output, a criterion, an answer or a question, and never a
prompt or a response. `var/events` keeps everything, for the crew's own use.

An allow-list, not a deny-list: a field added to an event later stays local
until someone decides it may leave.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Fields an event's detail may carry out, by name. Numbers, identifiers, and
# names from a fixed vocabulary (column, model alias, failure class, finish
# reason, phase), and nothing else.
DETAIL = frozenset(
    {
        # What the work was: #179's attribution.
        "repo",
        "sprint",
        "attempt",
        "for",
        "tick",
        # Which code ran, and how a tick and its phases went (crew#449): a commit id, a
        # flag, names and counts.
        "commit",
        "dirty",
        "repos",
        "passes",
        "settled",
        "moved",
        # A model call: alias, reason it stopped, tokens, time, ids (#179).
        "model",
        "tool",
        "finish_reason",
        "prompt_tokens",
        "completion_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "total_tokens",
        "duration_s",
        "call_id",
        "response_id",
        # The board: moves, and column counts at the start of a pass.
        "from",
        "to",
        "counts",
        "column",
        "count",
        "limit",
        # Outcomes.
        "failure_class",
        "accepted",
        "approved",
        "notes",
        "quiet",
        "rebuilt",
        "about",
        "artifact",
        # Breaking a loop (#246): card numbers and a fixed vocabulary.
        "superseded",
        "because",
        "reads_decision",
        # GitHub's limits (#293): how long, what, and the budget left.
        "wait_s",
        "status",
        "resource",
        "stopped",
        "path",
        "github_remaining",
    }
)

# Kinds whose `reason` is a fixed vocabulary, not prose: why a story went back
# ("gate round trips", "pinned tests", "review conflicts with a criterion").
REASON_IS_A_NAME = frozenset({"story.returned"})


def view(event: Any) -> dict[str, Any]:
    """The event, reduced to what may leave: its kind, role, card, time, and allowed detail."""
    data = event.model_dump(mode="json") if hasattr(event, "model_dump") else dict(event)
    detail = data.get("detail") or {}
    kept = {k: v for k, v in detail.items() if k in DETAIL}
    if data.get("kind") in REASON_IS_A_NAME and isinstance(detail.get("reason"), str):
        kept["reason"] = detail["reason"][:60]
    if data.get("kind") == "escalation.decided":
        # "VERIFY — retry_local": the disposition is the part after the dash.
        disposition = str(data.get("summary") or "").rpartition(" — ")[2]
        if disposition.replace("_", "").isalpha():
            kept["disposition"] = disposition
    out = {
        "at": data.get("at"),
        "kind": data.get("kind"),
        "role": data.get("role"),
        "card": data.get("card"),
    }
    out.update(kept)
    # The context it was written in (crew#449): ids and names, the same keys the log's
    # projection sends, so an event and a log line of one run join in Grafana.
    from crew_org.log import _CONTEXT_OUT  # noqa: PLC0415

    for key, value in (data.get("ctx") or {}).items():
        if key in _CONTEXT_OUT and out.get(key) is None:
            out[key] = value
    return {k: v for k, v in out.items() if v is not None}


def line(event: Any) -> str:
    return json.dumps(view(event), separators=(",", ":")) + "\n"


def path_for(events_path: Path | None) -> Path | None:
    """`var/events/tick.jsonl` -> `var/telemetry/tick.jsonl`; None for anything else."""
    if events_path is None or events_path.parent.name != "events":
        return None
    return events_path.parent.parent / "telemetry" / events_path.name


def backfill(events_dir: Path, telemetry_dir: Path) -> int:
    """Rewrite the telemetry log from `var/events`, so history reaches Loki too.

    Replaces each file rather than appending, so running it twice is the same
    as running it once. Returns how many events were written.
    """
    telemetry_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for source in sorted(events_dir.glob("*.jsonl")):
        lines = []
        for raw in source.read_text(encoding="utf-8").splitlines():
            try:
                lines.append(line(json.loads(raw)))
            except ValueError:
                continue
        (telemetry_dir / source.name).write_text("".join(lines), encoding="utf-8")
        written += len(lines)
    return written
