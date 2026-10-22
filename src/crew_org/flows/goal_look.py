"""The Goal's look on GitHub: what it's shown, and its answers as the Goal's comment (crew#858).

`gather` collects what both roles are shown: the Goal, the Sponsor's decisions for it
(`decisions.py`), the project's decision log, and its epics. Those it reads for are the
approved ones not yet in the Goal's record; the ones the record already covers, and
the delivered ones, come with them as what's settled. A later look is shown the
Goal's record and target diagram as they are.

`render` writes both answers as one marked comment on the Goal, with the answers
again as data. It is the audit trail of the look, and the settle reads its answers
from it, so a settle that waits on the Sponsor's reply settles the same answers a
tick later, not a second look's.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from crew_org.crews.goal_look_crew import (
    APPROVED,
    LOOKED_AT,
    EpicText,
    GoalContext,
    LookResult,
)
from crew_org.flows import record as record_flow
from crew_org.flows.decisions import collect_decisions
from crew_org.flows.decisions import render as render_decisions
from crew_org.flows.project_log import read_log
from crew_org.tools.github_issues import IssueClient

LOOK_MARKER = "<!-- crew:goal-look -->"
_DATA = re.compile(r"<!-- crew:goal-look-data (.*?) -->", re.S)
DIAGRAM_MARKER = "<!-- crew:goal-diagram -->"
# The diagram's source again, so a later look and the readers take it as written.
_DIAGRAM = re.compile(r"```mermaid\n(.*?)\n```", re.S)

# The Product Owner's approval footer, which is the crew's, not the epic's.
_FOOTER = re.compile(r"\n---\n\s*\nProposed by the Product Owner")


def _text(issue: dict[str, Any]) -> str:
    body = record_flow.split(issue.get("body") or "")[0]
    body = _FOOTER.split(body.strip())[0].strip()
    return f"# {issue.get('title', '')}\n\n{body}"


def diagram_comment(issues: IssueClient, repo: str, goal: int) -> dict[str, Any] | None:
    """The Goal's current target diagram comment: the latest, or None."""
    found = [c for c in issues.comments(repo, goal) if DIAGRAM_MARKER in (c.get("body") or "")]
    return found[-1] if found else None


def diagram_of(issues: IssueClient, repo: str, goal: int) -> str:
    """The Goal's target diagram's Mermaid source, or "" before its look."""
    comment = diagram_comment(issues, repo, goal)
    found = _DIAGRAM.search((comment or {}).get("body") or "")
    return found.group(1).strip() if found else ""


def gather(
    issues: IssueClient,
    repo: str,
    goal: int,
    *,
    approved: Iterable[int],
    search: Iterable[str],
) -> GoalContext:
    """What the look is shown for `goal`. `approved` is the epics it reads for, of those
    the record doesn't cover yet; `search` is where the Sponsor's decisions are looked for."""
    parent = issues.get(repo, goal)
    ref = f"{issues.owner}/{repo}"
    held = record_flow.goal_record_comment(issues, repo, goal)
    record = record_flow.comment_text(held.get("body") or "") if held else ""
    covered = set(record_flow.parse(record).looked_at) if record else set()
    wanted = set(approved)
    epics: list[EpicText] = []
    for child in issues.sub_issues(repo, goal):
        epic = f"{ref}#{child['number']}"
        if child.get("state") == "open" and epic in covered:
            epics.append(EpicText(epic, LOOKED_AT, _text(child)))
        elif child.get("state") == "open" and child["number"] in wanted:
            epics.append(EpicText(epic, APPROVED, _text(child)))
        elif child.get("state") != "open" and child.get("state_reason") == "completed":
            epics.append(EpicText(epic, "delivered", _text(child)))
    collected = collect_decisions(issues, goal_repo=repo, goal=goal, repos=search)
    return GoalContext(
        goal_ref=f"{ref}#{goal}",
        goal=f"{parent.get('title', '')}\n\n{(parent.get('body') or '').strip()}".strip(),
        decisions=render_decisions(collected, goal=f"{ref}#{goal}"),
        epics=epics,
        project_log=read_log(issues, repo),
        record=record,
        diagram=diagram_of(issues, repo, goal),
    )


def _list(items: list[str]) -> list[str]:
    return [f"- {i}" for i in items] or ["- (none)"]


def render(result: LookResult, context: GoalContext) -> str:
    """Both answers as the Goal's comment, with the answers as data."""
    lines = [
        LOOK_MARKER,
        "## The Goal's look",
        "",
        "The Architect and the UX Designer read "
        + ", ".join(context.looking_at)
        + " before any is split. Kept as the record of the look; the Goal's record is "
        "what the Product Owner settles from it.",
        "",
    ]
    if (system := result.system) is not None:
        lines += ["### Architect", "", "**Already supported:**", ""]
        lines += _list([f"{s.what} ({s.where})" for s in system.supported])
        lines += ["", "**Decided:**", ""]
        lines += _list(
            [
                f"**{d.context}:** {d.decision} Weighed: {d.weighed} Binds: {', '.join(d.epics)}."
                for d in system.decided
            ]
        )
        lines += ["", "**Risks and seams:**", ""]
        lines += _list([f"{c.risk} ({', '.join(c.epics)})" for c in system.callouts])
        if system.beyond_the_goal:
            lines += ["", "**Beyond the Goal, for the Sponsor:**", ""]
            lines += _list([f"{b.question} {b.why}" for b in system.beyond_the_goal])
        lines.append("")
    if (reader := result.reader) is not None:
        lines += ["### UX Designer", "", f"**The reader:** {reader.reader}", ""]
        for view in reader.epics:
            lines += [
                f"- **{view.epic}:** {view.meets} Missing: {view.missing}",
                *(f"  - {c}" for c in view.criteria),
            ]
        lines.append("")
    for role, why in result.failed.items():
        lines += [f"### {role}", "", f"Did not answer: {why}", ""]
    data = {
        "looked_at": context.looking_at,
        "system": result.system.model_dump() if result.system else None,
        "reader": result.reader.model_dump() if result.reader else None,
        "failed": result.failed,
    }
    # `--` can't appear inside an HTML comment; JSON reads `-` back as `-`.
    escaped = json.dumps(data).replace("--", "\\u002d\\u002d")
    lines += ["", f"<!-- crew:goal-look-data {escaped} -->"]
    return "\n".join(lines).rstrip() + "\n"
