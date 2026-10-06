"""Landing approved work.

Merging used to happen only at sprint close, which was consistent with
reviewing the increment at the end — but it meant every story branched from a
`main` that was missing its predecessors. Six stories editing one module from
six different starting points produce their conflicts all at once, at the worst
possible moment.

So approval and merge timing are decoupled. The Sponsor's gate is still the
approval; this lands whatever has passed it, and runs before new work is
claimed so a branch always starts from current `main`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from crew_org.columns import BLOCKED, DONE, IN_PROGRESS, MERGING
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows import artifacts
from crew_org.flows.ci import return_for_ci
from crew_org.flows.moves import move_card
from crew_org.git_ops import MergeConflict, branch_name
from crew_org.tools.github_issues import (
    FAILED_CONCLUSIONS,
    BranchUpdateConflict,
    IssueClient,
    IssueError,
)
from crew_org.tools.github_project import Card, ProjectClient

STORY_TYPE = "Story"

# GitHub's word for "this branch and main have both changed the same lines".
CONFLICTED = "dirty"
# GitHub's word for "main has moved on since this branch was cut". Only refused
# where branch protection requires up-to-date branches, as sprint-metrics does.
BEHIND = "behind"

# GitHub's word for "branch protection still wants an approving review". Read
# off `reviewDecision`, which is the verdict protection actually applies —
# unlike the review list, which records approvals that were never counted.
REVIEW_REQUIRED = "REVIEW_REQUIRED"


# How long to let GitHub work out mergeability. It computes it lazily, so a
# pull request read just after `main` moved reports neither `behind` nor
# `dirty` but `unknown`, with `mergeable` null — and a merge tried then is
# refused (#302).
SETTLE_TRIES = 4
SETTLE_WAIT = 2.0


class Landing(StrEnum):
    MERGED = "merged"
    # In the base branch's merge queue; GitHub merges it when its turn passes.
    QUEUED = "queued"
    # Brought up to date from `main`; merges on a later pass once checks pass.
    UPDATING = "updating"
    CONFLICTED = "conflicted"
    # The merge queue took it out, and nothing has changed in it since.
    REMOVED = "removed"
    # GitHub has not yet worked out whether it merges cleanly.
    UNSETTLED = "unsettled"
    # Its checks on GitHub are still running; it merges on a later pass.
    CHECKING = "checking"
    # Its checks on GitHub don't prove it: none passed, one failed, or they
    # couldn't be read. Not merged, and said so.
    UNPROVEN = "unproven"


@dataclass(frozen=True)
class Landed:
    how: Landing
    reason: str = ""
    # Queued by this call, not found already queued: the moment the crew acted.
    joined: bool = False
    # The branch whose queue holds it, for whoever waits on the queue (#314).
    base: str = ""


def settled(
    issues: IssueClient, repo: str, number: int, *, sleep: Callable[[float], None] = time.sleep
) -> dict | None:
    """The pull request once GitHub has worked out its mergeability, or None."""
    for attempt in range(SETTLE_TRIES):
        detail = issues.pull(repo, number)
        if detail.get("merged") or detail.get("mergeable") is not None:
            return detail
        if attempt < SETTLE_TRIES - 1:
            sleep(SETTLE_WAIT)
    return None


def land(
    issues: IssueClient, repo: str, number: int, *, sleep: Callable[[float], None] = time.sleep
) -> Landed:
    """Take one approved pull request as far towards `main` as it can go now.

    Where the base branch has a merge queue, it joins the queue and GitHub
    does the rest in order: each merge would otherwise leave every other
    approved pull request behind `main` again, and one updated alongside it
    wasted its checks (#302). Without a queue, it is merged, or brought up to
    date where protection requires that first (#116).

    Raises when GitHub refuses; the caller decides what that means.
    """
    detail = settled(issues, repo, number, sleep=sleep)
    if detail is None:
        return Landed(Landing.UNSETTLED, "GitHub is still working out whether it merges cleanly")
    if detail.get("merged"):
        return Landed(Landing.MERGED)
    base = (detail.get("base") or {}).get("ref") or "main"
    head = (detail.get("head") or {}).get("sha")
    queue = issues.queue_state(repo, number, branch=base)
    if queue.merged:
        return Landed(Landing.MERGED)
    if queue.queued:
        return Landed(Landing.QUEUED, base=base)
    if detail.get("mergeable_state") == CONFLICTED or detail.get("mergeable") is False:
        return Landed(Landing.CONFLICTED)
    if queue.removed:
        return Landed(Landing.REMOVED, queue.removed)
    if queue.has_queue:
        issues.enqueue(detail["node_id"], head=head)
        return Landed(Landing.QUEUED, joined=True, base=base)
    # Behind `main` where branch protection requires up to date. A merge
    # would be refused with a 405 naming a missing status check, which is
    # not what is wrong (#116). Bring it up to date instead: GitHub
    # re-runs the checks on the new head, and a later pass merges it.
    if detail.get("mergeable_state") == BEHIND:
        try:
            issues.update_branch(repo, number, head=head)
        except BranchUpdateConflict:
            return Landed(Landing.CONFLICTED)
        except Exception as exc:
            raise IssueError(f"could not update from main: {exc}") from exc
        return Landed(Landing.UPDATING)
    # Nothing but the crew may be holding it to its checks: a private repository
    # on a free organization gets no branch protection and no queue (#335). So
    # §19.7 is kept here, whatever GitHub enforces.
    held = unproven(issues, repo, head)
    if held:
        return held
    issues.merge_pull(repo, number)
    return Landed(Landing.MERGED)


def unproven(issues: IssueClient, repo: str, head: str | None) -> Landed | None:
    """Why the checks on its head don't yet prove it, or None when they do.

    A check ran, all of them finished, and none failed. `neutral` and
    `skipped` are not failures, but on their own they prove nothing.
    """
    try:
        runs = issues.check_runs(repo, head or "")
    except Exception as exc:  # noqa: BLE001
        return Landed(Landing.UNPROVEN, f"its checks couldn't be read: {exc}"[:120])
    running = [r.get("name") or "a check" for r in runs if r.get("status") != "completed"]
    if running:
        return Landed(Landing.CHECKING, "checks still running: " + ", ".join(running[:3]))
    failed = [r.get("name") or "a check" for r in runs if r.get("conclusion") in FAILED_CONCLUSIONS]
    if failed:
        return Landed(Landing.UNPROVEN, "checks failed: " + ", ".join(failed[:3]))
    if not any(r.get("conclusion") == "success" for r in runs):
        return Landed(
            Landing.UNPROVEN,
            "no check passed on its head, and nothing merges without one (constitution §19.7)",
        )
    return None


@dataclass
class MergeResult:
    merged: list[tuple[int, int]] = field(default_factory=list)
    conflicted: list[tuple[int, int]] = field(default_factory=list)
    # (card, PR) approved, conflicting with main, and returned for the crew to
    # rebuild on main rather than blocked for a person.
    rebuilding: list[tuple[int, int]] = field(default_factory=list)
    # (card, PR) approved, with a CI check that failed on its head or in the
    # merge queue: returned to the Developer with the log (#325).
    ci_failed: list[tuple[int, int]] = field(default_factory=list)
    awaiting_approval: list[tuple[int, int]] = field(default_factory=list)
    # Approved by the crew, and GitHub did not count it. A separate list
    # because the Sponsor's response differs: one is waiting, the other is a
    # gate no identity the crew holds can satisfy, and waiting will not fix it.
    unapprovable: list[tuple[int, int]] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)
    # (card, PR) brought up to date from main this pass; they merge on the next
    # once their checks pass on the new head.
    updating: list[tuple[int, int]] = field(default_factory=list)
    # (card, what is still running) approved, with checks not yet finished
    # where nothing on GitHub holds it to them (#335); merged on a later pass.
    checking: list[tuple[int, str]] = field(default_factory=list)
    # (card, PR) in the merge queue; GitHub merges it in its turn.
    queued: list[tuple[int, int]] = field(default_factory=list)
    # (repo, PR, base branch) of each queued pull request, so the tick can
    # wait for the queue rather than stop while it is about to land (#314).
    in_queue: list[tuple[str, int, str]] = field(default_factory=list)


def ready_to_land(cards: list[Card], repos: set[str] | None = None) -> list[Card]:
    """Stories that have passed QA and are waiting to land, oldest card first.

    Sorted, because this was a bare comprehension over whatever order the board
    returned. Card number ascends in the order the Business Analyst proposed the
    stories, so sorting by it is sorting by the order they were meant to be
    built — and merging siblings out of that order conflicts the remainder,
    which blocks those cards and labels them for a person.
    """
    return sorted(
        (
            c
            for c in cards
            if c.status == MERGING
            and c.work_type == STORY_TYPE
            and c.state != "CLOSED"
            and (repos is None or c.repo in repos)
        ),
        key=lambda c: c.number or 0,
    )


def merge_approved(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    *,
    cards: list[Card],
    default_repo: str,
    repos: set[str] | None = None,
    keep_both: Callable[[str, str], list[str] | None] | None = None,
) -> MergeResult:
    """Land every story the Sponsor has approved.

    `keep_both(repo, branch)` merges main into an approved branch that
    conflicts, keeping both sides where they only added lines, and pushes it.
    It returns the files it kept both sides of, or None when it couldn't (#436).

    A conflict is not something to retry or work around: it means two changes
    disagree and a person has to decide. The card is blocked and labelled so it
    is visible, rather than left looking merely slow.
    """
    result = MergeResult()

    # Merged by the queue since the last pass. Its `Closes #N` closed the card,
    # which `ready_to_land` passes over, and a closed card has nothing left to
    # land — but it merged without the crew, which records it here.
    for card in merged_elsewhere(cards, repos):
        repo = card.repo or default_repo
        merged = _merged_pull(issues, repo, branch_name(card.number or 0, card.title))
        if merged is not None:
            _done(board, sink, card, pull=merged, result=result, issues=issues, repo=repo)

    # Done, with its issue still open: the merge recorded, and GitHub never
    # acted on its `Closes #N` (sprint-metrics#339, crew#381). The crew closes
    # what it merged rather than trusting the keyword.
    for card in done_but_open(cards, repos):
        repo = card.repo or default_repo
        merged = _merged_pull(issues, repo, branch_name(card.number or 0, card.title))
        if merged is not None:
            _close_story(issues, sink, card, repo=repo, pull=merged)

    for card in ready_to_land(cards, repos):
        number = card.number or 0
        repo = card.repo or default_repo

        branch = branch_name(number, card.title)
        pull = issues.pull_for_branch(repo, branch, known=card.open_pull_on(branch))
        if pull is None:
            merged = _merged_pull(issues, repo, branch)
            if merged is not None:
                _done(board, sink, card, pull=merged, result=result, issues=issues, repo=repo)
            else:
                result.failed.append((number, "no open pull request"))
            continue

        approved = any(
            r.get("state") == "APPROVED" for r in issues.pull_reviews(repo, pull["number"])
        )
        if not approved:
            result.awaiting_approval.append((number, pull["number"]))
            continue

        # An approving review sits on it and GitHub still wants one. That is
        # not slowness: the reviewing identity's approval was recorded and
        # does not count, so no further tick will change it. Reported as a gate
        # the crew cannot satisfy rather than as an approval still to come —
        # sprint-metrics #31 and #32 waited a whole tick looking merely slow.
        if issues.review_decision(repo, pull["number"]) == REVIEW_REQUIRED:
            result.unapprovable.append((number, pull["number"]))
            move_card(
                board,
                sink,
                item_id=card.item_id,
                to=BLOCKED,
                by=None,
                card=number,
                frm=MERGING,
                summary=f"PR #{pull['number']} cannot be approved by any crew identity",
                kind=EventKind.CARD_BLOCKED,
            )
            artifacts.label(
                issues, sink, repo=repo, number=number, by=None, add=["blocked", "needs:human"]
            )
            artifacts.comment(
                issues,
                sink,
                repo=repo,
                number=number,
                body=f"**Blocked — an approval that cannot arrive.** PR #{pull['number']} "
                "carries an approving review from the crew's reviewing identity, and GitHub "
                "still reports `REVIEW_REQUIRED`: branch protection only counts an approval "
                "from an actor with repository write access.\n\n"
                "No further tick will change this. Either approve the pull request yourself, "
                "or give the reviewing app the access its approval needs — which also lets it "
                "push, and is a trade only you can make.",
                by=None,
            )
            continue

        # A check that failed on the head can't join the queue, and said
        # nothing: the Developer is shown why instead (#325).
        failed = _failed(issues.failed_checks, repo, (pull.get("head") or {}).get("sha", ""))
        if failed:
            return_for_ci(
                board, issues, sink, card, repo=repo, pull=pull, failed=failed, where="on its head"
            )
            result.ci_failed.append((number, pull["number"]))
            continue

        try:
            landed = land(issues, repo, pull["number"])
        except Exception as exc:  # noqa: BLE001
            result.failed.append((number, str(exc)[:120]))
            continue
        if landed.how == Landing.CONFLICTED:
            _conflict(
                board, issues, sink, card, repo=repo, pull=pull, result=result, keep_both=keep_both
            )
            continue
        # Taken out of the queue: a conflict with what landed ahead of it, or
        # checks that failed on top of it. A failed check goes back with its
        # log (#325); a conflict is rebuilt on `main` as it now is.
        if landed.how == Landing.REMOVED:
            sink.emit(
                CrewEvent(
                    kind=EventKind.NOTE,
                    card=number,
                    summary=f"PR #{pull['number']} left the merge queue: {landed.reason}"[:120],
                )
            )
            failed = _failed(issues.merge_group_failures, repo, pull["number"])
            if failed:
                return_for_ci(
                    board,
                    issues,
                    sink,
                    card,
                    repo=repo,
                    pull=pull,
                    failed=failed,
                    where="in the merge queue",
                )
                result.ci_failed.append((number, pull["number"]))
                continue
            _conflict(
                board, issues, sink, card, repo=repo, pull=pull, result=result, keep_both=keep_both
            )
            continue
        if landed.how in (Landing.UNSETTLED, Landing.UNPROVEN):
            result.failed.append((number, landed.reason))
            continue
        if landed.how == Landing.CHECKING:
            result.checking.append((number, landed.reason))
            continue
        if landed.how == Landing.QUEUED:
            result.queued.append((number, pull["number"]))
            result.in_queue.append((repo, pull["number"], landed.base or "main"))
            # GitHub merges it later, often before the crew looks again, and
            # the board's own automation then moves the card to Done. Joining
            # is the crew's act, so that is what the event log records.
            if landed.joined:
                sink.emit(
                    CrewEvent(
                        kind=EventKind.NOTE,
                        card=number,
                        summary=f"PR #{pull['number']} joined the merge queue",
                    )
                )
            continue
        if landed.how == Landing.UPDATING:
            result.updating.append((number, pull["number"]))
            sink.emit(
                CrewEvent(
                    kind=EventKind.NOTE,
                    card=number,
                    summary=f"PR #{pull['number']} was behind main — brought up to date",
                )
            )
            continue

        _done(board, sink, card, pull=pull, result=result, issues=issues, repo=repo)

    return result


def _failed(read: Callable, repo: str, key) -> list[dict]:
    """What failed, or nothing where it can't be read: a read never fails the pass."""
    if not key:
        return []
    try:
        return list(read(repo, key))
    except Exception:  # noqa: BLE001
        return []


def merged_elsewhere(cards: list[Card], repos: set[str] | None = None) -> list[Card]:
    """Stories still in Merging whose card has closed: merged by a merge queue."""
    return [
        c
        for c in cards
        if c.status == MERGING
        and c.work_type == STORY_TYPE
        and c.state == "CLOSED"
        and (repos is None or c.repo in repos)
    ]


def done_but_open(cards: list[Card], repos: set[str] | None = None) -> list[Card]:
    """Stories in Done whose issue is still open: merged, and never closed."""
    return [
        c
        for c in cards
        if c.status == DONE
        and c.work_type == STORY_TYPE
        and c.state != "CLOSED"
        and (repos is None or c.repo in repos)
    ]


def _merged_pull(issues: IssueClient, repo: str, branch: str) -> dict | None:
    """The pull request from `branch` that merged, if one did."""
    return next(
        (p for p in issues.pulls_for_branch(repo, branch, state="closed") if p.get("merged_at")),
        None,
    )


def _done(
    board: ProjectClient,
    sink: EventSink,
    card: Card,
    *,
    pull: dict,
    result: MergeResult,
    issues: IssueClient,
    repo: str,
) -> None:
    number = card.number or 0
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
    result.merged.append((number, pull["number"]))
    if card.state != "CLOSED":
        _close_story(issues, sink, card, repo=repo, pull=pull)


def _close_story(
    issues: IssueClient, sink: EventSink, card: Card, *, repo: str, pull: dict
) -> None:
    """Close a merged story's issue: its `Closes #N` isn't relied on (crew#381).

    GitHub usually closes it first, and closing a closed issue changes nothing. A
    close that fails is noted and retried by the next pass's sweep.
    """
    try:
        issues.close(repo, card.number or 0)
    except Exception as exc:  # noqa: BLE001
        sink.emit(
            CrewEvent(
                kind=EventKind.NOTE,
                card=card.number,
                summary=f"PR #{pull['number']} merged; closing the issue failed: {exc}"[:120],
            )
        )
        return
    sink.emit(
        CrewEvent(
            kind=EventKind.NOTE,
            card=card.number,
            summary=f"closed: PR #{pull['number']} merged",
        )
    )


# On a pull request whose approved head conflicted with main and was returned for
# a rebuild. Delivery reads it as work sent back (`awaiting_rework`).
REBUILD_MARKER = "<!-- crew:rebuild head={head} -->"
# Parallel work can conflict a rebuilt pull request again. Past this many
# rebuilds of one pull request, a person decides.
MAX_REBUILDS = 2


def _conflict(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    card: Card,
    *,
    repo: str,
    pull: dict,
    result: MergeResult,
    keep_both: Callable[[str, str], list[str] | None] | None = None,
) -> None:
    """An approved story that conflicts with main is rebuilt by the crew.

    Decided 2026-09-25, reversing #158's third criterion for approved work.
    sprint-metrics is one module, and parallel epics touch it together, so an
    approved pull request conflicting at merge is routine, not rare: sprint-metrics
    #95 and #100 both did in one tick. Rebuilding it on main costs model time and
    no person, and the gates run again on what's rebuilt, so nothing lands
    unreviewed. Past MAX_REBUILDS of one pull request, a person decides.
    """
    number = card.number or 0
    head = (pull.get("head") or {}).get("sha", "")
    # Two approved stories that only added to the same place: keep both, as a
    # person would, rather than rebuilding approved work from scratch (#436).
    branch = (pull.get("head") or {}).get("ref") or branch_name(number, card.title)
    # Where it conflicts, and what landed there first, when keeping both found out (#436).
    files: list[str] = []
    landed = ""
    if keep_both is not None:
        try:
            kept = keep_both(repo, branch)
        except MergeConflict as exc:
            kept = None
            files, landed = list(exc.files), exc.landed
        except Exception as exc:  # noqa: BLE001
            kept = None
            sink.note(EventKind.NOTE, f"#{number} PR #{pull['number']}: {exc}"[:120], card=number)
        if kept is not None:
            issues.comment(
                repo,
                pull["number"],
                "**Brought up to date with `main`, keeping both sides.** "
                + (
                    "Other work had added to the same place in "
                    + ", ".join(f"`{f}`" for f in kept)
                    + ". Both additions are kept, main's first. "
                    if kept
                    else ""
                )
                + "This story's own changes are as approved; CI runs again on the new head.",
            )
            sink.note(
                EventKind.NOTE,
                f"#{number} PR #{pull['number']} kept both sides of {', '.join(kept) or 'main'}"[
                    :120
                ],
                card=number,
            )
            result.updating.append((number, pull["number"]))
            return
    try:
        comments = issues.comments(repo, pull["number"])
    except Exception:  # noqa: BLE001
        comments = []
    rebuilt = sum(1 for c in comments if "<!-- crew:rebuild head=" in (c.get("body") or ""))
    if rebuilt >= MAX_REBUILDS:
        _block_on_conflict(board, issues, sink, card, repo=repo, pull=pull["number"])
        result.conflicted.append((number, pull["number"]))
        return
    where = f" in {', '.join(files)}" if files else ""
    if files:
        sink.note(
            EventKind.NOTE,
            f"#{number} PR #{pull['number']} conflicts in {', '.join(files)}"
            + (f" after {landed}" if landed else ""),
            card=number,
            files=files,
            landed=landed,
        )
    move_card(
        board,
        sink,
        item_id=card.item_id,
        to=IN_PROGRESS,
        by=None,
        card=number,
        frm=MERGING,
        summary=f"approved PR #{pull['number']} conflicts with main — returned for a rebuild",
    )
    issues.comment(
        repo,
        pull["number"],
        f"{REBUILD_MARKER.format(head=head)}\n**Conflicts with `main`{where}, returned for a "
        "rebuild.** This was approved, and other work has since merged into the same lines"
        + (f": the last to land there was *{landed}*" if landed else "")
        + (
            ". It isn't only an addition on both sides, so keeping both wasn't safe"
            if files
            else ""
        )
        + ". The crew rebuilds it on current `main` and it goes through review and QA again "
        f"(rebuild {rebuilt + 1} of {MAX_REBUILDS} before a person is asked).",
    )
    result.rebuilding.append((number, pull["number"]))


def _block_on_conflict(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    card: Card,
    *,
    repo: str,
    pull: int,
) -> None:
    """Two changes disagree, so landing needs a decision the crew should not make.

    No role made that happen, so nothing is claimed: the card keeps the role
    whose work is stuck, the ruling #22 made.
    """
    number = card.number or 0
    move_card(
        board,
        sink,
        item_id=card.item_id,
        to=BLOCKED,
        by=None,
        card=number,
        frm=MERGING,
        summary=f"merge conflict on PR #{pull}",
        kind=EventKind.CARD_BLOCKED,
    )
    artifacts.label(issues, sink, repo=repo, number=number, by=None, add=["blocked", "needs:human"])
    artifacts.comment(
        issues,
        sink,
        repo=repo,
        number=number,
        body=f"**Blocked — merge conflict.** PR #{pull} and `main` have both "
        "changed the same code, so landing it needs a decision the crew should "
        "not make on its own.\n\n"
        "Resolve the conflict on the branch, or close the pull request and let "
        "the story be re-delivered from current `main`.",
        by=None,
    )


def keeping_both(ws) -> Callable[[str, str], list[str] | None]:
    """`keep_both` for `merge_approved`, on a fresh workspace of the pull request's repo.

    A fresh one: the land phase must never move a worktree delivery has open.
    """
    from crew_org.git_ops import MergeConflict, Workspace, last_landed  # noqa: PLC0415

    def keep_both(repo: str, branch: str) -> list[str] | None:
        work = Workspace(ws.owner, repo, ws._token, ws.identity)
        try:
            work.open(branch, resume=True)
            if not work.resumed:
                return None
            try:
                kept = work.catch_up_keeping_both()
            except MergeConflict as exc:
                # Raised on with what the rebuild should say: the files, and the
                # story that landed in them first (#436).
                exc.landed = last_landed(work.path, exc.files) if work.path else ""
                raise
            work.push()
            return kept
        finally:
            work.close()

    return keep_both
