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

from crew_org.columns import (
    DONE,
    IN_PROGRESS,
    INBOX,
    MERGING,
    QAING,
    READY,
    REVIEWING,
    SPRINT_BACKLOG,
)
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows.board_flow import NEEDS_REWORK, TECHNICAL
from crew_org.flows.board_flow import builds_on as builds_on_line
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
    # Stories whose epic is waiting to be split again (`needs:rework`): they're
    # about to be superseded (#189, #242).
    waiting_on_rework: list[Card] = field(default_factory=list)
    # Points already in the sprint before this run, whatever their column (#222).
    committed: int = 0

    @property
    def points(self) -> int:
        """Points this run admitted."""
        return sum(s.points for s in self.slices)

    @property
    def total(self) -> int:
        """Points in the sprint once this run's admissions are in."""
        return self.committed + self.points

    @property
    def full(self) -> bool:
        return self.committed >= self.capacity

    @property
    def admitted(self) -> list[Card]:
        return [c for s in self.slices for c in s.admitted]


def _priority_key(card: Card) -> tuple[int, float, int]:
    """Priority (the Sponsor's override), then the Product Owner's Rank (#358), then number."""
    rank = card.rank if card.rank is not None else float("inf")
    return (PRIORITY_ORDER.get(card.priority or "", 99), rank, card.number or 0)


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
    builds_on: dict[tuple[str, int], set[int]] | None = None,
) -> SprintPlan:
    """Choose the sprint's contents. Pure — no I/O, so it is testable.

    `builds_on` is each candidate story's `**Builds on** —` line (#295), read
    from its body. A story waits for what it builds on unless that lands this
    sprint too (crew#358).

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

    # Capacity is the sprint's, not this run's. Every pass of every tick runs
    # admission, and each started from the full capacity: Sprint 6, capacity
    # 20, held 80 points and was about to take the API work on top (#222).
    plan.committed = committed_points(cards, sprint, repos=repos)
    remaining = max(capacity - plan.committed, 0)
    # Technical work first, as refinement already takes it (#192): planned last,
    # the image work 1.0.0 waits on got what Sprint 10's docs work left (#358).
    epics = sorted(approved_epics(cards), key=lambda e: TECHNICAL not in e.labels)
    for epic in epics:
        stories = sorted(
            (c for key, c in ready.items() if parents.get(key) == epic.key),
            key=lambda c: c.number or 0,
        )
        if not stories:
            continue
        if epic.key in (awaiting_design or set()):
            plan.waiting_on_design += stories
            continue
        # Sent back to be split again: its stories are about to be superseded.
        # sprint-metrics#59's were re-admitted the tick they went back, and
        # #145 was rebuilt against the design note that had caused its loop.
        if NEEDS_REWORK in epic.labels:
            plan.waiting_on_rework += stories
            continue

        piece = EpicSlice(number=epic.number or 0, title=epic.title)
        for story in stories:
            # Delivery holds a story until every earlier sibling has landed
            # (`held_by_a_sibling`). Admitted without them, it can't finish:
            # sprint-metrics#284 was, when #283 didn't fit (crew#353).
            if _held_by_an_earlier_sibling(cards, story, sprint, piece.admitted):
                piece.deferred.append(story)
                continue
            # And until what it builds on lands, across epics: sprint-metrics#293
            # (1.0.0) was admitted to Sprint 10 held on #303-#306 (crew#358).
            wanted = (builds_on or {}).get(story.key, set())
            admitted = [c for p in [*plan.slices, piece] for c in p.admitted]
            if _waits_on_work_outside(cards, parents, story, wanted, admitted):
                piece.deferred.append(story)
                continue
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


# Where an earlier sibling can be and still land ahead of a story this sprint.
ON_ITS_WAY = frozenset({SPRINT_BACKLOG, IN_PROGRESS, REVIEWING, QAING, MERGING})


def _held_by_an_earlier_sibling(
    cards: list[Card], story: Card, sprint: str, admitted: list[Card]
) -> bool:
    """An earlier open story in its epic that won't land this sprint: not in the
    sprint's flow, and not admitted by this plan.

    Earlier means a lower number, as delivery reads it: the order the Business
    Analyst proposed them in.
    """
    if story.parent is None:
        return False
    admitted_keys = {c.key for c in admitted}
    return any(
        c.repo == story.repo
        and c.parent == story.parent
        and c.work_type == STORY_TYPE
        and (c.number or 0) < (story.number or 0)
        and c.state != "CLOSED"
        and c.status != DONE
        and c.status not in ON_ITS_WAY
        and c.key not in admitted_keys
        for c in cards
    )


def committed_points(cards: list[Card], sprint: str, *, repos: set[str] | None = None) -> int:
    """The points already in the sprint. A story closed as not planned, superseded
    by a re-split, was never going to be built, so it holds none (#327)."""
    return sum(
        int(c.points or 0)
        for c in cards
        if c.sprint == sprint
        and c.work_type == STORY_TYPE
        and (repos is None or c.repo in repos)
        and not (c.state == "CLOSED" and c.state_reason == "NOT_PLANNED")
    )


def _waits_on_work_outside(
    cards: list[Card],
    parents: dict[tuple[str, int], tuple[str, int]],
    story: Card,
    wanted: set[int],
    admitted: list[Card],
) -> bool:
    """Something the story builds on won't land this sprint.

    A story it names lands if it's done, in the sprint's flow, or admitted by
    this plan. An epic lands when each of its open stories does; one with none
    yet (not split) won't.
    """
    by_key = {c.key: c for c in cards}
    admitted_keys = {c.key for c in admitted}

    def lands(card: Card) -> bool:
        return (
            card.state == "CLOSED"
            or card.status == DONE
            or card.status in ON_ITS_WAY
            or card.key in admitted_keys
        )

    for number in wanted:
        target = by_key.get((story.repo or "", number))
        if target is None or target.state == "CLOSED" or target.status == DONE:
            continue
        if target.work_type == STORY_TYPE:
            if not lands(target):
                return True
            continue
        children = [
            by_key[k] for k, parent in parents.items() if parent == target.key and k in by_key
        ]
        if not children or not all(lands(c) for c in children):
            return True
    return False


def left_over(cards: list[Card], sprint: str, *, repos: set[str] | None = None) -> list[Card]:
    """Stories an earlier sprint admitted and never started.

    Closing a sprint moves nothing, and delivery claims only the current
    sprint's stories, so one left in Sprint Backlog was never worked again:
    sprint-metrics#284 after Sprint 9 (crew#353).
    """
    return [
        c
        for c in cards
        if c.status == SPRINT_BACKLOG
        and c.work_type == STORY_TYPE
        and c.state != "CLOSED"
        and c.sprint
        and c.sprint != sprint
        and (repos is None or c.repo in repos)
    ]


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

    # Back to Ready first, so planning weighs them with their siblings.
    returned = {}
    for card in left_over(cards, sprint, repos=repos):
        move_card(
            board,
            sink,
            item_id=card.item_id,
            to=READY,
            by="Scrum Master",
            card=card.number,
            frm=SPRINT_BACKLOG,
            summary=f"not started in {card.sprint}: back to Ready for planning",
        )
        try:
            board.clear_field(card.item_id, "Sprint")
        except Exception as exc:  # noqa: BLE001
            sink.note(EventKind.NOTE, f"#{card.number} sprint not cleared: {exc}"[:120])
        returned[card.key] = card.model_copy(update={"status": READY, "sprint": None})
    cards = [returned.get(c.key, c) for c in cards]

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
    from crew_org.flows.presentation_notes import awaiting_presentation  # noqa: PLC0415

    # What each Ready story builds on, from its body; read only when the sprint
    # has room, since every pass of every tick runs admission.
    builds: dict[tuple[str, int], set[int]] = {}
    if committed_points(cards, sprint, repos=repos) < capacity:
        for card in cards:
            if not (
                card.status == READY
                and card.work_type == STORY_TYPE
                and card.state != "CLOSED"
                and (repos is None or card.repo in repos)
            ):
                continue
            try:
                body = issues.get(card.repo or default_repo, card.number or 0).get("body") or ""
            except Exception:  # noqa: BLE001
                body = ""
            if wanted := builds_on_line(body):
                builds[card.key] = wanted

    plan = plan_sprint(
        cards,
        parents,
        sprint=sprint,
        capacity=capacity,
        repos=repos,
        # Held until their notes exist: the Architect's and the UX Designer's (#377).
        awaiting_design=awaiting_design(issues, cards, default_repo)
        | awaiting_presentation(issues, cards, default_repo),
        builds_on=builds,
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
        f"{sprint}: {len(plan.admitted)} stories admitted ({plan.points} points); "
        f"{plan.total} of {capacity} points in the sprint",
    )
    return plan
