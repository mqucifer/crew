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

from pydantic import ValidationError

from crew_org.crews.panel_crew import ASKS, PanelContext, PanelResult, Sibling
from crew_org.flows import record as record_flow
from crew_org.flows.decisions import collect_decisions
from crew_org.flows.decisions import render as render_decisions
from crew_org.flows.project_log import read_log
from crew_org.permissions import load_agents
from crew_org.tools.github_issues import IssueClient

PANEL_MARKER = "<!-- crew:panel -->"
# The notes again as data, invisible on GitHub. The settle step may run ticks
# after the panel (it waits on a Sponsor reply), and the notes it numbers are
# these, not a re-run: a second run would not give the same notes.
_DATA = re.compile(r"<!-- crew:panel-data (.*?) -->", re.S)

_WHO = {
    "product_owner": "Product Owner",
    "architect": "Architect",
    "sponsor": "Sponsor",
    "infra": "Infra",
}

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


def _record(issue: dict[str, Any]) -> str:
    return record_flow.split(issue.get("body") or "")[1]


def _with_record(issue: dict[str, Any]) -> str:
    record = _record(issue)
    return f"{_text(issue)}\n\n{record}" if record else _text(issue)


def _carried(issue: dict[str, Any]) -> str:
    """A superseded epic's binding rows, without the plan that was set aside."""
    binding = record_flow.parse(_record(issue)).binding()
    if not binding:
        return ""
    rows = "\n".join(f"- {d.id}: {d.context}: {d.decision} ({d.set_by})" for d in binding)
    return (
        f"# {issue.get('title', '')}\n\nSet aside, and its plan isn't to be followed. Its "
        f"binding rows still hold for this Goal unless a later row replaces them:\n\n{rows}"
    )


def siblings_of(issues: IssueClient, repo: str, goal: int, epic: int) -> list[Sibling]:
    """The Goal's other epics, each with its record (ADR 0023, crew#611).

    Open ones and delivered ones are shown with what they decided: the text alone
    left out the record below the approval footer, and sprint-metrics#552's panel
    and Architect settled the same schema as sprint-metrics#551 differently. One closed as not
    planned describes a plan set aside, so only its binding rows are carried.
    """
    found = []
    for child in issues.sub_issues(repo, goal):
        if child["number"] == epic:
            continue
        ref = f"{issues.owner}/{repo}#{child['number']}"
        if child.get("state") == "open":
            found.append(Sibling(ref, "open", _with_record(child)))
        elif child.get("state_reason") == "completed":
            found.append(Sibling(ref, "delivered", _with_record(child)))
        elif carried := _carried(child):
            found.append(Sibling(ref, "superseded", carried))
    return found


def gather(issues: IssueClient, repo: str, epic: int, *, search: Iterable[str]) -> PanelContext:
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
        decisions=render_decisions(collected, goal=f"{ref}#{goal}"),
        epic_ref=f"{ref}#{epic}",
        epic=_text(card),
        siblings=siblings_of(issues, repo, goal, epic),
        project_log=read_log(issues, repo),
    )


def render(result: PanelResult) -> str:
    """The panel's answers as the epic's comment, each member under its own question."""
    roles = load_agents()
    items = [item for _, item in result.items()]
    by = {who: sum(1 for i in items if i.settled_by == who) for who in _WHO}
    lines = [
        PANEL_MARKER,
        "## Refinement panel",
        "",
        f"{len(items)} items from {len(result.answers)} members before the split: "
        + ", ".join(f"{count} for the {_WHO[who]}" for who, count in by.items())
        + ". Kept as the record of the discussion; the epic's conclusion is separate.",
        "",
    ]
    for role, answer in result.answers.items():
        ask = ASKS[role]
        lines += [f"### {roles[role]['role']}: {ask.question}", ""]
        if view := answer.view():
            lines += [f"- {view}", ""]
        if not answer.items():
            lines += [ask.none, ""]
            continue
        for i in answer.items():
            # An empty item, after all four sessions ran, is shown as one, not a crash.
            first, *rest = i.text.strip().splitlines() or ["(empty)"]
            lines += [f"- **{first}**", *[f"  - {r.strip()}" for r in rest]]
            lines += [f"  - Source: {i.source}", f"  - Settled by: {_WHO[i.settled_by]}"]
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
    """The panel's answers from its comment, or None if it carries none.

    A panel from before each member had its own form (ADR 0035) reads as none, so the
    panel runs again on an epic not yet settled. A settled epic's record is what a
    re-split reads, and its panel isn't read again.
    """
    found = _DATA.search(comment)
    if not found:
        return None
    data = json.loads(found.group(1))
    try:
        answers = {role: _answer(role, a) for role, a in data["answers"].items()}
    except (KeyError, ValidationError):
        return None
    return PanelResult(answers, data["failed"])


def _answer(role: str, data: dict[str, Any]) -> Any:
    form = ASKS[role].form
    # An old answer's `notes` would otherwise read as an empty new one.
    if not set(data) <= set(form.model_fields):
        raise KeyError(f"{role}'s answer isn't a {form.__name__}")
    return form.model_validate(data)


def panel_on(issues: IssueClient, repo: str, epic: int) -> PanelResult | None:
    """The panel already run on this epic: its latest comment that carries notes."""
    for comment in reversed(issues.comments(repo, epic)):
        body = comment.get("body") or ""
        if PANEL_MARKER in body and (found := read_panel(body)):
            return found
    return None


def post(issues: IssueClient, repo: str, epic: int, result: PanelResult) -> bool:
    """Post the notes once per epic. False if a panel that reads is already there.

    A panel from before the four forms stays as it was, and the new one goes below it:
    refusing on its marker alone left `panel_on` with nothing to read, so the panel ran
    again every tick the settle waited on the Sponsor, and its N-numbers drifted from
    the ones the question cited.
    """
    if panel_on(issues, repo, epic) is not None:
        return False
    issues.comment(repo, epic, render(result))
    return True
