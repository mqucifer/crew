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

import contextlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crew_org import log, profiles
from crew_org.columns import BLOCKED, DONE, IN_PROGRESS, QAING, REVIEWING, SPRINT_BACKLOG
from crew_org.crews import asks as file_asks
from crew_org.crews import epic_rows
from crew_org.crews.delivery_crew import Implementation, implement_story
from crew_org.escalation import (
    Disposition,
    EscalationDecision,
    EscalationLedger,
    EscalationPolicy,
    EscalationRecord,
    FailureClass,
    LocalFailure,
    utcnow,
)
from crew_org.events import CrewEvent, EventKind, EventSink, attributed
from crew_org.flows import artifacts, story_problem
from crew_org.flows.acceptance import ALREADY_DONE_MARKER, EXISTING_PROOF_MARKER, qa_marker
from crew_org.flows.artifacts import signed
from crew_org.flows.attempts import failing_tests, first_error
from crew_org.flows.ci import CI_MARKER, latest_ci_verdict
from crew_org.flows.conclusion import story_rows
from crew_org.flows.history import ANSWERED_MARKER, latest_answer
from crew_org.flows.merge import REBUILD_MARKER, keeping_both, merge_approved, rebuild_files
from crew_org.flows.moves import move_card
from crew_org.flows.presentation_notes import story_notes
from crew_org.flows.revert import RevertLanding, land_reverts
from crew_org.git_ops import MergeConflict, Workspace, branch_name
from crew_org.llm import reraise_if_down
from crew_org.process import ProcessRules
from crew_org.project import ProjectRecordError, brief, read_record
from crew_org.rules import Kind, Rule
from crew_org.tools import bounds, claude_code, regression, workspace
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, LinkedPull, ProjectClient
from crew_org.tools.repo_context import SELECTION_TEXT_CHARS, focused_context

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
    # Which rule blocked it, by name (crew#449): the reason above is for a person.
    blocked_rule: str | None = None
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
    # Where an unexpected error happened: the crew's last frames (#390).
    where: str | None = None
    # A story whose failures are the story's (#189): the evidence for its epic.
    returned: str | None = None
    # Whether the Business Analyst has ruled on merged tests this story broke and
    # didn't declare: once per delivery (crew#584, step A5).
    contract_ruled: bool = False
    # A first attempt that answered the story is already done (#221): each
    # criterion with the code that meets it and the test that proves it.
    already_done: list = field(default_factory=list)
    # Criteria the Developer named existing tests for (#217), for QA.
    proven_by_existing: list = field(default_factory=list)
    # Merged tests the story's declared contract change let go (#316), for the
    # Code Reviewer: the story said so, and the pull request says which.
    contract_changed: list[str] = field(default_factory=list)

    def seen(self, failure_class: str) -> int:
        return self.attempts_by_class.get(failure_class, 0)

    def count(self, failure_class: str) -> None:
        self.attempts_by_class[failure_class] = self.seen(failure_class) + 1
        self.attempts += 1

    @property
    def ok(self) -> bool:
        return self.pr is not None or self.diff is not None or bool(self.already_done)

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
    # (story, epic) sent back to refinement as a story problem (#189).
    returned: list[tuple[int, int]] = field(default_factory=list)
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
    # (card, what is still running): checks not finished where nothing on
    # GitHub holds it to them (#335). It merges on a later pass.
    checking: list[tuple[int, str]] = field(default_factory=list)
    # (card, PR) in the merge queue; GitHub merges it in its turn (#302).
    queued: list[tuple[int, int]] = field(default_factory=list)
    # Stories a passing service failure interrupted, left to be retried (#390).
    interrupted: list[int] = field(default_factory=list)
    in_queue: list[tuple[str, int, str]] = field(default_factory=list)
    # (story, the earlier sibling it is waiting for). Reported rather than
    # silently skipped: a card that could be claimed and was not needs a reason.
    waiting_on_a_sibling: list[tuple[int, int]] = field(default_factory=list)
    # (story, PR) a rebuild waits for: the open PR still changing the files it
    # conflicted in (#436).
    waiting_on_files: list[tuple[int, int]] = field(default_factory=list)
    # Reverts landed this pass, and those that could not land yet (#83).
    reverts: RevertLanding = field(default_factory=RevertLanding)
    rate_limited: bool = False


def _local_rule(decision: EscalationDecision, retried: Rule, otherwise: Rule | None = None) -> Rule:
    """The rule a guard's or an edit's decision names.

    They reach the policy under another class (an edit as SCHEMA, a guard as
    VERIFY or REGRESSION), so the policy's rule names that class. A local retry
    was this guard's or edit's own; anything else keeps the policy's rule.
    """
    if decision.disposition is Disposition.RETRY_LOCAL:
        return retried
    return otherwise or decision.rule


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
        refused = (
            any(
                r.get("state") == "CHANGES_REQUESTED" and r.get("commit_id") == head
                for r in issues.pull_reviews(repo, pull["number"])
            )
            or any(
                # Approved, but conflicting with main at merge: returned for a
                # rebuild. Or a CI check failed on this head (#325).
                marker in (c.get("body") or "")
                for c in issues.comments(repo, pull["number"])
                for marker in (REBUILD_MARKER.format(head=head), CI_MARKER.format(head=head))
            )
            or (
                # QA returned this head: its verdict is on the story, not the pull
                # request. Unseen, the card looked stranded and went straight back
                # to review with its approval standing, and the Developer never
                # fixed the unproven criterion (sprint-metrics#145).
                card is not None
                and card.number is not None
                and any(
                    qa_marker(head) in (c.get("body") or "")
                    and "## QA — not accepted" in (c.get("body") or "")
                    for c in issues.comments(repo, card.number)
                )
            )
        )
    except Exception:  # noqa: BLE001
        return None
    return pull if refused else None


# A crew story's branch, as `branch_name` writes it: `<type>/<number>-<summary>`.
_STORY_BRANCH = re.compile(r"^(?:feat|fix|chore|docs|test|refactor|exp)/(\d+)-")


