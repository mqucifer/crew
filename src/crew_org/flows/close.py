"""Sprint close: the increment, the merge, and the retro.

This is the Sponsor's second gate, and it is deliberately the only place
approval is asked for. The crew cannot approve its own pull requests, so
reviewing the increment *is* approving the pull requests that make it up —
one pass at the end of a sprint rather than a decision per story.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from crew_org.columns import DONE, MERGING
from crew_org.crews.retro_crew import Retro, write_retro
from crew_org.escalation import EscalationLedger
from crew_org.events import EventKind, EventSink
from crew_org.flows.moves import move_card
from crew_org.git_ops import branch_name
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient, many_repos

STORY_TYPE = "Story"

# GitHub's word for "protection still wants an approving review".
REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass
class SprintClose:
    sprint: str
    merged: list[int] = field(default_factory=list)
    awaiting_approval: list[tuple[int, int]] = field(default_factory=list)
    # Approved, and GitHub did not count it. Kept apart from awaiting_approval
    # because the sprint review would otherwise ask the Sponsor to approve a
    # pull request their approval is not what is missing from.
    unapprovable: list[tuple[int, int]] = field(default_factory=list)
    unmergeable: list[tuple[int, str]] = field(default_factory=list)
    # Names, not numbers: the sprint close reported "#12, #19, #20, #31, #32,
    # #32" for six stories in two repositories, and the retro then described
    # one card two ways. A number is not a name where two repositories are on
    # the board.
    still_open: list[str] = field(default_factory=list)
    retro: Retro | None = None

    @property
    def complete(self) -> bool:
        return not (
            self.awaiting_approval or self.unapprovable or self.still_open or self.unmergeable
        )


def sprint_cards(cards: list[Card], sprint: str) -> list[Card]:
    return [c for c in cards if c.sprint == sprint and c.work_type == STORY_TYPE]


def board_summary(cards: list[Card], sprint: str) -> str:
    """What the board says, for the Scrum Master to narrate from.

    Cards carry their repository when the board holds more than one. The retro
    is written from this, and it described sprint-metrics#32 and crew#32 as a
    single card listed twice — a 3-point scrape-endpoint story and a 0-point
    card about the board's own automations.
    """
    qualify = many_repos(cards)
    lines = []
    for card in sorted(sprint_cards(cards, sprint), key=lambda c: (c.repo or "", c.number or 0)):
        points = int(card.points or 0)
        lines.append(f"{card.name(qualify=qualify)} [{points}pt] {card.status}: {card.title}")
    return "\n".join(lines) or "No stories in this sprint."


def close_sprint(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    ledger: EscalationLedger,
    *,
    sprint: str,
    repo: str,
    merge: bool = True,
) -> SprintClose:
    """Merge what the Sponsor approved, then report on the sprint."""
    result = SprintClose(sprint=sprint)
    cards = board.cards()
    qualify = many_repos(cards)

    for card in sorted(sprint_cards(cards, sprint), key=lambda c: c.number or 0):
        number = card.number or 0
        if card.status == DONE:
            continue
        if card.status != MERGING:
            result.still_open.append(card.name(qualify=qualify))
            continue

        pull = issues.pull_for_branch(repo, branch_name(number, card.title))
        if pull is None:
            result.unmergeable.append((number, "no open pull request"))
            continue

        # The crew cannot approve its own work, so this is where the Sponsor's
        # approval is read rather than requested again.
        reviews = issues.pull_reviews(repo, pull["number"])
        approved = any(r.get("state") == "APPROVED" for r in reviews)
        if not approved:
            result.awaiting_approval.append((number, pull["number"]))
            continue

        # Approved and still refused. Branch protection only counts an approval
        # from an actor with repository write access, so the crew's reviewing
        # identity can approve into the void — and the sprint review must not
        # ask the Sponsor for an approval that is already there.
        if issues.review_decision(repo, pull["number"]) == REVIEW_REQUIRED:
            result.unapprovable.append((number, pull["number"]))
            continue

        if not merge:
            result.awaiting_approval.append((number, pull["number"]))
            continue

        try:
            issues.merge_pull(repo, pull["number"])
        except Exception as exc:  # noqa: BLE001
            result.unmergeable.append((number, str(exc)[:120]))
            continue

        move_card(
            board,
            sink,
            item_id=card.item_id,
            to=DONE,
            by=None,
            card=number,
            frm=MERGING,
            summary=f"merged PR #{pull['number']}",
        )
        result.merged.append(number)

    # Report the outcome alongside the failure. Without it an escalation reads
    # as an unresolved failure and the retro concludes the story was shipped
    # broken — which is what happened the first time this ran.
    outcomes = ledger.outcomes(sprint)
    seen: set[int] = set()
    lines = []
    for entry in ledger.entries(sprint):
        if entry.card in seen:
            continue
        seen.add(entry.card)
        ended = outcomes.get(entry.card) or "outcome not recorded"
        lines.append(
            f"#{entry.card} {entry.failure_class} after {entry.local_attempts} local "
            f"attempts — escalation {ended}. Trigger: {entry.detail[:100]}"
        )
    escalations = "\n".join(lines)
    try:
        result.retro = write_retro(sprint, board_summary(board.cards(), sprint), escalations)
    except Exception as exc:  # noqa: BLE001
        sink.note(EventKind.NOTE, f"retro could not be written: {exc}"[:120])

    return result
