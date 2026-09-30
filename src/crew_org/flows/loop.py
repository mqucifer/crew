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

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from crew_org.escalation import EscalationLedger, EscalationPolicy
from crew_org.events import (
    CrewEvent,
    EventKind,
    EventSink,
    attributed,
    bridge_crewai,
    flush_bridge,
)
from crew_org.git_ops import Workspace
from crew_org.process import ProcessRules
from crew_org.tools import github_http
from crew_org.tools.github_http import GitHubThrottled
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import ProjectClient, many_repos
from crew_org.tools.sandbox import Sandbox

# A pass that keeps moving forever is a bug, not a busy board. Five is well
# past what a real board needs: refinement feeds admission feeds delivery, and
# each is one pass deep.
MAX_PASSES = 5
# How long a pass that moved nothing waits for pull requests it left in the
# merge queue (#314). GitHub lands one in about a minute; its own check timeout
# is 60. Past this, the tick ends and the next one picks up what landed.
QUEUE_WAIT = 600.0
QUEUE_POLL = 15.0


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
    # Repositories in delivery.repos this tick leaves alone, and why: a
    # project without a usable record isn't worked (#132).
    not_onboarded: dict[str, str] = field(default_factory=dict)
    # Projects whose approved epics wait while the Architect revisits their
    # design, and why (#192). Set by the revisit phase each pass.
    design_holds: dict[str, str] = field(default_factory=dict)
    # Where the retro is recorded: a sprint with one takes no more work (#193).
    crew_repo: str | None = None


def not_onboarded(ws: Workspace, repos: set[str]) -> dict[str, str]:
    """Each repository without a usable record, and what's missing (#132).

    A precondition, not a gate (decided on #111): adding a repository to
    `delivery.repos` is already the Sponsor's decision, and working it on
    answers nobody gave would be the crew deciding instead. The standup says
    which, and why, until the project is onboarded.
    """
    from crew_org.project import RECORD_PATH, ProjectRecordError, read_record  # noqa: PLC0415

    gaps: dict[str, str] = {}
    for repo in sorted(repos):
        try:
            record = read_record(ws.for_repo(repo).current())
        except ProjectRecordError as exc:
            gaps[repo] = f"its record is unusable: {exc}. Finish it with `crew onboard {repo}`"
            continue
        except Exception as exc:  # noqa: BLE001
            gaps[repo] = f"its record couldn't be checked: {exc}"
            continue
        if record is None:
            gaps[repo] = f"it has no `{RECORD_PATH}`. Onboard it with `crew onboard {repo}`"
    return gaps


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
    # GitHub throttled past what a tick waits (#293): stopped, to resume next tick.
    throttled: bool = False
    # (repo, card) the Senior Engineer diagnosed at the end of this tick.
    diagnosed: list[tuple[str, int]] = field(default_factory=list)
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


def _land(crew: Crew) -> PhaseOutcome:
    """Land approved work and close what's finished, before any hold is judged (#388).

    The bookkeeping used to come last in a pass: merges in `deliver`, finished
    epics in `qa`. But `revisit`, `refine`, `admit` and `deliver` judge their
    holds earlier ("technical work lands first", "builds on #304"), so a hold a
    merge released was only seen a pass later. Sprint 11's first ticks each
    stopped at the pass cap with holds on work that had already finished.
    `deliver` still lands too, for work approved later in the same pass.
    """
    from crew_org.flows.acceptance import close_finished_parents  # noqa: PLC0415
    from crew_org.flows.merge import merge_approved  # noqa: PLC0415

    cards = crew.board.cards()
    landed = merge_approved(
        crew.board, crew.issues, crew.sink, cards=cards, default_repo=crew.repo, repos=crew.repos
    )
    if landed.merged or landed.conflicted or landed.rebuilding:
        cards = crew.board.cards()
    closed = close_finished_parents(
        crew.board, crew.issues, crew.sink, cards, repo=crew.repo, repos=crew.repos
    )
    return PhaseOutcome(
        "land",
        moved=bool(landed.merged or landed.conflicted or landed.rebuilding or closed),
        summary=f"{len(landed.merged)} merged, {len(closed)} closed",
        result=landed,
        counts={"merged": len(landed.merged), "closed": len(closed)},
    )