def rebuild_waits_for(
    issues: IssueClient, cards: list[Card], card: Card, *, repo: str
) -> tuple[int, list[str]] | None:
    """The open story PR still changing the files this story's rebuild conflicted in (#436).

    A story returned for a rebuild because its branch conflicted in some files waits
    to start it while another story's pull request changes those files: rebuilt now,
    it would conflict again as soon as that one lands, as sprint-metrics#384 did
    twice. Only a pull request that is moving holds it. One waiting on its own
    rebuild, or whose story is blocked, holds nothing, so two rebuilds never wait on
    each other.

    None when it may go ahead, and on any error: waiting saves a rebuild, it isn't a gate.
    """
    try:
        branch = branch_name(card.number or 0, card.title)
        pull = issues.pull_for_branch(repo, branch, known=card.open_pull_on(branch))
        if pull is None:
            return None
        head = (pull.get("head") or {}).get("sha", "")
        files: set[str] = set()
        for comment in issues.comments(repo, pull["number"]):
            body = comment.get("body") or ""
            if REBUILD_MARKER.format(head=head) in body:
                files = set(rebuild_files(body))
        if not files:
            return None
        blocked = {c.number for c in cards if (c.repo or repo) == repo and c.status == BLOCKED}
        for other in issues.open_pulls(repo):
            if other["number"] == pull["number"]:
                continue
            story = _STORY_BRANCH.match((other.get("head") or {}).get("ref") or "")
            if story is None or int(story.group(1)) in blocked:
                continue
            its_head = (other.get("head") or {}).get("sha", "")
            if any(
                REBUILD_MARKER.format(head=its_head) in (c.get("body") or "")
                for c in issues.comments(repo, other["number"])
            ):
                continue
            overlap = files & set(issues.pull_files(repo, other["number"]))
            if overlap:
                return other["number"], sorted(overlap)
    except Exception:  # noqa: BLE001
        return None
    return None


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


# On a card, a delivery a service interrupted (#390). Consecutive ones count
# towards blocking it; any other comment from the crew since resets the count.
INTERRUPTED_MARKER = "<!-- crew:interrupted -->"
INTERRUPTIONS_BEFORE_BLOCKING = 3


def _interrupted_streak(issues: IssueClient, repo: str, number: int) -> int:
    """How many interruptions in a row end the card's comments."""
    try:
        comments = issues.comments(repo, number)
    except Exception:  # noqa: BLE001
        return 0
    streak = 0
    for c in reversed(comments):
        body = c.get("body") or ""
        if INTERRUPTED_MARKER in body:
            streak += 1
        elif "<!-- crew:" in body:
            break
    return streak


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
            # What CI said, when only CI could say it (#325).
            ci = latest_ci_verdict(issues, repo, pull["number"])
            if ci:
                parts.append(ci)
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
            "- Change what the verdicts above identified: a definition they say is wrong "
            "with `replace`; one they say was removed with `add`, because it isn't in the "
            "files any more. Leave everything the verdicts did not flag alone.\n"
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


# What a declared contract change names: `path.py::test`, `path.py`, or a bare test name.
_DECLARED = re.compile(r"[\w/.-]+\.py(?:::[\w.]+)?|\btest_\w+")


# How many files one delivery may have added by asking (#231). A file it hasn't
# seen is shown whenever it asks; this bounds what asking adds to the prompt.
# Counting asks instead refused sprint-metrics#529's third, for the one file its
# change needed, three times over (2026-10-09).
ASKED_FILES_LIMIT = 8


def _deliver_in_steps(
    story: str,
    *,
    worktree: Path,
    header: str,
    about: str,
    why: str,
    sink: EventSink,
    number: int,
    repo: str,
    sprint: str,
    attempt: int,
) -> Implementation:
    """A plan, then one small answer per file, merged into one ordinary answer (#276).

    Each piece is shown the story, the plan, the file it writes in full, and
    what the steps before it wrote. The merged answer goes through the same
    guards, checks, review and QA as any other. A step's error fails the whole
    attempt, as a one-shot answer's would.
    """
    from crew_org.crews import stepped  # noqa: PLC0415

    context, _ = focused_context(worktree, about=about)
    plan = attributed(stepped.plan_story, card=number, repo=repo, sprint=sprint, attempt=attempt)(
        story, context=header + context, why=why
    )
    problems = stepped.check_plan(plan, worktree)
    if problems:
        raise ValueError("the plan doesn't fit the repository: " + "; ".join(problems))
    sink.note(
        EventKind.NOTE,
        f"#{number} planned {len(plan.files)} files: "
        + ", ".join(f.path for f in plan.files)[:160],
        card=number,
        plan=[f.path for f in plan.files],
        contracts=plan.contracts,
    )
    answers: list = []
    for index, step in enumerate(plan.files):
        extra = [step.path] if step.kind != "new" else []
        step_context, focus = focused_context(
            worktree, about=f"{story}\n\n{step.path}", extra=extra
        )
        earlier = "\n\n".join(stepped.describe(a) for a in answers)
        answer = attributed(
            stepped.implement_file, card=number, repo=repo, sprint=sprint, attempt=attempt
        )(
            story,
            plan=plan,
            step=index,
            context=header + step_context,
            earlier=earlier,
            written=stepped.written_tests(answers),
        )
        sink.note(
            EventKind.NOTE,
            f"#{number} step {index + 1}/{len(plan.files)}: {step.path}",
            card=number,
            context_chars=focus.chars,
        )
        answers.append(answer)
    return stepped.merge(plan, answers)


def coverage_for(ws: Any, repo: str, sink: EventSink, number: int) -> Any:
    """The project's coverage map at its default branch, or None, said why (crew#583, C3).

    Built once per base commit, in the sandbox, and read from `var/coverage/` after.
    Without it the Developer is shown the same, less the tests that run its code.
    """
    from crew_org.tools.coverage_map import build  # noqa: PLC0415

    try:
        return build(ws.current(), repo)
    except Exception as exc:  # noqa: BLE001 - context, never a reason to stop the work
        reraise_if_down(exc)
        sink.note(EventKind.NOTE, f"#{number} no coverage map: {exc}"[:160], card=number)
        return None


def amended_line(ruling: Any) -> str:
    """The story's Existing tests line after the Business Analyst amended it."""
    from crew_org.flows.board_flow import existing_tests_line  # noqa: PLC0415

    return existing_tests_line(f"contract change: {', '.join(ruling.tests)}: {ruling.asserts}")


