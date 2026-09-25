"""Sprint planning.

The Sponsor approves epics, not sprint contents. Approving an epic *is* the
scope decision, so planning is mechanical from there: pull the stories of
approved epics in priority order until capacity is reached, then stories that
belong to no epic.

A story with no epic is admissible (#46). It was filed as a story rather than
arrived at through a split, and filing it was its scope decision. Excluding it
made a second tier of backlog that only running the phases by hand could reach.

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

NO_EPIC = "Stories with no epic"

# Priority order. Anything unset sorts last — unprioritised work is not urgent.
PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


@dataclass
class EpicSlice:
    """How much of one epic made it into the sprint.

    `number` is None for the stories that belong to no epic, which are planned
    as one more slice after every epic's.
    """

    number: int | None
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

    @property
    def name(self) -> str:
        return f"#{self.number} {self.title}" if self.number is not None else self.title


@dataclass
class SprintPlan:
    sprint: str
    capacity: int
    slices: list[EpicSlice] = field(default_factory=list)
    # Stories with no epic and no estimate. An epic's stories are sized by the
    # Business Analyst when they are split; a story filed by hand can reach
    # Ready without one, and admitting it would spend capacity nobody measured.
    unestimated: list[Card] = field(default_factory=list)
    # Ready stories in a repository the crew does not deliver. Reported rather
    # than silently dropped: they are real work with real estimates, just not
    # the crew's to do, and a card that vanishes from planning without a word
    # is how five of eight admitted points came to be undeliverable.
    not_ours: list[Card] = field(default_factory=list)
    # Stories whose epic needs a design note it doesn't have yet (#155).
    waiting_on_design: list[Card] = field(default_factory=list)

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
    repos: set[str] | None = None,
    awaiting_design: set[tuple[str, int]] | None = None,
) -> SprintPlan:
    """Choose the sprint's contents. Pure — no I/O, so it is testable.

    `awaiting_design` is the epics labelled `needs:design` with no design note
    yet (#155). Their stories wait: built without the note, the first story sets
    the approach and the rest follow it or fight it.

    `repos` is the allow-list the crew delivers from, and filling a sprint
    without it spends capacity on work the crew is structurally incapable of
    doing. `delivery.sprint_stories` has always filtered; planning did not, so
    the two ends of the loop disagreed — planning admitted the card, delivery
    declined it as `not_ours`, and the capacity was gone either way. Measured
    on 2026-09-22: five of eight admitted points were undeliverable.

    None means every repository, which is what a caller with no delivery
    configuration should get.

    `parents` maps a story's key to its epic's key. Keys rather than numbers:
    keyed by number, a story in one repository could be attributed to an epic
    in another whenever the numbers happened to line up, and nothing in the
    board data prevents them lining up.
    """
    plan = SprintPlan(sprint=sprint, capacity=capacity)
    candidates = {
        c.key: c
        for c in cards
        if c.status == READY and c.work_type == STORY_TYPE and c.state != "CLOSED"
    }
    # Same test as `delivery.sprint_stories`, so the two ends of the loop agree
    # about what the crew can work.
    ready = {k: c for k, c in candidates.items() if repos is None or c.repo in repos}
    plan.not_ours = sorted(
        (c for k, c in candidates.items() if k not in ready),
        key=lambda c: (c.repo or "", c.number or 0),
    )

    remaining = capacity
    for epic in approved_epics(cards):
        stories = sorted(
            (c for key, c in ready.items() if parents.get(key) == epic.key),
            key=lambda c: c.number or 0,
        )
        if not stories:
            continue
        if epic.key in (awaiting_design or set()):
            plan.waiting_on_design += stories
            continue

        piece = EpicSlice(number=epic.number or 0, title=epic.title)
        for story in stories:
            remaining = _fill(piece, story, remaining)
        plan.slices.append(piece)

    # After every epic: epic priority orders the work that has it.
    parentless = [c for key, c in ready.items() if key not in parents]
    plan.unestimated = sorted(
        (c for c in parentless if c.points is None),
        key=lambda c: (c.repo or "", c.number or 0),
    )
    loose = EpicSlice(number=None, title=NO_EPIC)
    for story in sorted((c for c in parentless if c.points is not None), key=_priority_key):
        remaining = _fill(loose, story, remaining)
    if loose.admitted or loose.deferred:
        plan.slices.append(loose)
    return plan


def _fill(piece: EpicSlice, story: Card, remaining: int) -> int:
    """Admit a story whole if it fits, defer it if not. Returns what is left."""
    points = int(story.points or 0)
    # Never split a story to fit; a partially admitted story is not
    # deliverable, and shaving scope by halves is how sprints rot.
    if points <= remaining:
        piece.admitted.append(story)
        piece.points += points
        return remaining - points
    piece.deferred.append(story)
    return remaining


def start_sprint(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    rules: ProcessRules,
    *,
    sprint: str,
    capacity: int,
    default_repo: str,
    repos: set[str] | None = None,
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

    from crew_org.flows.design_notes import awaiting_design  # noqa: PLC0415

    plan = plan_sprint(
        cards,
        parents,
        sprint=sprint,
        capacity=capacity,
        repos=repos,
        awaiting_design=awaiting_design(issues, cards, default_repo),
    )
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
