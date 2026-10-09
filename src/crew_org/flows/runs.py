"""A card's runs, joined from the event log alone (crew#449, discussion 552).

The proof the record joins up: for each role's run on a card, what it ran with,
what went wrong and which rule decided, how it ended, and the crew issues that
cite the card. Nothing is matched by time or read from prose: every line comes
from a field.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Run:
    run: str
    role: str = ""
    started: str = ""
    ended: str = ""
    calls: int = 0
    tokens: int = 0
    prompts: set[str] = field(default_factory=set)
    schemas: set[str] = field(default_factory=set)
    kinds: Counter[str] = field(default_factory=Counter)
    rules: list[str] = field(default_factory=list)
    outcome: str = ""
    pr: int | None = None


def _events(events_dir: Path) -> list[dict[str, Any]]:
    out = []
    for path in sorted(events_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return sorted(out, key=lambda e: str(e.get("at") or ""))


def card_runs(events_dir: Path, repo: str, number: int) -> tuple[list[Run], list[dict[str, Any]]]:
    """The card's runs, oldest first, and the issues citing it (their latest record each)."""
    runs: dict[str, Run] = {}
    citing: dict[int, dict[str, Any]] = {}
    me = f"{repo}#{number}"
    for event in _events(events_dir):
        ctx = event.get("ctx") or {}
        detail = event.get("detail") or {}
        kind = str(event.get("kind") or "")
        if kind in ("issue.cites", "defect.filed") and me in (
            detail.get("cites") or detail.get("cards") or []
        ):
            citing[int(event.get("card") or 0)] = {"kind": kind, **detail}
            continue
        run_id = ctx.get("run")
        if not run_id or ctx.get("card") != number or ctx.get("repo") != repo:
            continue
        run = runs.setdefault(run_id, Run(run=run_id, started=str(event.get("at") or "")))
        run.role = run.role or str(ctx.get("role") or event.get("role") or "")
        run.ended = str(event.get("at") or "")
        if kind == "llm.finished":
            run.calls += 1
            run.tokens += int(detail.get("prompt_tokens") or 0) + int(
                detail.get("completion_tokens") or 0
            )
        if detail.get("prompt_hash"):
            run.prompts.add(str(detail["prompt_hash"]))
        if detail.get("output_schema"):
            run.schemas.add(str(detail["output_schema"]))
        if detail.get("failure_kind") and kind != "llm.started":
            run.kinds[str(detail["failure_kind"])] += 1
        if detail.get("rule"):
            run.rules.append(str(detail["rule"]))
        if detail.get("pr"):
            run.pr = int(detail["pr"])
        if kind == "agent.finished":
            run.outcome = str(event.get("summary") or "")
    return list(runs.values()), [{"issue": n, **c} for n, c in sorted(citing.items())]


def render(repo: str, number: int, runs: list[Run], citing: list[dict[str, Any]]) -> list[str]:
    """The card's story as lines a person reads."""
    lines = [f"{repo}#{number}: {len(runs)} runs"]
    for r in runs:
        lines.append(
            f"- {r.started[:16].replace('T', ' ')}Z {r.role or '?'} run {r.run}: "
            f"{r.calls} calls, {r.tokens:,} tokens" + (f", PR #{r.pr}" if r.pr else "")
        )
        if r.prompts or r.schemas:
            lines.append(
                f"    ran with prompt {', '.join(sorted(r.prompts)) or '?'}, "
                f"form {', '.join(sorted(r.schemas)) or '?'}"
            )
        if r.kinds:
            lines.append("    failures: " + ", ".join(f"{k} ×{n}" for k, n in r.kinds.items()))
        if r.rules:
            lines.append("    decided by: " + " → ".join(r.rules))
        if r.outcome:
            lines.append(f"    ended: {r.outcome}")
    if citing:
        lines.append("Crew issues citing it:")
        for c in citing:
            state = c.get("state") or ("filed" if c["kind"] == "defect.filed" else "")
            lines.append(f"- {c.get('repo', 'crew')}#{c['issue']} ({state})")
    else:
        lines.append("No crew issue cites it.")
    return lines