def rule_on_contract(
    issues: IssueClient,
    sink: EventSink,
    *,
    card: Card,
    repo: str,
    story: str,
    rows: str,
    failures: dict[str, str],
) -> Any:
    """The Business Analyst's ruling on merged tests a story broke and didn't declare (A5).

    Amended, the story's issue carries the new Existing tests line, the comment
    says so, and the tests become rows of the epic's record declared by the story.
    Returned, the return writes them (`story_problem.record_pinned`). None when the
    ruling couldn't be had: the story then goes back, as before.
    """
    from crew_org.crews.contract_crew import rule_on_contract as ask  # noqa: PLC0415
    from crew_org.flows import record  # noqa: PLC0415
    from crew_org.flows.board_flow import EXISTING_TESTS  # noqa: PLC0415

    number = card.number or 0
    try:
        ruling = attributed(ask, card=number, repo=repo)(story=story, rows=rows, failures=failures)
    except Exception as exc:  # noqa: BLE001
        reraise_if_down(exc)
        sink.note(EventKind.NOTE, f"#{number} no contract ruling: {exc}"[:160], card=number)
        return None
    sink.emit(
        CrewEvent(
            kind=EventKind.NOTE,
            role="Business Analyst",
            card=number,
            summary=f"#{number} contract {ruling.decision}: {ruling.why}"[:120],
            detail={"repo": repo, "decision": ruling.decision, "tests": ruling.tests},
        )
    )
    if ruling.decision != "amend":
        return ruling
    line = amended_line(ruling)
    try:
        body = issues.get(repo, number).get("body") or ""
        lines = [ln for ln in body.splitlines() if not ln.startswith(EXISTING_TESTS)]
        issues.edit_issue(repo, number, body="\n".join(lines).rstrip() + f"\n\n{line}\n")
        artifacts.comment(
            issues,
            sink,
            repo=repo,
            number=number,
            body=f"**The story's contract, amended in delivery.** {line}\n\n{ruling.why}",
            by="Business Analyst",
        )
        if card.parent is not None and record.has_record(
            issues.get(repo, card.parent).get("body") or ""
        ):

            def change(current: record.Record) -> record.Record:
                for test in ruling.tests:
                    current = current.declare(
                        test,
                        ruling.asserts,
                        f"{issues.owner}/{repo}#{number}, amended in delivery",
                    )
                return current

            record.edit(
                issues,
                sink,
                repo=repo,
                epic=card.parent,
                by="Business Analyst",
                change=change,
                card=number,
                kind="tests",
                why="A story's contract, amended in delivery by the Business Analyst (crew#584).",
            )
    except Exception as exc:  # noqa: BLE001
        reraise_if_down(exc)
        sink.note(EventKind.NOTE, f"#{number} amendment not written: {exc}"[:160], card=number)
    return ruling


def declared_contract(body: str) -> set[str]:
    """The merged tests a story declares it changes, from its **Existing tests** line (#316).

    Empty unless the line declares a contract change: an opt-in story promises
    the merged tests keep passing, so nothing of theirs may go.
    """
    from crew_org.flows.board_flow import (  # noqa: PLC0415
        EXISTING_TESTS,
        EXISTING_TESTS_BEFORE_311,
    )

    line = next(
        (
            ln
            for ln in body.splitlines()
            if ln.startswith((EXISTING_TESTS, EXISTING_TESTS_BEFORE_311))
        ),
        "",
    )
    if "contract change" not in line and "changes what they assert" not in line:
        return set()
    return set(_DECLARED.findall(line))


# The pull request's sections naming merged tests it lets go, read back on a rework.
RETIRED_HEADING = "## Tests retired"
DECLARED_HEADING = "## Tests the story declares it changes"
_LISTED_TEST = re.compile(r"^- `([^`\s]+\.py::[\w.]+)`", re.MULTILINE)


def carried_retirements(issues: IssueClient, repo: str, pull: int) -> set[str]:
    """The merged tests a story's open pull request already let go, by `path::test` (#316).

    sprint-metrics#200's pull request retired five tests. Its review asked for
    four back and let the fifth go; the rework restored four and, not listing
    the fifth again, was refused for it by the regression guard three times:
    the guard read only the rework's own retirements. The pull request's list
    is the declaration the review read, so a rework keeps it.
    """
    try:
        body = issues.pull(repo, pull).get("body") or ""
    except Exception:  # noqa: BLE001
        return set()
    found: set[str] = set()
    for heading in (RETIRED_HEADING, DECLARED_HEADING):
        start = body.find(f"{heading}\n")
        if start == -1:
            continue
        section = body[start + len(heading) :].split("\n## ", 1)[0]
        found |= set(_LISTED_TEST.findall(section))
    return found