def _revisit(crew: Crew) -> PhaseOutcome:
    """The Architect revisits a design delivery shows is under strain (#192).

    Before refinement, so an approved epic isn't split against a structure the
    Architect is about to change.
    """
    from crew_org.crews.design_crew import propose_design, review_design  # noqa: PLC0415
    from crew_org.flows.main_watch import watch_default_branches  # noqa: PLC0415
    from crew_org.flows.release_check import check_releases  # noqa: PLC0415
    from crew_org.flows.revisit import revisit_designs  # noqa: PLC0415
    from crew_org.flows.strain import REVISIT_CONFLICTS  # noqa: PLC0415

    # A workflow red on a default branch becomes a technical epic first, so the
    # holds below see it and this pass refines it (#335).
    repos = crew.repos - set(crew.not_onboarded)
    red = watch_default_branches(crew.issues, crew.board, crew.sink, repos=repos)
    # And what the last release actually published (#335).
    released = check_releases(crew.issues, crew.board, crew.sink, repos=repos)
    result = revisit_designs(
        crew.issues,
        crew.reviewer,
        crew.board,
        crew.sink,
        crew.ws,
        crew.board.cards(),
        repos=repos,
        events_dir=crew.sink.path.parent if crew.sink.path else None,
        propose_design=propose_design,
        review_design=review_design,
        threshold=int((crew.org.get("design") or {}).get("revisit_conflicts", REVISIT_CONFLICTS)),
    )
    crew.design_holds = dict(result.holds)
    return PhaseOutcome(
        "revisit",
        moved=result.moved or bool(red.filed or red.closed or released.filed),
        summary=(
            f"{len(result.proposed)} design revisions proposed, {len(result.merged)} merged, "
            f"{len(result.epics)} technical epics"
            + (f", {len(red.filed)} for a red default branch" if red.filed else "")
            + (f", {len(released.filed)} for an incomplete release" if released.filed else "")
        ),
        result=result,
        counts={
            "proposed": len(result.proposed),
            "unchanged": len(result.unchanged),
            "merged": len(result.merged),
            "technical epics": len(result.epics),
        },
        held=[f"{repo} — {why}" for repo, why in sorted(result.holds.items())]
        + [f"{repo} — design revisit failed: {why}" for repo, why in result.failed]
        + [f"{repo} — default branch unread: {why}" for repo, why in red.failed]
        + [f"{repo} — release unchecked: {why}" for repo, why in released.failed]
        + [
            f"{repo} v{version} — waiting on the Sponsor: make the package public "
            "(its settings page; crew#386)"
            for repo, version in released.sponsor
        ],
    )


def _order(crew: Crew) -> PhaseOutcome:
    """The Product Owner orders a repository's backlog when new work arrives (#358)."""
    from crew_org.crews.refinement_crew import order_backlog  # noqa: PLC0415
    from crew_org.flows.backlog_order import order_backlogs  # noqa: PLC0415

    result = order_backlogs(
        crew.board,
        crew.issues,
        crew.sink,
        crew.board.cards(),
        repos=crew.repos,
        order=order_backlog,
    )
    return PhaseOutcome(
        "order",
        moved=bool(result.ordered or result.holds),
        summary=f"{len(result.ordered)} backlogs ordered, {len(result.holds)} new holds",
        result=result,
        counts={"ordered": len(result.ordered), "holds": len(result.holds)},
        held=[f"{repo} — backlog not ordered: {why}" for repo, why in result.failed],
    )


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
        repos=crew.repos,
        holds=crew.design_holds,
    )
    # Parking an epic is movement: the card left Needs Refinement, and a pass
    # that reports "nothing moved" over it would hide the one thing that
    # changed.
    moved = bool(result.epics_created or result.stories_created or result.parked or result.admitted)
    return PhaseOutcome(
        "refine",
        moved=moved,
        summary=f"{len(result.epics_created)} epics, {len(result.stories_created)} stories",
        result=result,
        counts={
            "epics": len(result.epics_created),
            "stories": len(result.stories_created),
            "admitted": len(result.admitted),
            # What it looked at and left alone, so a quiet pass says why it was
            # quiet rather than only that it was.
            "already answered": sum(1 for _n, why in result.skipped if why == "already answered"),
        },
        held=[f"#{n} — {why}" for n, why in result.failed]
        + [f"#{n} — {why}" for n, why in result.skipped if "refused" in why]
        + [f"#{n} — waiting for room: {why}" for n, why in result.waiting]
        + [f"#{n} — waits: {why}" for n, why in result.held_for_design],
        blocked=[
            f"#{n} — the split failed the same way twice; parked for a person"
            for n in result.parked
        ],
    )


