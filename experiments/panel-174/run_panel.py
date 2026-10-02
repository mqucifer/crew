"""The refinement panel test (sprint-metrics Goal #174): see README.md here.

Four focused calls per epic, one per role (Architect, UX Designer, QA, DevOps),
each built from the role's spec in agents.yaml with `build_agent`, on the
role's own model alias. Every member is shown the same context:

1. Goal #174's body before its revision (`inputs/goal-174.md`)
2. the project record at sprint-metrics 01c0357, as the crew shows it (`brief`)
3. the epic, and its sibling epics under the Goal (`inputs/epic-*.md`)
4. the Sponsor's recorded decisions, collected by `collect_decisions.py`

    uv run python experiments/panel-174/run_panel.py serial 1
    uv run python experiments/panel-174/run_panel.py serial 1 --no-decisions
    uv run python experiments/panel-174/run_panel.py parallel 1
    uv run python experiments/panel-174/run_panel.py serial 2 --from 2

`serial`: the twelve calls one after another. `parallel`: each epic's four
members at once, the epics one after another. `--no-decisions` leaves item 4
out. `--from N` numbers the runs from N, to add runs to an arm. Each call
appends one line to `results/results.jsonl`.

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

from pydantic import BaseModel, Field, model_validator

HERE = Path(__file__).parent
INPUTS = HERE / "inputs"
EPICS = [184, 185, 187]
SIBLINGS = [184, 185, 186, 187]

# What each member reads the epic for: the area it owns, from its backstory in
# agents.yaml, and nothing about what to find.
MEMBERS = {
    "architect": (
        "You own coherence: the data shapes, interfaces and decisions in this epic "
        "that would be expensive to reverse, and whether they fit the rest of the Goal."
    ),
    "ux_designer": (
        "You own what a person or a program using this sees: what they send, what "
        "they get back, and what they read to use it."
    ),
    "qa_engineer": (
        "You will have to prove what this epic delivers. You own whether what the Goal "
        "asks of it can be proven, and whether everything it promises can hold together."
    ),
    "devops_engineer": (
        "You own whether what this epic ships really runs where it is meant to, and "
        "stays healthy there."
    ),
}


class PanelNote(BaseModel):
    problem: str = Field(
        description="What the epic gets wrong, leaves out or contradicts, in one or two sentences"
    )
    source: str = Field(
        description=(
            "What says so, quoted, and where: the Goal, the project's record, a Sponsor "
            "decision (name its issue), or another epic (name it)"
        )
    )
    settle: str = Field(description="What should be settled before the epic is split")


class PanelAnswer(BaseModel):
    nothing_to_add: bool = Field(
        description="True if there is nothing in your area to add. Then notes is empty"
    )
    notes: list[PanelNote] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_or_the_other(self) -> PanelAnswer:
        if self.nothing_to_add and self.notes:
            raise ValueError("nothing_to_add with notes: give notes, or nothing to add")
        if not self.nothing_to_add and not self.notes:
            raise ValueError("no notes: give at least one, or say nothing to add")
        return self


def epic_text(n: int) -> str:
    """The epic's body, without the Product Owner's approval footer."""
    text = (INPUTS / f"epic-{n}.md").read_text()
    return re.split(r"\n---\n\s*\nProposed by the Product Owner", text)[0].strip()


def inputs() -> dict:
    from crew_org.project import brief, parse

    return {
        "goal": (INPUTS / "goal-174.md").read_text().split("\n---\n")[0].strip(),
        "project": brief(parse((INPUTS / "project-01c0357.yaml").read_text())),
        "decisions": (INPUTS / "decisions.md").read_text().strip(),
        "epics": {n: epic_text(n) for n in SIBLINGS},
    }


def describe(given: dict, epic: int, role: str, decisions: bool) -> str:
    from crew_org.permissions import load_agents

    title = load_agents()[role]["role"]
    others = "\n\n".join(given["epics"][n] for n in SIBLINGS if n != epic)
    return (
        f"## The Goal (sprint-metrics #174), set by the Sponsor\n\n{given['goal']}\n\n"
        f"{given['project']}\n\n"
        + (f"{given['decisions']}\n\n" if decisions else "")
        + f"## The other epics under this Goal\n\n{others}\n\n"
        f"## The epic\n\n{given['epics'][epic]}\n\n"
        "## Your task\n\n"
        "This epic is about to be split into stories. Before it is, four roles each read "
        f"it once, for what they own. You are the {title}. {MEMBERS[role]}\n\n"
        "Name each problem in your area that should be settled before the split: "
        "something the epic gets wrong, leaves out or contradicts, against the Goal, the "
        "project's record, "
        + ("the Sponsor's decisions " if decisions else "")
        + "or the other epics. Quote its source.\n\n"
        'If there is nothing in your area to add, say so: "nothing to add" is a complete '
        "answer, and better than a note that isn't needed. Don't restate the epic, propose "
        "stories or a design, or note things outside your area; the other roles cover theirs."
    )


def member(arm: str, run: int, epic: int, role: str, given: dict, decisions: bool) -> dict:
    from crewai import Crew, Process, Task

    from crew_org.agents import build_agent
    from crew_org.events import attributed

    start, t = datetime.now(UTC), time.monotonic()
    row: dict = {"arm": arm, "run": run, "epic": epic, "role": role, "start": start.isoformat()}

    def call() -> PanelAnswer:
        agent = build_agent(role)
        task = Task(
            description=describe(given, epic, role, decisions),
            expected_output="Your notes on this epic, or nothing to add.",
            agent=agent,
            output_pydantic=PanelAnswer,
        )
        crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
        return crew.kickoff().pydantic

    try:
        answer = attributed(call, purpose=f"panel-174 {arm} run {run} #{epic} {role}")()
        row.update(answer.model_dump())
    except Exception as exc:  # noqa: BLE001 - a failed call is a result too
        row["error"] = f"{type(exc).__name__}: {exc}"[:500]
    row["end"] = datetime.now(UTC).isoformat()
    row["seconds"] = round(time.monotonic() - t, 1)
    return row


def main() -> None:
    from crew_org import tracing
    from crew_org.config import load_org
    from crew_org.events import EventSink, bridge_crewai, flush_bridge

    mode, runs = sys.argv[1], int(sys.argv[2])
    if mode not in {"serial", "parallel"}:
        raise SystemExit("mode is serial or parallel")
    flags = sys.argv[3:]
    decisions = "--no-decisions" not in flags
    first = int(flags[flags.index("--from") + 1]) if "--from" in flags else 1
    if not decisions and mode == "parallel":
        raise SystemExit("the parallel arm runs with the decisions")
    crew = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    given = inputs()
    arm = f"{mode}{'' if decisions else '-nodecisions'}"
    out = HERE / "results" / "results.jsonl"
    out.parent.mkdir(exist_ok=True)
    bridge_crewai(EventSink(Path("var/experiments/panel-174/events.jsonl")))
    tracing.start(load_org())

    def record(row: dict) -> None:
        with out.open("a") as f:
            f.write(json.dumps({**row, "crew": crew}) + "\n")
        notes = (
            "nothing to add" if row.get("nothing_to_add") else f"{len(row.get('notes', []))} notes"
        )
        print(
            row["arm"],
            row["run"],
            f"#{row['epic']}",
            row["role"],
            f"{row['seconds']}s",
            row.get("error") or notes,
            flush=True,
        )

    try:
        for run in range(first, first + runs):
            with tracing.span(f"panel-174 {arm} run {run}", **{"crew.for": f"panel-174 {arm}"}):
                for epic in EPICS:
                    if mode == "serial":
                        for role in MEMBERS:
                            record(member(arm, run, epic, role, given, decisions))
                        continue
                    with ThreadPoolExecutor(max_workers=len(MEMBERS)) as pool:
                        # Each thread gets its own copy of this context, so its
                        # call's span nests under the run's.
                        futures = [
                            pool.submit(
                                contextvars.copy_context().run,
                                member,
                                arm,
                                run,
                                epic,
                                role,
                                given,
                                decisions,
                            )
                            for role in MEMBERS
                        ]
                        for f in futures:
                            record(f.result())
    finally:
        flush_bridge()
        tracing.stop()


if __name__ == "__main__":
    main()
