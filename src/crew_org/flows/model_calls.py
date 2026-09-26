"""A sprint's model calls, for `crew export` (#179).

Raw data only. Each call's card, attempt, role, tokens, finish reason, model
alias and duration, as the event log recorded them, beside the stories and
attempts the export already carries. Nothing is computed from them here: no
rates, thresholds or charts. That's the crew and model performance project's
job, through its own Goals.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

CALL_KINDS = ("llm.finished", "llm.failed")
FIELDS = (
    "attempt",
    "for",
    "sprint",
    "model",
    "finish_reason",
    "prompt_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "cached_tokens",
    "total_tokens",
    "duration_s",
    "call_id",
    "response_id",
    "error",
)


def read_model_calls(events_dir: Path, stories: Iterable[Any], repo: str) -> list[dict[str, Any]]:
    """Every recorded model call for these stories in `repo`, oldest first."""
    numbers = {s.number for s in stories}
    calls: list[dict[str, Any]] = []
    for path in sorted(events_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("kind") not in CALL_KINDS or event.get("card") not in numbers:
                continue
            detail = event.get("detail") or {}
            # A call from before #179 names no repository; its card number is all there is.
            if detail.get("repo") not in (None, repo):
                continue
            calls.append(
                {
                    "at": event.get("at"),
                    "card": event["card"],
                    "role": event.get("role"),
                    "failed": event["kind"] == "llm.failed",
                    **{k: detail[k] for k in FIELDS if k in detail},
                }
            )
    return sorted(calls, key=lambda c: c["at"] or "")