def _design(crew: Crew) -> PhaseOutcome:
    """Design notes for the epics that need one, before their stories are admitted (#155)."""
    from crew_org.crews import presentation_note_crew as ux  # noqa: PLC0415
    from crew_org.crews.design_crew import review_design  # noqa: PLC0415
    from crew_org.crews.design_note_crew import render, write_note  # noqa: PLC0415
    from crew_org.flows import presentation_notes  # noqa: PLC0415
    from crew_org.flows.design_notes import write_notes  # noqa: PLC0415

    result = write_notes(
        crew.issues,
        crew.sink,
        crew.ws,
        crew.board.cards(),
        default_repo=crew.repo,
        repos=crew.repos,
        write=write_note,
        review=review_design,
        render=render,
    )
    # The UX Designer's note, for epics that change what a reader sees (#377).
    shown = presentation_notes.write_notes(
        crew.issues,
        crew.sink,
        crew.ws,
        crew.board.cards(),
        default_repo=crew.repo,
        repos=crew.repos,
        write=ux.write_note,
        render=ux.render,
        line=ux.criterion_lines,
    )
    return PhaseOutcome(
        "design",
        moved=bool(result.written or result.blocked or shown.written or shown.blocked),
        summary=(
            f"{len(result.written)} design notes written"
            + (f", {len(shown.written)} presentation notes" if shown.written else "")
        ),
        result=result,
        counts={"notes": len(result.written), "presentation_notes": len(shown.written)},
        held=[f"epic #{n} — design note failed: {why}" for n, why in result.failed]
        + [f"epic #{n} — presentation note failed: {why}" for n, why in shown.failed],
        blocked=[f"epic #{n} — no design note, needs a person: {why}" for n, why in result.blocked]
        + [f"epic #{n} — no presentation note, needs a person: {why}" for n, why in shown.blocked],
    )


def _admit(crew: Crew) -> PhaseOutcome:
    """Ready to Sprint Backlog.

    Approving the epic was the scope decision, so this is arithmetic. It was
    the one hop `tick` refused to make, on a docstring claiming a third human
    gate that section 1 of the constitution does not have.
    """
    from crew_org.flows.sprint import start_sprint  # noqa: PLC0415

    closed = _closed(crew)
    if closed:
        return PhaseOutcome("admit", summary="the sprint is closed", held=[closed])

    plan = start_sprint(
        crew.board,
        crew.issues,
        crew.sink,
        crew.rules,
        sprint=crew.sprint,
        capacity=crew.capacity,
        default_repo=crew.repo,
        repos=crew.repos,
    )
    return PhaseOutcome(
        "admit",
        moved=bool(plan.admitted),
        summary=(
            f"{len(plan.admitted)} stories, {plan.points} points; "
            f"{plan.total} of {plan.capacity} in {plan.sprint}"
        ),
        result=plan,
        counts={"stories": len(plan.admitted), "points": plan.points},
        held=[
            f"{c.name(qualify=many_repos(plan.unestimated))} — no epic and no estimate"
            for c in plan.unestimated
        ]
        + [f"{c.name(qualify=True)} — ready, and not the crew's to deliver" for c in plan.not_ours]
        + [
            f"{c.name(qualify=True)} — waits for its epic's design note"
            for c in plan.waiting_on_design
        ]
        + [
            f"{c.name(qualify=True)} — waits for its epic to be split again"
            for c in plan.waiting_on_rework
        ]
        + (
            [f"{plan.sprint} is full: {plan.committed} of {plan.capacity} points"]
            if plan.full and any(s.deferred for s in plan.slices)
            else []
        ),
    )