def held_by_what_it_builds_on(cards: list[Card], story: Card, body: str) -> Card | None:
    """A story in another epic this one builds on, not landed yet (#295).

    The same wait `held_by_a_sibling` gives an epic's own stories, across
    epics: the split names them in the story (`**Builds on** — #189, #190`),
    and a story built before them writes their foundation a second time.
    """
    from crew_org.flows.board_flow import builds_on  # noqa: PLC0415

    wanted = builds_on(body)
    blockers = [
        c
        for c in cards
        if c.repo == story.repo and c.number in wanted and c.state != "CLOSED" and c.status != DONE
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
    # The merged tests this story may let go: what it declares, and on a rework
    # what its pull request already retired, which the review has read (#316).
    declared = declared_contract(story_text)
    if live is not None:
        declared |= carried_retirements(issues, repo, live["number"])
    # Which merged tests run which code, on the base this story builds on (C3).
    coverage = coverage_for(ws, repo, sink, number)
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
                outcome.blocked_rule = Rule.CONFLICT_WITH_MAIN
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
        outcome.blocked_rule = Rule.RECORD_UNREADABLE
        outcome.blocked_reason = f"the project's record can't be read: {exc}"
        return outcome
    # Each file's profile comes from this project's parts, until the card is done (#404).
    profiles.set_project(record)
    # The notes for this story's epic, if it has them: the Architect's (#155)
    # and the UX Designer's (#377).
    note = story_notes(issues, card, repo)
    # And only the rows of the epic's conclusion this story names (crew#440).
    decided = story_rows(issues, card, repo)
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
    attempt = 0
    # Files the Developer asked to see in full (#231).
    asked: list[str] = []
    # A first attempt with no usable answer is followed by one in steps (#276).
    in_steps = False
    stepped = False
    # Offered once per story (#221): an already-done answer QA refused isn't
    # offered again, or the story would go round between the two.
    may_be_done = not rework and not _answered_done(issues, repo, number)

    # What the registries hold for the base images the Dockerfiles use: the
    # sandbox can't look, so a pinned digest was invented without it (#364).
    # Only for work about the image, and read once a tick (`base_images`).
    from crew_org.tools import base_images  # noqa: PLC0415

    images = ""

    while True:
        attempt += 1
        # Recomputed every pass: a repair must see the files it just wrote, or
        # it is fixing code it cannot read. Focused on what the work names
        # when the repository is large (#231): the story, the gates' verdicts,
        # the last failure, and what the Developer asked for. Under a ceiling,
        # what it wrote and the failing tests come first, and what only a test
        # report names comes last (crew#591): sprint-metrics#537's repair was
        # shown every file its report's tracebacks passed through.
        failing = list(dict.fromkeys(t["id"].split("::")[0] for t in failing_tests(feedback)))
        report = feedback if failing else ""
        about = "\n\n".join([story_text, prior, feedback])
        context, focus = focused_context(
            worktree,
            about="\n\n".join([story_text, prior, "" if failing else feedback]),
            extra=asked,
            written=bounds.touched(implementation) if implementation is not None else (),
            failing=failing,
            report=report,
            coverage=coverage,
        )
        sink.emit(
            CrewEvent(
                kind=EventKind.FILES_SHOWN,
                role="Developer",
                card=number,
                summary=f"#{number} context: {focus.chars:,} chars"
                + (f", {len(focus.shown)} files in full" if focus.focused else ", whole repository")
                + (f", {len(focus.omitted)} left out" if focus.omitted else ""),
                detail={
                    "context_chars": focus.chars,
                    "focused": focus.focused,
                    "shown": focus.shown,
                    "asked": focus.asked,
                    "omitted": focus.omitted,
                    # What the selection read to choose them (discussion 553). Local
                    # only: it's the story, and telemetry never takes it (ADR 0010).
                    "selection_text": about[:SELECTION_TEXT_CHARS],
                },
            )
        )
        header = f"# The design note for this story's epic\n\n{note}\n\n" if note else ""
        header = (
            epic_rows.block(
                decided,
                "These are decided for the epic. Build to them: don't contradict one, and "
                "don't settle in code what the design note leaves open.",
                level=1,
            )
            + header
        )
        if record is not None:
            header = f"{brief(record)}\n\n{header}"
        if not images and (
            base_images.concerns("\n\n".join([story_text, prior, feedback]))
            or any(base_images.is_dockerfile(p) for p in focus.asked)
        ):
            try:
                images = base_images.section(worktree)
            except Exception:  # noqa: BLE001 - a context aid, never a blocker
                images = ""
        header += images
        context = header + context
        # Set once an answer arrives and validates: what fails after that is the
        # answer's content, which a repair handles, not the answer's absence.
        answered = False
        try:
            if in_steps:
                in_steps = False
                stepped = True
                implementation = _deliver_in_steps(
                    story_text,
                    worktree=worktree,
                    header=header,
                    about="\n\n".join([story_text, prior, feedback]),
                    why=feedback,
                    sink=sink,
                    number=number,
                    repo=repo,
                    sprint=sprint,
                    attempt=attempt,
                )
            else:
                implementation = attributed(
                    implement_story, card=number, repo=repo, sprint=sprint, attempt=attempt
                )(
                    story_text,
                    context=context,
                    feedback=feedback,
                    prior=prior,
                    returned=rework,
                    may_be_done=may_be_done,
                )
            answered = True
            _keep_proposal(sink, repo, number, implementation)
            done_now = getattr(implementation, "already_done", None) or []
            named = [met.test for met in done_now if met.test]
            named += [
                test
                for proof in getattr(implementation, "proven_by_existing", None) or []
                for test in proof.tests
            ]
            missing = [test for test in named if not names_a_test(worktree, test)]
            # A workflow step as evidence, checked as a test is (#397).
            unproven = ci_evidence_problems(
                worktree,
                issues,
                repo,
                default_branch,
                [met.ci_step for met in done_now if met.ci_step],
            )
            if unproven:
                raise ValueError(
                    "this CI evidence doesn't hold: "
                    + "; ".join(unproven)
                    + ". Name a workflow step that exists and whose workflow passed on "
                    f"`{default_branch}`, or change the code."
                )
            if missing:
                # Refused, naming each (#221, #217): evidence that doesn't
                # exist is no evidence.
                raise ValueError(
                    "these named tests don't exist in the repository: "
                    + ", ".join(dict.fromkeys(missing))
                    + ". Name existing tests, or write the test in `criteria_tests`."
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
                        "rule": decision.rule,
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
                # One answer was too much to produce (#312): the next attempt plans
                # the work and answers a file at a time (#276). Once per delivery,
                # and only after a first attempt, whose answer is the whole story.
                if attempt == 1 and not answered and not rework and not stepped:
                    in_steps = True
                    sink.note(
                        EventKind.NOTE,
                        f"#{number} no usable answer: the next attempt goes in steps",
                        card=number,
                    )
                continue
            outcome.blocked_rule = decision.rule
            outcome.blocked_reason = decision.reason
            return outcome

        # It asked to see files first (#231). Not a failure, and not applied:
        # it's asked again with them shown, up to ASKED_FILES_LIMIT files a delivery.
        if implementation.asks:
            wanted = file_asks.paths(implementation.need_files)
            fresh = [f for f in dict.fromkeys(wanted) if f not in asked and f not in focus.shown]
            exists = [f for f in fresh if (worktree / f).is_file()]
            sink.emit(
                CrewEvent(
                    kind=EventKind.FILES_ASKED,
                    role="Developer",
                    card=number,
                    summary=f"#{number} asked to see {', '.join(wanted)[:200]}",
                    # Each file's reason, kept local (discussion 553).
                    detail={
                        "need_files": wanted,
                        "why": file_asks.reasons(implementation.need_files),
                    },
                )
            )
            added = exists[: max(ASKED_FILES_LIMIT - len(asked), 0)]
            if added:
                asked += added
                continue
            missing = [f for f in fresh if f not in exists]
            # Said as it is: "you've been shown what you asked for", said of a file
            # past the limit, had it ask again for the same file.
            feedback = (
                (f"These aren't files in the repository: {', '.join(missing)}. " if missing else "")
                + (
                    f"Not shown: {', '.join(exists)}. A delivery may have "
                    f"{ASKED_FILES_LIMIT} files added by asking, and this one has. "
                    if exists
                    else ""
                )
                + ("Everything else you named is already shown above. " if not fresh else "")
                + "Do the work now with the files you can see, and leave `need_files` empty."
            )
            continue

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
                    detail={
                        "rule": _local_rule(decision, Rule.GUARD_LOCAL_REPAIR),
                        "failure_class": "OVERWRITE",
                        "paths": overwrites,
                    },
                )
            )
            if decision.disposition is Disposition.RETRY_LOCAL:
                feedback = (
                    f"These already exist: {', '.join(overwrites)}. Change them with "
                    "`edits`, addressed by name, rather than rewriting them as new "
                    "files. Only a file that does not exist yet belongs in new_files." + NOT_APPLIED
                )
                continue
            outcome.blocked_rule = Rule.GUARD_OVERWRITE
            outcome.blocked_reason = f"kept rewriting existing files: {', '.join(overwrites)}"
            return outcome

        # The record's bounds, before anything is written: never-touch paths, the
        # record itself, and CI that stops enforcing a design check. Never
        # escalated, like a contract break: a stronger model would be spent
        # getting past a rule the Sponsor set.
        # A workflow change the app can't push (#279): GitHub refuses a push
        # that touches .github/workflows without the `workflows` permission,
        # and it would fail at the very end looking like a crew bug. Say so
        # now. Blocked, not retried: no retry grants a permission.
        missing = workflows_not_permitted(implementation)
        if missing:
            outcome.blocked_rule = Rule.GUARD_WORKFLOW_PERMISSION
            outcome.blocked_reason = missing
            return outcome

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
                    detail={
                        "rule": _local_rule(decision, Rule.GUARD_LOCAL_REPAIR),
                        "failure_class": "BOUNDS",
                        "reasons": outside,
                    },
                )
            )
            if decision.disposition is Disposition.RETRY_LOCAL:
                feedback = (
                    "This change goes outside what the project allows:\n\n"
                    + "\n".join(f"- {reason}" for reason in outside)
                    + NOT_APPLIED
                )
                continue
            outcome.failure_detail = "\n".join(outside)
            outcome.blocked_rule = Rule.GUARD_PROTECTED
            outcome.blocked_reason = f"kept changing what the project protects: {outside[0]}"
            return outcome

        # Checked before a single byte is written. A contract break is only
        # visible as a wall of failing tests once it has been applied, and by
        # then the model is repairing a symptom several steps from the cause —
        # story #9 changed a return type to None and spent every attempt on the
        # TypeError it produced three functions away.
        # Judged against what's merged, not this story's draft: an earlier
        # attempt's changes are the story's own, and its damage still counts.
        merged = regression.merged_base(worktree)
        broken = regression.broken_contracts(worktree, implementation.all_edits, merged)

        def skipped(why: str) -> None:
            sink.note(EventKind.NOTE, f"#{number} {why}"[:300], card=number)

        broken = regression.draft_breaks(worktree, implementation, merged, skipped) | broken
        # A definition moved to another module, and still reachable where
        # callers look for it, isn't removed (#202).
        broken = regression.without_moves(worktree, implementation, broken, merged)
        # A name the module passes along, or a constant, that another file
        # still imports from it (sprint-metrics#129).
        broken |= regression.lost_names(worktree, implementation, merged, skipped)
        # A merged test the story declares it retires, with why, may go: the
        # pull request lists it for the Code Reviewer (sprint-metrics#132).
        broken = regression.without_retired(broken, implementation)
        # And one the story itself declares it changes (#316): named there, it's
        # the story's job, not an accident.
        broken, outcome.contract_changed = regression.without_declared(broken, declared)
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
                        "rule": decision.rule,
                        "failure_class": FailureClass.REGRESSION,
                        "attempt": outcome.seen("REGRESSION"),
                        "reason": decision.reason,
                        "contracts": {k: list(v) for k, v in broken.items()},
                    },
                )
            )
            if decision.disposition is Disposition.RETRY_LOCAL:
                feedback = regression.describe_contracts(broken, merged) + NOT_APPLIED
                continue
            outcome.failure_detail = regression.describe_contracts(broken, merged)
            outcome.blocked_rule = decision.rule
            outcome.blocked_reason = decision.reason
            return outcome

        try:
            workspace.apply_implementation(worktree, implementation)
            # Tests in a part that isn't Python are written as files: each one the
            # answer names must be in them, by its title (#404).
            unwritten = [
                f"{c.path}::{c.test}"
                for c in getattr(implementation, "criteria_tests", None) or []
                if not profiles.profile_for(c.path).edit_by_name
                and not names_a_test(worktree, f"{c.path}::{c.test}")
            ]
            if unwritten:
                raise ValueError(
                    "these tests aren't in their files: "
                    + ", ".join(unwritten)
                    + ". Write each one in new_files or text_edits, titled as named here."
                )
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
                        "rule": _local_rule(
                            decision, Rule.EDIT_LOCAL_REPAIR, Rule.EDIT_NOT_APPLIED
                        ),
                        "failure_class": "EDIT",
                        "attempt": outcome.seen("EDIT"),
                        "error": str(exc)[:400],
                    },
                )
            )
            if decision.disposition is Disposition.RETRY_LOCAL:
                feedback = f"An edit could not be applied:\n\n{exc}"
                continue
            outcome.blocked_rule = Rule.EDIT_NOT_APPLIED
            outcome.blocked_reason = f"edits could not be applied: {exc}"
            return outcome

        check = workspace.check(worktree)
        if check.ok:
            break

        # Merged tests, not this story's, that it breaks and doesn't declare: the
        # story is changing behaviour other work pinned. On the first such failure
        # the Business Analyst rules for this story alone: amend its contract, or
        # return it (crew#584, step A5). More attempts won't fix a story (#189).
        pinned = story_problem.pinned_failures(
            check.failure_report, regression.merged_base(worktree), implementation
        )
        undeclared = {k: why for k, why in pinned.items() if not regression._declares(declared, k)}
        amended = ""
        ruling = None
        if undeclared and not outcome.contract_ruled:
            outcome.contract_ruled = True
            ruling = rule_on_contract(
                issues,
                sink,
                card=card,
                repo=repo,
                story=story_text,
                rows=decided,
                failures=undeclared,
            )
            if ruling is not None and ruling.decision == "amend":
                declared |= set(ruling.tests)
                story_text += f"\n\n{amended_line(ruling)}"
                amended = (
                    "The Business Analyst amended this story's contract: it now changes "
                    f"{', '.join(f'`{t}`' for t in ruling.tests)}. After your change they must "
                    f"assert: {ruling.asserts}. Update them in this story.\n\n"
                )
        if undeclared and not amended:
            failure = LocalFailure(
                card=number,
                role="Developer",
                failure_class=FailureClass.SCOPE,
                attempts=outcome.seen("VERIFY"),
                detail="breaks merged tests it doesn't declare: "
                + ", ".join(sorted(undeclared))[:300]
                + (f"; the Business Analyst: {ruling.why}" if ruling is not None else ""),
            )
            decision = policy.decide(failure, spent=ledger.spent(sprint))
            sink.emit(
                CrewEvent(
                    kind=EventKind.ESCALATION_DECIDED,
                    role="Developer",
                    card=number,
                    summary=f"SCOPE — {decision.disposition}",
                    detail={
                        "rule": decision.rule,
                        "failure_class": FailureClass.SCOPE,
                        # What happened to the work, which the class doesn't say.
                        "failure_kind": Kind.TESTS_FAILED,
                        "reason": decision.reason,
                        "pinned": sorted(pinned),
                        "first_error": first_error(check.failure_report),
                    },
                )
            )
            outcome.failure_detail = check.failure_report
            outcome.blocked_rule = decision.rule
            outcome.blocked_reason = decision.reason
            if decision.disposition is Disposition.RETURN_TO_REFINEMENT:
                outcome.returned = story_problem.evidence(
                    card, undeclared, outcome.seen("VERIFY") + 1
                ) + (f"\n\n**The Business Analyst:** {ruling.why}" if ruling is not None else "")
            return outcome

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
                    "rule": decision.rule,
                    "failure_class": FailureClass.VERIFY,
                    "attempt": outcome.seen("VERIFY"),
                    "reason": decision.reason,
                    "failing_commands": [r.command for r in check.results if not r.ok],
                    "output": check.failure_report[:600],
                    # From the whole report: the 600 above stop before pytest's
                    # summary, and the retro counts causes from this (#157).
                    "first_error": first_error(check.failure_report),
                    "failing_tests": failing_tests(check.failure_report),
                },
            )
        )

        if decision.disposition is Disposition.RETRY_LOCAL:
            feedback = f"{amended}Lint or tests failed:\n\n{check.failure_report}"
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
                outcome.blocked_rule = Rule.USAGE_LIMIT
                outcome.blocked_reason = result.detail
                return outcome
            check = workspace.check(worktree)
            if check.ok:
                ledger.resolve(number, sprint, "resolved — lint and tests pass")
                break
            ledger.resolve(number, sprint, "escalated but still failing")
            outcome.failure_detail = check.failure_report
            outcome.blocked_rule = Rule.ESCALATION_UNRESOLVED
            outcome.blocked_reason = f"escalation did not resolve it: {check.failure_report[:200]}"
            return outcome

        outcome.failure_detail = check.failure_report
        outcome.blocked_rule = decision.rule
        outcome.blocked_reason = decision.reason
        return outcome

    # Returned work may answer that nothing needs to change, with evidence per
    # finding (#161). The checks above ran on the branch as it stands, so the
    # claim is verified before it is sent. It is committed empty: the pull
    # request's head has to move, or the review it answers keeps applying.
    satisfied = getattr(implementation, "already_satisfied", None) or []
    done = [] if rework else getattr(implementation, "already_done", None) or []
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
            outcome.blocked_rule = Rule.GATES_DISAGREE
            outcome.blocked_reason = (
                f"PR #{live['number']} was answered without a change and sent back again: "
                "the Code Reviewer and the Developer disagree, which is for a person to settle"
            )
            return outcome
    if not ws.commit(
        (
            f"chore({number}): answer the review, no change needed\n\n{implementation.summary}"
            if answered
            else f"chore({number}): already done, no change needed\n\n{implementation.summary}"
            if done
            else f"feat({number}): {card.title}\n\n{implementation.summary}\n\nCloses #{number}"
        ),
        allow_empty=answered or bool(done),
    ):
        outcome.blocked_rule = Rule.GUARD_NO_CHANGE
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
        outcome.blocked_rule = Rule.OPEN_PULL_REQUEST
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
        from crew_org.tools.remote_errors import transient_remote  # noqa: PLC0415

        # GitHub's side failing isn't the story's (crew#444): it goes to the
        # interruption path, tried again next pass and blocked only after a streak.
        if transient_remote(exc):
            raise
        # Landing failed, not the work. Returning the outcome keeps what it
        # already knows — how many repairs it took, the diff it produced — where
        # letting this propagate discards all of it and the card blocks saying
        # "Attempts: 0". That is #10's defect in a path #10 did not cover.
        outcome.blocked_rule = Rule.PUSH_FAILED
        outcome.blocked_reason = f"could not push `{branch}`: {exc}"[:400]
        return outcome

    if done:
        # No pull request: there's no diff to review (#221). The branch is pushed
        # so QA can check it out and run the named tests; QA decides.
        outcome.already_done = list(done)
        sink.emit(
            CrewEvent(
                kind=EventKind.AGENT_FINISHED,
                role="Developer",
                card=number,
                summary=f"already done: {len(done)} criteria met by existing code",
            )
        )
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
                f"{how} Lint and the full test suite pass."
                + "\n".join(_let_go(implementation, outcome)),
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
        outcome.proven_by_existing = list(getattr(implementation, "proven_by_existing", None) or [])
    sink.emit(
        CrewEvent(
            kind=EventKind.AGENT_FINISHED,
            role="Developer",
            card=number,
            summary=f"PR #{outcome.pr}",
        )
    )
    return outcome


