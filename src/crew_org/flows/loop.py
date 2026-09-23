"""One tick: take the board as far as it can go.

Refinement always ran to quiescence. Everything after it — admitting a sprint,
reviewing, verifying, merging, delivering — was a separate command invoked by
hand, in the right order, by the Sponsor. Story #8 sat in In Review through a
whole delivery run because nobody had run `crew qa`.

The phases run drain-first: work already started is pushed forward before new
work is claimed, so a story does not branch from a default branch missing its
predecessors. A pass that moves nothing is quiescence, and the tick stops.

A phase that fails does not end the pass. The later phases act on the cards
they can, and the failure is reported beside what did happen — a tick that
aborts on the first error leaves the board in a state nobody chose.

A tick lands what it produces. There is no dry mode: a dry run cost the same
inference as a real one and left nothing that could land, which made it a
rehearsal rather than a preview — and its restores injected movement the crew's
own measurements then had to filter out. What made landing frightening was
having no way to undo it, so the answer is a revert, not a rehearsal (crew#82).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from crew_org.escalation import EscalationLedger, EscalationPolicy
from crew_org.events import EventKind, EventSink, bridge_crewai
from crew_org.git_ops import Workspace
from crew_org.process import ProcessRules
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import ProjectClient, many_repos
from crew_org.tools.sandbox import Sandbox

# A pass that keeps moving forever is a bug, not a busy board. Five is well
# past what a real board needs: refinement feeds admission feeds delivery, and
# each is one pass deep.
MAX_PASSES = 5


@dataclass
class Crew:
    """Everything the phases need, built once."""

    board: ProjectClient
    issues: IssueClient
    sink: EventSink
    ws: Workspace
    sandbox: Sandbox
    rules: ProcessRules
    policy: EscalationPolicy
    ledger: EscalationLedger
    org: dict[str, Any]
    repo: str
    repos: set[str]
    sprint: str
    capacity: int
    # Reviewing is done by a second identity: GitHub refuses an approval from
    # the app that opened the pull request.
    reviewer: IssueClient
    reviewer_login: str
    # The one person who may set a Goal. None means unconfigured, and the old
    # behaviour: anything typed Goal is decomposed.
    sponsor: str | None = None


@dataclass
class PhaseOutcome:
    name: str
    moved: bool = False
    summary: str = ""
    error: str | None = None
    result: Any = None
    # What this phase did, as numbers rather than a sentence, so a run of
    # several passes can be added up. A summary string cannot be.
    counts: dict[str, int] = field(default_factory=dict)
    # Cards this phase could not act on, each with the reason — awaiting an
    # approval, held by a sibling, already judged. This is *state*: it is true
    # now, so the last pass to look is the one that knows.
    held: list[str] = field(default_factory=list)
    # Cards this phase blocked. This is an *event*: it happened, and a later
    # pass will not see it because the card is no longer claimable. Reporting it
    # from the last pass alone meant a card that blocked mid-run vanished from
    # the summary — #31 blocked on an exhausted escalation budget and the run
    # reported only that its sibling was waiting.
    blocked: list[str] = field(default_factory=list)


@dataclass
class LoopResult:
    passes: int = 0
    outcomes: list[PhaseOutcome] = field(default_factory=list)
    # True only when a pass moved nothing. Hitting the cap is not the same as
    # the board being stable, and reporting it as such tells the Sponsor the
    # work is finished when it was merely stopped.
    settled: bool = False
    # Columns found over their WIP limit, as {column: (count, limit)}. The
    # limits are enforced on the way *in* — `may_move` refuses — so a column
    # cannot be pushed over. It can still *be* over, after a limit is lowered
    # or cards are moved by hand, and nothing said so.
    over_limit: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def failed(self) -> list[PhaseOutcome]:
        return [o for o in self.outcomes if o.error]

    @property
    def moved(self) -> list[PhaseOutcome]:
        return [o for o in self.outcomes if o.moved]

    @property
    def blocked_any(self) -> bool:
        """Did this run block a card? Something a person has to look at."""
        return any(o.blocked for o in self.outcomes)

    @property
    def blocked_count(self) -> int:
        return sum(len(self.blocked(name)) for name in {o.name for o in self.outcomes})

    @property
    def stuck(self) -> bool:
        """Is anything waiting on something that did not happen?

        Different from `settled`. A pass can move nothing because there is
        nothing to do, or because nothing it could do was allowed — and those
        are opposite states reported identically until now.
        """
        return any(o.held for o in self.outcomes)

    def last(self, name: str) -> PhaseOutcome | None:
        """The most recent outcome for one phase."""
        for outcome in reversed(self.outcomes):
            if outcome.name == name:
                return outcome
        return None

    def moved_in(self, name: str) -> bool:
        """Did this phase move anything, in any pass?

        Not "is any count non-zero": `2 already judged` is a phase declining to
        act, and reporting that as movement is the same class of lie this card
        was about.
        """
        return any(o.moved for o in self.outcomes if o.name == name)

    def totals(self, name: str) -> dict[str, int]:
        """What a phase did across the whole run.

        The report used to show `last(name)`, so a run whose first pass admitted
        a story and whose second admitted none reported zero. Work happened and
        the report denied it.
        """
        out: dict[str, int] = {}
        for outcome in self.outcomes:
            if outcome.name != name:
                continue
            for key, value in outcome.counts.items():
                out[key] = out.get(key, 0) + value
        return out

    def held(self, name: str) -> list[str]:
        """What is still waiting, as of the last pass that looked."""
        last = self.last(name)
        return list(last.held) if last else []

    def blocked(self, name: str) -> list[str]:
        """What this phase blocked, across the whole run.

        Accumulated, because blocking is something that happened rather than
        something that is still true: the pass after it will not see the card at
        all. Deduplicated, because a phase can report the same block twice in
        one pass — once from the outcome and once from the card move.
        """
        out: list[str] = []
        for outcome in self.outcomes:
            if outcome.name != name:
                continue
            out += [line for line in outcome.blocked if line not in out]
        return out


def _refine(crew: Crew) -> PhaseOutcome:
    from crew_org.flows.board_flow import tick as refine  # noqa: PLC0415

    result = refine(
        crew.board,
        crew.issues,
        crew.sink,
        default_repo=crew.repo,
        org=crew.org,
        ws=crew.ws,
        sponsor=crew.sponsor,
    )
    # Parking an epic is movement: the card left Needs Refinement, and a pass
    # that reports "nothing moved" over it would hide the one thing that
    # changed.
    moved = bool(result.epics_created or result.stories_created or result.parked)
    return PhaseOutcome(
        "refine",
        moved=moved,
        summary=f"{len(result.epics_created)} epics, {len(result.stories_created)} stories",
        result=result,
        counts={
            "epics": len(result.epics_created),
            "stories": len(result.stories_created),
            # What it looked at and left alone, so a quiet pass says why it was
            # quiet rather than only that it was.
            "already answered": sum(1 for _n, why in result.skipped if why == "already answered"),
        },
        held=[f"#{n} — {why}" for n, why in result.failed]
        + [f"#{n} — {why}" for n, why in result.skipped if "refused" in why],
        blocked=[
            f"#{n} — the split failed the same way twice; parked for a person"
            for n in result.parked
        ],
    )


def _admit(crew: Crew) -> PhaseOutcome:
    """Ready to Sprint Backlog.

    Approving the epic was the scope decision, so this is arithmetic. It was
    the one hop `tick` refused to make, on a docstring claiming a third human
    gate that section 1 of the constitution does not have.
    """
    from crew_org.flows.sprint import start_sprint  # noqa: PLC0415

    plan = start_sprint(
        crew.board,
        crew.issues,
        crew.sink,
        crew.rules,
        sprint=crew.sprint,
        capacity=crew.capacity,
        default_repo=crew.repo,
    )
    return PhaseOutcome(
        "admit",
        moved=bool(plan.admitted),
        summary=f"{len(plan.admitted)} stories, {plan.points} points",
        result=plan,
        counts={"stories": len(plan.admitted), "points": plan.points},
        held=[
            f"{c.name(qualify=many_repos(plan.unparented))} — no parent epic"
            for c in plan.unparented
        ],
    )


def _review(crew: Crew) -> PhaseOutcome:
    from crew_org.flows.review import review_open_pulls  # noqa: PLC0415

    moved_any = False
    reviewed = skipped = 0
    failed: list[str] = []
    cards = crew.board.cards()
    for repo in sorted(crew.repos):
        result = review_open_pulls(
            crew.reviewer,
            crew.sink,
            repo=repo,
            bot_login=crew.reviewer_login,
            board=crew.board,
            cards=cards,
        )
        reviewed += len(result.reviewed)
        skipped += len(result.skipped)
        failed += [f"PR #{n} — {why}" for n, why in result.failed]
        moved_any = moved_any or bool(result.reviewed)
    return PhaseOutcome(
        "review",
        moved=moved_any,
        summary=f"{reviewed} reviewed, {skipped} already judged",
        counts={"reviewed": reviewed, "already judged": skipped},
        held=failed,
    )


def _qa(crew: Crew) -> PhaseOutcome:
    from crew_org.flows.acceptance import close_finished_parents, run_qa  # noqa: PLC0415

    cards = crew.board.cards()
    result = run_qa(
        crew.board, crew.issues, crew.sink, crew.ws, crew.sandbox, cards=cards, repo=crew.repo
    )
    result.parents_closed = close_finished_parents(
        crew.board, crew.issues, crew.sink, crew.board.cards(), repo=crew.repo
    )
    moved = bool(result.verified or result.returned or result.parents_closed)
    skipped = f", {len(result.skipped)} already judged" if result.skipped else ""
    return PhaseOutcome(
        "qa",
        moved=moved,
        summary=f"{len(result.verified)} accepted, {len(result.returned)} returned{skipped}",
        result=result,
        counts={
            "accepted": len(result.verified),
            "returned": len(result.returned),
            "already judged": len(result.skipped),
        },
        held=[f"#{n} — {why}" for n, why in result.failed],
    )


def _deliver(crew: Crew) -> PhaseOutcome:
    """Merge what is approved, then claim what fits.

    `deliver` merges first by design — a story branching from a default branch
    missing its predecessors is a conflict scheduled for later — so landing and
    claiming are one phase rather than two.
    """
    from crew_org.flows.delivery import deliver  # noqa: PLC0415

    result = deliver(
        crew.board,
        crew.issues,
        crew.sink,
        crew.rules,
        crew.policy,
        crew.ledger,
        crew.ws,
        sprint=crew.sprint,
        repo=crew.repo,
        limit=None,
        repos=crew.repos,
    )
    moved = bool(
        result.landed or result.delivered or result.blocked or result.recovered or result.reworked
    )
    # State: still true after this pass.
    held = (
        [f"#{n} — waiting on an approving review (PR #{pr})" for n, pr in result.awaiting_approval]
        + [f"#{n} — {why}" for n, why in result.unmergeable]
        + [f"#{n} — waits for #{b} in the same epic" for n, b in result.waiting_on_a_sibling]
        + [f"#{n} — would merge; a dry run does not" for n in result.would_land]
    )
    # Events: they happened, and the next pass will not see them.
    blocked = (
        [f"#{o.card} — {o.blocked_reason}" for o in result.blocked if o.blocked_reason]
        + [f"#{n} — merge conflict, needs a person" for n in result.conflicted]
        + [
            f"#{n} — PR #{pr} is approved and GitHub will not count it, needs a person"
            for n, pr in result.unapprovable
        ]
    )
    return PhaseOutcome(
        "deliver",
        moved=moved,
        summary=(
            f"{len(result.landed)} merged, {len(result.delivered)} delivered"
            + (f", {len(result.reworked)} re-worked" if result.reworked else "")
        ),
        result=result,
        counts={"merged": len(result.landed), "delivered": len(result.delivered)},
        held=held,
        blocked=blocked,
    )


# Drain-first, in dependency order. Refinement produces stories, admission puts
# them in a sprint, and the three that follow push started work forward before
# delivery claims more.
PHASES: tuple[tuple[str, Callable[..., PhaseOutcome]], ...] = (
    ("refine", _refine),
    ("admit", _admit),
    ("review", _review),
    ("qa", _qa),
    ("deliver", _deliver),
)


def run(crew: Crew, *, max_passes: int = MAX_PASSES) -> LoopResult:
    """Run every phase, in order, until a pass moves nothing."""
    result = LoopResult()

    # Model calls, their tokens and the tools they used. Installed once, for
    # the whole tick: the bridge existed and nothing called it, so every real
    # run's log was blind to everything the model did.
    bridge_crewai(crew.sink)

    for _ in range(max_passes):
        result.passes += 1
        moved_this_pass = False

        # The board as it actually stands, once per pass. The live view seeds
        # its swimlanes from this; without it the lanes only ever showed the
        # deltas of whatever moved while someone was watching.
        # Best effort: a panel that cannot be seeded is worth less than a tick,
        # and every phase reads the board for itself anyway.
        try:
            counts = crew.board.counts(crew.board.cards())
        except Exception as exc:  # noqa: BLE001
            counts = {}
            crew.sink.note(EventKind.NOTE, f"could not read the board: {exc}"[:120])
        crew.sink.note(
            EventKind.TICK_STARTED,
            f"pass {result.passes}",
            tick=result.passes,
            sprint=crew.sprint,
            counts=counts,
        )

        # A breach is reported, not merely prevented. `over_limit` has always
        # been able to answer this and nothing asked it, so a column that was
        # already over — a limit lowered, cards moved by hand — looked exactly
        # like a column at its limit, which is a different and healthy thing.
        breaches = crew.rules.over_limit(counts)
        result.over_limit = breaches
        for column, (count, limit) in sorted(breaches.items()):
            crew.sink.note(
                EventKind.NOTE,
                f"{column} is over its WIP limit: {count} cards against {limit}",
                column=column,
                count=count,
                limit=limit,
            )

        for name, phase in PHASES:
            try:
                outcome = phase(crew)
            except Exception as exc:  # noqa: BLE001
                # The pass continues. Later phases act on cards this one never
                # touched, and a tick that aborts here leaves the board partway
                # through a state nobody chose.
                outcome = PhaseOutcome(name, error=f"{type(exc).__name__}: {exc}"[:200])
                crew.sink.note(EventKind.NOTE, f"{name} failed: {outcome.error}"[:120])
            result.outcomes.append(outcome)
            moved_this_pass = moved_this_pass or outcome.moved

        if not moved_this_pass:
            result.settled = True
            break

    crew.sink.note(
        EventKind.TICK_FINISHED,
        f"{result.passes} pass{'es' if result.passes != 1 else ''}, "
        f"{'stable' if result.settled else 'stopped at the pass cap'}, "
        f"{len(result.failed)} failed",
    )
    return result
