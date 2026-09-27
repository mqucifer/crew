"""Prometheus metrics for the crew, from its attributes-only telemetry log (#283).

Standard library only, so it runs in a bare Python container that mounts the
telemetry log read-only and can see nothing else of the crew. It reads the
log on every scrape: the crew is a command that runs and exits, so there is
no process of its own to scrape, and its log is the record.

No card numbers in labels: a label per card grows the series without bound.
Cards are for Loki, where the same log is searchable.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections import Counter
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

TELEMETRY = Path(os.environ.get("TELEMETRY_DIR", "/telemetry"))
PORT = int(os.environ.get("PORT", "9464"))
TOKENS = ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens")


def _events(folder: Path):
    for path in sorted(folder.glob("*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _label(value) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _series(name: str, labels: dict, value) -> str:
    inner = ",".join(f'{k}="{_label(v)}"' for k, v in labels.items())
    return f"{name}{{{inner}}} {value}" if inner else f"{name} {value}"


def render(folder: Path) -> str:
    """The metrics, in Prometheus' text format."""
    events = Counter()
    moves = Counter()
    calls = Counter()
    failures = Counter()
    tokens = Counter()
    seconds = Counter()
    retries = Counter()
    ticks = 0
    budget = None
    board: dict = {}
    last = 0.0
    for e in _events(folder):
        kind, role = e.get("kind") or "", e.get("role") or ""
        events[(kind, role)] += 1
        at = e.get("at")
        if at:
            with contextlib.suppress(ValueError):
                when = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
                last = max(last, when.timestamp())
        if kind == "card.moved" and e.get("to"):
            moves[e["to"]] += 1
        elif kind == "llm.finished":
            model = e.get("model") or ""
            calls[(role, model, e.get("finish_reason") or "")] += 1
            for t in TOKENS:
                if isinstance(e.get(t), int):
                    tokens[(role, model, t.removesuffix("_tokens"))] += e[t]
            if isinstance(e.get("duration_s"), int | float):
                seconds[(role, model)] += e["duration_s"]
        elif kind == "llm.failed":
            failures[(role, e.get("model") or "")] += 1
        elif kind == "escalation.decided":
            retries[(e.get("failure_class") or "", e.get("disposition") or "")] += 1
        elif kind == "tick.started" and e.get("tick") == 1:
            ticks += 1
        if kind == "tick.started" and isinstance(e.get("counts"), dict):
            board = e["counts"]
        if kind == "tick.started" and isinstance(e.get("github_remaining"), int):
            budget = e["github_remaining"]

    out = [
        "# HELP crew_events_total Events the crew recorded, by kind and role.",
        "# TYPE crew_events_total counter",
        *(_series("crew_events_total", {"kind": k, "role": r}, n) for (k, r), n in events.items()),
        "# HELP crew_card_moves_total Card moves, by the column moved to.",
        "# TYPE crew_card_moves_total counter",
        *(_series("crew_card_moves_total", {"to": c}, n) for c, n in moves.items()),
        "# HELP crew_llm_calls_total Model calls finished, by role, model alias and finish reason.",
        "# TYPE crew_llm_calls_total counter",
        *(
            _series("crew_llm_calls_total", {"role": r, "model": m, "finish_reason": f}, n)
            for (r, m, f), n in calls.items()
        ),
        "# HELP crew_llm_failures_total Model calls that failed, by role and model alias.",
        "# TYPE crew_llm_failures_total counter",
        *(
            _series("crew_llm_failures_total", {"role": r, "model": m}, n)
            for (r, m), n in failures.items()
        ),
        "# HELP crew_llm_tokens_total Tokens, by role, model alias and type.",
        "# TYPE crew_llm_tokens_total counter",
        *(
            _series("crew_llm_tokens_total", {"role": r, "model": m, "type": t}, n)
            for (r, m, t), n in tokens.items()
        ),
        "# HELP crew_llm_seconds_total Seconds spent in model calls, by role and model alias.",
        "# TYPE crew_llm_seconds_total counter",
        *(
            _series("crew_llm_seconds_total", {"role": r, "model": m}, round(n, 3))
            for (r, m), n in seconds.items()
        ),
        "# HELP crew_retries_total Retry decisions, by failure class and disposition.",
        "# TYPE crew_retries_total counter",
        *(
            _series("crew_retries_total", {"failure_class": f, "disposition": d}, n)
            for (f, d), n in retries.items()
        ),
        "# HELP crew_ticks_total Ticks started.",
        "# TYPE crew_ticks_total counter",
        _series("crew_ticks_total", {}, ticks),
        "# HELP crew_board_cards Cards per column, as the latest pass read the board.",
        "# TYPE crew_board_cards gauge",
        *(_series("crew_board_cards", {"column": c}, n) for c, n in board.items()),
        "# HELP crew_github_requests_remaining GitHub requests left, as last seen.",
        "# TYPE crew_github_requests_remaining gauge",
        *([_series("crew_github_requests_remaining", {}, budget)] if budget is not None else []),
        "# HELP crew_last_event_timestamp_seconds When the crew last recorded anything.",
        "# TYPE crew_last_event_timestamp_seconds gauge",
        _series("crew_last_event_timestamp_seconds", {}, last),
    ]
    return "\n".join(out) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - the http.server name
        if self.path != "/metrics":
            self.send_error(404)
            return
        body = render(TELEMETRY).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        return  # a scrape every few seconds is not news


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104 - in its container
