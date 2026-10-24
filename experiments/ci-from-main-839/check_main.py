"""Would main open DevOps's CI Task now? A read-only run of the tick's own check (crew#839).

Runs `ci_from_main.held_by` against a repository's main through the crew's App, as a
pass does, without `keep`: the GitHub reads and, when they hold, main's whole checks
in a container with its services. Nothing is filed and nothing is stored. No tick.

    uv run python experiments/ci-from-main-839/check_main.py sprint-metrics

The credentials are read inside this process from the main checkout's `.env`
(`load_env`); the App's key path in it is taken from that checkout's root.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from crew_org.auth import resolve_credentials
from crew_org.config import load_env
from crew_org.events import EventSink
from crew_org.flows import ci_from_main
from crew_org.git_ops import Workspace
from crew_org.tools import workspace
from crew_org.tools.github_issues import IssueClient

MAIN = Path.home() / "Documents/git/crew"


def main(repo: str) -> None:
    env = load_env(MAIN / ".env")
    key = env.get("GITHUB_APP_PRIVATE_KEY")
    if key and not Path(key).expanduser().is_absolute():
        env["GITHUB_APP_PRIVATE_KEY"] = str(MAIN / key)
    token, identity = resolve_credentials(env)
    owner = env["GITHUB_OWNER"]
    issues = IssueClient(token, owner)
    # It only reads main, so it commits nothing and needs no bot identity.
    ws = Workspace(owner, repo, token, None)  # type: ignore[arg-type]
    events = Path(tempfile.mkdtemp(prefix="ci-from-main-")) / "events.jsonl"
    ran: list[ci_from_main.MainCheck] = []
    # Each check's last output line, so the run shows what pytest counted.
    lines: list[str] = []
    real = workspace.check

    def check(worktree: Path, **kw: Any) -> workspace.CheckResult:
        done = real(worktree, **kw)
        lines.extend(f"{r.command}: {r.output.strip().splitlines()[-1:]}" for r in done.results)
        return done

    workspace.check = check  # type: ignore[assignment]

    def run_checks(worktree: Path, name: str) -> ci_from_main.MainCheck:
        ran.append(ci_from_main.run_main_checks(worktree, name))
        return ran[-1]

    started = time.monotonic()
    why = ci_from_main.held_by(
        issues,
        EventSink(events),
        ws,
        repo=repo,
        store=ci_from_main.STORE,
        run_checks=run_checks,
        keep=False,
    )
    took = time.monotonic() - started
    print(f"{repo}: " + (f"held: {why}" if why else "would open the CI Task now"))
    print(f"took {took:.0f} s; main's checks: {ran[0] if ran else 'not run'}")
    print("\n".join(lines))
    if events.exists():
        print(events.read_text())


if __name__ == "__main__":
    main(sys.argv[1])