def _closed(crew: Crew) -> str:
    """Why nothing may be admitted into the current sprint, or '' if it may.

    A sprint whose retro is recorded is over, whatever its dates say. Sprint 6
    was closed in the afternoon and still took fifteen stories that evening,
    which its retro never saw (#193).
    """
    from crew_org.flows.retro import existing_retro  # noqa: PLC0415

    if not crew.crew_repo:
        return ""
    try:
        retro = existing_retro(crew.issues, crew.crew_repo, crew.sprint)
    except Exception:  # noqa: BLE001
        return ""
    if retro is None:
        return ""
    after = crew.board.schema.field("Sprint").next_iteration(crew.sprint)
    starts = f"{after[0]} starts on {after[1]:%Y-%m-%d}" if after else "no later sprint is set up"
    return f"{crew.sprint} is closed (retro #{retro}): nothing more is admitted; {starts}"


def _clone(crew: Crew, repo: str):
    """The repository at its base branch, or None: the review goes ahead without it."""
    try:
        return crew.ws.for_repo(repo).current()
    except Exception as exc:  # noqa: BLE001
        crew.sink.note(EventKind.NOTE, f"{repo}: no clone for the review: {exc}"[:120])
        return None


def _review(crew: Crew) -> PhaseOutcome:
    from crew_org.flows.review import review_open_pulls  # noqa: PLC0415

    moved_any = False
    reviewed = skipped = 0
    failed: list[str] = []
    returned: list[str] = []
    cards = crew.board.cards()
    for repo in sorted(crew.repos):
        result = review_open_pulls(
            crew.reviewer,
            crew.sink,
            repo=repo,
            bot_login=crew.reviewer_login,
            board=crew.board,
            cards=cards,
            writer=crew.issues,
            clone=_clone(crew, repo),
        )
        reviewed += len(result.reviewed)
        returned += [f"#{n}" for n, _epic in result.returned]
        skipped += len(result.skipped)
        failed += [f"PR #{n} — {why}" for n, why in result.failed]
        moved_any = moved_any or bool(result.reviewed)
    return PhaseOutcome(
        "review",
        moved=moved_any,
        summary=f"{reviewed} reviewed, {skipped} already judged"
        + (
            f"; {', '.join(returned)} back to refinement: review conflicts with its criteria"
            if returned
            else ""
        ),
        counts={"reviewed": reviewed, "already judged": skipped, "returned": len(returned)},
        held=failed,
    )