def workflows_not_permitted(implementation, permissions: dict[str, str] | None = None) -> str:
    """Why a workflow change can't be pushed, or "" if it can or we can't tell (#279)."""
    from crew_org.auth import app_permissions  # noqa: PLC0415
    from crew_org.tools import ci_guard  # noqa: PLC0415

    held = app_permissions() if permissions is None else permissions
    changed = [p for p in bounds.touched(implementation) if ci_guard.is_workflow(p)]
    if not changed or not held or held.get("workflows") == "write":
        return ""
    return (
        f"this change edits {', '.join(changed)}, and the crew's GitHub App has no "
        "`workflows: write` permission, so GitHub would refuse the push. The Sponsor grants "
        "it on the app and accepts it on the installation (crew#279)"
    )


def _answered_done(issues: IssueClient, repo: str, number: int) -> bool:
    """Has this story been answered as already done before? Unknown counts as yes."""
    try:
        comments = issues.comments(repo, number)
    except Exception:  # noqa: BLE001
        return True
    return any(ALREADY_DONE_MARKER in (c.get("body") or "") for c in comments)


def existing_proof_block(proven: list) -> str:
    """The criteria existing tests prove, on the story, for QA (#217)."""
    if not proven:
        return ""
    return f"\n\n{EXISTING_PROOF_MARKER}\n**Proven by existing tests:**\n" + "\n".join(
        f"- {p.criterion}: " + ", ".join(f"`{t}`" for t in p.tests) for p in proven
    )


