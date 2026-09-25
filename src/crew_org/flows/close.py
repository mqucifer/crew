"""Sprint close: the increment, the merge, and the retro.

This is the Sponsor's second gate, and it is deliberately the only place
approval is asked for. The crew cannot approve its own pull requests, so
reviewing the increment *is* approving the pull requests that make it up —
one pass at the end of a sprint rather than a decision per story.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from crew_org.columns import DONE, MERGING
from crew_org.crews.retro_crew import Retro, write_retro
from crew_org.escalation import EscalationLedger
from crew_org.events import EventKind, EventSink, blocked_since, replay_dir
from crew_org.flows.artifacts import signed
from crew_org.flows.merge import BEHIND
from crew_org.flows.moves import move_card
from crew_org.flows.retro import RetroRecord, existing_retro, known_issues, record_retro
from crew_org.flows.standup import standups_for_retro
from crew_org.git_ops import branch_name
from crew_org.process import ProcessRules
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
    # (story, PR) behind main and brought up to date; they merge once checks pass.
    updating: list[tuple[int, int]] = field(default_factory=list)
    # Names, not numbers: the sprint close reported "#12, #19, #20, #31, #32,
    # #32" for six stories in two repositories, and the retro then described
    # one card two ways. A number is not a name where two repositories are on
    # the board.
    still_open: list[str] = field(default_factory=list)
    # (card, days blocked) for cards blocked longer than the configured
    # threshold. §12 says these are raised to the Sponsor at sprint review and
    # `process.aging_blocked` has always been able to say which — nothing
    # called it, so a card blocked most of a day went unmentioned.
    aging_blocked: list[tuple[str, int]] = field(default_factory=list)
    retro: Retro | None = None
    # Where the retro was recorded (#50): the issue on the crew repository, and
    # whether this close wrote it or found it already written.
    retro_record: RetroRecord | None = None
    retro_already: bool = False

    @property
    def complete(self) -> bool:
        return not (
            self.awaiting_approval
            or self.unapprovable
            or self.still_open
            or self.unmergeable
            or self.updating
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
    rules: ProcessRules | None = None,
    events_dir: Path | None = None,
    now: datetime | None = None,
    crew_repo: str | None = None,
    delivery_repos: list[str] | None = None,
) -> SprintClose:
    """Merge what the Sponsor approved, then report on the sprint.

    `rules` and `events_dir` are what raising an aging blocked card needs: the
    threshold, and the log that says when each card was blocked. Both optional
    so a caller that only wants the merge still gets one — but with neither,
    §12's "raise it at sprint review" cannot happen, and the report says so
    rather than quietly omitting it.
    """
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

        branch = branch_name(number, card.title)
        pull = issues.pull_for_branch(repo, branch, known=card.open_pull_on(branch))
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

        # Behind `main` where protection requires up to date: the merge would be
        # refused with a 405 about a status check (#116). Update it instead; it
        # lands on the next tick, or on a second close, once checks pass.
        detail = issues.pull(repo, pull["number"])
        if detail.get("mergeable_state") == BEHIND:
            try:
                issues.update_branch(
                    repo, pull["number"], head=(detail.get("head") or {}).get("sha")
                )
            except Exception as exc:  # noqa: BLE001
                result.unmergeable.append((number, f"behind main, and could not update: {exc}"))
                continue
            result.updating.append((number, pull["number"]))
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

    # §12: a card blocked longer than the threshold is raised to the Sponsor at
    # sprint review. The function that answers "which ones" was written, the
    # threshold was configured with a comment saying exactly this, and nothing
    # joined them — on 2026-09-19 sprint-metrics#12 had been blocked most of a
    # day and the close did not mention it.
    if rules is not None and events_dir is not None:
        blocked = blocked_since(replay_dir(events_dir), blocked_column=rules.blocked_column)
        aging = rules.aging_blocked(blocked, now=now or datetime.now(UTC))
        by_number = {c.number: c for c in cards}
        result.aging_blocked = sorted(
            (
                (by_number[number].name(qualify=qualify) if number in by_number else f"#{number}"),
                days,
            )
            for number, days in aging.items()
        )

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

    # One retro per sprint. A close run again finds the one it wrote rather
    # than paying for a second retro and filing every defect twice.
    if crew_repo is not None:
        found = existing_retro(issues, crew_repo, sprint)
        if found is not None:
            result.retro_record = RetroRecord(issue=found)
            result.retro_already = True
            return result

    # How the sprint went, not only how it ended: every tick left a standup.
    standup, standups = (
        standups_for_retro(issues, crew_repo, sprint) if crew_repo is not None else (None, "")
    )
    # What is already filed, so a symptom is cited as its known cause rather
    # than re-diagnosed and filed again (#124).
    known, known_text = known_issues(issues, crew_repo) if crew_repo is not None else (set(), "")
    try:
        result.retro = write_retro(
            sprint,
            board_summary(board.cards(), sprint),
            escalations,
            delivery_repos=delivery_repos,
            standups=standups,
            known=known_text,
        )
    except Exception as exc:  # noqa: BLE001
        sink.note(EventKind.NOTE, f"retro could not be written: {exc}"[:120])
        return result

    if crew_repo is not None and result.retro is not None:
        try:
            result.retro_record = record_retro(
                issues,
                sink,
                result.retro,
                sprint=sprint,
                crew_repo=crew_repo,
                delivery_repos=delivery_repos or [],
                standup=standup,
                known=known,
            )
        except Exception as exc:  # noqa: BLE001
            # The retro is still printed. What failed is the record of it.
            sink.note(EventKind.NOTE, f"retro could not be recorded: {exc}"[:120])
        else:
            # The retro has read them; the sprint's standups are finished.
            if standup is not None and result.retro_record.issue is not None:
                issues.comment(
                    crew_repo,
                    standup,
                    signed(
                        f"Sprint closed. The retro that read these is "
                        f"#{result.retro_record.issue}.",
                        "Scrum Master",
                    ),
                )
                issues.close(crew_repo, standup)

    return result
