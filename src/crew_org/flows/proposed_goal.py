"""A Goal the Product Owner proposes, when the Goal points elsewhere (crew#846, ADR 0027).

Serving a Goal's reader can need something its intent doesn't cover. The
Product Owner then proposes a Goal, written as intent (ADR 0035), and it is
filed as a draft in Inbox (Goals) without the `goal` label. The Sponsor
approves it by adding the label, edits it first, or closes it: the gate a
drafted Goal already has (ADR 0003). Until then nothing decomposes it.
"""

from __future__ import annotations

from typing import Any

from crew_org.columns import INBOX
from crew_org.crews.settle_crew import OUT, READER, SEE_OR_DECIDE, Elsewhere, ProposedGoal
from crew_org.events import CrewEvent, EventKind, EventSink

PROPOSED_LABEL = "proposed-goal"
PROPOSED_COLOR = "d4c5f9"
PROPOSED_ABOUT = "A Goal the Product Owner proposes; the Sponsor approves it with `goal` (ADR 0027)"
NEEDS_HUMAN = "needs:human"


def file_in_inbox(
    issues: Any, board: Any, repo: str, title: str, body: str, labels: list[str]
) -> int:
    """File a Goal on `repo` and put it in Inbox (Goals), typed as a Goal."""
    issue = issues.create(repo, title, body, labels=labels)
    item = board.add_issue(issue["node_id"])
    board.set_status(item, INBOX)
    board.set_select(item, "Work Type", "Goal")
    return int(issue["number"])


def render(goal: ProposedGoal, *, why: str, source: str) -> str:
    """The proposed Goal's body, under the headings a Goal written as intent has."""
    return "\n".join(
        [
            f"## {READER}",
            "",
            goal.reader,
            "",
            f"## {SEE_OR_DECIDE}",
            "",
            goal.see_or_decide,
            "",
            f"## {OUT}",
            "",
            goal.out,
            "",
            "---",
            "",
            f"Proposed by the Product Owner from {source}: {why}",
            "",
            "**A draft for the Sponsor.** Approve it by adding the `goal` label, edit it "
            "first, or close it. Its words are the Product Owner's until you add your own.",
        ]
    )


def propose(
    issues: Any, board: Any, repo: str, goal: ProposedGoal, *, why: str, source: str
) -> int:
    """File the Product Owner's proposed Goal as a draft. Its number."""
    issues.ensure_label(repo, PROPOSED_LABEL, color=PROPOSED_COLOR, description=PROPOSED_ABOUT)
    return file_in_inbox(
        issues,
        board,
        repo,
        goal.title,
        render(goal, why=why, source=source),
        [PROPOSED_LABEL, NEEDS_HUMAN],
    )


def bring(
    issues: Any,
    board: Any,
    sink: EventSink,
    *,
    repo: str,
    goal: int,
    elsewhere: Elsewhere,
    step: str,
    rule: str,
) -> str:
    """Bring "the Goal points elsewhere" to the Sponsor, from Goal `goal` (ADR 0018).

    A proposed Goal is filed as a draft and its reference returned; a question is
    the caller's to put where the Sponsor reads the step's answer, and "" returned.
    """
    filed = (
        propose(
            issues,
            board,
            repo,
            elsewhere.goal,
            why=elsewhere.why,
            source=f"{issues.owner}/{repo}#{goal}",
        )
        if elsewhere.goal
        else None
    )
    proposed = f"{issues.owner}/{repo}#{filed}" if filed else ""
    sink.emit(
        CrewEvent(
            kind=EventKind.PRODUCT_ASKED,
            role="Product Owner",
            card=goal,
            summary=(
                f"goal #{goal} points elsewhere: "
                + (f"proposes {proposed}" if proposed else elsewhere.question)
            )[:120],
            detail={
                "repo": repo,
                "question": elsewhere.question,
                "proposed_goal": filed,
                "step": step,
                "rule": rule,
            },
        )
    )
    return proposed