def names_a_test(worktree, test: str) -> bool:
    """Does `path::name` (or `path::Class::name`) name a test that exists in the worktree?"""
    from pathlib import Path  # noqa: PLC0415

    from crew_org.profiles import profile_for  # noqa: PLC0415

    profile = profile_for(test.partition("::")[0])
    path, name = profile.split_test_id(test)
    target = Path(worktree) / path
    if not name or not target.is_file():
        return False
    return profile.test_exists(target.read_text(encoding="utf-8", errors="ignore"), name)


def ci_evidence_problems(
    worktree: Path, issues: IssueClient, repo: str, branch: str, steps: list[str]
) -> list[str]:
    """What's wrong with each `workflow#step` offered as evidence, or nothing (#397).

    The file exists, the step is in it, and that workflow's latest run on the
    default branch passed. Only CI proves what a workflow does, so this is the
    same bar QA holds a CI-only change to.
    """
    if not steps:
        return []
    problems: list[str] = []
    try:
        runs = issues.latest_runs(repo, branch)
    except Exception as exc:  # noqa: BLE001
        return [f"the default branch's runs couldn't be read ({exc})"]
    for evidence in steps:
        path, _, step = evidence.partition("#")
        path, step = path.strip().removeprefix("./"), step.strip()
        target = worktree / path
        if not path.startswith(".github/workflows/") or not target.is_file():
            problems.append(f"`{path}` isn't a workflow in the repository")
            continue
        if not step or f"name: {step}" not in target.read_text(encoding="utf-8", errors="ignore"):
            problems.append(f"`{path}` has no step named `{step}`")
            continue
        run = runs.get(path)
        if run is None or run.get("conclusion") != "success":
            problems.append(
                f"`{path}`'s latest run on `{branch}` "
                + ("didn't pass" if run is not None else "hasn't run")
            )
    return problems


