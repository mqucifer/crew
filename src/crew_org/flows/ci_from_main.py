"""DevOps's CI Task opened from the default branch itself (crew#839).

`role_tasks.ci_services_task` was opened only at a non-DevOps story's `done`, when its
whole checks passed with the default branch's services up. In the reset (project-print
`ROADMAP.md`, M7) an existing project's stories wait on `crew design`, which waits on
the step whose done-when is this Task, so no story reached `done` and it never opened.

So each pass also asks main: once the first workspace Task is closed and main declares
services, main's whole checks run in a container with them, at most once per head
commit, and a pass opens the Task. Cheapest first: once the Task is there, or the first
isn't closed, or the head was checked, nothing starts. What each head did is kept as a
file per commit, as the coverage map keeps its own (`var/coverage/`).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from crew_org.events import EventKind, EventSink
from crew_org.flows import role_tasks
from crew_org.llm import reraise_if_down
from crew_org.tools import services
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient

STORE = Path("var/ci-services")

PASSED = "passed"
# Main's checks ran and failed: commands or tests.
CHECKS = "checks"
NO_SERVICES = "no services"
# What a head did that's main's own, so it's never run again. A container that didn't
# start, a setup that failed, a service down or a container lost is the container's,
# not main's: the next pass tries that head again (the operator, 2026-10-24).
DEFINITIVE = frozenset({PASSED, CHECKS, NO_SERVICES})


@dataclass
class MainCheck:
    """What main's checks did at a head, in counts only."""

    outcome: str
    commands_failed: int = 0
    tests_failed: int = 0
    skipped: int = 0
    services_down: int = 0
    hung: int = 0

    @property
    def definitive(self) -> bool:
        return self.outcome in DEFINITIVE


@dataclass
class FromMain:
    opened: list[tuple[str, int]] = field(default_factory=list)
    # Each repository and what held it, said as a short line.
    held: list[tuple[str, str]] = field(default_factory=list)


def stored(repo: str, sha: str, *, store: Path = STORE) -> Path:
    return store / repo / f"{sha}.json"


def checked_at(repo: str, sha: str, *, store: Path = STORE) -> MainCheck | None:
    """What main's checks did at `sha`, if a pass recorded it."""
    path = stored(repo, sha, store=store)
    if not path.exists():
        return None
    return MainCheck(**json.loads(path.read_text()))


def run_main_checks(worktree: Path, name: str) -> MainCheck:
    """Main's whole checks in a container with the services and setup it declares, as a
    story's gate runs them at `done`. Setup gets the start's second try."""
    from crew_org.tools.container import Held  # noqa: PLC0415
    from crew_org.tools.devcontainer import read_inside  # noqa: PLC0415
    from crew_org.tools.workspace import check  # noqa: PLC0415

    declared = services.declared(lambda path: read_inside(worktree, path))
    held = Held(worktree, name, declared=declared)
    try:
        if not held.start().ok:
            return MainCheck("start", services_down=len(held.down))
        setup = held.setup()
        if setup is not None and not setup.ok:
            setup = held.setup()
        if setup is not None and not setup.ok:
            return MainCheck("setup", services_down=len(held.down))
        checked = check(worktree, held=held)
        if held.lost:
            return MainCheck("lost")
        counts = {
            "commands_failed": sum(not r.ok for r in checked.results),
            "tests_failed": len(checked.failed),
            "skipped": len(checked.skipped or {}),
            "services_down": len(checked.services_down),
            "hung": len(checked.hung),
        }
        if checked.services_down:
            return MainCheck("services", **counts)
        return MainCheck(PASSED if checked.ok else CHECKS, **counts)
    finally:
        held.stop()


def _head(worktree: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True, check=True
    ).stdout.strip()


def held_by(
    issues: IssueClient,
    sink: EventSink,
    ws: Any,
    *,
    repo: str,
    store: Path,
    run_checks: Callable[[Path, str], MainCheck],
    keep: bool,
) -> str:
    """Why main doesn't open the Task now, or "" when it does. `keep` writes what a head
    did to the store; without it this only reads, as the proof's run does."""
    tasks = issues.labelled(repo, role_tasks.DEVOPS.label)

    def found(kind: str) -> dict[str, Any] | None:
        wanted = role_tasks.marker(role_tasks.DEVOPS, kind, "repository")
        return next((i for i in tasks if wanted in (i.get("body") or "")), None)

    if found(role_tasks.CI_SERVICES) is not None:
        return "its CI Task is already opened"
    first = found(role_tasks.FIRST_WORKSPACE)
    if first is None or first.get("state") != "closed":
        return "its first workspace Task isn't closed"
    sha = issues.head_sha(repo, issues.default_branch(repo))
    seen = checked_at(repo, sha, store=store)
    if seen is None:
        if not services.declared(lambda path: issues.file_at(repo, path, sha)).services:
            seen = MainCheck(NO_SERVICES)
        else:
            worktree = ws.for_repo(repo).at_main()
            # Main may have moved since its head was read: what ran is what's kept.
            sha = _head(worktree)
            seen = run_checks(worktree, f"main-{repo}")
            if seen.outcome != PASSED:
                sink.note(
                    EventKind.NOTE,
                    f"{repo} main's checks at {sha[:12]} didn't pass: {seen.outcome}",
                    repo=repo,
                    sha=sha,
                    rule="decision.role_tasks.ci_services",
                    **asdict(seen),
                )
        if keep and seen.definitive:
            path = stored(repo, sha, store=store)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(seen)))
    if seen.outcome != PASSED:
        return f"main at {sha[:12]}: {seen.outcome}"
    return ""


def ci_services_from_main(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    ws: Any,
    *,
    repos: set[str],
    cards: list[Card],
    store: Path = STORE,
    run_checks: Callable[[Path, str], MainCheck] = run_main_checks,
) -> FromMain:
    """Each repository whose main passes its checks with its services gets DevOps's CI
    Task, as a story's `done` would open it. One that fails is recorded, and the next
    pass tries again.
    """
    result = FromMain()
    for repo in sorted(repos):
        try:
            why = held_by(
                issues, sink, ws, repo=repo, store=store, run_checks=run_checks, keep=True
            )
            if why:
                result.held.append((repo, why))
                continue
            opened = role_tasks.ci_services_task(
                issues, board, sink, repo=repo, cards=cards, path="main"
            )
        except Exception as exc:  # noqa: BLE001 - the next pass tries again
            reraise_if_down(exc)
            why = role_tasks.not_opened(
                sink,
                repo=repo,
                role=role_tasks.DEVOPS,
                kind=role_tasks.CI_SERVICES,
                card=None,
                why=exc,
            )
            result.held.append((repo, why))
            continue
        if opened is not None and opened.new:
            result.opened.append((repo, opened.number))
    return result
