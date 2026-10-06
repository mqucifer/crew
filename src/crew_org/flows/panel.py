"""The refinement panel's context, and its notes as the epic's comment (crew#440).

`gather` collects what every member is shown: the Goal, the project's record,
the Sponsor's decisions for the Goal (crew#440, `decisions.py`), the epic, and
its sibling epics. `render` writes the notes as one marked comment, the audit
trail of the discussion. It is kept, and never passed on (the settle step
writes the short conclusion the later steps read).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from crew_org.crews.panel_crew import PanelAnswer, PanelContext, PanelResult, Sibling
from crew_org.flows.decisions import collect_decisions
from crew_org.flows.decisions import render as render_decisions
from crew_org.permissions import load_agents
from crew_org.tools.github_issues import IssueClient

PANEL_MARKER = "<!-- crew:panel -->"
# The notes again as data, invisible on GitHub. The settle step may run ticks
# after the panel (it waits on a Sponsor reply), and the notes it numbers are
# these, not a re-run: a second run would not give the same notes.
_DATA = re.compile(r"<!-- crew:panel-data (.*?) -->", re.S)

_WHO = {"product_owner": "Product Owner", "architect": "Architect", "sponsor": "Sponsor"}

# The Product Owner's approval footer, which is the crew's, not the epic's.
_FOOTER = re.compile(r"\n---\n\s*\nProposed by the Product Owner")


class NotUnderAGoal(ValueError):
    """The epic has no parent, so there is no Goal to read it against."""


def _text(issue: dict[str, Any]) -> str:
    body = _FOOTER.split((issue.get("body") or "").strip())[0].strip()
    return f"# {issue.get('title', '')}\n\n{body}"


def _parent(issue: dict[str, Any]) -> int | None:
    found = re.search(r"/issues/(\d+)$", issue.get("parent_issue_url") or "")
    return int(found.group(1)) if found else None


def siblings_of(issues: IssueClient, repo: str, goal: int, epic: int) -> list[Sibling]:
    """The Goal's other epics: open ones, and delivered ones as background.

    One closed as not planned was set aside by the Sponsor; it describes a plan
    the epic is not to be read against.
    """
    found = []
    for child in issues.sub_issues(repo, goal):
        if child["number"] == epic:
            continue
        ref = f"{issues.owner}/{repo}#{child['number']}"
        if child.get("state") == "open":
            found.append(Sibling(ref, "open", _text(child)))
        elif child.get("state_reason") == "completed":
            found.append(Sibling(ref, "delivered", _text(child)))
    return found


def gather(
    issues: IssueClient, repo: str, epic: int, *, project: str, search: Iterable[str]
) -> PanelContext:
    """What the panel is shown for `epic`. `search` is where decisions are looked for."""
    card = issues.get(repo, epic)
    goal = _parent(card)
    if goal is None:
        raise NotUnderAGoal(f"{repo}#{epic} has no parent Goal")
    parent = issues.get(repo, goal)
    ref = f"{issues.owner}/{repo}"
    collected = collect_decisions(issues, goal_repo=repo, goal=goal, repos=search)
    return PanelContext(
        goal_ref=f"{ref}#{goal}",
        goal=f"{parent.get('title', '')}\n\n{(parent.get('body') or '').strip()}".strip(),
        project=project,
        decisions=render_decisions(collected, goal=f"{ref}#{goal}"),
        epic_ref=f"{ref}#{epic}",
        epic=_text(card),
        siblings=siblings_of(issues, repo, goal, epic),
    )


def render(result: PanelResult) -> str:
    """The panel's notes as the epic's comment."""
    roles = load_agents()
    notes = [n for a in result.answers.values() for n in a.notes]
    by = {who: sum(1 for n in notes if n.settled_by == who) for who in _WHO}
    lines = [
        PANEL_MARKER,
        "## Refinement panel",
        "",
        f"{len(notes)} notes from {len(result.answers)} members before the split: "
        + ", ".join(f"{count} for the {_WHO[who]}" for who, count in by.items())
        + ". Kept as the record of the discussion; the epic's conclusion is separate.",
        "",
    ]
    for role, answer in result.answers.items():
        lines += [f"### {roles[role]['role']}", ""]
        if answer.nothing_to_add:
            lines += ["Nothing to add.", ""]
            continue
        for n in answer.notes:
            lines += [
                f"- **{n.problem}**",
                f"  - Source: {n.source}",
                f"  - To settle: {n.settle}",
                f"  - Settled by: {_WHO[n.settled_by]}",
            ]
        lines.append("")
    for role, why in result.failed.items():
        lines += [f"### {roles[role]['role']}", "", f"Did not answer: {why}", ""]
    data = {
        "answers": {role: a.model_dump() for role, a in result.answers.items()},
        "failed": result.failed,
    }
    # `--` can't appear inside an HTML comment; JSON reads `-` back as `-`.
    escaped = json.dumps(data).replace("--", "\\u002d\\u002d")
    lines += ["", f"<!-- crew:panel-data {escaped} -->"]
    return "\n".join(lines).rstrip() + "\n"


def read_panel(comment: str) -> PanelResult | None:
    """The panel's notes from its comment, or None if it carries none."""
    found = _DATA.search(comment)
    if not found:
        return None
    data = json.loads(found.group(1))
    return PanelResult(
        {role: PanelAnswer.model_validate(a) for role, a in data["answers"].items()},
        data["failed"],
    )


def panel_on(issues: IssueClient, repo: str, epic: int) -> PanelResult | None:
    """The panel already run on this epic: its latest comment that carries notes."""
    for comment in reversed(issues.comments(repo, epic)):
        body = comment.get("body") or ""
        if PANEL_MARKER in body and (found := read_panel(body)):
            return found
    return None


def post(issues: IssueClient, repo: str, epic: int, result: PanelResult) -> bool:
    """Post the notes once per epic. False if a panel comment is already there."""
    if issues.has_comment_marked(repo, epic, PANEL_MARKER):
        return False
    issues.comment(repo, epic, render(result))
    return True