def already_done_comment(done: list) -> str:
    """The Developer's evidence, on the story, for QA and for whoever reads it (#221)."""
    rows = "\n".join(
        f"| {m.criterion} | {m.code} | "
        + (f"`{m.test}`" if m.test else f"CI: `{m.ci_step}`, passed on the default branch")
        + " |"
        for m in done
    )
    return (
        f"{ALREADY_DONE_MARKER}\n**Already done.** The code as it stands meets every acceptance "
        "criterion, so nothing was changed and no pull request was opened. Lint and the full "
        "test suite pass. QA judges it like any story.\n\n"
        "| Criterion | The code that meets it | The test that proves it |\n|---|---|---|\n" + rows
    )


# Said on every refusal made before anything is written. A repair is told to
# send only what fixes the failure, so after a refusal the Developer sent only
# the new piece and dropped the rest, taking it as already applied:
# sprint-metrics#132's third attempt left out the `__init__.py` change its
# first two had carried, and blocked on it.
NOT_APPLIED = (
    "\n\nNothing from this attempt was applied: the repository is as it was before it. "
    "Send the whole change again, corrected, not only the part that was wrong."
)


def _keep_proposal(sink: EventSink, repo: str, number: int, implementation) -> None:
    """Save what the Developer proposed, applied or not, beside the event log.

    A refusal records only why; what was refused was lost. Three blocks of
    sprint-metrics#127 and #129 could only be guessed at because of it. Best
    effort: a proposal that can't be saved never costs the attempt.
    """
    if sink.path is None:
        return
    import json  # noqa: PLC0415
    from datetime import UTC, datetime  # noqa: PLC0415

    try:
        folder = sink.path.parent.parent / "proposals" / repo / str(number)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        target = folder / f"{stamp}.json"
        target.write_text(json.dumps(implementation.model_dump(mode="json"), indent=1))
        sink.note(EventKind.NOTE, f"#{number} proposal kept", card=number, path=str(target))
    except Exception:  # noqa: BLE001
        return


def _touched_count(implementation: Implementation) -> int:
    return (
        len(implementation.new_files)
        + len(implementation.all_edits)
        + len(implementation.text_edits)
    )


def _let_go(implementation: Implementation, outcome: DeliveryOutcome) -> list[str]:
    """The merged tests this change lets go, in sections a rework reads back (#316)."""
    lines: list[str] = []
    if implementation.retired_tests:
        lines += ["", RETIRED_HEADING, ""]
        lines += [f"- `{t.path}::{t.test}`: {t.why}" for t in implementation.retired_tests]
    if outcome.contract_changed:
        lines += ["", DECLARED_HEADING, ""]
        lines += [f"- `{key}`" for key in outcome.contract_changed]
    return lines


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
    for move in implementation.moves:
        lines.append(f"- `{move.name}` moved from `{move.from_path}` to `{move.to_path}`")
    for path in implementation.deleted_files:
        lines.append(f"- `{path}` (deleted)")
    proven = getattr(implementation, "proven_by_existing", None) or []
    if proven:
        lines += ["", "## Criteria the existing tests prove", ""]
        lines += [f"- {p.criterion}: " + ", ".join(f"`{t}`" for t in p.tests) for p in proven]
    lines += _let_go(implementation, outcome)
    lines += ["", f"Closes #{card.number}"]
    # Signed last, after the Verification section the Code Reviewer is shown.
    return signed("\n".join(lines), "Developer")


def _gates_disagree(card: Card, *, board, issues, sink, repo: str, result) -> bool:
    """A story returned again after MAX_ROUND_TRIPS deliveries goes back to refinement (#242).

    sprint-metrics#145 went round six times: QA returned it because the test
    didn't assert stdout was empty, as its criterion said; the reviewer asked
    for that assertion to go, as the epic's design note said. Repairing can't
    settle a story that contradicts its own design. True if it went back.
    """
    rounds = story_problem.round_trips(issues, repo, card.number or 0)
    if rounds < story_problem.MAX_ROUND_TRIPS:
        return False
    branch = branch_name(card.number or 0, card.title)
    try:
        pull = issues.pull_for_branch(repo, branch, known=card.open_pull_on(branch))
    except Exception:  # noqa: BLE001
        pull = None
    number = pull["number"] if pull else None
    comment = story_problem.round_trip_evidence(issues, repo, card, number, rounds)
    if not story_problem.return_to_refinement(
        board,
        issues,
        sink,
        card,
        repo=repo,
        cards=board.cards(),
        comment=comment,
        reason="gate round trips",
    ):
        return False
    if number is not None:
        with contextlib.suppress(Exception):
            issues.comment(
                repo,
                number,
                signed(
                    f"Closed: #{card.number} went back to refinement after {rounds} "
                    "deliveries the gates kept returning. See its epic.",
                    "Developer",
                ),
            )
            issues.close_pull(repo, number)
    result.returned.append((card.number or 0, card.parent or 0))
    return True


