"""Every send-back, declared once, and the one event that records it (crew#862).

The Sponsor, 2026-10-21: "anything that forces re-work of a step should definitely be
tracked in sprint metrics and presentation". sprint-metrics can count only what the
crew records, and some send-backs recorded nothing: measuring the criteria check's
repairs took LiteLLM's saved conversations. Rework is failure demand (Seddon): work
a step has to do again because an earlier pass at it wasn't right, counted per step.

**A send-back** is a step's finished output that a check or a role sends back to the
same step or an earlier one, to be done again. Each writes one `work.sent_back`: the
step that sent it, the step it goes back to, the rule that decided it, the card, its
round where the site knows it, and named counts. Never text (ADR 0010).

**Not counted,** because they aren't rework or are recorded already:
- a refusal inside a session or a call (a `done` or a form refused), recorded as
  `tool.finished` and `llm.refused`;
- a hold, an interruption, or a retry of a step that didn't finish;
- a block for a person, which `card.blocked` records with its rule;
- a finding filed as a new Task or bug, which `finding.filed` records.

The events that recorded some send-backs before this one (`story.returned`,
`gate.checked`, `epic.resplit`, `goal.reworked`, a `card.moved`'s rule) stay as they
are: `delivery-history` and its consumers read them. This sits beside them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.rules import Rule


class Step(StrEnum):
    """A step of the flow, in its order: where a send-back came from and where it went."""

    SPONSOR = "sponsor"
    DECOMPOSE = "decompose"
    REFINEMENT = "refinement"
    # The Product Owner's answer to a story problem comes before the split it leads to.
    STORY_PROBLEM = "story_problem"
    SPLIT = "split"
    CRITERIA_CHECK = "criteria_check"
    BUILD = "build"
    GATE = "gate"
    CI = "ci"
    REVIEW = "review"
    DEPLOY_REVIEW = "deploy_review"
    QA = "qa"
    MERGE = "merge"
    MAIN = "main"
    RELEASE_CHECK = "release_check"
    REVERT = "revert"
    REVISIT = "revisit"
    DESIGN = "design"


@dataclass(frozen=True)
class SendBack:
    """One kind of send-back: which step sends it, where to, and the rule that decides."""

    sent_from: Step
    sent_to: Step
    rule: str
    means: str


# --- refinement ---
GOAL_REWORK = SendBack(
    Step.SPONSOR,
    Step.DECOMPOSE,
    "decision.board_flow.rework_gate",
    "The Sponsor sent a Goal back (`needs:rework`): its unstarted epics are decomposed again.",
)
GOAL_LACKS_EPICS = SendBack(
    Step.SPONSOR,
    Step.DECOMPOSE,
    "decision.board_flow.lacking_goals",
    "The Sponsor said a Goal lacks epics (`needs:epic`): it's decomposed again for them.",
)
EPIC_REWORK = SendBack(
    Step.SPONSOR,
    Step.SPLIT,
    Rule.SENT_BACK_EPIC_REWORK,
    "The Sponsor sent an epic back (`needs:rework`): it's split again.",
)
WAITS_FOR_CLOSED = SendBack(
    Step.REFINEMENT,
    Step.SPLIT,
    "skip.board_flow.waits_for_planned",
    "An epic's stories it waits for closed undelivered: it's split again.",
)
CRITERIA_REPAIR = SendBack(
    Step.CRITERIA_CHECK,
    Step.SPLIT,
    Rule.CRITERIA_REPAIR,
    "The criteria check found criteria that can't all pass: the flagged stories' are rewritten.",
)
CRITERIA_RESPLIT = SendBack(
    Step.CRITERIA_CHECK,
    Step.SPLIT,
    Rule.CRITERIA_RESPLIT,
    "The criteria check's findings named no story to repair: the epic is split again whole.",
)
CRITERIA_TO_PRODUCT_OWNER = SendBack(
    Step.CRITERIA_CHECK,
    Step.STORY_PROBLEM,
    Rule.CRITERIA_CANNOT_ALL_PASS,
    "The criteria still can't all pass after the repair: a story problem for the Product Owner.",
)

# --- delivery ---
GATE_FINDINGS = SendBack(
    Step.GATE,
    Step.BUILD,
    Rule.GATE_FINDINGS,
    "The gates at `done` found problems: the Developer works on in a new session.",
)
CONTRACT_REPAIR = SendBack(
    Step.GATE,
    Step.BUILD,
    "check.gates.the_business_analyst_ruled_that",
    "The Business Analyst ruled the merged tests it changed must stay: a new session repairs it.",
)
SESSION_BLOCKED = SendBack(
    Step.BUILD,
    Step.SPLIT,
    Rule.RETURN_SESSION_BLOCKED,
    "Its session ended blocked on a file or fact, or was stopped: back to refinement.",
)
CONTRACT_RULING = SendBack(
    Step.GATE,
    Step.SPLIT,
    Rule.RETURN_CONTRACT_RULING,
    "The Business Analyst ruled the story shouldn't change merged tests: back to refinement.",
)
ROUND_TRIPS = SendBack(
    Step.BUILD,
    Step.SPLIT,
    Rule.RETURN_GATE_ROUND_TRIPS,
    "Review, QA or CI kept returning it, up to the round-trip limit: back to refinement.",
)
CI_BEFORE_REVIEW = SendBack(
    Step.CI,
    Step.BUILD,
    Rule.CI_BEFORE_REVIEW,
    "CI failed on the pull request's head before review: back to the Developer.",
)
REVIEW_CHANGES = SendBack(
    Step.REVIEW,
    Step.BUILD,
    Rule.REVIEW_CHANGES_REQUESTED,
    "The Code Reviewer requested changes: back to the Developer.",
)
DEPLOY_REVIEW_CHANGES = SendBack(
    Step.DEPLOY_REVIEW,
    Step.BUILD,
    Rule.REVIEW_CHANGES_REQUESTED,
    "Only the DevOps Engineer's deploy review requested changes: back to the Developer.",
)
REVIEW_CONFLICT = SendBack(
    Step.REVIEW,
    Step.SPLIT,
    Rule.RETURN_REVIEW_CONFLICT,
    "The review asks for what a criterion forbids: back to refinement.",
)
QA_UNPROVEN = SendBack(
    Step.QA,
    Step.BUILD,
    Rule.QA_UNPROVEN,
    "QA found criteria the work doesn't prove: back to the Developer.",
)
MERGE_CI_FAILED = SendBack(
    Step.MERGE,
    Step.BUILD,
    Rule.MERGE_CI_FAILED,
    "CI failed at merge, or the merge queue removed it: back to the Developer.",
)
MERGE_CHANGES_REQUESTED = SendBack(
    Step.MERGE,
    Step.BUILD,
    Rule.MERGE_CHANGES_REQUESTED,
    "Changes were requested on the head that would merge, by anyone: back to its role.",
)
MERGE_CONFLICT = SendBack(
    Step.MERGE,
    Step.BUILD,
    Rule.MERGE_CONFLICT,
    "It conflicts with main at merge: rebuilt on main by the Developer.",
)

# --- after merge ---
MAIN_RED = SendBack(
    Step.MAIN,
    Step.REFINEMENT,
    Rule.MAIN_RED,
    "The default branch went red after a merge: a technical epic to fix it.",
)
RELEASE_GAPS = SendBack(
    Step.RELEASE_CHECK,
    Step.REFINEMENT,
    Rule.RELEASE_GAPS,
    "The release check found gaps: a technical epic to close them.",
)
REVERTED = SendBack(
    Step.REVERT,
    Step.REFINEMENT,
    Rule.REVERTED,
    "Its merged change was reverted: the card goes back to Needs Refinement.",
)
DESIGN_FAULTS = SendBack(
    Step.REVISIT,
    Step.DESIGN,
    Rule.DESIGN_FAULTS,
    "Blocked stories found faults in the design: the Architect revises it.",
)
REBUILD_STRAIN = SendBack(
    Step.REVISIT,
    Step.DESIGN,
    Rule.REBUILD_STRAIN,
    "Rebuilds kept straining one file: the Architect revises the design.",
)
DESIGN_REFUSED = SendBack(
    Step.REVIEW,
    Step.DESIGN,
    Rule.DESIGN_REFUSED,
    "The Code Reviewer refused the Architect's design: it's proposed again.",
)

CATALOG = (
    GOAL_REWORK,
    GOAL_LACKS_EPICS,
    EPIC_REWORK,
    WAITS_FOR_CLOSED,
    CRITERIA_REPAIR,
    CRITERIA_RESPLIT,
    CRITERIA_TO_PRODUCT_OWNER,
    GATE_FINDINGS,
    CONTRACT_REPAIR,
    SESSION_BLOCKED,
    CONTRACT_RULING,
    ROUND_TRIPS,
    CI_BEFORE_REVIEW,
    REVIEW_CHANGES,
    DEPLOY_REVIEW_CHANGES,
    REVIEW_CONFLICT,
    QA_UNPROVEN,
    MERGE_CI_FAILED,
    MERGE_CHANGES_REQUESTED,
    MERGE_CONFLICT,
    MAIN_RED,
    RELEASE_GAPS,
    REVERTED,
    DESIGN_FAULTS,
    REBUILD_STRAIN,
    DESIGN_REFUSED,
)

# A story sent back to refinement, by its `story.returned` reason, and who sent it.
RETURNS = {
    "session blocked": (SESSION_BLOCKED, "Developer"),
    "contract ruling": (CONTRACT_RULING, "Business Analyst"),
    "gate round trips": (ROUND_TRIPS, None),
    "review conflicts with a criterion": (REVIEW_CONFLICT, "Code Reviewer"),
}


def record(
    sink: EventSink,
    back: SendBack,
    *,
    card: int | None,
    repo: str | None,
    role: str | None = None,
    round_: int | None = None,
    **counts: int,
) -> None:
    """Write the send-back's event. `round_` only where the site knows it; counts by name.

    `card` is None for a project's design, which no card holds.
    """
    detail: dict[str, object] = {
        "repo": repo,
        "sent_from": back.sent_from.value,
        "sent_to": back.sent_to.value,
        "rule": str(back.rule),
        "round": round_,
        "counts": dict(counts),
    }
    sink.emit(
        CrewEvent(
            kind=EventKind.WORK_SENT_BACK,
            role=role,
            card=card,
            summary=f"{f'#{card}' if card is not None else repo} sent back: "
            f"{back.sent_from.value} to {back.sent_to.value}",
            detail=detail,
        )
    )
