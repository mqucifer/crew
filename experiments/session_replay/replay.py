"""Replay one story as a tool-driven session (crew#778, Stage R).

    uv run python experiments/session_replay/replay.py sprint-metrics-662

Checks the story's repository out at its base commit in a fresh worktree, starts its
workspace container, runs the session, and at `done` runs the crew's gate: the
project's autofix, then its whole checks, in the same container. Writes everything
to `runs/<case>/<run>/`; nothing goes to the board, GitHub or `var/events`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from cases import CASES, RUNS, Case, clone, story  # noqa: E402
from container import Held, sweep  # noqa: E402
from history import history  # noqa: E402
from session import Session  # noqa: E402
from start import start  # noqa: E402
from tools import Tools  # noqa: E402

from crew_org.calls import role_spec  # noqa: E402
from crew_org.tools.coverage_map import CoverageMap, with_static  # noqa: E402

# The crew's maps, measured at these commits already: read, never written.
COVERAGE = Path("/Users/quarters/Documents/git/crew/var/coverage")


def system() -> str:
    """Who the Developer is, without the forms' rules: the start tells the session's."""
    spec = role_spec("developer")
    return (
        f"You are {spec['role']}. {spec['backstory'].strip()}\n\nYour goal: {spec['goal'].strip()}"
    )


def worktree(case: Case, run: str) -> Path:
    repo = clone(case.repo)
    path = (repo.parent / "worktrees" / f"{case.name}-{run}").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "--detach", str(path), case.base],
        check=True,
    )
    return path


def coverage(case: Case, root: Path) -> Any:
    found = sorted((COVERAGE / case.repo).glob(f"{case.base}*.json"))
    return with_static(CoverageMap.from_json(found[0].read_text()), root) if found else None


def gate(held: Held) -> dict[str, Any]:
    """What the crew runs on the branch at `done`: autofix, then every check."""
    for command in held.tools.autofix:
        held.exec(command, timeout=900)
    results = [held.exec(c, timeout=1800) for c in held.tools.verify]
    return {
        "green": all(r.ok for r in results),
        "checks": [
            {"command": r.command, "code": r.code, "tail": r.output[-3000:]} for r in results
        ],
    }


def diff(root: Path, base: str) -> str:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return subprocess.run(
        ["git", "-C", str(root), "diff", "--cached", base, "--", ".", ":!.crew-home"],
        capture_output=True,
        text=True,
    ).stdout


def replay(
    name: str,
    backstop_s: float | None = None,
    context: str = "cut",
    label: str = "",
    parse_guard: bool = True,
) -> dict[str, Any]:
    case = CASES[name]
    # Pass 2 (the "whole" context rule) is told apart by its runs' names.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    label = label or ("" if context == "cut" else "p2")
    run = f"{label}-{stamp}" if label else stamp
    out = RUNS / name / run
    out.mkdir(parents=True, exist_ok=True)
    text = story(case)
    root = worktree(case, run)
    result: dict[str, Any] = {"case": name, "run": run, "base": case.base, "note": case.note}
    if (root / ".venv").exists():
        # crew#780: a host-made environment would be run inside the container.
        result["refused"] = "the worktree has a .venv the host made"
        (out / "result.json").write_text(json.dumps(result, indent=1))
        return result
    cov = coverage(case, root)
    opening, sizes, notes = start(root, text, cov, context)
    (out / "start.md").write_text(opening, encoding="utf-8")
    result["start_chars"] = sizes
    held = Held(root, name)
    log = (out / "transcript.jsonl").open("w", encoding="utf-8")
    began = time.monotonic()
    try:
        started = held.start()
        if not started.ok:
            result["refused"] = f"the container didn't start: {started.output[-500:]}"
            return result
        setup = held.setup()
        result["setup"] = (
            None
            if setup is None
            else {"code": setup.code, "seconds": setup.seconds, "tail": setup.output[-1500:]}
        )
        cut = held.cut_network()
        result["network_cut"] = cut.ok
        if not cut.ok:
            result["refused"] = f"the network wasn't cut: {cut.output}"
            return result
        tools = Tools(root, held, cov, notes, parse_guard=parse_guard)

        def write(entry: dict[str, Any]) -> None:
            log.write(
                json.dumps({"at": datetime.now(UTC).isoformat(timespec="seconds"), **entry}) + "\n"
            )
            log.flush()

        session = Session(tools, system(), opening, log=write, context=context)
        if backstop_s is not None:
            session.backstop_s = backstop_s
        ending = session.run()
        last = tools.runs[-1] if tools.runs else None
        green = [i for i, r in enumerate(tools.runs, 1) if r["ok"] and r["what"] == "suite"]
        result.update(
            {
                "ending": ending,
                "model_minutes": round(session.model_s / 60, 1),
                "calls": session.calls,
                "usage": session.usage,
                "tools_used": session.used,
                "files_read": sorted(set(tools.read)),
                "files_edited": sorted(set(tools.edited)),
                "runs": tools.runs,
                "developer_last_run": last,
                "first_green_suite_run": green[0] if green else None,
                "context": context,
                "label": label or "p1",
                "parse_guard": parse_guard,
                "compacted": session.compacted or None,
                "troubles": session.troubles,
            }
        )
        if ending.get("how") == "done":
            result["gate_at_done"] = gate(held)
        (out / "diff.patch").write_text(diff(root, case.base), encoding="utf-8")
    finally:
        log.close()
        held.stop()
        result["wall_minutes"] = round((time.monotonic() - began) / 60, 1)
        result["history"] = history(case.repo, case.card)
        (out / "result.json").write_text(
            json.dumps(result, indent=1, default=str), encoding="utf-8"
        )
    return result


def main() -> None:
    sweep()
    try:
        args = sys.argv[1:]
        context = "whole" if "--whole" in args else "cut"
        label = next((a.split("=", 1)[1] for a in args if a.startswith("--label=")), "")
        guard = "--no-parse-guard" not in args
        names = [a for a in args if not a.startswith("--")]
        for name in names or list(CASES):
            r = replay(name, context=context, label=label, parse_guard=guard)
            u = r.get("usage", {})
            g = r.get("gate_at_done", {}).get("green")
            print(
                f"{name}: {r.get('ending', {}).get('how', r.get('refused'))}, gate "
                f"{'green' if g else 'red' if g is not None else '-'}, "
                f"{r.get('model_minutes')} model min, {u.get('completion_tokens')} out "
                f"({u.get('reasoning_tokens')} reasoning), {r.get('calls')} calls"
            )
    finally:
        sweep()


if __name__ == "__main__":
    main()