def _work_one_card(card: Card, **kwargs: Any) -> None:
    """`_deliver_and_move`, as one Developer run: its records share the run's id (crew#449)."""
    with log.run(
        "Developer",
        card=card.number,
        repo=card.repo or kwargs["repo"],
        sprint=kwargs["sprint"],
    ):
        _deliver_and_move(card, **kwargs)


def _deliver_and_move(
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
    if rework and _gates_disagree(
        card, board=board, issues=issues, sink=sink, repo=card_repo, result=result
    ):
        counts[IN_PROGRESS] -= 1
        return
    outcome = None
    interrupted: str | None = None
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
        from crew_org.tools.remote_errors import transient_remote, where  # noqa: PLC0415

        # A service stumbling isn't the story's failure (#390): sprint-metrics#358
        # was blocked for a person on an unreadable GitHub reply mid-outage.
        # The card stays In Progress, and the next pass's reconciliation
        # returns it to the backlog to be tried again. Only a streak blocks it.
        service = transient_remote(exc)
        streak = _interrupted_streak(issues, card_repo, card.number or 0) if service else 0
        if service and streak + 1 < INTERRUPTIONS_BEFORE_BLOCKING:
            interrupted = (
                f"{INTERRUPTED_MARKER}\n**Interrupted by {service}, not by the story.** "
                f"`{type(exc).__name__}: {str(exc)[:200]}` at {where(exc)}.\n\n"
                f"It's tried again next pass ({streak + 1} of "
                f"{INTERRUPTIONS_BEFORE_BLOCKING} before it's blocked for a person)."
            )
        else:
            # A last resort. Anything deliver_story can attribute it returns on
            # its own outcome; reaching here means it could not.
            reason = f"{type(exc).__name__}: {exc}"
            if service:
                reason = (
                    f"{service} failed {INTERRUPTIONS_BEFORE_BLOCKING} times in a row: {reason}"
                )
            outcome = DeliveryOutcome(
                card=card.number or 0, blocked_reason=reason[:400], where=where(exc)
            )
    finally:
        # Nothing is committed or pushed until verification passes, so for a
        # card that failed this worktree is the only copy of what was
        # written. Read it before removing it, or the card blocks with no
        # evidence of why and the diagnosis has to be guessed.
        if outcome is not None and not outcome.ok:
            outcome.rejected_diff = card_ws.diff_if_open()
        card_ws.close()
        profiles.clear()

    if interrupted is not None:
        artifacts.comment(
            issues, sink, repo=card_repo, number=card.number or 0, body=interrupted, by=None
        )
        sink.note(
            EventKind.NOTE,
            f"#{card.number} interrupted by a service, not the story: tried again next pass",
            card=card.number,
        )
        result.interrupted.append(card.number or 0)
        return

    if outcome.already_done:
        # Straight to QA with the evidence (#221): no diff, so nothing to review.
        move_card(
            board,
            sink,
            item_id=card.item_id,
            to=QAING,
            by="Developer",
            card=card.number,
            frm=IN_PROGRESS,
            summary="already done — every criterion met by existing code",
        )
        counts[IN_PROGRESS] -= 1
        counts[QAING] = counts.get(QAING, 0) + 1
        artifacts.comment(
            issues,
            sink,
            repo=repo,
            number=card.number or 0,
            body=already_done_comment(outcome.already_done),
            by="Developer",
        )
        result.delivered.append(outcome)
    elif outcome.ok:
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
            + existing_proof_block(outcome.proven_by_existing)
            + (" Escalated to finish." if outcome.escalated else ""),
            by="Developer",
        )
        result.delivered.append(outcome)
    elif outcome.returned and story_problem.return_to_refinement(
        board,
        issues,
        sink,
        card,
        repo=repo,
        cards=board.cards(),
        comment=outcome.returned,
    ):
        counts[IN_PROGRESS] -= 1
        result.returned.append((card.number or 0, card.parent or 0))
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
            rule=getattr(outcome, "blocked_rule", None),
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
            + (f"Where: `{outcome.where}`\n\n" if outcome.where else "")
            + f"Attempts: {outcome.attempts}. "
            f"{'Escalated.' if outcome.escalated else 'Not escalated.'}",
            by="Developer",
        )
        # One block, one event: the move above already recorded `card.blocked`
        # (#321; the merge path's duplicate went in #304).
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
    landed = merge_approved(
        board,
        issues,
        sink,
        cards=cards,
        default_repo=repo,
        repos=repos,
        keep_both=keeping_both(ws),
    )
    result.conflicted = [card for card, _pr in landed.conflicted]
    result.rebuilding = list(landed.rebuilding)
    result.awaiting_approval = list(landed.awaiting_approval)
    result.unapprovable = list(landed.unapprovable)
    result.unmergeable = list(landed.failed)
    result.updating = list(landed.updating)
    result.checking = list(landed.checking)
    result.queued = list(landed.queued)
    result.in_queue = list(landed.in_queue)
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
        waiting = rebuild_waits_for(issues, cards, card, repo=card.repo or repo)
        if waiting is not None:
            other, paths = waiting
            result.waiting_on_files.append((card.number or 0, other))
            sink.note(
                EventKind.NOTE,
                f"#{card.number} waits to rebuild until PR #{other} lands: both change "
                + ", ".join(paths),
                card=card.number,
                pull=other,
                files=paths,
            )
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
                card=card.number,
                waits_for=blocker.number,
                rule=Rule.HOLD_SIBLING,
            )
            continue
        try:
            body = issues.get(card.repo or repo, card.number or 0).get("body") or ""
        except Exception:  # noqa: BLE001
            body = ""
        needed = held_by_what_it_builds_on(cards, card, body)
        if needed is not None:
            result.waiting_on_a_sibling.append((card.number or 0, needed.number or 0))
            sink.note(
                EventKind.NOTE,
                f"#{card.number} waits for #{needed.number}, which it builds on (#295)",
                card=card.number,
                waits_for=needed.number,
                rule=Rule.HOLD_BUILDS_ON,
            )
            continue

        verdict = rules.may_move(frm=SPRINT_BACKLOG, to=IN_PROGRESS, counts=counts)
        if not verdict.allowed:
            sink.note(EventKind.NOTE, verdict.reason, rule=Rule.HOLD_WIP_LIMIT)
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
