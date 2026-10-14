"""A new project's first Goal, its walking skeleton (crew#690, ADR 0030).

The same text for every project, kept in `config/skeleton_goal.md`. `crew
onboard` files it on the Sponsor's yes, which is their authorship: a Goal is the
only thing the Sponsor writes, and this one they agreed once, for every project.
Refinement takes it from the crew's own identity only when it carries the
`skeleton` label, which only someone with triage on the repository can add.

What is the same in every project, such as CI and `board.yml`, isn't the
skeleton's: it comes from the template (`flows/template.py`).
"""

from __future__ import annotations

from typing import Any

from crew_org.columns import INBOX
from crew_org.config import CONFIG_DIR
from crew_org.flows.readiness import SKELETON_LABEL

GOAL_TEXT = CONFIG_DIR / "skeleton_goal.md"

# What a placeholder says, in a comment in any language (D2). The removal guard
# lets the story that replaces it go without a declared contract change.
PLACEHOLDER = "crew:placeholder"

LABELS = {
    "goal": ("0e8a16", "A Goal: the Sponsor's outcome for the crew to decompose"),
    SKELETON_LABEL: ("c5def5", "A project's first Goal: its walking skeleton (ADR 0030)"),
}


def goal() -> tuple[str, str]:
    """The skeleton Goal's title and body."""
    title, _, body = GOAL_TEXT.read_text(encoding="utf-8").partition("\n---\n")
    return title.strip(), body.strip() + "\n"


def file_goal(issues: Any, board: Any, repo: str) -> int | None:
    """File the skeleton Goal on `repo`, in Inbox (Goals). None if one is open already."""
    if any(i.get("state") == "open" for i in issues.labelled(repo, SKELETON_LABEL)):
        return None
    for name, (color, description) in LABELS.items():
        issues.ensure_label(repo, name, color=color, description=description)
    title, body = goal()
    issue = issues.create(repo, title, body, labels=["goal", SKELETON_LABEL])
    item = board.add_issue(issue["node_id"])
    board.set_status(item, INBOX)
    board.set_select(item, "Work Type", "Goal")
    return int(issue["number"])
