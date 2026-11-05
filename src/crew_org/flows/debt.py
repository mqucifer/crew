"""Technical debt a role finds is filed and paid by the crew, with no Sponsor step
(the Sponsor, mqucifer/crew#1033, 2026-11-05).

Debt is code that is dead, duplicated or misleading, where fixing it changes no
behaviour. It had no route that ended in a fix: mqucifer/sprint-metrics#995, an unused
parameter the Code Reviewer found, was filed as a bug because no role's Task may change
its file (crew#1018), and the Business Analyst closed it, fairly, since nothing behaved
wrongly. The bug gate couldn't have taken its fix either: dead code fails no test on main.

So debt is a technical epic, as the Architect's design changes are, marked `debt`:
filed from a finding that names it, or by triage from a bug that changes no behaviour.
It needs no Goal, is split by the Business Analyst like any technical epic, and its
stories carry the mark. Unlike other technical work it never holds a Goal's epics:
sprint planning takes one debt story per project ahead of Goal work while the sprint
has none, and the rest after it (`sprint.plan_sprint`). Its proof is refactoring under
unchanged tests (Fowler, *Refactoring*): the project's tests pass, none removed and
none checking less (`gates.debt_kept`).
"""

from __future__ import annotations

from typing import Any

from crew_org.columns import NEEDS_REFINEMENT
from crew_org.events import EventSink
from crew_org.flows.moves import move_card
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient

DEBT = "debt"
# `board_flow.TECHNICAL`, which this can't import: board_flow imports role_tasks, which
# files debt from here.
TECHNICAL = "technical"
EPIC_TYPE = "Epic"
LABELS = {
    TECHNICAL: ("5319e7", "Decided by the crew: how the project is built (crew#192)"),
    DEBT: ("bfd4f2", "Technical debt the crew found and pays: no behaviour changes (crew#1033)"),
}
# On a debt epic filed from a finding, so the same finding isn't filed twice: the mark a
# finding's bug carries, so delivery-history reads it as filed from a finding (crew#974).
_MARKER = "<!-- crew:finding key={key} -->"
# What a debt epic's stories prove, and the standard it follows.
PROOF = (
    "**Its proof:** refactoring under unchanged tests. The evidence above is gone, and "
    "nothing behaves differently: the project's tests pass unchanged, none removed and "
    "none checking less. A test may change only where it calls what the work removes."
)
ROUTE = (
    "Technical debt: the crew found it, so it goes straight to refinement with no Goal "
    "and no Sponsor step, and never holds this project's Goal epics (mqucifer/crew#1033)."
)


def is_debt(card: Card) -> bool:
    """A debt epic, or a story split from one."""
    return DEBT in card.labels


def ensure_labels(issues: IssueClient, repo: str) -> None:
    for name, (color, description) in LABELS.items():
        issues.ensure_label(repo, name, color=color, description=description)


def file_debt_epic(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    repo: str,
    key: str,
    title: str,
    what: str,
    why: str,
    by: str,
) -> int:
    """A debt epic for a role's finding, in Needs Refinement, filed once per `key`."""
    wanted = _MARKER.format(key=key)
    found: dict[str, Any] | None = next(
        (i for i in issues.labelled(repo, DEBT) if wanted in (i.get("body") or "")), None
    )
    if found is not None:
        return int(found["number"])
    ensure_labels(issues, repo)
    issue = issues.create(
        repo,
        title,
        f"{wanted}\n**The work:** {what.strip() or title}\n\n{why}\n\n{PROOF}\n\n{ROUTE}",
        labels=[TECHNICAL, DEBT],
    )
    item = board.add_issue(issue["node_id"])
    move_card(
        board,
        sink,
        item_id=item,
        to=NEEDS_REFINEMENT,
        by=by,
        card=issue["number"],
        summary=f"technical debt — {title}"[:100],
        repo=repo,
    )
    board.set_select(item, "Work Type", EPIC_TYPE)
    return int(issue["number"])


def from_bug(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    card: Card,
    *,
    repo: str,
    report: str,
    reason: str,
    by: str,
) -> None:
    """A bug triage found changes no behaviour becomes a debt epic: the same issue, its
    report kept, typed an epic and left in refinement for its split."""
    number = card.number or 0
    ensure_labels(issues, repo)
    issues.edit_issue(
        repo,
        number,
        body=f"{report.rstrip()}\n\n---\n\n"
        f"Triaged as technical debt by the {by}: {reason}\n\n{PROOF}\n\n{ROUTE}",
    )
    issues.add_labels(repo, number, [TECHNICAL, DEBT])
    issues.remove_label(repo, number, "bug")
    board.set_select(card.item_id, "Work Type", EPIC_TYPE)
