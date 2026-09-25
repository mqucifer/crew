"""Delivery: a story in the sprint becomes a pull request.

The loop is deliberately narrow. A Developer produces a typed implementation,
it is written into an isolated worktree, and the worktree is linted and tested.
If that fails, the failure is *classified* before anything else happens — which
is what keeps escalation from becoming the easy path.

Only VERIFY failures escalate, and only after local repair is exhausted. A
SCHEMA failure means the prompt or schema is wrong and gets repaired locally; a
SCOPE failure means the story was not ready and goes back to refinement.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from crew_org.columns import BLOCKED, DONE, IN_PROGRESS, REVIEWING, SPRINT_BACKLOG
from crew_org.crews.delivery_crew import Implementation, implement_story
from crew_org.escalation import (
    Disposition,
    EscalationLedger,
    EscalationPolicy,
    EscalationRecord,
    FailureClass,
    LocalFailure,
    utcnow,
)
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows import artifacts
from crew_org.flows.artifacts import signed
from crew_org.flows.attempts import first_error
from crew_org.flows.design_notes import story_note
from crew_org.flows.history import ANSWERED_MARKER, latest_answer
from crew_org.flows.merge import REBUILD_MARKER, merge_approved
from crew_org.flows.moves import move_card
from crew_org.flows.revert import RevertLanding, land_reverts
from crew_org.git_ops import MergeConflict, Workspace, branch_name
from crew_org.llm import reraise_if_down
from crew_org.process import ProcessRules
from crew_org.project import ProjectRecordError, brief, read_record
from crew_org.tools import bounds, claude_code, regression, workspace
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, LinkedPull, ProjectClient
from crew_org.tools.repo_context import repository_context

STORY_TYPE = "Story"

# Marks the comment a repair leaves on the pull request it updated, so the
# thread says why a new commit appeared rather than leaving the reviewer to
# infer it from a push notification.
REWORK_MARKER = "<!-- crew:rework -->"


@dataclass
class DeliveryOutcome:
    card: int
    branch: str | None = None
    pr: int | None = None
    blocked_reason: str | None = None
    attempts: int = 0
    # Counted per failure class: a mechanical mistake like naming a definition
    # that does not exist should not spend the budget kept for real failures.
    # Observed on story #8, where two invented names left the first genuine test
    # failure with no repair left and it escalated immediately.
    attempts_by_class: dict[str, int] = field(default_factory=dict)
    escalated: bool = False
    diff: str | None = None
    # What the Developer actually wrote, kept when the card blocks. Deliberately
    # not `diff`: that field means "verified work a dry run chose not to land",
    # and `ok` is defined in terms of it. This is the opposite — the artifact of
    # a failure, which would otherwise be deleted with the worktree.
    rejected_diff: str | None = None
    # The whole failure report, not the 400-character extract the event log
    # carries. A pytest run with thirteen failures does not fit in 400
    # characters, and the part that identifies the defect is rarely the front.
    failure_detail: str | None = None

    def seen(self, failure_class: str) -> int:
        return self.attempts_by_class.get(failure_class, 0)

    def count(self, failure_class: str) -> None:
        self.attempts_by_class[failure_class] = self.seen(failure_class) + 1
        self.attempts += 1

    @property
    def ok(self) -> bool:
        return self.pr is not None or self.diff is not None

    @property
    def landed(self) -> bool:
        """Did this actually reach a pull request? False for a dry run."""
        return self.pr is not None


@dataclass
class DeliveryResult:
    delivered: list[DeliveryOutcome] = field(default_factory=list)
    blocked: list[DeliveryOutcome] = field(default_factory=list)
    recovered: list[int] = field(default_factory=list)
    # Cards the Code Reviewer sent back that this pass picked up again. Its own
    # list because "delivered" reads as new work, and the difference between a
    # pass that started something and one that finally finished something the
    # loop had been dropping is the whole point of the card that added it.
    reworked: list[int] = field(default_factory=list)
    not_ours: list[int] = field(default_factory=list)
    landed: list[int] = field(default_factory=list)
    conflicted: list[int] = field(default_factory=list)
    # (card, PR) approved, conflicting with main, and returned for a rebuild.
    rebuilding: list[tuple[int, int]] = field(default_factory=list)
    # Why a story that was ready to land did not. merge_approved has always
    # worked these out and the command printed neither, so a run that silently
    # skipped every merge looked exactly like a run with nothing to merge.
    awaiting_approval: list[tuple[int, int]] = field(default_factory=list)
    # Approved and refused by branch protection — a gate no tick can satisfy.
    unapprovable: list[tuple[int, int]] = field(default_factory=list)
    unmergeable: list[tuple[int, str]] = field(default_factory=list)
    # (card, PR) behind main and brought up to date this pass; they merge once
    # their checks pass on the new head (#116).
    updating: list[tuple[int, int]] = field(default_factory=list)
    # (story, the earlier sibling it is waiting for). Reported rather than
    # silently skipped: a card that could be claimed and was not needs a reason.
    waiting_on_a_sibling: list[tuple[int, int]] = field(default_factory=list)
    # Reverts landed this pass, and those that could not land yet (#83).
    reverts: RevertLanding = field(default_factory=RevertLanding)
    rate_limited: bool = False


def awaiting_rework(
    issues: IssueClient, repo: str, branch: str, card: Card | None = None
) -> dict | None:
    """The open pull request on `branch` whose *current* head was refused.

    The middle of the loop that was missing. Review moves a card back to In
    Progress when it requests changes, and nothing ever picked it up again:
    `sprint_stories` only reads Sprint Backlog, and `reconcile_orphans` saw an
    In Progress card with an open pull request and moved it straight back to
    Reviewing, where the review phase skipped it as already judged. On
    sprint-metrics#31 that cycle took two minutes and then ran forever.

    Scoped to the head the reviewer actually judged. A repair pushes a new
    commit, so the request no longer matches and the card stops being claimed —
    which is what makes this self-clearing rather than a second loop. GitHub's
    own `reviewDecision` would not do: it stays CHANGES_REQUESTED until someone
    reviews again, so a repaired card would be re-worked forever.

    Any reviewer's request counts, not only the crew's. A person who asks for
    changes is owed the same repair.
    """
    try:
        known = card.open_pull_on(branch) if card is not None else None
        pull = issues.pull_for_branch(repo, branch, known=known)
        if pull is None:
            return None
        head = (pull.get("head") or {}).get("sha")
        if not head:
            return None
        refused = any(
            r.get("state") == "CHANGES_REQUESTED" and r.get("commit_id") == head
            for r in issues.pull_reviews(repo, pull["number"])
        ) or any(
            # Approved, but conflicting with main at merge: returned for a rebuild.
            REBUILD_MARKER.format(head=head) in (c.get("body") or "")
            for c in issues.comments(repo, pull["number"])
        )
    except Exception:  # noqa: BLE001
        return None
    return pull if refused else None


def needs_rework(issues: IssueClient, cards: list[Card], *, repo: str) -> list[Card]:
    """Cards whose pull request is waiting on the Developer, in card order.

    Both columns, because the deadlock could park a card in either: In Progress
    is where review puts it, and Reviewing is where reconciliation bounced it
    back to. Reading both is what lets a card already stuck in Reviewing be
    recovered without anyone touching the board by hand.
    """
    return [
        c
        for c in sorted(
            (
                c
                for c in cards
                if c.status in (IN_PROGRESS, REVIEWING)
                and c.work_type == STORY_TYPE
                and c.state != "CLOSED"
            ),
            key=lambda c: c.number or 0,
        )
        if awaiting_rework(issues, c.repo or repo, branch_name(c.number or 0, c.title), c)
    ]


def reconcile_orphans(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    cards: list[Card],
    *,
    repo: str,
) -> list[int]:
    """Return cards stranded In Progress to the backlog.

    A tick can die — killed, crashed, machine slept — and leave a card claimed
    with nobody working it. The board is the state, so the loop heals it on the
    next pass rather than assuming it was left tidy. This is what makes it a
    reconciliation loop rather than a script that must not be interrupted.

    A card with an open pull request is not stranded; it is waiting for review.
    """
    in_progress = [c for c in cards if c.status == IN_PROGRESS and c.work_type == STORY_TYPE]
    if not in_progress:
        return []

    try:
        open_prs = {pull["head"]["ref"]: pull["number"] for pull in issues.open_pulls(repo)}
    except Exception:  # noqa: BLE001
        open_prs = {}

    recovered: list[int] = []
    for card in in_progress:
        number = card.number or 0
        branch = branch_name(number, card.title)
        if branch in open_prs:
            # Not stranded, and not waiting for review either: the reviewer has
            # already refused this head, so the card is exactly where it should
            # be and the rework pass will claim it. Moving it to Reviewing is
            # what made the ping-pong — review skips it as already judged, and
            # the card never comes back.
            if awaiting_rework(issues, repo, branch, card) is not None:
                continue
            move_card(
                board,
                sink,
                item_id=card.item_id,
                to=REVIEWING,
                by=None,
                card=number,
                frm=IN_PROGRESS,
                summary=f"already has PR #{open_prs[branch]} — moved to review",
            )
            continue

        move_card(
            board,
            sink,
            item_id=card.item_id,
            to=SPRINT_BACKLOG,
            by=None,
            card=number,
            frm=IN_PROGRESS,
            summary="stranded In Progress with no PR — returned to the backlog",
        )
        recovered.append(number)
    return recovered


def sprint_stories(cards: list[Card], sprint: str, *, repos: set[str] | None = None) -> list[Card]:
    """Stories in this sprint the crew may pick up.

    `repos` is the allow-list of repositories the crew works in. A card outside
    it belongs on the board — refined, prioritised, visible — but is not the
    crew's to implement, so it is never claimed.
    """
    return sorted(
        (
            c
            for c in cards
            if c.status == SPRINT_BACKLOG
            and c.work_type == STORY_TYPE
            and c.state != "CLOSED"
            and (c.sprint == sprint or sprint is None)
            and (repos is None or c.repo in repos)
        ),
        key=lambda c: c.number or 0,
    )


def prior_verdicts(
    issues: IssueClient, repo: str, number: int, branch: str, *, known: LinkedPull | None = None
) -> str:
    """What the Reviewer and QA said the last time this story was delivered.

    Both, not the most recent: they judge different things — the diff and the
    behaviour — and a card can be returned by one while the other was content.
    Returning a card for a named defect and then implementing it again knowing
    nothing about that defect is how a story is returned twice for the same
    reason.

    Best effort. A card being delivered for the first time has neither, and a
    reading failure is not worth losing the delivery over.
    """
    from crew_org.flows.acceptance import QA_MARKER  # noqa: PLC0415
    from crew_org.flows.review import REVIEW_MARKER  # noqa: PLC0415

    parts: list[str] = []
    try:
        qa = [c for c in issues.comments(repo, number) if QA_MARKER in (c.get("body") or "")]
        if qa:
            parts.append(qa[-1]["body"])
    except Exception:  # noqa: BLE001, S110
        pass
    try:
        pull = issues.pull_for_branch(repo, branch, known=known)
        if pull:
            reviews = [
                r
                for r in issues.pull_reviews(repo, pull["number"])
                if REVIEW_MARKER in (r.get("body") or "")
            ]
            if reviews:
                parts.append(reviews[-1]["body"])
    except Exception:  # noqa: BLE001, S110
        pass
    return "\n\n---\n\n".join(parts)


def previous_fate(issues: IssueClient, repo: str, branch: str) -> str:
    """What became of the last pull request from this branch, in one line.

    A verdict says what was wrong. This says what happened next, and the two
    are not the same: a pull request closed without merging carries no verdict
    at all, so a story could come back with nothing on the card explaining why.

    Best effort — a story delivered for the first time has no pull request, and
    a read failure is not worth losing the delivery over.
    """
    try:
        pulls = issues.pulls_for_branch(repo, branch)
    except Exception:  # noqa: BLE001
        return ""
    if not pulls:
        return ""
    pull = pulls[0]
    number = pull.get("number")
    if pull.get("merged_at"):
        return f"PR #{number} was merged."
    if pull.get("state") == "closed":
        return f"PR #{number} was **closed without merging**. The work on it was not accepted."
    return f"PR #{number} is still open, and this story was sent back to be re-worked."


def prior_context(
    issues: IssueClient,
    repo: str,
    number: int,
    branch: str,
    *,
    resumed: bool,
    known: LinkedPull | None = None,
) -> str:
    """Everything a second attempt needs: what it did, what happened, and where that leaves it.

    The three have to arrive together. A verdict on its own describes code, and
    a Developer handed a verdict about code its worktree does not contain will
    try to edit a file that is not there — sprint-metrics #31 spent an attempt
    doing exactly that, was refused by the edit tool, and returned an empty
    implementation rather than admit it could not see what it was being asked
    about. Incomplete context was worse than none.

    So `resumed` is stated rather than assumed. It is the difference between
    "your previous work is in these files" and "it is not, and here is why".
    """
    verdicts = prior_verdicts(issues, repo, number, branch, known=known)
    fate = previous_fate(issues, repo, branch)
    if not (verdicts or fate):
        return ""

    parts: list[str] = []
    if fate:
        parts.append(f"## What happened to your previous attempt\n\n{fate}")
    if verdicts:
        parts.append(f"## What the gates said about it\n\n{verdicts}")
    if resumed:
        parts.append(
            "## Where that leaves your files\n\n"
            f"**Your previous attempt is already in the repository below**, on branch "
            f"`{branch}`. You are continuing it, not starting again.\n\n"
            "- Change what the verdicts above identified, with `replace`, and leave the "
            "rest alone.\n"
            "- Do not re-send work that is already there. `add` on a name already in the "
            "listing is rejected.\n"
            "- If the listing does not contain something a verdict mentions, say so rather "
            "than inventing it."
        )
    else:
        parts.append(
            "## Where that leaves your files\n\n"
            "**Your previous attempt is not in the repository below.** This branch starts "
            "from the default branch, so anything the verdicts above describe has to be "
            "written again. Treat them as what went wrong before, not as a description of "
            "code you can edit."
        )
    return "\n\n".join(parts)


def held_by_a_sibling(cards: list[Card], story: Card) -> Card | None:
    """The earlier story in this story's epic that has not landed yet.

    Stories in one epic extend each other. `In Progress` has a WIP limit of 3,
    so without this the crew claims three siblings at once, each branching from
    a default branch that does not yet contain the others.

    sprint-metrics #31 created `scrape.py` with an HTTP server on it; #32, whose
    story was "handle unavailable data **at the scrape endpoint**", was claimed
    100 seconds later, could not see `scrape.py`, and built a second server in
    another module. Both passed their own tests.

    Earlier means lower card number, which is the order the Business Analyst
    proposed the stories: `refine_epics` creates their issues in that order, so
    the numbers ascend in it. Landed means Done or closed — an open pull request
    is not in anyone's base.
    """
    if story.parent is None:
        return None
    blockers = [
        c
        for c in cards
        # The repository as well as the parent number. Both are plain numbers,
        # so a story in one repository could be held back by a sibling of an
        # epic in another whenever the epic numbers happened to line up.
        if c.repo == story.repo
        and c.parent == story.parent
        and c.work_type == STORY_TYPE
        and (c.number or 0) < (story.number or 0)
        and c.state != "CLOSED"
        and c.status != DONE
    ]
    return min(blockers, key=lambda c: c.number or 0) if blockers else None


def not_ours(cards: list[Card], sprint: str, repos: set[str]) -> list[Card]:
    """Sprint stories the crew is not permitted to work on. Reported, not hidden."""
    return [
        c
        for c in cards
        if c.status == SPRINT_BACKLOG
        and c.work_type == STORY_TYPE
        and c.state != "CLOSED"
        and (c.sprint == sprint or sprint is None)
        and c.repo not in repos
    ]


def escalation_prompt(story: str, failure: str) -> str:
    return (
        "You are finishing work a smaller model could not complete, in a git worktree.\n\n"
        f"## The story\n\n{story}\n\n"
        f"## What is failing\n\n```\n{failure}\n```\n\n"
        "Fix the implementation so the tests and lint pass. Write the test that "
        "expresses each acceptance criterion if it is missing. Stay inside the story's "
        "scope — anything else you notice belongs in a new issue, not this change.\n"
        "Do not commit or push; the crew handles that."
    )


def deliver_story(
    card: Card,
    *,
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    ws: Workspace,
    policy: EscalationPolicy,
    ledger: EscalationLedger,
    sprint: str,
    repo: str,
    default_branch: str,
    rework: bool = False,
) -> DeliveryOutcome:
    """Take one story from Sprint Backlog to a pull request.

    `rework` says the story is coming back from the Code Reviewer and there is
    an open pull request to *update*. That inverts the open-pull-request guard
    below: normally an open pull request means stop, because overwriting it
    would destroy the diff a review is judging. Here updating it is the whole
    point, and the new commit is what the reviewer asked for.

    A dry run stops once the work is verified: the diff is captured and nothing
    is committed, pushed or opened. It is the same code path as a real run up to
    that point, so what it shows is what would land.
    """
    number = card.number or 0
    outcome = DeliveryOutcome(card=number)
    story_text = f"{card.title}\n\n{issues.get(repo, number).get('body') or ''}"

    branch = branch_name(number, card.title)
    outcome.branch = branch
    # Resume only live rework: a branch whose pull request is still open, so a
    # gate's feedback is answered on the code it was about. A branch whose pull
    # request was closed, or never opened, is dead history, and its base can
    # predate work this story depends on — sprint-metrics #32 was resumed on a
    # four-day-old branch cut before its sibling #31 landed, and produced
    # nothing (#119). That starts from current `main` instead.
    live = issues.pull_for_branch(repo, branch, known=card.open_pull_on(branch))
    worktree = ws.open(branch, resume=live is not None)
    # Set when a returned story's branch conflicted with main and it was rebuilt
    # from main instead (#158): the conflicting paths, for the Developer and the PR.
    rebuilt_over: list[str] | None = None
    if getattr(ws, "resumed", False):
        # Resumed, but `main` has moved since it was cut. Bring it up to date
        # before the work, not after, so the Developer sees what has landed.
        try:
            ws.catch_up()
        except MergeConflict as conflict:
            paths = ", ".join(conflict.files) or "unknown files"
            if not rework:
                outcome.blocked_reason = (
                    f"`{branch}` conflicts with main in {paths}; "
                    "resolving that is a decision for a person, not something to force"
                )
                return outcome
            # Returned by a gate: the code is being rewritten anyway, so a
            # conflict in it isn't a decision for a person. Rebuild on current
            # main; the gate's findings carry over as the card's history.
            # sprint-metrics#73 was blocked here before its Developer ever saw
            # the review, because #70 had merged into the same lines (#158).
            worktree = ws.open(branch, resume=False)
            rebuilt_over = conflict.files or ["unknown files"]
            # Structured, for the Architect's evidence (#192): which files keep
            # colliding, on which project's cards.
            sink.note(
                EventKind.NOTE,
                f"#{number} rebuilt from main: its branch conflicted in {paths}",
                rebuilt=True,
                repo=repo,
                card=number,
                paths=list(conflict.files),
            )
    # The project's own answers (#131). A record that exists but can't be read
    # stops the card: its rules are unknown, and working on without them would
    # be working on rules nobody chose.
    try:
        record = read_record(worktree)
    except ProjectRecordError as exc:
        outcome.blocked_reason = f"the project's record can't be read: {exc}"
        return outcome
    # The Architect's note for this story's epic, if it has one (#155).
    note = story_note(issues, card, repo)
    sink.emit(
        CrewEvent(kind=EventKind.AGENT_STARTED, role="Developer", card=number, summary=branch)
    )

    feedback = ""
    # What the gates said if this story has been round before. Read once: it is
    # fixed for this delivery, where `feedback` changes on every attempt.
    prior = prior_context(
        issues,
        repo,
        number,
        branch,
        resumed=getattr(ws, "resumed", False),
        known=card.open_pull_on(branch),
    )
    if rebuilt_over:
        # Self-contained: it must read right even when no verdict could be found.
        answering = " Answer every finding the gates gave it, above." if prior else ""
        prior = (
            (f"{prior}\n\n" if prior else "") + "## Why you are starting again\n\n"
            "While your previous attempt waited to land, other work merged into the same "
            "lines of "
            + ", ".join(f"`{p}`" for p in rebuilt_over)
            + ". Rather than merge the two, this story is rebuilt on current main: **your "
            "previous attempt is not in the repository below.** Write it again against "
            "the code as it is now." + answering
        )
    if prior:
        carried = "on its previous work" if getattr(ws, "resumed", False) else "from a clean branch"
        sink.note(EventKind.NOTE, f"#{number} is re-delivered {carried}")
    implementation: Implementation | None = None

    while True:
        # Recomputed every pass: a repair must see the files it just wrote, or
        # it is fixing code it cannot read.
        context = repository_context(worktree)
        if note:
            context = f"# The design note for this story's epic\n\n{note}\n\n{context}"
        if record is not None:
            context = f"{brief(record)}\n\n{context}"
        try:
            implementation = implement_story(
                story_text, context=context, feedback=feedback, prior=prior, returned=rework
            )
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            # The model could not produce a valid implementation at all.
            failure = LocalFailure(
                card=number,
                role="Developer",
                failure_class=FailureClass.SCHEMA,
                attempts=outcome.seen("EDIT"),
                detail=str(exc)[:400],
            )
            decision = policy.decide(failure, spent=ledger.spent(sprint))
            outcome.count("EDIT")
            sink.emit(
                CrewEvent(
                    kind=EventKind.ESCALATION_DECIDED,
                    role="Developer",
                    card=number,
                    summary=f"SCHEMA — {decision.disposition}",
                    detail={
                        # The reason is the whole diagnostic. Recording only the
                        # disposition says what happened and not why, which is
                        # exactly what a retro needs.
                        "failure_class": FailureClass.SCHEMA,
                        "attempt": outcome.attempts,
                        "reason": decision.reason,
                        "error": str(exc)[:600],
                    },
                )
            )
            if decision.disposition is Disposition.RETRY_LOCAL:
                feedback = f"Your output did not validate:\n{exc}"
                continue
            outcome.blocked_reason = decision.reason
            return outcome

        # A "new file" that already exists is a whole-file rewrite wearing a
        # different name, which is the thing editing by name exists to prevent.
        overwrites = regression.overwrites_existing(worktree, implementation.new_files)
        if overwrites:
            failure = LocalFailure(
                card=number,
                role="Developer",
                failure_class=FailureClass.VERIFY,
                attempts=outcome.seen("OVERWRITE"),
                detail=f"would overwrite existing files: {', '.join(overwrites)}",
            )
            decision = policy.decide(failure, spent=ledger.spent(sprint))
            outcome.count("OVERWRITE")
            sink.emit(
                CrewEvent(
                    kind=EventKind.ESCALATION_DECIDED,
                    role="Developer",
                    card=number,
                    summary=f"OVERWRITE — {decision.disposition}",
                    detail={"failure_class": "OVERWRITE", "paths": overwrites},
                )
            )
            if decision.disposition is Disposition.RETRY_LOCAL:
                feedback = (
                    f"These already exist: {', '.join(overwrites)}. Change them with "
                    "`edits`, addressed by name, rather than rewriting them as new "
                    "files. Only a file that does not exist yet belongs in new_files."
                )
                continue
            outcome.blocked_reason = f"kept rewriting existing files: {', '.join(overwrites)}"
            return outcome

        # The record's bounds, before anything is written: never-touch paths, the
        # record itself, and CI that stops enforcing a design check. Never
        # escalated, like a contract break: a stronger model would be spent
        # getting past a rule the Sponsor set.
        outside = bounds.out_of_bounds(worktree, implementation, record)
        if outside:
            failure = LocalFailure(
                card=number,
                role="Developer",
                failure_class=FailureClass.REGRESSION,
                attempts=outcome.seen("BOUNDS"),
                detail="; ".join(outside)[:400],
            )
            decision = policy.decide(failure, spent=ledger.spent(sprint))
            outcome.count("BOUNDS")
            sink.emit(
                CrewEvent(
                    kind=EventKind.ESCALATION_DECIDED,
                    role="Developer",
                    card=number,
                    summary=f"BOUNDS — {decision.disposition}",
                    detail={"failure_class": "BOUNDS", "reasons": outside},
                )
            )
            if decision.disposition is Disposition.RETRY_LOCAL:
                feedback = "This change goes outside what the project allows:\n\n" + "\n".join(
                    f"- {reason}" for reason in outside
                )
                continue
            outcome.failure_detail = "\n".join(outside)
            outcome.blocked_reason = f"kept changing what the project protects: {outside[0]}"
            return outcome

        # Checked before a single byte is written. A contract break is only
        # visible as a wall of failing tests once it has been applied, and by
        # then the model is repairing a symptom several steps from the cause —
        # story #9 changed a return type to None and spent every attempt on the
        # TypeError it produced three functions away.
        broken = regression.broken_contracts(worktree, implementation.all_edits)
        if broken:
            failure = LocalFailure(
                card=number,
                role="Developer",
                failure_class=FailureClass.REGRESSION,
                attempts=outcome.seen("REGRESSION"),
                detail="; ".join(f"{k}: {was} -> {now}" for k, (was, now) in broken.items())[:400],
            )
            decision = policy.decide(failure, spent=ledger.spent(sprint))
            outcome.count("REGRESSION")
            sink.emit(
                CrewEvent(
                    kind=EventKind.ESCALATION_DECIDED,
                    role="Developer",
                    card=number,
                    summary=f"REGRESSION — {decision.disposition}",
                    detail={
                        "failure_class": FailureClass.REGRESSION,
                        "attempt": outcome.seen("REGRESSION"),
                        "reason": decision.reason,
                        "contracts": {k: list(v) for k, v in broken.items()},
                    },
                )
            )
            if decision.disposition is Disposition.RETRY_LOCAL:
                feedback = regression.describe_contracts(broken)
                continue
            outcome.failure_detail = regression.describe_contracts(broken)
            outcome.blocked_reason = decision.reason
            return outcome

        try:
            workspace.apply_implementation(worktree, implementation)
        except Exception as exc:  # noqa: BLE001
            failure = LocalFailure(
                card=number,
                role="Developer",
                failure_class=FailureClass.SCHEMA,
                attempts=outcome.seen("EDIT"),
                detail=str(exc)[:400],
            )
            decision = policy.decide(failure, spent=ledger.spent(sprint))
            outcome.count("EDIT")
            sink.emit(
                CrewEvent(
                    kind=EventKind.ESCALATION_DECIDED,
                    role="Developer",
                    card=number,
                    summary=f"EDIT — {decision.disposition}",
                    detail={
                        "failure_class": "EDIT",
                        "attempt": outcome.seen("EDIT"),
                        "error": str(exc)[:400],
                    },
                )
            )
            if decision.disposition is Disposition.RETRY_LOCAL:
                feedback = f"An edit could not be applied:\n\n{exc}"
                continue
            outcome.blocked_reason = f"edits could not be applied: {exc}"
            return outcome

        check = workspace.check(worktree)
        if check.ok:
            break

        failure = LocalFailure(
            card=number,
            role="Developer",
            failure_class=FailureClass.VERIFY,
            attempts=outcome.seen("VERIFY"),
            detail=check.failure_report[:400],
        )
        decision = policy.decide(failure, spent=ledger.spent(sprint))
        outcome.count("VERIFY")
        sink.emit(
            CrewEvent(
                kind=EventKind.ESCALATION_DECIDED,
                role="Developer",
                card=number,
                summary=f"VERIFY — {decision.disposition}",
                detail={
                    "failure_class": FailureClass.VERIFY,
                    "attempt": outcome.seen("VERIFY"),
                    "reason": decision.reason,
                    "failing_commands": [r.command for r in check.results if not r.ok],
                    "output": check.failure_report[:600],
                    # From the whole report: the 600 above stop before pytest's
                    # summary, and the retro counts causes from this (#157).
                    "first_error": first_error(check.failure_report),
                },
            )
        )

        if decision.disposition is Disposition.RETRY_LOCAL:
            feedback = f"Lint or tests failed:\n\n{check.failure_report}"
            continue

        if decision.disposition is Disposition.ESCALATE:
            ledger.record(
                EscalationRecord(
                    at=utcnow(),
                    sprint=sprint,
                    card=number,
                    role="Developer",
                    failure_class=FailureClass.VERIFY,
                    local_attempts=outcome.seen("VERIFY"),
                    justification=None,
                    detail=check.failure_report[:400],
                )
            )
            outcome.escalated = True
            sink.emit(
                CrewEvent(
                    kind=EventKind.ESCALATED,
                    role="Developer",
                    card=number,
                    summary="local repair exhausted",
                    failure_class="VERIFY",
                )
            )
            result = claude_code.escalate(
                worktree, escalation_prompt(story_text, check.failure_report)
            )
            if result.should_park:
                # Not an outcome yet — the work is unfinished, not failed.
                ledger.resolve(number, sprint, "parked on a usage limit")
                outcome.blocked_reason = result.detail
                return outcome
            check = workspace.check(worktree)
            if check.ok:
                ledger.resolve(number, sprint, "resolved — lint and tests pass")
                break
            ledger.resolve(number, sprint, "escalated but still failing")
            outcome.failure_detail = check.failure_report
            outcome.blocked_reason = f"escalation did not resolve it: {check.failure_report[:200]}"
            return outcome

        outcome.failure_detail = check.failure_report
        outcome.blocked_reason = decision.reason
        return outcome

    # Returned work may answer that nothing needs to change, with evidence per
    # finding (#161). The checks above ran on the branch as it stands, so the
    # claim is verified before it is sent. It is committed empty: the pull
    # request's head has to move, or the review it answers keeps applying.
    satisfied = getattr(implementation, "already_satisfied", None) or []
    answered = rework and implementation.changes_nothing and bool(satisfied)
    if answered and live is not None:
        before = latest_answer(issues, repo, live["number"])
        if before:
            # Answered with evidence once already, and sent back again: the gate
            # and the Developer disagree, and another round would only repeat it.
            review = _latest_review(issues, repo, live["number"])
            outcome.failure_detail = (
                f"## The review\n\n{review}\n\n## The earlier answer\n\n{before}"
            )
            outcome.blocked_reason = (
                f"PR #{live['number']} was answered without a change and sent back again: "
                "the Code Reviewer and the Developer disagree, which is for a person to settle"
            )
            return outcome
    if not ws.commit(
        (
            f"chore({number}): answer the review, no change needed\n\n{implementation.summary}"
            if answered
            else f"feat({number}): {card.title}\n\n{implementation.summary}\n\nCloses #{number}"
        ),
        allow_empty=answered,
    ):
        outcome.blocked_reason = "the implementation produced no change"
        return outcome
    # A branch left behind by an earlier attempt is the crew's own dead history:
    # the pull request is closed, and `open()` already declared this worktree
    # authoritative by resetting to origin/HEAD. Overwrite it. An *open* pull
    # request is a different thing — force-pushing under a review in progress
    # would destroy the context the Reviewer is judging — so that blocks
    # instead, with the reason named.
    open_pull = issues.pull_for_branch(repo, branch, known=card.open_pull_on(branch))
    if open_pull is not None and not rework:
        outcome.blocked_reason = (
            f"PR #{open_pull['number']} is still open on `{branch}`. "
            "Close it, or let that pull request finish; re-delivering would "
            "overwrite the diff it is reviewing."
        )
        return outcome
    try:
        # A repair adds a commit on top of the branch it resumed, so it is a
        # fast-forward and must not be forced: the history the reviewer read is
        # the history it keeps, with the answer appended to it. A rebuild is the
        # exception: it starts from main, so it replaces the branch, with a
        # lease so only the crew's own refused attempt is overwritten.
        ws.push(force=not rework or rebuilt_over is not None)
    except Exception as exc:  # noqa: BLE001
        # Landing failed, not the work. Returning the outcome keeps what it
        # already knows — how many repairs it took, the diff it produced — where
        # letting this propagate discards all of it and the card blocks saying
        # "Attempts: 0". That is #10's defect in a path #10 did not cover.
        outcome.blocked_reason = f"could not push `{branch}`: {exc}"[:400]
        return outcome

    if open_pull is not None:
        # The push updated it. Opening a second pull request from the same
        # branch is not possible and would not be wanted: the review thread,
        # the findings and the card's history all hang off this one.
        outcome.pr = open_pull["number"]
        how = (
            "**Answered without a change:** the code as it stands already satisfies "
            "the findings. Lint and the full test suite pass on this head.\n\n"
            + "\n".join(f"- **{s.finding}**: {s.evidence}" for s in satisfied)
            if answered
            else "Rebuilt from main: while this waited, other work merged into the same "
            "lines of "
            + ", ".join(f"`{p}`" for p in rebuilt_over)
            + ", so the story was written again on current main rather than merged. "
            "The branch was replaced; the findings above are what it answers."
            if rebuilt_over
            else "Pushed on top of the commit that was refused."
        )
        issues.comment(
            repo,
            open_pull["number"],
            signed(
                f"{REWORK_MARKER}\n{ANSWERED_MARKER}\n"
                f"**Re-worked.** {implementation.summary}\n\n{how}"
                if answered
                else f"{REWORK_MARKER}\n**Re-worked.** {implementation.summary}\n\n"
                f"{how} Lint and the full test suite pass.",
                "Developer",
            ),
        )
    else:
        pr = issues.create_pull(
            repo,
            title=card.title,
            head=branch,
            base=default_branch,
            body=_pr_body(card, implementation, outcome),
        )
        outcome.pr = pr["number"]
    sink.emit(
        CrewEvent(
            kind=EventKind.AGENT_FINISHED,
            role="Developer",
            card=number,
            summary=f"PR #{outcome.pr}",
        )
    )
    return outcome


def _touched_count(implementation: Implementation) -> int:
    return (
        len(implementation.new_files)
        + len(implementation.all_edits)
        + len(implementation.text_edits)
    )


def _pr_body(card: Card, implementation: Implementation, outcome: DeliveryOutcome) -> str:
    lines = [
        implementation.summary,
        "",
        "## Verification",
        "",
        "Lint and the full test suite pass in an isolated worktree.",
        "",
        f"- attempts: {outcome.attempts + 1}",
        f"- escalated: {'yes' if outcome.escalated else 'no'}",
        "",
        "## Changes",
        "",
    ]
    for new in implementation.new_files:
        lines.append(f"- `{new.path}` (new)")
    for edit in implementation.all_edits:
        lines.append(f"- `{edit.path}` — {edit.operation} `{edit.target}`")
    for text_edit in implementation.text_edits:
        lines.append(f"- `{text_edit.path}` — edited")
    lines += ["", f"Closes #{card.number}"]
    # Signed last, after the Verification section the Code Reviewer is shown.
    return signed("\n".join(lines), "Developer")


def _work_one_card(
    card: Card,
    *,
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    ws: Workspace,
    policy: EscalationPolicy,
    ledger: EscalationLedger,
    result: DeliveryResult,
    counts: dict[str, int],
    sprint: str,
    repo: str,
    default_branch: str,
    rework: bool = False,
) -> None:
    """Deliver one card that is already In Progress, and move it on.

    Shared by the two ways a card gets here: freshly claimed from the sprint
    backlog, and sent back by the Code Reviewer. They differ only in `rework`,
    which says whether there is an open pull request to update rather than to
    refuse to overwrite.
    """

    # The card names its own repository. Using a global default would
    # implement a card belonging to one repo inside another, silently.
    card_repo = card.repo or repo
    # Bound, not inlined: for_repo returns a *new* workspace when the
    # repository differs, so closing `ws` would leak the worktree that was
    # opened and close one that never was.
    card_ws = ws.for_repo(card_repo)
    outcome = None
    try:
        outcome = deliver_story(
            card,
            board=board,
            issues=issues,
            sink=sink,
            ws=card_ws,
            policy=policy,
            ledger=ledger,
            sprint=sprint,
            repo=card_repo,
            default_branch=default_branch,
            rework=rework,
        )
    except Exception as exc:  # noqa: BLE001
        reraise_if_down(exc)
        # A last resort. Anything deliver_story can attribute it returns on
        # its own outcome; reaching here means it could not, so the card
        # blocks knowing nothing but the error.
        outcome = DeliveryOutcome(
            card=card.number or 0, blocked_reason=f"{type(exc).__name__}: {exc}"[:400]
        )
    finally:
        # Nothing is committed or pushed until verification passes, so for a
        # card that failed this worktree is the only copy of what was
        # written. Read it before removing it, or the card blocks with no
        # evidence of why and the diagnosis has to be guessed.
        if outcome is not None and not outcome.ok:
            outcome.rejected_diff = card_ws.diff_if_open()
        card_ws.close()

    if outcome.ok:
        move_card(
            board,
            sink,
            item_id=card.item_id,
            to=REVIEWING,
            by="Developer",
            card=card.number,
            frm=IN_PROGRESS,
            summary=f"delivered — PR #{outcome.pr}",
        )
        counts[IN_PROGRESS] -= 1
        counts[REVIEWING] = counts.get(REVIEWING, 0) + 1
        artifacts.comment(
            issues,
            sink,
            repo=repo,
            number=card.number or 0,
            body=f"Implemented in #{outcome.pr} on `{outcome.branch}`. Lint and tests pass."
            + (" Escalated to finish." if outcome.escalated else ""),
            by="Developer",
        )
        result.delivered.append(outcome)
    else:
        move_card(
            board,
            sink,
            item_id=card.item_id,
            to=BLOCKED,
            by="Developer",
            card=card.number,
            frm=IN_PROGRESS,
            summary=(outcome.blocked_reason or "")[:80],
            kind=EventKind.CARD_BLOCKED,
        )
        counts[IN_PROGRESS] -= 1
        artifacts.label(
            issues, sink, repo=repo, number=card.number or 0, by="Developer", add=["blocked"]
        )
        artifacts.comment(
            issues,
            sink,
            repo=repo,
            number=card.number or 0,
            body=f"**Blocked.** {outcome.blocked_reason}\n\n"
            f"Attempts: {outcome.attempts}. "
            f"{'Escalated.' if outcome.escalated else 'Not escalated.'}",
            by="Developer",
        )
        sink.emit(
            CrewEvent(
                kind=EventKind.CARD_BLOCKED,
                role="Developer",
                card=card.number,
                summary=(outcome.blocked_reason or "")[:80],
            )
        )
        result.blocked.append(outcome)
        # A usage limit means come back later, not try the next card. The
        # caller stops on the flag; this one is finished either way.
        if outcome.blocked_reason and "usage limit" in outcome.blocked_reason.lower():
            result.rate_limited = True


def deliver(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    rules: ProcessRules,
    policy: EscalationPolicy,
    ledger: EscalationLedger,
    ws: Workspace,
    *,
    sprint: str,
    repo: str,
    default_branch: str = "main",
    limit: int | None = None,
    repos: set[str] | None = None,
) -> DeliveryResult:
    """Pull stories into progress up to the WIP limit, and deliver them."""
    result = DeliveryResult()
    cards = board.cards()

    # Land first, then branch. A story that branches from a main missing its
    # predecessors is a conflict scheduled for later.
    landed = merge_approved(board, issues, sink, cards=cards, default_repo=repo, repos=repos)
    result.conflicted = [card for card, _pr in landed.conflicted]
    result.rebuilding = list(landed.rebuilding)
    result.awaiting_approval = list(landed.awaiting_approval)
    result.unapprovable = list(landed.unapprovable)
    result.unmergeable = list(landed.failed)
    result.updating = list(landed.updating)
    result.landed = [card for card, _pr in landed.merged]
    if landed.merged or landed.conflicted or landed.rebuilding:
        cards = board.cards()

    # Reverts land on the same terms, and before new work branches for the
    # same reason: a story should start from the main the revert produced.
    result.reverts = land_reverts(
        board, issues, sink, cards=cards, repos=repos if repos is not None else {repo}
    )
    if result.reverts.merged or result.reverts.returned:
        cards = board.cards()

    # Heal before acting: an interrupted run leaves cards claimed by nobody.
    recovered = reconcile_orphans(board, issues, sink, cards, repo=repo)
    if recovered:
        cards = board.cards()
    result.recovered = recovered
    counts = board.counts(cards)

    if repos is not None:
        skipped = not_ours(cards, sprint, repos)
        if skipped:
            result.not_ours = [c.number or 0 for c in skipped]
            sink.note(
                EventKind.NOTE,
                f"{len(skipped)} cards are outside the crew's repositories: "
                + ", ".join(f"#{c.number} ({c.repo})" for c in skipped),
            )

    # Drain-first, and this is the most drained work there is: a story the
    # reviewer has already read and sent back. Before this existed those cards
    # were the only ones the loop could not finish, so they are claimed before
    # anything new — and before the WIP arithmetic below, because a card that
    # is already In Progress occupies no new slot.
    returned = needs_rework(issues, cards, repo=repo)
    for card in returned:
        if repos is not None and (card.repo or repo) not in repos:
            continue
        if card.status == REVIEWING:
            verdict = rules.may_move(frm=REVIEWING, to=IN_PROGRESS, counts=counts)
            if not verdict.allowed:
                sink.note(EventKind.NOTE, f"#{card.number} needs re-work: {verdict.reason}")
                continue
            move_card(
                board,
                sink,
                item_id=card.item_id,
                to=IN_PROGRESS,
                by="Developer",
                card=card.number,
                frm=REVIEWING,
                summary="changes requested — returned for re-work",
            )
            counts[REVIEWING] = max(counts.get(REVIEWING, 1) - 1, 0)
            counts[IN_PROGRESS] = counts.get(IN_PROGRESS, 0) + 1
        result.reworked.append(card.number or 0)
        _work_one_card(
            card,
            board=board,
            issues=issues,
            sink=sink,
            ws=ws,
            policy=policy,
            ledger=ledger,
            result=result,
            counts=counts,
            sprint=sprint,
            repo=repo,
            default_branch=default_branch,
            rework=True,
        )
        if result.rate_limited:
            return result
    if result.reworked:
        cards = board.cards()

    claimed = 0
    for card in sprint_stories(cards, sprint, repos=repos):
        if limit is not None and claimed >= limit:
            break

        blocker = held_by_a_sibling(cards, card)
        if blocker is not None:
            result.waiting_on_a_sibling.append((card.number or 0, blocker.number or 0))
            sink.note(
                EventKind.NOTE,
                f"#{card.number} waits for #{blocker.number} in the same epic",
            )
            continue

        verdict = rules.may_move(frm=SPRINT_BACKLOG, to=IN_PROGRESS, counts=counts)
        if not verdict.allowed:
            sink.note(EventKind.NOTE, verdict.reason)
            break

        move_card(
            board,
            sink,
            item_id=card.item_id,
            to=IN_PROGRESS,
            by="Developer",
            card=card.number,
            frm=SPRINT_BACKLOG,
            summary=card.title[:60],
        )
        counts[IN_PROGRESS] = counts.get(IN_PROGRESS, 0) + 1
        claimed += 1
        _work_one_card(
            card,
            board=board,
            issues=issues,
            sink=sink,
            ws=ws,
            policy=policy,
            ledger=ledger,
            result=result,
            counts=counts,
            sprint=sprint,
            repo=repo,
            default_branch=default_branch,
        )
        if result.rate_limited:
            break
    return result


def _latest_review(issues: IssueClient, repo: str, pull: int) -> str:
    try:
        reviews = issues.pull_reviews(repo, pull)
    except Exception:  # noqa: BLE001
        return ""
    return (reviews[-1].get("body") or "") if reviews else ""
