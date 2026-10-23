"""Delivery: a story in the sprint becomes a pull request.

The deliver phase claims stories up to the WIP limit and hands each to a Developer
session (`session_delivery`, ADR 0032), then moves the card on by where it ended:
review, QA, back to refinement, or blocked for a person. Rework from review or QA
is a new session. The helpers here are what the session's gates and the board's
flow around it share.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from typing import Any

from crew_org import log, profiles
from crew_org.columns import BLOCKED, DONE, IN_PROGRESS, QAING, REVIEWING, SPRINT_BACKLOG
from crew_org.events import CrewEvent, EventKind, EventSink, attributed, first_hold, held
from crew_org.flows import artifacts, story_problem
from crew_org.flows.acceptance import ALREADY_DONE_MARKER, qa_marker
from crew_org.flows.artifacts import signed
from crew_org.flows.ci import CI_MARKER, latest_ci_verdict
from crew_org.flows.merge import (
    REBUILD_MARKER,
    changes_requested,
    keeping_both,
    merge_approved,
    rebuild_files,
)
from crew_org.flows.moves import move_card
from crew_org.flows.revert import RevertLanding, land_reverts
from crew_org.flows.siblings import unlanded_earlier_siblings
from crew_org.git_ops import Workspace, branch_name
from crew_org.llm import reraise_if_down
from crew_org.process import ProcessRules
from crew_org.rulebook import says
from crew_org.rules import Rule
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, LinkedPull, ProjectClient
from crew_org.work_types import DELIVERED, open_in

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
    # Its sessions, each round after the gates included (crew#778).
    attempts: int = 0
    # What the Developer actually wrote, kept when the card blocks: the artifact
    # of a failure, which would otherwise be deleted with the worktree.
    rejected_diff: str | None = None
    # Where an unexpected error happened: the crew's last frames (#390).
    where: str | None = None
    # A story the gates found already done (#221): each criterion with the code
    # that meets it and the test that proves it.
    already_done: list = field(default_factory=list)
    # The files the project's own fixers changed before the checks at `done`, and each
    # allowed unsafe fix (crew#758): in the diff, and named for review and QA.
    autofixed: list[str] = field(default_factory=list)
    unsafe_fixes: list[str] = field(default_factory=list)
    # Where a session's ending already moved the card (crew#778): back to refinement,
    # or blocked for a person. Only the clean-up is left.
    routed: str | None = None
    # Its sessions ended, other than by an interruption: the clone's checkpoints go.
    ended: bool = False

    @property
    def ok(self) -> bool:
        return self.pr is not None or bool(self.already_done)

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
    # A role's Task the Sponsor approves before it merges (crew#839).
    awaiting_sponsor: list[tuple[int, int]] = field(default_factory=list)
    no_sponsor: list[tuple[int, int]] = field(default_factory=list)
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
            bool(changes_requested(issues.pull_reviews(repo, pull["number"]), head))
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
                and c.work_type in DELIVERED
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
    in_progress = [c for c in cards if c.status == IN_PROGRESS and c.work_type in DELIVERED]
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
                rule="decision.delivery.reconcile_orphans",
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
            for c in open_in(cards, SPRINT_BACKLOG, repos=repos)
            if c.sprint == sprint or sprint is None
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


def held_by_a_sibling(cards: list[Card], story: Card) -> Card | None:
    """The first earlier story in this story's epic that has not landed yet.

    `In Progress` has a WIP limit of 3, so without this the crew claims three
    siblings at once, each branching from a default branch that does not yet
    contain the others. Which siblings are earlier is `siblings`' reading, the
    one sprint planning reads too.
    """
    blockers = unlanded_earlier_siblings(cards, story)
    return min(blockers, key=lambda c: c.number or 0) if blockers else None


# What a declared contract change names: `path.py::test`, `path.py`, or a bare test name.
_DECLARED = re.compile(r"[\w/.-]+\.py(?:::[\w.]+)?|\btest_\w+")


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
        if card.parent is not None and record.text_for(issues, repo, card.parent):

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
    line = _existing_tests_line(body)
    if not declares_a_change(body):
        return set()
    return set(_DECLARED.findall(line))


def declares_a_change(body: str) -> bool:
    """Does the story's **Existing tests** line say it changes merged tests? (crew#778)"""
    line = _existing_tests_line(body)
    return "contract change" in line or "changes what they assert" in line


