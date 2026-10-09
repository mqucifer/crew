"""A split whose criteria can't all be met goes to the Product Owner, not the board (#428).

The check runs on the Business Analyst's proposal before any story is
created. A conflict gets one repair: the flagged stories' criteria are rewritten
and the rest stay as they were (crew#440). One that survives goes on the epic as
a story problem with `needs:rework`, so the Product Owner settles it (#189) and
the epic is split again following that.
"""

from __future__ import annotations

import contextlib
from typing import Any

from crew_org.crews.criteria_crew import CriteriaCheck
from crew_org.crews.refinement_crew import CriteriaRepair, Story, StoryProposal
from crew_org.events import EventKind, EventSink
from crew_org.flows import artifacts
from crew_org.rules import Rule

# What the check is shown of other epics' planned stories. A pass can plan
# dozens; past this, their titles are still in the split's own context.
PLANNED_LIMIT = 30
CRITERIA_LIMIT = 2000
CRITERIA = "## Acceptance criteria"


def render_split(proposal: StoryProposal) -> str:
    """The proposed stories as the check reads them: titles, criteria, existing tests."""
    return render_stories(proposal.stories)


def render_stories(stories: list[Story]) -> str:
    parts = []
    for story in stories:
        lines = [f"### {story.title}"]
        lines += [
            f"{i}. Given {ac.given} / When {ac.when} / Then {ac.then}"
            for i, ac in enumerate(story.acceptance_criteria, 1)
        ]
        if story.pinned_behaviour:
            lines.append(f"Existing tests: {story.pinned_behaviour}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def planned_criteria(issues: Any, repo: str, planned: list[tuple[int, str, int]]) -> str:
    """Other epics' planned stories in this repository, with their criteria."""
    parts = []
    for number, title, epic in planned[:PLANNED_LIMIT]:
        try:
            body = issues.get(repo, number).get("body") or ""
        except Exception:  # noqa: BLE001
            continue
        start = body.find(CRITERIA)
        if start == -1:
            continue
        section = body[start + len(CRITERIA) :].split("\n**Estimate**", 1)[0].strip()
        parts.append(f"### #{number} {title} (epic #{epic})\n\n{section[:CRITERIA_LIMIT]}")
    return "\n\n".join(parts)


def described(check: CriteriaCheck) -> str:
    """The conflicts, one per line, for the re-split and for the epic."""
    return "\n".join(
        f'- *{c.story}*: "{c.criterion}" can\'t pass alongside {c.against}. {c.why}'
        for c in check.conflicts
    )


def _key(title: str) -> str:
    return title.strip().strip("*\"'“”‘’ ").casefold()


def flagged(proposal: StoryProposal, check: CriteriaCheck) -> list[Story] | None:
    """The proposal's stories the conflicts name, once each, in the proposal's order.

    None when a conflict names a story the proposal doesn't have: then it can't be
    repaired in place, and the epic is split again whole.
    """
    names = {_key(c.story) for c in check.conflicts}
    found = [s for s in proposal.stories if _key(s.title) in names]
    if names - {_key(s.title) for s in found}:
        return None
    return found


def with_repairs(
    proposal: StoryProposal, named: list[Story], repair: CriteriaRepair
) -> StoryProposal:
    """The proposal with the named stories' criteria replaced by their repair.

    Only the criteria change, and only for stories named: titles, order, rows
    followed and dependencies were checked already and stay. A named story the
    repair left out keeps its criteria, and the check refuses it again.
    """
    repaired = {_key(r.title): r.acceptance_criteria for r in repair.stories}
    allowed = {_key(s.title) for s in named}
    stories = [
        s.model_copy(update={"acceptance_criteria": repaired[_key(s.title)]})
        if _key(s.title) in allowed and _key(s.title) in repaired
        else s
        for s in proposal.stories
    ]
    return proposal.model_copy(update={"stories": stories})


def feedback(check: CriteriaCheck) -> str:
    """What the Business Analyst is told when its split is sent back once."""
    return (
        "Your last split had criteria that can't all pass:\n\n"
        f"{described(check)}\n\n"
        "Split it again so every criterion can pass alongside the others and the "
        "project's code. Where the epic doesn't say which outcome it wants, choose "
        "none: leave the case out of the criteria rather than guessing."
    )


def to_product_owner(
    issues: Any,
    sink: EventSink,
    *,
    repo: str,
    number: int,
    check: CriteriaCheck,
    marker: str,
    rework: str,
) -> None:
    """The conflicts on the epic as a story problem, for the Product Owner to settle."""
    with contextlib.suppress(Exception):
        issues.ensure_label(
            repo,
            rework,
            color="d4c5f9",
            description="Split this epic again: see the latest comment",
        )
    artifacts.label(issues, sink, repo=repo, number=number, by="QA Engineer", add=[rework])
    artifacts.comment(
        issues,
        sink,
        repo=repo,
        number=number,
        body=f"{marker}\n"
        "**Not split: its stories' criteria can't all pass, even after a second split.**\n\n"
        f"{described(check)}\n\n"
        "Settle which outcome holds before this epic is split again. No story was created.",
        by="QA Engineer",
    )
    sink.note(
        EventKind.NOTE,
        f"#{number} not split: {len(check.conflicts)} criteria can't all pass",
        card=number,
        rule=Rule.CRITERIA_CANNOT_ALL_PASS,
    )
