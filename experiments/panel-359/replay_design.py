"""Architect panel (crew Discussion #359): do three designs find what one missed?

sprint-metrics#302 revised the design for the image rule (§19.8), "a service
answers from outside the container", and declared no change to `serve.py`,
which binds 127.0.0.1. The Architect had been shown the whole repository,
`serve.py` included. The deploy review caught it one step later (PR #330).

Three proposals at a time, from sprint-metrics as it stood just before #302
(446d6ec), through the crew's own `propose_design`:

    uv run python experiments/panel-359/replay_design.py same
    uv run python experiments/panel-359/replay_design.py lens
    uv run python experiments/panel-359/replay_design.py critic

`same`: the real reason, three times. `lens`: one line of focus each.
`critic`: one proposal, then two more shown its changes and asked what it
misses. Each proposal's declared changes are tagged by what they cover; the
one that matters is `bind`, the gap the real design missed.
"""

from __future__ import annotations

import contextvars
import json
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import yaml

HERE = Path(__file__).parent
CLONE = Path(tempfile.gettempdir()) / "crew-panel-sprint-metrics"
BASE = "446d6eceaa770710daf0990d92bb002f8b63f686"
REASON = HERE / "inputs" / "design-reason-19.8.txt"
LENSES = [
    "Focus on what runs where, and how each part is reached by the people who use it.",
    "Focus on what could break it in use: how would this design fail once deployed?",
    "Focus on the simplest design that meets the reason.",
]
# What a declared change covers, by what it says. `bind` is the gap.
KINDS = {
    "bind": r"serve\.py|127\.0\.0\.1|0\.0\.0\.0|all interfaces|bind address|\bbind\b",
    "entrypoint": r"ENTRYPOINT",
    "user": r"non-root|\bUSER\b|useradd",
    "digest": r"digest|sha256",
    "updater": r"dependabot|renovate",
    "smoke": r"smoke|docker job|reachab|/metrics",
    "lint": r"hadolint|trivy",
    "healthcheck": r"HEALTHCHECK|health check",
}


def kinds(change) -> set[str]:
    text = " ".join(filter(None, [change.what, change.was, change.why, change.work]))
    return {k for k, pattern in KINDS.items() if re.search(pattern, text, re.I)}


def inputs() -> dict:
    from crew_org.flows.design import workflows
    from crew_org.flows.onboard import describe
    from crew_org.project import brief, read_record

    worktree = Path(tempfile.mkdtemp(prefix="design-302-"))
    subprocess.run(
        ["git", "worktree", "add", "-q", "--detach", str(worktree), BASE], cwd=CLONE, check=True
    )
    record = read_record(worktree)
    return {
        "worktree": worktree,
        "project": brief(record.model_copy(update={"design": None})),
        "repository": describe(worktree, branch="main", protection=None),
        "current": yaml.safe_dump(
            record.design.model_dump(exclude_none=True, exclude_defaults=True), sort_keys=False
        ),
        "ci": workflows(worktree),
    }


def one(arm: str, n: int, given: dict, reason: str, feedback: str = "") -> dict:
    from crew_org.crews.design_crew import propose_design
    from crew_org.events import attributed

    start, t = datetime.now(UTC), time.monotonic()
    row: dict = {"arm": arm, "run": n, "start": start.isoformat()}
    try:
        proposal = attributed(propose_design, purpose=f"panel {arm} run {n}")(
            project=given["project"],
            repository=given["repository"],
            current=given["current"],
            reason=reason,
            feedback=feedback,
        )
        changes = [
            {"what": c.what, "work": c.work, "kinds": sorted(kinds(c))} for c in proposal.changes
        ]
        covered = sorted({k for c in changes for k in c["kinds"]})
        row.update(
            {
                "changes": changes,
                "covered": covered,
                "found_bind": "bind" in covered,
                "ci_checks": [c.value for c in proposal.ci_checks],
                "proposal": proposal.model_dump(),
            }
        )
    except Exception as exc:  # noqa: BLE001 - an empty or invalid answer is a result too
        row["error"] = f"{type(exc).__name__}: {exc}"[:300]
    row["seconds"] = round(time.monotonic() - t, 1)
    return row


def critic_feedback(first: dict) -> str:
    listed = "\n".join(f"- {c['what']}" for c in first.get("changes", []))
    return (
        "Another architect proposed a revision for the same reason, declaring these "
        f"changes:\n\n{listed}\n\nWhat does that proposal miss or get wrong, against the "
        "reason and the code you can see? Make your own proposal, complete, correcting it."
    )


def main() -> None:
    from crew_org import tracing
    from crew_org.config import load_org
    from crew_org.events import EventSink, bridge_crewai, flush_bridge

    mode = sys.argv[1]
    if mode not in {"same", "lens", "critic"}:
        raise SystemExit("mode is same, lens or critic")
    reason = REASON.read_text().strip()
    given = inputs()
    arm = f"design-{mode}-{datetime.now(UTC):%H%M%S}"
    bridge_crewai(EventSink(Path("var/experiments/panel-359/events.jsonl")))
    tracing.start(load_org())

    def run(n: int, reason_n: str, feedback: str = "") -> dict:
        return one(arm, n, given, reason_n, feedback)

    try:
        with (
            tracing.span(f"panel {arm}", **{"crew.for": f"panel {arm}"}),
            ThreadPoolExecutor(max_workers=3) as pool,
        ):
            if mode == "critic":
                first = run(1, reason)
                futures = [
                    pool.submit(
                        contextvars.copy_context().run, run, n, reason, critic_feedback(first)
                    )
                    for n in (2, 3)
                ]
                results = [first, *(f.result() for f in futures)]
            else:
                futures = [
                    pool.submit(
                        contextvars.copy_context().run,
                        run,
                        n,
                        f"{reason}\n\n{LENSES[n - 1]}" if mode == "lens" else reason,
                    )
                    for n in (1, 2, 3)
                ]
                results = [f.result() for f in futures]
    finally:
        flush_bridge()
        tracing.stop()
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(given["worktree"])],
            cwd=CLONE,
            capture_output=True,
        )
    with (HERE / "results" / "design-results.jsonl").open("a") as out:
        for r in results:
            out.write(json.dumps(r) + "\n")
    for r in results:
        print(
            arm,
            r["run"],
            f"{r['seconds']}s",
            "BIND" if r.get("found_bind") else "-",
            r.get("covered") or r.get("error"),
        )


if __name__ == "__main__":
    main()
