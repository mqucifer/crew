"""Developer best-of-three (crew Discussion #359): does one of three first attempts pass more often?

A story whose first attempt failed, replayed from the commit `main` was at when
it started, through the crew's own delivery path for a first attempt:

    answer (with up to ASK_LIMIT asks) -> overwrites -> workflow permission ->
    the record's bounds -> the regression guards -> apply -> the sandbox check

"Passed" is where delivery would stop retrying: a green check. Each run gets its
own worktree. Crew-written code runs only in the configured sandbox, never on
this Mac: the script refuses to start if the sandbox isn't available.

    uv run python experiments/panel-359/replay_dev.py <card> <runs> [--lens]

`--lens` gives each run a different one-line focus instead of the same prompt.
"""

from __future__ import annotations

import contextvars
import json
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).parent
REPO = "sprint-metrics"
CLONE = Path(tempfile.gettempdir()) / "crew-panel-sprint-metrics"
LENSES = [
    "Make the smallest change that meets every criterion.",
    "Make the most robust change: think about the edge cases a user will hit.",
    "Start from the tests: write the test for each criterion first, then the code that passes it.",
]


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def first_attempt_at(card: int) -> str:
    """When the story's first Developer call began, from the crew's event log."""
    for path in sorted(Path("var/events").glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if f'"card":{card},' in line and '"llm.started"' in line and '"Developer"' in line:
                return json.loads(line)["at"]
    raise SystemExit(f"no Developer call for #{card} in var/events")


def base_commit(at: str) -> str:
    """The commit `main` was at then."""
    if not CLONE.exists():
        subprocess.run(
            ["gh", "repo", "clone", f"mqucifer/{REPO}", str(CLONE), "--", "-q"], check=True
        )
    git("fetch", "-q", "origin", cwd=CLONE)
    return git("rev-list", "-1", f"--before={at}", "origin/main", cwd=CLONE).strip()


def story_text(card: int) -> str:
    out = subprocess.run(
        [
            "gh",
            "issue",
            "view",
            str(card),
            "-R",
            f"mqucifer/{REPO}",
            "--json",
            "title,body",
            "--jq",
            '.title + "\\n\\n" + .body',
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def one(card: int, n: int, story: str, sha: str, lens: str, sandbox) -> dict:
    """One first attempt, timed. The result says how far it got."""
    t = time.monotonic()
    row = _attempt(card, n, story, sha, lens, sandbox)
    return {**row, "seconds": round(time.monotonic() - t, 1)}


def _attempt(card: int, n: int, story: str, sha: str, lens: str, sandbox) -> dict:
    from crew_org.crews.delivery_crew import implement_story
    from crew_org.events import attributed
    from crew_org.flows.delivery import ASK_LIMIT, declared_contract, workflows_not_permitted
    from crew_org.project import brief, read_record
    from crew_org.tools import bounds, regression, workspace
    from crew_org.tools.repo_context import focused_context

    worktree = Path(tempfile.mkdtemp(prefix=f"replay-{card}-{n}-"))
    git("worktree", "add", "-q", "--detach", str(worktree), sha, cwd=CLONE)
    start = datetime.now(UTC)
    outcome: dict = {
        "card": card,
        "run": n,
        "lens": lens,
        "base": sha[:10],
        "start": start.isoformat(),
    }
    try:
        record = read_record(worktree)
        header = f"{brief(record)}\n\n" if record else ""
        text = f"{story}\n\n**Focus for this attempt:** {lens}" if lens else story
        asked: list[str] = []
        for _ in range(ASK_LIMIT + 1):
            context, focus = focused_context(worktree, about=text, extra=asked)
            answer = attributed(implement_story, card=card, repo=REPO, purpose=f"replay run {n}")(
                text, context=header + context, may_be_done=True
            )
            if not getattr(answer, "asks", False):
                break
            asked += [f for f in answer.need_files if (worktree / f).is_file() and f not in asked]
        outcome["context_chars"] = focus.chars
        if getattr(answer, "asks", False):
            return {**outcome, "result": "asked past the limit"}
        if getattr(answer, "already_done", None):
            return {**outcome, "result": "answered already done"}
        if overwrites := regression.overwrites_existing(worktree, answer.new_files):
            return {**outcome, "result": "overwrite", "detail": overwrites}
        if missing := workflows_not_permitted(answer):
            return {**outcome, "result": "workflow permission", "detail": missing}
        if outside := bounds.out_of_bounds(worktree, answer, record):
            return {**outcome, "result": "bounds", "detail": outside[:3]}
        merged = regression.merged_base(worktree)
        broken = regression.broken_contracts(worktree, answer.all_edits, merged)
        broken = regression.draft_breaks(worktree, answer, merged, lambda why: None) | broken
        broken = regression.without_moves(worktree, answer, broken, merged)
        broken |= regression.lost_names(worktree, answer, merged, lambda why: None)
        broken = regression.without_retired(broken, answer)
        broken, _ = regression.without_declared(broken, declared_contract(story))
        if broken:
            return {**outcome, "result": "regression", "detail": sorted(broken)[:5]}
        try:
            workspace.apply_implementation(worktree, answer)
        except Exception as exc:  # noqa: BLE001
            return {**outcome, "result": "edit", "detail": str(exc)[:300]}
        check = workspace.check(worktree, sandbox=sandbox)
        return {
            **outcome,
            "result": "passed" if check.ok else "verify",
            "detail": "" if check.ok else check.failure_report[-600:],
        }
    except Exception as exc:  # noqa: BLE001 - an empty or invalid answer is a result too
        return {
            **outcome,
            "result": "no usable answer",
            "detail": f"{type(exc).__name__}: {exc}"[:300],
        }
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)], cwd=CLONE, capture_output=True
        )


def main() -> None:
    from crew_org import tracing
    from crew_org.config import load_org
    from crew_org.events import EventSink, bridge_crewai, flush_bridge
    from crew_org.tools.sandbox import Sandbox

    card, runs, lensed = int(sys.argv[1]), int(sys.argv[2]), "--lens" in sys.argv[3:]
    org = load_org()
    sandbox = Sandbox.from_config(org)
    if why := sandbox.unavailable_reason():
        raise SystemExit(f"sandbox unavailable, refusing to run crew-written code: {why}")
    story = story_text(card)
    sha = base_commit(first_attempt_at(card))
    arm = f"dev-{card}-{'lens' if lensed else 'same'}-{runs}-{datetime.now(UTC):%H%M%S}"
    bridge_crewai(EventSink(Path("var/experiments/panel-359/events.jsonl")))
    tracing.start(org)
    with (
        tracing.span(f"panel {arm}", **{"crew.for": f"panel {arm}", "crew.card": card}),
        ThreadPoolExecutor(max_workers=runs) as pool,
    ):
        futures = [
            pool.submit(
                contextvars.copy_context().run,
                one,
                card,
                n,
                story,
                sha,
                LENSES[(n - 1) % len(LENSES)] if lensed else "",
                sandbox,
            )
            for n in range(1, runs + 1)
        ]
        results = [f.result() for f in futures]
    flush_bridge()
    tracing.stop()
    with (HERE / "results" / "dev-results.jsonl").open("a") as out:
        for r in results:
            out.write(json.dumps({"arm": arm, **r}) + "\n")
    for r in results:
        print(arm, r["run"], f"{r.get('seconds')}s", r["result"], (r.get("lens") or "")[:40])


if __name__ == "__main__":
    main()