def _qa(crew: Crew) -> PhaseOutcome:
    from crew_org.flows.acceptance import close_finished_parents, run_qa  # noqa: PLC0415

    cards = crew.board.cards()
    result = run_qa(
        crew.board,
        crew.issues,
        crew.sink,
        crew.ws,
        crew.sandbox,
        cards=cards,
        repo=crew.repo,
        repos=crew.repos,
    )
    result.parents_closed = close_finished_parents(
        crew.board, crew.issues, crew.sink, crew.board.cards(), repo=crew.repo, repos=crew.repos
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
    reverts = result.reverts
    moved = bool(
        result.landed
        or result.delivered
        or result.blocked
        or result.recovered
        or result.reworked
        or reverts.merged
        or reverts.returned
    )
    # State: still true after this pass.
    held = (
        [f"#{n} — waiting on an approving review (PR #{pr})" for n, pr in result.awaiting_approval]
        + [
            f"#{n} — PR #{pr} was behind main; brought up to date, merges once checks pass"
            for n, pr in result.updating
        ]
        + [f"#{n} — {why}; merges once they pass" for n, why in result.checking]
        + [f"#{n} — PR #{pr} is in the merge queue" for n, pr in result.queued]
        + [f"#{n} — {why}" for n, why in result.unmergeable]
        + [f"#{n} — waits for #{b} in the same epic" for n, b in result.waiting_on_a_sibling]
        + [
            f"#{n} — PR #{pr} was approved and conflicts with main; returned for a rebuild"
            for n, pr in result.rebuilding
        ]
        + [f"revert PR #{pr} — waiting on an approving review" for pr in reverts.awaiting_approval]
        + [f"revert PR #{pr} — conflicts with main, needs a person" for pr in reverts.conflicted]
        + [
            f"revert PR #{pr} — approved and GitHub will not count it"
            for pr in reverts.unapprovable
        ]
        + [f"revert PR #{pr} — {why}" for pr, why in reverts.failed]
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
        counts={
            "merged": len(result.landed),
            "delivered": len(result.delivered),
            "reverted": len(reverts.merged),
        },
        held=held,
        blocked=blocked,
    )


# Drain-first, in dependency order. The Architect revisits a strained design
# before refinement splits epics against it. Refinement produces stories,
# admission puts them in a sprint, and the three that follow push started work
# forward before delivery claims more.
PHASES: tuple[tuple[str, Callable[..., PhaseOutcome]], ...] = (
    # First, so every hold below is judged against the board as it now stands.
    ("land", _land),
    ("revisit", _revisit),
    # Before refinement, so new holds and the order apply to this pass (#358).
    ("order", _order),
    ("refine", _refine),
    ("design", _design),
    ("admit", _admit),
    ("review", _review),
    ("qa", _qa),
    ("deliver", _deliver),
)


def wait_for_queue(
    crew: Crew,
    queued: list[tuple[str, int, str]],
    *,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> tuple[int, float]:
    """Wait until the merge queue lets go of `queued`, or QUEUE_WAIT passes.

    Returns how many left it (merged or removed, the next pass tells which)
    and how long it waited. A lookup that fails counts as still queued.
    """
    # Looked up when called, not bound at import, so a test's stand-in is seen.
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    pending = set(queued)
    start = clock()
    while pending and clock() - start < QUEUE_WAIT:
        sleep(QUEUE_POLL)
        for repo, pull, base in sorted(pending):
            try:
                state = crew.issues.queue_state(repo, pull, branch=base)
            except Exception:  # noqa: BLE001
                continue
            if not state.queued:
                pending.discard((repo, pull, base))
    return len(queued) - len(pending), clock() - start


def diagnose_blocked_cards(crew: Crew) -> list[tuple[str, int]]:
    """Diagnose what this tick left blocked for a person, as the Senior Engineer."""
    from pathlib import Path  # noqa: PLC0415

    from crew_org.crews.diagnosis_crew import choose_files  # noqa: PLC0415
    from crew_org.crews.diagnosis_crew import diagnose as diagnose_files  # noqa: PLC0415
    from crew_org.flows.diagnose import diagnose_blocked  # noqa: PLC0415
    from crew_org.permissions import Capability, Permissions  # noqa: PLC0415

    Permissions.from_agents().require("Senior Engineer", Capability.DIAGNOSE_CREW)
    return attributed(
        lambda c: diagnose_blocked(
            c.issues,
            c.sink,
            cards=c.board.cards(),
            repos=c.repos,
            events_dir=Path("var/events"),
            out_dir=Path("var/diagnoses"),
            choose=choose_files,
            diagnose=diagnose_files,
        ),
        sprint=crew.sprint,
        purpose="diagnose",
    )(crew)


def run(crew: Crew, *, max_passes: int = MAX_PASSES) -> LoopResult:
    """Run every phase, in order, until a pass moves nothing."""
    result = LoopResult()

    # Model calls, their tokens and the tools they used. Installed once, for
    # the whole tick: the bridge existed and nothing called it, so every real
    # run's log was blind to everything the model did.
    bridge_crewai(crew.sink)
    # Every throttle GitHub asks for, waited out, is seen (#293).
    github_http.observe(
        lambda wait, detail: crew.sink.emit(
            CrewEvent(
                kind=EventKind.GITHUB_THROTTLED,
                summary=f"GitHub asked for {wait:.0f}s; waiting",
                detail={"wait_s": round(wait, 1), **detail},
            )
        )
    )

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
            # What GitHub last said is left of the crew's budget (#293).
            github_remaining=github_http.BUDGET.get("core", {}).get("remaining"),
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
                # Every model call a phase makes says which phase, and which
                # sprint, even one made for no card (#179).
                outcome = attributed(phase, sprint=crew.sprint, purpose=name)(crew)
            except GitHubThrottled as exc:
                # GitHub asked for longer than a tick waits (#293). Stop here:
                # every further request would only be refused, and the next
                # tick resumes from the board as it stands.
                crew.sink.emit(
                    CrewEvent(
                        kind=EventKind.GITHUB_THROTTLED,
                        summary=f"{name}: {exc}"[:120],
                        detail={"wait_s": round(exc.wait, 1), "stopped": True},
                    )
                )
                result.outcomes.append(PhaseOutcome(name, error=str(exc)[:200]))
                result.throttled = True
                break
            except Exception as exc:  # noqa: BLE001
                # The pass continues. Later phases act on cards this one never
                # touched, and a tick that aborts here leaves the board partway
                # through a state nobody chose.
                outcome = PhaseOutcome(name, error=f"{type(exc).__name__}: {exc}"[:200])
                crew.sink.note(EventKind.NOTE, f"{name} failed: {outcome.error}"[:120])
            result.outcomes.append(outcome)
            moved_this_pass = moved_this_pass or outcome.moved
        if result.throttled:
            break

        if not moved_this_pass:
            # Nothing moved, but the queue may be about to land what the next
            # story waits for: #198 merged one second after tick G called the
            # board stable, and each tick finished one story of an epic (#314).
            queued = [
                q
                for o in result.outcomes[-len(PHASES) :]
                if o.name in ("land", "deliver") and o.result is not None
                for q in getattr(o.result, "in_queue", [])
            ]
            if queued and result.passes < max_passes:
                left, waited = wait_for_queue(crew, queued)
                crew.sink.note(
                    EventKind.NOTE,
                    f"waited {waited:.0f}s for the merge queue: {left} of {len(queued)} left it",
                    waited_s=round(waited, 1),
                    left=left,
                    queued=len(queued),
                )
                if left:
                    continue
            result.settled = True
            break

    # The Senior Engineer on cards now blocked for a person, once each (#9).
    # After the passes, so it reads the board as the tick left it. Best
    # effort: a diagnosis that fails is noted, and the tick still ends.
    if not result.throttled:
        try:
            result.diagnosed = diagnose_blocked_cards(crew)
        except Exception as exc:  # noqa: BLE001
            crew.sink.note(EventKind.NOTE, f"diagnosis skipped: {exc}"[:120])

    # Comments left unread because their author isn't trusted (crew#399):
    # said, so a real contributor is never dropped silently.
    for repo, number, login in sorted(getattr(crew.issues, "ignored", None) or ()):
        crew.sink.note(
            EventKind.NOTE,
            f"{repo}#{number}: a comment by {login or 'an unknown account'} was not read "
            "(not in org.yaml's trust list, crew#399)",
        )

    crew.sink.note(
        EventKind.TICK_FINISHED,
        f"{result.passes} pass{'es' if result.passes != 1 else ''}, "
        f"{'stable' if result.settled else 'stopped at the pass cap'}, "
        f"{len(result.failed)} failed",
    )
    # The bus delivers on a thread pool; the tick's last model calls can still
    # be in flight. Wait for them, or the log loses exactly the end of the run.
    flush_bridge()
    return result
