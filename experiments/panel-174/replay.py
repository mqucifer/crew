"""Replay of the panel test with the crew's own panel code (crew#440).

See README.md here, "The replay".

Runs `crew_org.crews.panel_crew.run_panel` (the new focus lines, the scope line
and the settled-by tag) on the stored inputs of the three epics, the way
`run_panel.py` ran the old members, so the notes can be scored against the same
ten findings.

    uv run python experiments/panel-174/replay.py 3
    uv run python experiments/panel-174/replay.py 1 --from 4

The argument is the number of runs; each is 12 calls, the four members of an
epic at once and the epics one after another. `--from N` numbers the runs from
N. Each member's answer appends one line to `results/replay.jsonl`.

Run it only when no tick is running: the crew and this would share the Spark.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).parent
INPUTS = HERE / "inputs"
EPICS = [184, 185, 187]
SIBLINGS = [184, 185, 186, 187]
REPO = "mqucifer/sprint-metrics"


def epic_text(n: int) -> str:
    """The epic's body, without the Product Owner's approval footer."""
    text = (INPUTS / f"epic-{n}.md").read_text()
    return re.split(r"\n---\n\s*\nProposed by the Product Owner", text)[0].strip()


def context(epic: int):
    from crew_org.crews.panel_crew import PanelContext, Sibling
    from crew_org.project import brief, parse

    return PanelContext(
        goal_ref=f"{REPO}#174",
        goal=(INPUTS / "goal-174.md").read_text().split("\n---\n")[0].strip(),
        project=brief(parse((INPUTS / "project-01c0357.yaml").read_text())),
        decisions=(INPUTS / "decisions.md").read_text().strip(),
        epic_ref=f"{REPO}#{epic}",
        epic=epic_text(epic),
        # As they stood that morning: #186 delivered, the others still open.
        siblings=[
            Sibling(f"{REPO}#{n}", "delivered" if n == 186 else "open", epic_text(n))
            for n in SIBLINGS
            if n != epic
        ],
    )


def main() -> None:
    from crew_org import tracing
    from crew_org.config import load_org
    from crew_org.crews.panel_crew import run_panel
    from crew_org.events import EventSink, bridge_crewai, flush_bridge, working_on

    runs = int(sys.argv[1])
    flags = sys.argv[2:]
    first = int(flags[flags.index("--from") + 1]) if "--from" in flags else 1
    crew = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    out = HERE / "results" / "replay.jsonl"
    out.parent.mkdir(exist_ok=True)
    bridge_crewai(EventSink(Path("var/experiments/panel-174/replay-events.jsonl")))
    tracing.start(load_org())
    try:
        for run in range(first, first + runs):
            with tracing.span(f"panel-174 replay run {run}", **{"crew.for": "panel-174 replay"}):
                for epic in EPICS:
                    start = datetime.now(UTC)
                    with working_on(card=epic, repo="sprint-metrics"):
                        result = run_panel(context(epic))
                    seconds = round((datetime.now(UTC) - start).total_seconds(), 1)
                    with out.open("a") as f:
                        for role, answer in result.answers.items():
                            row = {"arm": "replay", "run": run, "epic": epic, "role": role}
                            f.write(json.dumps({**row, **answer.model_dump(), "crew": crew}) + "\n")
                        for role, why in result.failed.items():
                            row = {"arm": "replay", "run": run, "epic": epic, "role": role}
                            f.write(json.dumps({**row, "error": why, "crew": crew}) + "\n")
                    counts = {r: len(a.notes) for r, a in result.answers.items()}
                    print(
                        f"run {run} #{epic} {seconds}s notes {counts} failed {list(result.failed)}",
                        flush=True,
                    )
    finally:
        flush_bridge()
        tracing.stop()


if __name__ == "__main__":
    main()
