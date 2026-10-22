"""What every writer of a record shares: its cells, and "the Goal points elsewhere".

The per-epic settle that lived here went with the per-epic panel (crew#858): the
Goal's look and its settle (`flows/goal_look.py`) write the Goal's record. What's
left is used by every step that writes a row: references made explicit and linked
(`explicit`), the Product Owner's own call said as such (`_source`), and the Goal
pointing elsewhere, brought to the Sponsor on the Goal (`_points_elsewhere`).
"""

from __future__ import annotations

import re
from typing import Any

from crew_org.crews.goal_look_crew import GoalContext
from crew_org.crews.settle_crew import Elsewhere, Row
from crew_org.events import EventSink
from crew_org.flows import artifacts, proposed_goal
from crew_org.flows import record as record_flow
from crew_org.tools.github_issues import IssueClient

NEEDS_HUMAN = "needs:human"
# Rows that decide something for every epic go into the project's decision log
# too (crew#468). The record names them, and the label says the log hasn't
# caught up yet; the revisit phase, which has the clone and the approving
# identity, writes them and takes the label off once they've merged.
TO_LOG_LABEL = "decision:to-log"
_BARE = re.compile(r"(?<![\w/#])#(\d+)\b")


def _cell(text: str, *, owner: str, repo: str, known: set[str]) -> str:
    """One table cell: references explicit and linked, and nothing that breaks the table.

    A bare number means this epic's repository: the members copy the epics'
    own bare numbers, and a conclusion is written into that repository
    (crew#456).
    """
    text = " ".join(text.split()).replace("|", "\\|")
    text = _BARE.sub(lambda m: f"{owner}/{repo}#{m.group(1)}", text)
    # `repo#n` for a repository the crew knows, qualified with the owner.
    word = "|".join(sorted((re.escape(k) for k in known), key=len, reverse=True))
    if word:
        text = re.sub(
            rf"(?<![\w/.\[-])({word})#(\d+)\b", lambda m: f"{owner}/{m.group(1)}#{m.group(2)}", text
        )
    return re.sub(
        r"(?<![\w/.\[-])([A-Za-z0-9][\w.-]*)/([A-Za-z0-9][\w.-]*)#(\d+)\b",
        lambda m: (
            f"[{m.group(0)}](https://github.com/{m.group(1)}/{m.group(2)}/issues/{m.group(3)})"
        ),
        text,
    )


# A Goal's reference as the panel's context gives it: owner/repo#number.
_GOAL_REF = re.compile(r"([\w.-]+)#(\d+)$")


def explicit(text: str, *, owner: str, repo: str) -> str:
    """A cell for the epic's record, from any writer: references explicit and linked."""
    return _cell(text, owner=owner, repo=repo, known={repo})


def _source(row: Row) -> str:
    """A call the Product Owner made itself says so, and the Goal's intent it serves."""
    if not row.own_call:
        return row.source
    return f'{record_flow.OWN_CALL}, serving the Goal: "{row.serves}". {row.source}'


def _points_elsewhere(
    board: Any,
    issues: IssueClient,
    sink: EventSink,
    *,
    context: GoalContext,
    epic: str,
    elsewhere: Elsewhere,
) -> None:
    """ "The Goal points elsewhere", on the Goal, where its direction is the Sponsor's
    (ADR 0018). The Goal's record is written and goes on regardless."""
    found = _GOAL_REF.search(context.goal_ref)
    assert found is not None, context.goal_ref
    repo, goal = found.group(1), int(found.group(2))
    proposed = proposed_goal.bring(
        issues,
        board,
        sink,
        repo=repo,
        goal=goal,
        elsewhere=elsewhere,
        step="goal settle",
        rule="decision.goal_look.look_step",
    )
    asked = (
        f"**A question for the Sponsor about this Goal.** {elsewhere.question}"
        if elsewhere.question
        else f"**A Goal I propose:** {proposed}, a draft in `Inbox (Goals)`. Add the `goal` "
        "label to approve it, edit it first, or close it."
    )
    artifacts.comment(
        issues,
        sink,
        repo=repo,
        number=goal,
        body=f"### The Goal points elsewhere\n\nFrom settling {epic}'s look: "
        f"{elsewhere.why}\n\n{asked}",
        by="Product Owner",
    )
    artifacts.label(
        issues,
        sink,
        repo=repo,
        number=goal,
        by="Product Owner",
        add=[NEEDS_HUMAN],
        rule="decision.goal_look.look_step",
    )
