"""A sprint closes itself once its time is up, at a tick's start or end (ADR 0028).

The close was run by hand, so a sprint ran on past its dates until someone did.
Sprint 22 ended at midnight on 2026-10-11, and its first tick into Sprint 23 still
found it open. Cards finished after midnight counted in Sprint 22 for
sprint-metrics, which goes by the day, and in Sprint 21 for the close, which goes
by the board's Sprint field. The two disagreed on what Sprint 22 delivered.

A close is due for a sprint whose last day is past, that has cards, and whose
retro isn't recorded yet. That last condition makes it once per sprint: whatever
closes it first, a tick or `crew sprint close`, records the retro, and nothing
closes it again.

Nothing moves columns at a close. Stories not started go back to Ready at the
next admission (`sprint.left_over`), and started work carries on whatever sprint
it's in. Only approved, unmerged stories are carried by the close itself.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from crew_org.flows.close import sprint_cards
from crew_org.flows.retro import existing_retro
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import ProjectClient


def _last_day(it: dict[str, Any]) -> date:
    days = int(it.get("duration", 14))
    return date.fromisoformat(it["startDate"]) + timedelta(days=days - 1)


def ended_on(sprints: list[dict[str, Any]], sprint: str | None) -> date | None:
    """The last day of `sprint`, from all the board's sprints, or None."""
    found = [it for it in sprints if str(it["title"]) == sprint]
    return _last_day(found[0]) if found else None


def last_end(sprints: list[dict[str, Any]], today: date) -> date | None:
    """The last day of the sprint that ended most recently before `today`, or None."""
    past = [_last_day(it) for it in sprints if _last_day(it) < today]
    return max(past) if past else None


def last_ended(sprints: list[dict[str, Any]], today: date) -> str | None:
    """The sprint that ended most recently before `today`, from all the board's sprints.

    From `ProjectClient.sprints()`, not the board's schema: the schema keeps only
    current and planned sprints, so a finished one isn't in it.
    """
    past = [(_last_day(it), str(it["title"])) for it in sprints if _last_day(it) < today]
    return max(past)[1] if past else None


def due(
    board: ProjectClient, issues: IssueClient, *, sprint: str | None, today: date, crew_repo: str
) -> bool:
    """Whether `sprint` is past its last day, has cards, and has no retro yet."""
    if not sprint:
        return False
    found = [it for it in board.sprints() if str(it["title"]) == sprint]
    if not found or today <= _last_day(found[0]):
        return False
    if existing_retro(issues, crew_repo, sprint) is not None:
        return False
    return bool(sprint_cards(board.cards(), sprint))
