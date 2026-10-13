"""delivery-history as it stands now: gathered from the crew's events, the board and GitHub.

`crew package build` and `crew package push` use it, and so does the tick, which
pushes to sprint-metrics after each pass (crew#280). Only the package's commands
pass `answers`: the tick's push builds it without sprint-metrics' part, as before.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crew_org.clock import sprint_today
from crew_org.clock import timezone as clock_zone
from crew_org.packages import delivery_history as dh
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import ProjectClient


def history_now(
    board: ProjectClient,
    issues: IssueClient,
    *,
    org: dict[str, Any],
    crew_repo: str,
    events_dir: Path,
    answers: Callable[[list[dh.Sprint]], dh.SprintMetrics | None] | None = None,
) -> tuple[dh.DeliveryHistory, dict[str, dh.PeriodEvents], dh.Manifest]:
    """delivery-history up to today, by the sprint clock.

    `answers`, given the sprints started by today, returns sprint-metrics' part.
    """
    events: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(events_dir.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                events.append((path.stem, json.loads(line)))
            except ValueError:
                continue
    cards = board.cards()
    # Every repository with cards on the board, paused or not: a pause doesn't
    # take a project out of the history.
    repositories = sorted({c.repo for c in cards if c.repo})
    pulls = {repo: issues.all_pulls(repo) for repo in [*repositories, crew_repo]}
    sprints = dh.sprints_from(board.sprints())
    today = sprint_today(org)
    started = sorted((s for s in sprints if s.start <= today), key=lambda s: s.start)
    return dh.build(
        dh.Inputs(
            events=events,
            cards=cards,
            sprints=sprints,
            pulls=pulls,
            crew_repository=crew_repo,
            repositories=repositories,
            timezone=clock_zone(org),
            date=today,
            generated=datetime.now(UTC),
            sprint_metrics=answers(started) if answers else None,
        )
    )
