"""Panel experiment (crew Discussion #359): does a panel of deploy reviews catch more, at what cost?

The deploy review of sprint-metrics#269's Dockerfile, with the evidence it was
given on 2026-09-28, run through the crew's own `review_deploy` (so through the
LiteLLM proxy, on `crew-code-think`).

    uv run python experiments/panel-359/run_panel.py serial 5
    uv run python experiments/panel-359/run_panel.py parallel 3

Each run appends one line to `results.jsonl`: the arm, the wall time, the
verdict, and which of #335's four findings it raised (by keyword; the verdict
is kept whole, so a tag can be checked by reading it). Per-request numbers
(time to first token, tokens, cached prompt tokens) come afterwards from
LiteLLM's spend log for the arm's time window: see README.md here.

Run it only when no tick is running: the crew and this would share the Spark,
and each would distort the other's timings.
"""

from __future__ import annotations

import contextvars
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).parent
TITLE = "Write the Dockerfile for the sprint-metrics container image"

# #335's four findings. Each finding gets at most one: the first whose
# pattern its claim (the concern's first sentence) matches.
FINDINGS = {
    "2 entrypoint": re.compile(r"ENTRYPOINT|\bCMD\b", re.I),
    "4 base pin": re.compile(
        r"digest|sha256|mutable tag|floating|base image .*pin|pin.*base", re.I
    ),
    "3 root": re.compile(r"\broot\b|\bUSER\b|non-root|uid 0", re.I),
    "1 bind": re.compile(r"127\.0\.0\.1|loopback|0\.0\.0\.0|unreachable", re.I),
}


def inputs() -> dict:
    from crew_org.project import parse

    record = parse((HERE / "inputs" / "project-269.yaml").read_text())
    release = record.intent.release
    lines = [
        f"- A release is {record.release_is}" + (f": {release.where}" if release.where else ".")
    ]
    if record.design and record.design.release_how:
        lines.append(f"- How: {record.design.release_how.strip()}")
    if record.design and record.design.checks:
        lines.append("- Checks: " + "; ".join(f"`{c}`" for c in record.design.checks))
    story = (HERE / "inputs" / "s259.md").read_text()
    at = story.find("## Acceptance criteria")
    return {
        "diff": (HERE / "inputs" / "pr269.diff").read_text(),
        "evidence": (HERE / "inputs" / "ev269.md").read_text(),
        "acceptance_criteria": story[at:] if at >= 0 else story,
        "release": "\n".join(lines),
    }


def tags(verdict) -> dict[str, str]:
    """Each finding raised, and whether it blocked: {'3 root': 'block', ...}."""
    found: dict[str, str] = {}
    for f in verdict.findings:
        # The claim is the first sentence; what follows explains it, and
        # mentions other things ("the repository root", "its loopback").
        claim = re.split(r"(?<=[.!?])\s", f.concern.strip(), maxsplit=1)[0]
        name = next((n for n, pattern in FINDINGS.items() if pattern.search(claim)), None)
        if name is not None and found.get(name) != "block":
            found[name] = "block" if f.blocking else "note"
    return dict(sorted(found.items()))


def one(arm: str, n: int, given: dict) -> dict:
    from crew_org.crews.deploy_review_crew import review_deploy
    from crew_org.events import attributed

    start = datetime.now(UTC)
    t = time.monotonic()
    try:
        # A span per run under the arm's, with its model calls beneath (#283),
        # so the runs can be read in Grafana as DevOps would read them.
        review = attributed(review_deploy, purpose=f"panel {arm} run {n}")
        verdict = review(
            TITLE,
            given["diff"],
            evidence=given["evidence"],
            acceptance_criteria=given["acceptance_criteria"],
            release=given["release"],
        )
        outcome = {
            "approve": verdict.approve,
            "found": tags(verdict),
            "verdict": verdict.model_dump(),
        }
    except Exception as exc:  # noqa: BLE001 - a failed run is a result too
        outcome = {"error": f"{type(exc).__name__}: {exc}"[:500]}
    return {
        "arm": arm,
        "run": n,
        "start": start.isoformat(),
        "seconds": round(time.monotonic() - t, 1),
        **outcome,
    }


def main() -> None:
    mode, count = sys.argv[1], int(sys.argv[2])
    hidden = "--hide-rule" in sys.argv[3:]
    if hidden:
        # The image rule (§19.8) spells out the four findings, so with it shown
        # the review applies a rule. Hidden, it has to find them: the case where
        # a panel should earn its cost, and where correlated misses would show.
        import crew_org.agents as agents
        from crew_org.constitution import excerpt

        def without_rule_8(*sections):
            text = excerpt(*sections)
            start = text.find("8. **What ships as an image")
            end = text.find("Rules 3 and 4", start)
            return text if start < 0 or end < 0 else text[:start] + text[end:]

        agents.excerpt = without_rule_8
    if mode not in {"serial", "parallel"}:
        raise SystemExit("mode is serial or parallel")
    crew = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    from crew_org import tracing
    from crew_org.config import load_org
    from crew_org.events import EventSink, bridge_crewai, flush_bridge

    given = inputs()
    arm = f"{mode}-{count}{'-hidden' if hidden else ''}-{datetime.now(UTC):%H%M%S}"
    window = datetime.now(UTC)
    # The crew's own event log and traces for these calls, as a tick records
    # them: llm.* events here, spans to the collector (#283).
    bridge_crewai(EventSink(Path("var/experiments/panel-359/events.jsonl")))
    tracing.start(load_org())
    with tracing.span(f"panel {arm}", **{"crew.for": f"panel {arm}"}):
        if mode == "serial":
            results = [one(arm, n, given) for n in range(1, count + 1)]
        else:
            with ThreadPoolExecutor(max_workers=count) as pool:
                # Each thread gets its own copy of this context, so its run's
                # span nests under the arm's: a pool thread starts with none.
                futures = [
                    pool.submit(contextvars.copy_context().run, one, arm, n, given)
                    for n in range(1, count + 1)
                ]
                results = [f.result() for f in futures]
    flush_bridge()
    tracing.stop()
    with (HERE / "results" / "results.jsonl").open("a") as out:
        for r in results:
            out.write(json.dumps({**r, "crew": crew, "window_from": window.isoformat()}) + "\n")
    for r in results:
        print(r["arm"], r["run"], f"{r['seconds']}s", r.get("found") or r.get("error"))


if __name__ == "__main__":
    main()
