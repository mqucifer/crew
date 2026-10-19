"""A story's real delivery, from the crew's event log, read-only (crew#778, Stage R)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# The main checkout's log: read, never written.
EVENTS = Path("/Users/quarters/Documents/git/crew/var/events")
# The Developer's answer forms: one call of these is one attempt's answer.
ANSWERS = ("FirstAttempt", "FirstOrDone", "Implementation", "Rework", "Plan", "StepAnswer")


def history(repo: str, card: int) -> dict[str, Any]:
    """Its attempts, model time and tokens, and how it ended, as the crew delivered it."""
    events: list[dict[str, Any]] = []
    for path in sorted(EVENTS.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("card") != card:
                continue
            where = (event.get("ctx") or {}).get("repo") or (event.get("detail") or {}).get("repo")
            if where == repo:
                events.append(event)
    events.sort(key=lambda e: e.get("at", ""))
    finished = [
        e for e in events if e.get("kind") == "llm.finished" and e.get("role") == "Developer"
    ]
    answers = [
        e
        for e in finished
        if str((e.get("detail") or {}).get("output_schema", "")).split(":")[0] in ANSWERS
    ]
    moves = [
        (e["at"][:16], (e.get("detail") or {}).get("to"))
        for e in events
        if e.get("kind") == "card.moved"
    ]
    d = [e.get("detail") or {} for e in finished]
    return {
        "developer_calls": len(finished),
        "answers": len(answers),
        "model_minutes": round(sum(x.get("duration_s") or 0 for x in d) / 60, 1),
        "completion_tokens": sum(x.get("completion_tokens") or 0 for x in d),
        "reasoning_tokens": sum(x.get("reasoning_tokens") or 0 for x in d),
        "prompt_tokens": sum(x.get("prompt_tokens") or 0 for x in d),
        "returns_from_review": sum(
            1
            for (_, to), (_, before) in zip(moves[1:], moves, strict=False)
            if before == "Reviewing" and to == "In Progress"
        ),
        "reached": [to for _, to in moves][-1] if moves else None,
        "moves": moves,
    }
