"""Run one Developer session on a story at a commit, outside the tick (crew#778, Stage C).

    uv run python scripts/run_session.py sprint-metrics 662 aa78bf2 --story story.md \
        --coverage ../crew/var/coverage

For proving the session before delivery runs it (Stage D): the story's repository is
checked out at `base` in a fresh worktree, its workspace container is held for the
session, and the session runs through LiteLLM. At `done`, the crew's gate (the
project's autofix, then its whole checks) runs in the same container, recorded apart
from the session's own runs. Nothing goes to the board or GitHub, and nothing is
pushed. The session's events go to `var/events/session.jsonl`; the run's story,
start, diff and result go to `var/sessions/<repo>-<card>-<time>/`.

`--coverage` names a store of coverage maps to read, such as the main checkout's: a
map not in it is measured, in the sandbox, as delivery would.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crew_org import events
from crew_org.events import EventSink
from crew_org.session import conversation
from crew_org.tools import coverage_map
from crew_org.tools.workspace import toolchain

RUNS = Path("var/sessions")


def worktree(repo: str, base: str, out: Path) -> Path:
    """A detached worktree of the repository at `base`, from a clone fetched once."""
    clone = RUNS / "clones" / repo
    if not clone.exists():
        clone.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["gh", "repo", "clone", f"mqucifer/{repo}", str(clone), "--", "-q"], check=True
        )
    else:
        subprocess.run(["git", "-C", str(clone), "fetch", "-q", "origin"], check=True)
    path = (out / "work").resolve()
    subprocess.run(
        ["git", "-C", str(clone), "worktree", "add", "-q", "--detach", str(path), base], check=True
    )
    return path


def gate(held: Any, checks: Any) -> dict[str, Any]:
    """What the crew runs on the branch at `done`: the autofix, then every check."""
    for command in checks.autofix:
        held.exec(list(command), timeout=900)
    results = [held.exec(list(c), timeout=1800) for c in checks.verify]
    return {
        "green": all(r.code == 0 for r in results),
        "checks": [
            {"command": r.command, "code": r.code, "tail": r.output[-2000:]} for r in results
        ],
    }


def diff(root: Path, base: str) -> str:
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return subprocess.run(
        ["git", "-C", str(root), "diff", "--cached", base, "--", ".", ":!.crew-home"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("repo")
    parser.add_argument("card", type=int)
    parser.add_argument("base")
    parser.add_argument("--story", type=Path, required=True, help="the story's text, as a file")
    parser.add_argument("--coverage", type=Path, default=coverage_map.COVERAGE_DIR)
    args = parser.parse_args()
    # Stage B's held container (crew#778), which this merges after.
    from crew_org.tools.container import Held, sweep  # noqa: PLC0415

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    out = RUNS / f"{args.repo}-{args.card}-{stamp}"
    out.mkdir(parents=True)
    story = args.story.read_text(encoding="utf-8")
    (out / "story.md").write_text(story, encoding="utf-8")
    root = worktree(args.repo, args.base, out)
    if reason := conversation.unfit(root):
        raise SystemExit(reason)
    checks = toolchain(root)
    try:
        cov = coverage_map.build(root, args.repo, store=args.coverage)
    except coverage_map.CoverageUnavailable as exc:
        print(f"no coverage map: {exc}")
        cov = None
    sink = EventSink(Path("var/events/session.jsonl"))
    events.forward_calls_to(sink, card=args.card)
    sweep()
    held = Held(root, f"{args.repo}-{args.card}")
    result: dict[str, Any] = {"repo": args.repo, "card": args.card, "base": args.base}
    began = time.monotonic()
    try:
        started = held.start()
        if started.code != 0:
            raise SystemExit(f"the container didn't start: {started.output[-500:]}")
        setup = held.setup()
        result["setup"] = None if setup is None else {"code": setup.code, "seconds": setup.seconds}
        with events.working_on(card=args.card, repo=args.repo, purpose="session"):
            session = conversation.session(root, story, held, checks, coverage=cov, base=args.base)
            (out / "start.md").write_text(session.start, encoding="utf-8")
            ending = session.run()
        result |= {
            "how": ending.how,
            "stop": ending.stop,
            "said": ending.said,
            "evidence": ending.evidence,
            "model_minutes": round(session.model_s / 60, 1),
            "calls": session.calls,
            "usage": dict(session.usage),
            "tools_used": dict(session.used),
            "runs": session.tools.runs,
            "compacted": session.compacted or None,
            "troubles": session.troubles,
        }
        if ending.how == "done":
            result["gate_at_done"] = gate(held, checks)
        (out / "diff.patch").write_text(diff(root, args.base), encoding="utf-8")
    finally:
        held.stop()
        result["wall_minutes"] = round((time.monotonic() - began) / 60, 1)
        (out / "result.json").write_text(
            json.dumps(result, indent=1, default=str), encoding="utf-8"
        )
    usage = result.get("usage", {})
    green = (result.get("gate_at_done") or {}).get("green")
    print(
        f"{args.repo}#{args.card}: {result.get('how')} {result.get('stop') or ''}".rstrip()
        + f", gate {'green' if green else 'red' if green is not None else '-'}, "
        f"{result.get('model_minutes')} model min, {result.get('calls')} calls, "
        f"{usage.get('completion_tokens')} out ({usage.get('reasoning_tokens')} reasoning). "
        f"Records: {out}"
    )


if __name__ == "__main__":
    main()
