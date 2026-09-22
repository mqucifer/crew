"""Sprint planning.

The Sponsor approves epics, not sprint contents. Approving an epic *is* the
scope decision, so planning is mechanical from there: pull the stories of
approved epics in priority order until capacity is reached.

Everything here reports at the epic level. A Sponsor who has to read eight
stories to understand a sprint has been put back into the work.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from crew_org.columns import INBOX, READY, SPRINT_BACKLOG
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows.moves import move_card
from crew_org.process import ProcessRules
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient

EPIC_TYPE = "Epic"
STORY_TYPE = "Story"

# Priority order. Anything unset sorts last — unprioritised work is not urgent.
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


@dataclass
class EpicSlice:
    """How much of one epic made it into the sprint."""

    number: int
    title: str
    # The cards themselves, not their numbers. A number is not an identity —
    # two repositories number independently — and carrying the card means the
    # repository travels with it into every lookup and every report.
    admitted: list[Card] = field(default_factory=list)
    deferred: list[Card] = field(default_factory=list)
    points: int = 0

    @property
    def complete(self) -> bool:
        return not self.deferred


@dataclass
class SprintPlan:
    sprint: str
    capacity: int
    slices: list[EpicSlice] = field(default_factory=list)
    unparented: list[Card] = field(default_factory=list)

    @property
    def points(self) -> int:
        return sum(s.points for s in self.slices)

    @property
    def admitted(self) -> list[Card]:
        return [c for s in self.slices for c in s.admitted]


def _priority_key(card: Card) -> tuple[int, int]:
    return (PRIORITY_ORDER.get(card.priority or "", 99), card.number or 0)


def approved_epics(cards: list[Card]) -> list[Card]:
    """Epics past the Sponsor's gate — approving them was the scope decision."""
    return sorted(
        (
            c
            for c in cards
            if c.work_type == EPIC_TYPE and c.status != INBOX and c.state != "CLOSED"
        ),
        key=_priority_key,
    )


def plan_sprint(
    cards: list[Card],
    parents: dict[tuple[str, int], tuple[str, int]],
    *,
    sprint: str,
    capacity: int,
) -> SprintPlan:
    """Choose the sprint's contents. Pure — no I/O, so it is testable.

    `parents` maps a story's key to its epic's key. Keys rather than numbers:
    keyed by number, a story in one repository could be attributed to an epic
    in another whenever the numbers happened to line up, and nothing in the
    board data prevents them lining up.
    """
    plan = SprintPlan(sprint=sprint, capacity=capacity)
    ready = {
        c.key: c
        for c in cards
        if c.status == READY and c.work_type == STORY_TYPE and c.state != "CLOSED"
    }

    remaining = capacity
    for epic in approved_epics(cards):
        stories = sorted(
            (c for key, c in ready.items() if parents.get(key) == epic.key),
            key=lambda c: c.number or 0,
        )
        if not stories:
            continue

        piece = EpicSlice(number=epic.number or 0, title=epic.title)
        for story in stories:
            points = int(story.points or 0)
            # Never split a story to fit; a partially admitted story is not
            # deliverable, and shaving scope by halves is how sprints rot.
            if points <= remaining:
                piece.admitted.append(story)
                piece.points += points
                remaining -= points
            else:
                piece.deferred.append(story)
        plan.slices.append(piece)

    parented = set(parents)
    plan.unparented = sorted(
        (c for key, c in ready.items() if key not in parented),
        key=lambda c: (c.repo or "", c.number or 0),
    )
    return plan


def start_sprint(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    rules: ProcessRules,
    *,
    sprint: str,
    capacity: int,
    default_repo: str,
) -> SprintPlan:
    """Admit the planned stories into the sprint."""
    cards = board.cards()

    # Parentage comes from sub-issue nesting, asked once per epic.
    parents: dict[tuple[str, int], tuple[str, int]] = {}
    for epic in approved_epics(cards):
        repo = epic.repo or default_repo
        try:
            # A sub-issue lives in its parent's repository, so the child's key
            # is that repository and the number GitHub gave it.
            for child in issues.sub_issues(repo, epic.number or 0):
                parents[(epic.repo or "", child["number"])] = epic.key
        except Exception as exc:  # noqa: BLE001
            sink.emit(
                CrewEvent(
                    kind=EventKind.NOTE,
                    card=epic.number,
                    summary=f"could not read sub-issues: {exc}"[:90],
                )
            )

    plan = plan_sprint(cards, parents, sprint=sprint, capacity=capacity)
    counts = board.counts(cards)

    for piece in plan.slices:
        for card in list(piece.admitted):
            number = card.number or 0
            verdict = rules.may_move(frm=READY, to=SPRINT_BACKLOG, counts=counts)
            if not verdict.allowed:
                sink.emit(CrewEvent(kind=EventKind.NOTE, card=number, summary=verdict.reason[:90]))
                piece.deferred.append(card)
                continue
            board.set_iteration(card.item_id, "Sprint", sprint)
            move_card(
                board,
                sink,
                item_id=card.item_id,
                to=SPRINT_BACKLOG,
                by="Scrum Master",
                card=number,
                frm=READY,
                summary=f"admitted to {sprint}",
            )
            counts[SPRINT_BACKLOG] = counts.get(SPRINT_BACKLOG, 0) + 1

        held_back = {c.key for c in piece.deferred}
        piece.admitted = [c for c in piece.admitted if c.key not in held_back]
        piece.points = sum(int(c.points or 0) for c in piece.admitted)

    sink.note(
        EventKind.TICK_FINISHED,
        f"{sprint}: {len(plan.admitted)} stories, {plan.points} of {capacity} points",
    )
    return plan