def _existing_tests_line(body: str) -> str:
    from crew_org.flows.board_flow import (  # noqa: PLC0415
        EXISTING_TESTS,
        EXISTING_TESTS_BEFORE_311,
    )

    return next(
        (
            ln
            for ln in body.splitlines()
            if ln.startswith((EXISTING_TESTS, EXISTING_TESTS_BEFORE_311))
        ),
        "",
    )


# The pull request's section naming merged tests the story declared it changes.
DECLARED_HEADING = "## Tests the story declares it changes"
# The skeleton's placeholders it replaced, marked `crew:placeholder` (crew#690).
PLACEHOLDERS_HEADING = "## Skeleton placeholders it replaces"


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
        and c.work_type in DELIVERED
        and c.state != "CLOSED"
        and (c.sprint == sprint or sprint is None)
        and c.repo not in repos
    ]


def workflows_not_permitted(paths: list[str], permissions: dict[str, str] | None = None) -> str:
    """Why a workflow change can't be pushed, or "" if it can or we can't tell (#279).

    `paths` are what the branch's diff changes (crew#778).
    """
    from crew_org.auth import app_permissions  # noqa: PLC0415
    from crew_org.tools import ci_guard  # noqa: PLC0415

    held = app_permissions() if permissions is None else permissions
    changed = [p for p in paths if ci_guard.is_workflow(p)]
    if not changed or not held or held.get("workflows") == "write":
        return ""
    return says("check.delivery.workflows_not_permitted", files=", ".join(changed))


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


def already_done_comment(done: list) -> str:
    """The Developer's evidence, on the story, for QA and for whoever reads it (#221)."""
    rows = "\n".join(f"| {m.criterion} | {m.code} | `{m.test}` |" for m in done)
    return (
        f"{ALREADY_DONE_MARKER}\n**Already done.** The code as it stands meets every acceptance "
        "criterion, so nothing was changed and no pull request was opened. Lint and the full "
        "test suite pass. QA judges it like any story.\n\n"
        "| Criterion | The code that meets it | The test that proves it |\n|---|---|---|\n" + rows
    )


