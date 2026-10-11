"""One view of everything a Goal's epics have decided (ADR 0023, crew#611).

Each epic keeps its own record. The Goal shows an index of every row across
them, so the Sponsor, and anyone reading the Goal, sees the whole of what was
decided in one place: configuration status accounting (ISO 10007), with the
Goal as the baseline its epics sit under. It is one comment the crew keeps
current, rewritten whenever an epic's record changes, never a second record:
the rows live in the epics, and this only points at them.
"""

from __future__ import annotations

import re
from typing import Any

from crew_org.events import EventKind, EventSink
from crew_org.flows import record as record_flow

INDEX_MARKER = "<!-- crew:goal-index -->"


def _parent(issue: dict[str, Any]) -> int | None:
    found = re.search(r"/issues/(\d+)$", issue.get("parent_issue_url") or "")
    return int(found.group(1)) if found else None


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def render(issues: Any, repo: str, goal: int) -> str:
    """The index: each epic's binding rows and open questions, in the Goal's order."""
    rows = []
    for child in issues.sub_issues(repo, goal):
        if child.get("state") == "open":
            state = "open"
        elif child.get("state_reason") == "completed":
            state = "delivered"
        else:
            state = "superseded"
        _, text = record_flow.split(child.get("body") or "")
        record = record_flow.parse(text) if text else record_flow.Record()
        epic = f"{issues.owner}/{repo}#{child['number']}"
        for d in record.binding():
            rows.append(
                f"| {epic} ({state}) | {d.id} | {_cell(d.context)} | {_cell(d.decision)} "
                f"| {_cell(d.set_by)} | {d.replaces or '—'} |"
            )
        for q in record.open:
            rows.append(
                f"| {epic} ({state}) | {q.id} | {_cell(q.question)} | *open* "
                f"| {_cell(q.settled_by)} | — |"
            )
    if not rows:
        return ""
    return "\n".join(
        [
            INDEX_MARKER,
            "**What this Goal's epics have decided.** Kept current by the crew. Each row "
            "lives in its epic's record; this is the index.",
            "",
            "| Epic | ID | Context | Decision | Set by | Replaces |",
            "|---|---|---|---|---|---|",
            *rows,
        ]
    )


def refresh(issues: Any, sink: EventSink, *, repo: str, goal: int) -> None:
    """Write the Goal's index, or rewrite the one already there. Nothing if unchanged."""
    body = render(issues, repo, goal)
    if not body:
        return
    mine = [c for c in issues.comments(repo, goal) if INDEX_MARKER in (c.get("body") or "")]
    if mine and (mine[-1].get("body") or "").strip() == body.strip():
        return
    if mine:
        issues.edit_comment(repo, mine[-1]["id"], body)
    else:
        issues.comment(repo, goal, body)
    sink.note(EventKind.NOTE, f"#{goal} goal index updated", card=goal)


def refresh_for_epic(issues: Any, sink: EventSink, *, repo: str, epic: int) -> None:
    """The index of the Goal this epic sits under, after its record changed.

    A view: a failure here never fails the edit that called it.
    """
    try:
        goal = _parent(issues.get(repo, epic))
        if goal is not None:
            refresh(issues, sink, repo=repo, goal=goal)
    except Exception as exc:  # noqa: BLE001
        sink.note(EventKind.NOTE, f"#{epic}: goal index not updated: {exc}"[:120], card=epic)