def _gates_disagree(card: Card, *, board, issues, sink, repo: str, result) -> bool:
    """A story returned again after MAX_ROUND_TRIPS deliveries goes back to refinement (#242).

    sprint-metrics#145 went round six times: QA returned it because the test
    didn't assert stdout was empty, as its criterion said; the reviewer asked
    for that assertion to go, as the epic's design note said. Repairing can't
    settle a story that contradicts its own design. A story with no epic to be
    split again is blocked for a person with the same evidence, rather than going
    round again without end (crew#778). True if it went back or was blocked.
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
        round_=rounds,
    ):
        from crew_org.flows.session_end import blocked_for_a_person  # noqa: PLC0415

        said = comment.replace(story_problem.STORY_PROBLEM_MARKER + "\n", "")
        blocked_for_a_person(
            card,
            repo=repo,
            comment=said,
            rule=Rule.ROUND_TRIPS_NO_EPIC,
            board=board,
            issues=issues,
            sink=sink,
        )
        result.blocked.append(
            DeliveryOutcome(
                card=card.number or 0,
                blocked_reason=said.splitlines()[0][:400],
                blocked_rule=Rule.ROUND_TRIPS_NO_EPIC,
            )
        )
        return True
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
    from crew_org.flows.session_delivery import deliver_by_session  # noqa: PLC0415

    outcome = None
    interrupted: str | None = None
    try:
        outcome = deliver_by_session(
            card,
            board=board,
            issues=issues,
            sink=sink,
            ws=card_ws,
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
        # A session interrupted by its call layer or its container is the same,
        # and resumes from its last checkpoint (crew#778, P3).
        service = transient_remote(exc) or getattr(exc, "service", None)
        streak = _interrupted_streak(issues, card_repo, card.number or 0) if service else 0
        if service and streak + 1 < INTERRUPTIONS_BEFORE_BLOCKING:
            interrupted = (
                f"{INTERRUPTED_MARKER}\n**Interrupted by {service}, not by the story.** "
                f"`{type(exc).__name__}: {str(exc)[:200]}` at {where(exc)}.\n\n"
                f"It's tried again next pass ({streak + 1} of "
                f"{INTERRUPTIONS_BEFORE_BLOCKING} before it's blocked for a person)."
            )
        else:
            # A last resort. Anything the session's delivery can attribute it
            # returns on its own outcome; reaching here means it could not.
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
        if outcome is not None and not outcome.ok and outcome.rejected_diff is None:
            outcome.rejected_diff = card_ws.diff_if_open()
        card_ws.close()
        if outcome is not None and outcome.ended and outcome.branch:
            card_ws.forget(outcome.branch)
        profiles.clear()

    if interrupted is not None:
        artifacts.comment(
            issues, sink, repo=card_repo, number=card.number or 0, body=interrupted, by=None
        )
        sink.note(
            EventKind.NOTE,
            f"#{card.number} interrupted by a service, not the story: tried again next pass",
            card=card.number,
            rule="decision.delivery.interrupted",
        )
        result.interrupted.append(card.number or 0)
        return
    assert outcome is not None, "delivered, or blocked by the error, unless interrupted"

    if outcome.routed:
        # The session's ending took it back to refinement, or blocked it for a
        # person, and said why on the card or its epic (crew#778, decision 9).
        counts[IN_PROGRESS] -= 1
        if outcome.routed == "refinement":
            result.returned.append((card.number or 0, card.parent or 0))
        else:
            result.blocked.append(outcome)
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
            rule="decision.delivery.outcome",
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
            rule="decision.delivery.outcome",
        )
        counts[IN_PROGRESS] -= 1
        counts[REVIEWING] = counts.get(REVIEWING, 0) + 1
        artifacts.comment(
            issues,
            sink,
            repo=repo,
            number=card.number or 0,
            body=f"Implemented in #{outcome.pr} on `{outcome.branch}`. Lint and tests pass.",
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
            + f"Sessions: {outcome.attempts}.",
            by="Developer",
        )
        # One block, one event: the move above already recorded `card.blocked`
        # (#321; the merge path's duplicate went in #304).
        result.blocked.append(outcome)


def deliver(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    rules: ProcessRules,
    ws: Workspace,
    *,
    sprint: str,
    repo: str,
    default_branch: str = "main",
    limit: int | None = None,
    repos: set[str] | None = None,
    sponsor: str | None = None,
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
        sponsor=sponsor,
    )
    result.conflicted = [card for card, _pr in landed.conflicted]
    result.rebuilding = list(landed.rebuilding)
    result.awaiting_approval = list(landed.awaiting_approval)
    result.awaiting_sponsor = list(landed.awaiting_sponsor)
    result.no_sponsor = list(landed.no_sponsor)
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
            held("decision.delivery.sprint_stories", repo=card.repo or repo, card=card.number)
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
                rule="decision.delivery.waits_on_files",
            )
            continue
        if card.status == REVIEWING:
            verdict = rules.may_move(frm=REVIEWING, to=IN_PROGRESS, counts=counts)
            if not verdict.allowed:
                sink.note(
                    EventKind.NOTE,
                    f"#{card.number} needs re-work: {verdict.reason}",
                    rule="decision.delivery.rework",
                )
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
                rule="decision.delivery.rework",
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
            result=result,
            counts=counts,
            sprint=sprint,
            repo=repo,
            default_branch=default_branch,
            rework=True,
        )
    if result.reworked:
        cards = board.cards()

    claimed = 0
    for card in sprint_stories(cards, sprint, repos=repos):
        if limit is not None and claimed >= limit:
            held("decision.delivery.limit", repo=card.repo or repo, card=card.number)
            break

        blocker = held_by_a_sibling(cards, card)
        if blocker is not None:
            result.waiting_on_a_sibling.append((card.number or 0, blocker.number or 0))
            held(Rule.HOLD_SIBLING, repo=card.repo or repo, card=card.number)
            if first_hold(
                sink,
                sprint=sprint,
                rule=Rule.HOLD_SIBLING,
                repo=card.repo or repo,
                card=card.number,
            ):
                sink.note(
                    EventKind.NOTE,
                    f"#{card.number} waits for #{blocker.number} in the same epic",
                    card=card.number,
                    # A card number alone names no repository (crew#752).
                    repo=card.repo or repo,
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
            held(Rule.HOLD_BUILDS_ON, repo=card.repo or repo, card=card.number)
            if first_hold(
                sink,
                sprint=sprint,
                rule=Rule.HOLD_BUILDS_ON,
                repo=card.repo or repo,
                card=card.number,
            ):
                sink.note(
                    EventKind.NOTE,
                    f"#{card.number} waits for #{needed.number}, which it builds on (#295)",
                    card=card.number,
                    repo=card.repo or repo,
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
            result=result,
            counts=counts,
            sprint=sprint,
            repo=repo,
            default_branch=default_branch,
        )
    return result
