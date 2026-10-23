"""The deliver phase's Developer: a session of tool calls, run to its end (crew#778, ADR 0032).

One story at a time (P1). Its worktree and branch are opened as they always were;
then its workspace container is held, set up and started (decision 4b), and a
session runs in it (Stage C). How the session ends decides the rest:

- **done:** the gates read the branch (`session_end.route`, Stage D part 1), in the
  same container. Passed, it's pushed and opened for review as before; findings start
  a new session from the branch as it stands (P2).
- **blocked,** or a stop: the route takes the story back to the Business Analyst, or
  to the Sponsor for a choice (decision 9).
- **interrupted:** a server that's down stops the tick, as it always has. Anything
  else, a failed call or a lost container, is retried next pass (`SessionInterrupted`).

Every session in one delivery shares one 3-hour budget of model time, so a loop
between a session and the gates can't run unbounded (the operator, on crew#778). The
gates sending back the same findings 3 rounds in a row ends it sooner, as a stop
(`gate_repeat`, the Sponsor on crew#790): no session can count those rounds, since
each starts a new one. A return from review or QA is a new delivery, and a new
session (P2).

**Checkpoints (P3):** after each green run the branch is committed, never pushed: a
push to an open pull request's branch would move the head under review. The clone's
branch outlives the worktree, so a crashed or stopped tick leaves the checkpoint for
the next one, which resumes from it. Any other ending drops it, so stale work is never
resumed.

**Kept work:** a delivery that leaves for a rewrite, blocked, stopped or ruled
against, first pushes its work to a branch of its own, `crew/kept/<n>-<when>`, with
any edits since the last checkpoint as one more commit, and no pull request. Its note
links it (`session_end.Leaving`). sprint-metrics#662 lost two hours of green work
without it. A push that fails is handled as any failed push, and the clone keeps the
work. Resuming from kept work is for later (the Sponsor, crew#778): a fresh delivery
starts from main.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from crew_org import profiles, sendbacks
from crew_org.crews import epic_rows
from crew_org.events import CrewEvent, EventKind, EventSink, attributed
from crew_org.flows import gates, role_tasks
from crew_org.flows.conclusion import story_diagram, story_rows
from crew_org.flows.delivery import (
    DeliveryOutcome,
    coverage_for,
    previous_fate,
    prior_verdicts,
)
from crew_org.flows.epic_notes import story_notes
from crew_org.git_ops import (
    CHECKPOINT,
    DONE_TRAILER,
    GitError,
    MergeConflict,
    Workspace,
    branch_name,
    kept_name,
)
from crew_org.llm import reraise_if_down
from crew_org.project import ProjectRecordError, read_record
from crew_org.rules import Rule
from crew_org.session import conversation
from crew_org.tools import reproduction
from crew_org.tools.container import Held, own_services
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient
from crew_org.tools.regression import merged_base
from crew_org.tools.workspace import CheckResult, CommandResult, check, shown_test

if TYPE_CHECKING:
    from crew_org.flows import session_end

# One delivery's model time, shared by its sessions (decision 3; the operator, crew#778).
BACKSTOP_S = conversation.BACKSTOP_S
# The gates' same findings this many rounds in a row end the delivery: the in-session
# no-progress stop's count, which a new session starts again (the Sponsor, crew#790).
# sprint-metrics#671's gates refused five times with one finding, a workspace with no
# network, and only the backstop bounded it.
GATE_REPEATS = 3
# Where a story goes when the route itself fails: blocked for a person, by delivery.
UNROUTED = "unrouted"
# Where it goes when the push that keeps its work fails: blocked as any failed push is.
UNKEPT = "unkept"
# The commit that keeps a stopped session's edits after its last green run.
STOPPED = "stopped({number}): edits since the last green run, not run green"


class SessionInterrupted(RuntimeError):
    """A session ended by its call layer or its container, not by the story (P3).

    `service` names which, for the interruption path delivery already has: retried
    next pass from the last checkpoint, and blocked only after a streak.
    """

    def __init__(self, service: str, said: str) -> None:
        super().__init__(f"{service}: {said}")
        self.service = service


@dataclass
class Delivered:
    """Where a delivery's sessions took the story: the route's, with the last `done`."""

    to: str
    said: dict[str, Any]
    verdict: Any = None
    rule: str = ""
    # The pull request's sections from the gates (`Route.sections`).
    sections: list[str] = field(default_factory=list)
    # When the route couldn't be settled: how the session ended, why, and its evidence.
    how: str = ""
    error: str = ""
    evidence: str = ""


def deliver_by_session(
    card: Card,
    *,
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    ws: Workspace,
    sprint: str,
    repo: str,
    default_branch: str,
    rework: bool = False,
) -> DeliveryOutcome:
    """Take one story to a pull request by Developer sessions, or to where they end it.

    A role's Task goes the same way, worked by its role's session in the Developer's
    place, with the Developer's tools (crew#839).
    """
    from crew_org.flows import session_end  # noqa: PLC0415

    number = card.number or 0
    role = role_tasks.role_of(card)
    # Who the work is the crew's record of: a Task's own role, or the Developer (crew#839).
    by = role.name if role else "Developer"
    outcome = DeliveryOutcome(card=number)
    body = issues.get(repo, number).get("body") or ""
    story_text = f"{card.title}\n\n{body}"
    branch = branch_name(number, card.title)
    outcome.branch = branch
    live = issues.pull_for_branch(repo, branch, known=card.open_pull_on(branch))
    if live is not None and not rework:
        # Before any session: re-delivering would overwrite the diff a review is
        # reading, so nothing is worth building. Checked again before the push.
        outcome.blocked_rule = Rule.OPEN_PULL_REQUEST
        return _open_pull(outcome, live)
    coverage = coverage_for(ws, repo, sink, number)

    checkpoint = ws.checkpoint_on(branch)
    worktree = ws.open(branch, resume=live is not None, checkpoint=checkpoint is not None)
    rebuilt = False
    if ws.resumed:
        try:
            ws.catch_up()
        except MergeConflict as conflict:
            # A checkpoint's or a returned story's branch, either way being rewritten:
            # rebuilt on current main, as today's rework is (#158).
            worktree = ws.open(branch, resume=False)
            checkpoint, rebuilt = None, True
            sink.note(
                EventKind.NOTE,
                f"#{number} rebuilt from main: its branch conflicted in "
                + (", ".join(conflict.files) or "unknown files"),
                rebuilt=True,
                repo=repo,
                card=number,
                paths=list(conflict.files),
                rule="delivery.conflict_with_main",
            )
    try:
        record = read_record(worktree)
    except ProjectRecordError as exc:
        outcome.blocked_rule = Rule.RECORD_UNREADABLE
        outcome.blocked_reason = f"the project's record can't be read: {exc}"
        outcome.ended = True
        return outcome
    profiles.set_project(record)
    note, rows = story_notes(issues, card, repo), story_rows(issues, card, repo)
    diagram = story_diagram(issues, card, repo)
    story = story_section(
        f"{role_tasks.task_start(role, _today())}\n\n{story_text}" if role else story_text,
        note,
        rows,
        diagram,
    )
    # The story's part of the start, measured by what it's made of (the operator, crew#778).
    measured = {
        "card": len(story_text),
        "decided": len(rows or ""),
        "note": len(note or ""),
        "diagram": len(diagram),
    }
    if checkpoint is not None:
        instead = resumed_start(checkpoint)
    elif live is not None and not rebuilt:
        instead = returned_start(issues, repo, number, branch, worktree, known=card)
    else:
        instead = None
    # After a catch-up, the branch's base is main as it is now.
    since = _merge_base(worktree) or "origin/HEAD"
    sink.emit(
        CrewEvent(
            kind=EventKind.AGENT_STARTED,
            role=by,
            card=number,
            summary=branch,
            detail=_task_detail(role),
        )
    )

    unfit = conversation.unfit(worktree)
    if unfit:
        return _unready(outcome, Rule.WORKSPACE_UNREADY, unfit)
    held = Held(worktree, f"{repo}-{number}")
    try:
        started = held.start()
        if not started.ok and started.code == 2:
            # The project's own definition can't be run: the design's to fix, said as
            # a refused design is today, so it's found as one (crew#690).
            return _unready(
                outcome, Rule.WORKSPACE_UNREADY, f"$ design\n(exit 2)\n{started.output}"
            )
        if not started.ok:
            # The engine down, or the image unreachable: not the story's.
            raise SessionInterrupted("the container engine", started.output[-300:])
        setup = held.setup()
        if setup is not None and not setup.ok:
            # Once more: a fetch can fail for a moment.
            setup = held.setup()
        if setup is not None and not setup.ok:
            # As a failed setup is reported today, so a design's fault is still found
            # as one (crew#690).
            ran = f"$ {setup.command}\n(exit {setup.code})\n{setup.output[-1500:]}"
            return _unready(outcome, Rule.WORKSPACE_UNREADY, ran)
        reached = _sessions(
            card,
            story=story,
            story_text=story_text,
            instead=instead,
            worktree=worktree,
            held=held,
            ws=ws,
            coverage=coverage,
            record=record,
            since=since,
            board=board,
            issues=issues,
            sink=sink,
            sprint=sprint,
            repo=repo,
            outcome=outcome,
            route=session_end.route,
            measured=measured,
            role=role.spec if role else conversation.ROLE,
            task=role,
        )
    finally:
        held.stop()
    return _land(
        reached,
        card,
        outcome=outcome,
        ws=ws,
        issues=issues,
        sink=sink,
        repo=repo,
        default_branch=default_branch,
        live=live,
        rework=rework,
        rebuilt=rebuilt,
        since=since,
        worktree=worktree,
        task=role,
    )


def own_checks(worktree: Path, name: str, since: str) -> Any:
    """The whole checks of a workspace Task, in a container of its own with the services
    and setup its branch declares (the Sponsor, 2026-10-23, crew#839).

    The session's container has the default branch's services, so on
    mqucifer/sprint-metrics#739 the Postgres DevOps declared was never there. This one
    has the branch's, held to the default branch's rules (`own_services`), and runs the
    branch's setup with the network, as a returned story's session already does. Setup
    gets the start's second try. A container that won't start, or a setup that fails,
    is the checks' failure, for the Task to answer.
    """
    own = Held(worktree, name, declared=own_services(worktree))
    try:
        started = own.start()
        if not started.ok:
            failed = CommandResult(started.command, started.code or 1, started.output)
            return CheckResult(results=[failed], services_down=dict(own.down))
        setup = own.setup()
        if setup is not None and not setup.ok:
            setup = own.setup()
        if setup is not None and not setup.ok:
            return CheckResult(
                results=[CommandResult(setup.command, setup.code, setup.output)],
                services_down=dict(own.down),
            )
        merged = merged_base(worktree)
        paths = reproduction._changed(worktree, merged.sha) if merged is not None else []
        caused = gates.caused_by(worktree, merged, paths) if merged is not None else None
        # Each hung test's file, set aside, with what was seen of it: by file, since the
        # test that hung waits on state an earlier test left (the operator, crew#839). On
        # sprint-metrics' test copy setting the waiting test aside hung the next, 13 times.
        deselected: dict[str, str] = {}
        aside: dict[str, str] = {}
        rounds = 0
        while True:
            checked = check(worktree, held=own, since=since, deselect=deselected)
            if own.lost:
                raise SessionInterrupted("the workspace container", "lost while the gates ran")
            # A hung file the branch didn't cause is set aside and the rest run again, so
            # what they turn on can be told apart. One it caused, or one past the bound,
            # is the gate's finding that the checks timed out.
            new = {t: w for t, w in checked.hung.items() if t.partition("::")[0] not in deselected}
            files = {t.partition("::")[0] for t in new}
            if (
                not new
                or caused is None
                or any(caused(f) for f in files)
                or len(deselected) + len(files) > HANG_ROUNDS
            ):
                checked.hang_rounds, checked.aside = rounds, dict(aside)
                handed = sorted({*checked.failed, *aside.values()})
                if merged is not None and handed and not checked.hung:
                    # Only what the workspace turned on is the Developer's: how each did
                    # in the default branch's own workspace (the review of crew#877).
                    checked.on_main = reproduction.on_the_default_branch(
                        worktree, merged, handed, f"{name}-main"
                    )
                return checked
            for test, where in sorted(new.items()):
                said = f"in `{shown_test(test)}`, waiting in {where}"
                if deselected:
                    # Pollution can cross files: said plainly, not looped to the bound.
                    said += (
                        f"; it hung again here after `{list(deselected)[-1]}` was set aside, "
                        "so the test leaving the state may be in an earlier file"
                    )
                deselected[test.partition("::")[0]] = said
                aside[test.partition("::")[0]] = shown_test(test)
            rounds += 1
    finally:
        own.stop()


# How many hung tests' files a workspace Task's gate sets aside before it gives up
# (crew#839). Started high, 3x the most seen (4 hangs on sprint-metrics' test copy), to be
# lowered from each gate's `hang_rounds` (the Sponsor, 2026-10-18: start limits high).
HANG_ROUNDS = 12


def _task_detail(task: role_tasks.TaskRole | None) -> dict[str, Any]:
    """A Task's work events name its role, so they're placed by its card, not as a review
    of a pull request (delivery-history's DevOps Engineer)."""
    return {"task_role": task.name} if task else {}


def _sessions(
    card: Card,
    *,
    story: str,
    story_text: str,
    instead: str | None,
    worktree: Path,
    held: Held,
    ws: Workspace,
    coverage: Any,
    record: Any,
    since: str,
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    sprint: str,
    repo: str,
    outcome: DeliveryOutcome,
    route: Any,
    measured: dict[str, int],
    role: str = conversation.ROLE,
    task: role_tasks.TaskRole | None = None,
) -> Delivered:
    """Sessions until one ends somewhere other than back in a session, within the budget."""
    from crew_org.flows import session_end  # noqa: PLC0415

    number = card.number or 0
    spent = 0.0
    rounds = 0
    # The gates' findings at each `done` they sent back, for a note if it leaves.
    found: list[list[Any]] = []

    def keep() -> session_end.Kept | None:
        return _keep(ws, worktree, since, number, sink)

    def run_checks(at: Path) -> Any:
        merged = merged_base(at)
        paths = reproduction._changed(at, merged.sha) if merged is not None else []
        if role_tasks.changes_workspace(task, at, paths):
            return own_checks(at, f"{repo}-{number}-done", since)
        checked = check(at, held=held, since=since)
        if held.lost:
            # A gate's finding would blame the branch for the infrastructure.
            raise SessionInterrupted("the workspace container", "lost while the gates ran")
        return checked

    while True:
        rounds += 1
        session = conversation.session(
            worktree,
            story,
            held,
            held.tools,
            coverage=coverage,
            instead_of_hints=instead,
            after_run=checkpointed(ws, sink, number),
            base=since,
            round=rounds,
            story_parts=measured,
            role=role,
            specs=role_tasks.done_specs(task) if task else None,
            scope=task.scope if task else (),
        )
        session.backstop_s = max(BACKSTOP_S - spent, 0.0)
        ending = attributed(
            session.run, card=number, repo=repo, sprint=sprint, attempt=rounds, purpose="session"
        )()
        spent += session.model_s
        outcome.attempts += 1
        if ending.how == "interrupted":
            if ending.error is not None:
                reraise_if_down(ending.error)
            service = (
                "the workspace container" if ending.why == "workspace_gone" else "a model call"
            )
            raise SessionInterrupted(service, str(ending.said.get("error") or ending.why))
        if ending.how == "done":
            # Always a commit, so the next delivery can read what this `done` said, and
            # resume from it if the gates don't finish.
            try:
                ws.commit(
                    f"feat({number}): {card.title}\n\n{session_end.last_done(ending.said)}\n"
                    f"Closes #{number}\n\n{DONE_TRAILER}",
                    allow_empty=True,
                )
            except GitError as exc:
                raise SessionInterrupted("git", f"the commit at `done` failed: {exc}") from exc
        leaving = session_end.Leaving(keep, spent, rounds, list(found))
        routed = _route(
            route,
            ending,
            card=card,
            repo=repo,
            worktree=worktree,
            story=story_text,
            record=record,
            board=board,
            issues=issues,
            sink=sink,
            run_checks=run_checks,
            leaving=leaving,
        )
        stopped = None
        if routed.to == session_end.SESSION:
            found.append(list(getattr(routed.verdict, "findings", None) or []))
            stopped = _stopped_between(found, spent, ending)
        if stopped is not None:
            # A stop, as one inside a session is: back to the Business Analyst, with the
            # work kept.
            leaving = session_end.Leaving(keep, spent, rounds, list(found))
            routed = _route(
                route,
                stopped,
                card=card,
                repo=repo,
                worktree=worktree,
                story=story_text,
                record=record,
                board=board,
                issues=issues,
                sink=sink,
                run_checks=run_checks,
                leaving=leaving,
            )
        if routed.to != session_end.SESSION:
            return Delivered(
                routed.to,
                ending.said,
                routed.verdict,
                routed.rule,
                sections=list(getattr(routed, "sections", None) or []),
                how=ending.how,
                error=getattr(routed, "error", ""),
                evidence=(
                    session_end.last_done(ending.said)
                    if ending.how == "done"
                    else session_end.evidence(ending, leaving, leaving.kept, story_text)
                ),
            )
        # Back to a session: the gates' send-back, in the round `session.started` names.
        sendbacks.record(
            sink,
            sendbacks.CONTRACT_REPAIR
            if routed.rule == sendbacks.CONTRACT_REPAIR.rule
            else sendbacks.GATE_FINDINGS,
            card=number,
            repo=repo,
            round_=rounds,
            findings=len(found[-1]),
        )
        instead = routed.start


def _stopped_between(found: list[list[Any]], spent: float, ending: Any) -> Any:
    """The stop the gates' returns reach between sessions, or None for the next session.

    The gates' same findings `GATE_REPEATS` rounds in a row, or the budget spent with
    their findings still open. Each ends as a stopped session does, `blocked` of kind
    `stop`, with the last session's evidence.
    """
    repeats = _repeats(found)
    if repeats >= GATE_REPEATS:
        missing = f"the gates' same findings, sent back {repeats} times in a row"
        stop, evidence = "gate_repeat", {**(ending.evidence or {}), "stop": {"repeats": repeats}}
    elif spent >= BACKSTOP_S:
        missing = (
            f"the gates' findings, still open after {round(spent / 60)} minutes of model "
            "time, the backstop"
        )
        stop, evidence = "backstop", ending.evidence
    else:
        return None
    return SimpleNamespace(
        how="blocked",
        said={"missing": missing, "kind": "stop"},
        stop=stop,
        why="",
        evidence=evidence,
        error=None,
    )


def _repeats(found: list[list[Any]]) -> int:
    """How many gate rounds in a row, to the last, sent back the same findings.

    Compared by each finding's rule and its first lines (`session_end.first_lines`), so
    a test run's timing on its last line doesn't tell two rounds apart. A round with no
    findings is never a repeat.
    """
    from crew_org.flows import session_end  # noqa: PLC0415

    def same(findings: list[Any]) -> list[tuple[str, str]]:
        return sorted((f.rule, session_end.first_lines(f.says)) for f in findings)

    if not found or not found[-1]:
        return 0
    last, repeats = same(found[-1]), 0
    for findings in reversed(found):
        if same(findings) != last:
            break
        repeats += 1
    return repeats


def _keep(
    ws: Workspace, worktree: Path, since: str, number: int, sink: EventSink
) -> session_end.Kept | None:
    """Push the delivery's work to a branch of its own, before its note is written.

    A push GitHub's side failed is an interruption, as any is. Any other failure is
    `NotKept`, which delivery blocks for a person as it does any failed push.
    """
    from crew_org.flows import session_end  # noqa: PLC0415
    from crew_org.tools.remote_errors import transient_remote  # noqa: PLC0415

    name = kept_name(number, datetime.now(UTC))
    head = _git(worktree, "rev-parse", "HEAD")
    try:
        sha = ws.keep(name, base=since, message=STOPPED.format(number=number))
    except Exception as exc:  # noqa: BLE001 - every failure has a handled ending
        if transient_remote(exc):
            raise
        raise session_end.NotKept(name, str(exc)[:300]) from exc
    if sha is None:
        return None
    sink.note(
        EventKind.NOTE,
        f"#{number} its work is kept on {name}, with no pull request",
        card=number,
        kept=name,
    )
    return session_end.Kept(
        name,
        f"https://github.com/{ws.owner}/{ws.repo}/compare/{since}...{name}",
        files=_changes(worktree, since, sha),
        tests=gates.merged_tests_changed(worktree),
        unproven=sha != head,
    )


def _route(route: Any, ending: Any, **kw: Any) -> Any:
    """Where the ending goes; or, when that can't be settled, why, for a person.

    A failure that passes, or a lost container, is an interruption, resumed next pass.
    Anything else, the gates raising or the route's moves failing partway, blocks the
    card for a person with the session's evidence: wherever the moves stopped, the
    card ends blocked, labelled and said why (the Sponsor, on crew#778).
    """
    from crew_org.flows import session_end  # noqa: PLC0415
    from crew_org.tools.remote_errors import transient_remote, where  # noqa: PLC0415

    try:
        return route(ending, **kw)
    except SessionInterrupted:
        raise
    except session_end.NotKept as exc:
        return SimpleNamespace(
            to=UNKEPT, rule=Rule.PUSH_FAILED, start="", verdict=None, error=str(exc)
        )
    except Exception as exc:  # noqa: BLE001 - every failure has a handled ending
        reraise_if_down(exc)
        if transient_remote(exc):
            raise
        return SimpleNamespace(
            to=UNROUTED,
            rule=Rule.ROUTE_FAILED,
            start="",
            verdict=None,
            error=f"{type(exc).__name__}: {str(exc)[:300]} at {where(exc)}",
        )


def _land(
    reached: Delivered,
    card: Card,
    *,
    outcome: DeliveryOutcome,
    ws: Workspace,
    issues: IssueClient,
    sink: EventSink,
    repo: str,
    default_branch: str,
    live: dict[str, Any] | None,
    rework: bool,
    rebuilt: bool,
    since: str,
    worktree: Path,
    task: role_tasks.TaskRole | None = None,
) -> DeliveryOutcome:
    """Push and open for review, or for QA, as today; a story the route moved is done here."""
    from crew_org.flows import session_end  # noqa: PLC0415
    from crew_org.tools.remote_errors import transient_remote  # noqa: PLC0415

    number = card.number or 0
    by = task.name if task else "Developer"
    outcome.ended = True
    if reached.to == UNROUTED:
        # Delivery blocks it, labels it and says why, wherever the route's moves stopped.
        outcome.blocked_rule = Rule.ROUTE_FAILED
        outcome.blocked_reason = (
            f"its session ended `{reached.how}`, and where that sends the story couldn't be "
            f"settled: {reached.error}. It's blocked for a person, with the session's "
            f"evidence.\n\n{reached.evidence}"
        ).strip()
        outcome.rejected_diff = _diff(worktree, since)
        return outcome
    if reached.to == UNKEPT:
        # As any failed push: blocked for a person, the work kept in the clone, both its
        # checkpoint and the branch the push would have made.
        outcome.blocked_rule = Rule.PUSH_FAILED
        outcome.blocked_reason = (
            f"its session ended `{reached.how}`, and {reached.error}"[:400]
            + f". The work is kept in the crew's clone.\n\n{reached.evidence}"
        ).strip()
        outcome.ended = False
        outcome.rejected_diff = _diff(worktree, since)
        return outcome
    if reached.to not in (session_end.REVIEW, session_end.QA):
        # Back to refinement, or blocked for a person: the route has moved the card.
        outcome.routed = reached.to
        outcome.blocked_rule = reached.rule
        outcome.rejected_diff = _diff(worktree, since)
        return outcome
    _record_autofix(getattr(reached.verdict, "check", None), outcome, sink, number, task)
    try:
        # The gates ran the project's fixers on the branch, after the commit at `done`:
        # what they changed is part of what passed, and lands with it (crew#758).
        ws.commit(f"style({number}): what the project's fixers changed before the checks")
    except GitError as exc:
        raise SessionInterrupted("git", f"the fixers' commit failed: {exc}") from exc
    opened = live or issues.pull_for_branch(repo, outcome.branch or "", known=None)
    if opened is not None and not rework:
        outcome.ended = False  # the work is kept, and nothing is pushed over the review
        outcome.blocked_rule = Rule.OPEN_PULL_REQUEST
        return _open_pull(outcome, opened)
    try:
        # A rework's commits go on top of what the review read; a fresh branch or a
        # rebuild replaces the crew's own dead branch, with a lease.
        ws.push(force=not rework or rebuilt)
    except Exception as exc:  # noqa: BLE001
        if transient_remote(exc):
            raise
        outcome.blocked_rule = Rule.PUSH_FAILED
        outcome.blocked_reason = f"could not push `{outcome.branch}`: {exc}"[:400]
        # The work is only in the clone: kept, so it isn't lost with the block.
        outcome.ended = False
        return outcome
    summary = str(reached.said.get("summary") or card.title)
    if reached.to == session_end.QA:
        outcome.already_done = [
            SimpleNamespace(
                criterion=c.get("criterion"),
                code="the code as it stands",
                test=c.get("test"),
                ci_step=None,
            )
            for c in reached.said.get("criteria") or []
        ]
        sink.emit(
            CrewEvent(
                kind=EventKind.AGENT_FINISHED,
                role=by,
                card=number,
                summary=f"already done: {len(outcome.already_done)} criteria met by existing code",
                detail={"rule": "decision.delivery.outcome", **_task_detail(task)},
            )
        )
        return outcome
    if live is not None:
        outcome.pr = live["number"]
        _say_reworked(issues, sink, repo, number, live["number"], summary, reached, rebuilt, by)
    else:
        try:
            pr = issues.create_pull(
                repo,
                title=card.title,
                head=outcome.branch or "",
                base=default_branch,
                body=pr_body(card, reached, outcome, _changes(worktree, since), by=by),
            )
        except Exception as exc:  # noqa: BLE001 - pushed, so the clone's branch is kept
            if transient_remote(exc):
                raise
            outcome.blocked_rule = Rule.PULL_REQUEST_FAILED
            outcome.blocked_reason = f"pushed `{outcome.branch}`, and no pull request: {exc}"[:400]
            outcome.ended = False
            return outcome
        outcome.pr = pr["number"]
    _record_turned_on(issues, sink, repo, outcome.pr, worktree, reached, by)
    sink.emit(
        CrewEvent(
            kind=EventKind.AGENT_FINISHED,
            role=by,
            card=number,
            summary=f"PR #{outcome.pr}",
            detail=_task_detail(task),
        )
    )
    return outcome


def _record_turned_on(
    issues: IssueClient,
    sink: EventSink,
    repo: str,
    pull: int | None,
    worktree: Path,
    reached: Delivered,
    by: str,
) -> None:
    """The crew's record of what a workspace Task's merge files for the Developer, bound to
    the head just pushed, after every `done`, a rework's too (crew#839). A failure to write
    it is noted: with no record for the merged head, nothing is filed."""
    tests = getattr(reached.verdict, "for_developer", None) or {}
    if not tests or pull is None:
        return
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True
    ).stdout.strip()
    try:
        role_tasks.record_turned_on(
            issues, sink, repo=repo, pull=pull, head=head, tests=tests, by=by
        )
    except Exception as exc:  # noqa: BLE001 - the push and the pull request stand
        reraise_if_down(exc)
        sink.note(
            EventKind.NOTE, f"PR #{pull}: the tests it turns on weren't recorded: {exc}"[:300]
        )


def _say_reworked(
    issues: IssueClient,
    sink: EventSink,
    repo: str,
    number: int,
    pull: int,
    summary: str,
    reached: Delivered,
    rebuilt: bool,
    by: str = "Developer",
) -> None:
    """The rework, said on its pull request, as today. The push already updated it."""
    from crew_org.flows.artifacts import signed  # noqa: PLC0415
    from crew_org.flows.delivery import REWORK_MARKER  # noqa: PLC0415

    rebuilt_line = "Rebuilt from main, where other work had changed the same lines. "
    try:
        issues.comment(
            repo,
            pull,
            signed(
                f"{REWORK_MARKER}\n**Re-worked.** {summary}\n\n"
                + (rebuilt_line if rebuilt else "")
                + "Lint and the full test suite pass in the story's workspace."
                + "\n".join(reached.sections),
                by,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - a missing comment doesn't undo the push
        reraise_if_down(exc)
        sink.note(
            EventKind.NOTE,
            f"#{number} re-worked on PR #{pull}, and its comment failed: {exc}"[:300],
            card=number,
        )


def _today() -> str:
    """Today by the sprint clock, for a role's Task that dates what it writes (crew#839)."""
    from crew_org.clock import sprint_today  # noqa: PLC0415
    from crew_org.config import load_org  # noqa: PLC0415

    return sprint_today(load_org()).isoformat()


def pr_body(
    card: Card,
    reached: Delivered,
    outcome: DeliveryOutcome,
    changes: list[str],
    *,
    by: str = "Developer",
) -> str:
    """The pull request: what `done` said, how it was checked, and what the gates noted,
    signed by the role whose sessions did the work."""
    from crew_org.flows import session_end  # noqa: PLC0415
    from crew_org.flows.artifacts import signed  # noqa: PLC0415

    lines = [
        str(reached.said.get("summary") or ""),
        "",
        "## Verification",
        "",
        "Lint and the full test suite pass in the story's workspace container.",
        "",
        f"- {by} sessions: {outcome.attempts}",
        "",
        "## Changes",
        "",
        *(f"- {change}" for change in changes),
        "",
        session_end.last_done(reached.said).rstrip(),
    ]
    if outcome.autofixed or outcome.unsafe_fixes:
        lines += ["", "## Fixed by the project's own tools before the checks", ""]
        lines += [f"- `{path}`" for path in outcome.autofixed]
        lines += [
            f"- unsafe fix `{u.split()[0]}` at `{u.split()[1]}`, allowed by "
            "`workspace.UNSAFE_FIXES`"
            for u in outcome.unsafe_fixes
        ]
    lines += reached.sections
    lines += ["", f"Closes #{card.number}"]
    return signed("\n".join(lines), by)


def story_section(story_text: str, note: str, rows: str, diagram: str = "") -> str:
    """The story as the session starts with it: the card, then what its epic decided, and
    the Goal's target diagram (crew#858)."""
    parts = [story_text.strip()]
    decided = epic_rows.block(
        rows,
        "These are decided for the epic. Build to them: don't contradict one, and don't "
        "settle in code what the design note leaves open.",
        level=3,
    )
    if decided:
        parts.append(decided.strip())
    drawn = epic_rows.diagram(
        diagram, "What the system will be once the Goal is delivered: build to its parts.", level=3
    )
    if drawn:
        parts.append(drawn.strip())
    if note:
        parts.append(f"### The design note for this story's epic\n\n{note.strip()}")
    return "\n\n".join(parts)


def resumed_start(checkpoint: str) -> str:
    """What a session resuming an interrupted one is told, in place of the hints (P3)."""
    return (
        "## Where the last session stopped\n\n"
        "It was interrupted: a model call failed, its container was lost, or the tick "
        "stopped. Its work up to its last checkpoint, a green run or its `done`, is in the "
        "files below, committed. Anything it changed after that is gone. If the checkpoint "
        "is a `done`, the gates didn't finish with it: check the work and call `done` "
        "again. Its last checkpoint:\n\n" + _quoted(checkpoint)
    )


def returned_start(
    issues: IssueClient, repo: str, number: int, branch: str, worktree: Path, *, known: Card
) -> str:
    """What a session reworking a story review or QA sent back is told (P2)."""
    verdicts = prior_verdicts(issues, repo, number, branch, known=known.open_pull_on(branch))
    fate = previous_fate(issues, repo, branch)
    parts = [
        "## Why this is a new session\n\n"
        "Review or QA sent the story back. Its previous work is in the files below, on "
        "its branch: change what the findings ask, and leave the rest."
    ]
    if fate:
        parts.append(f"## What happened to it\n\n{fate}")
    if verdicts:
        parts.append(f"## What review or QA said\n\n{verdicts}")
    done = last_done_on(worktree, number)
    if done:
        parts.append(done)
    return "\n\n".join(parts)


def last_done_on(worktree: Path, number: int) -> str:
    """The last `done` committed on the branch, as the commit at `done` wrote it."""
    found = _git(worktree, "log", "-1", "--format=%B", "--fixed-strings", f"--grep=feat({number}):")
    body = found.split("\n", 1)[1] if "\n" in found else ""
    return body.replace(f"Closes #{number}", "").replace(DONE_TRAILER, "").strip()


def checkpointed(ws: Workspace, sink: EventSink, number: int) -> Any:
    """What runs after each `run`: a checkpoint commit when it passed (P3)."""

    def after_run(run: dict[str, Any]) -> None:
        if not run.get("ok"):
            return
        ran = "\n".join(
            f"$ {c.get('command')}\n(exit {c.get('code')}, {c.get('seconds')}s)"
            for c in run.get("commands") or []
        )
        if ws.commit(f"{CHECKPOINT}{number}): run {run.get('what')} passed\n\n{ran}"):
            sink.note(
                EventKind.NOTE,
                f"#{number} checkpoint after a green `run {run.get('what')}`",
                card=number,
                checkpoint=True,
            )

    return after_run


def _record_autofix(
    check: Any,
    outcome: DeliveryOutcome,
    sink: EventSink,
    number: int,
    task: role_tasks.TaskRole | None = None,
) -> None:
    """What the project's fixers changed in the gates' last checks, for review and QA (crew#758)."""
    fixed = list(getattr(check, "fixed", None) or [])
    reverted = list(getattr(check, "reverted", None) or [])
    unsafe = list(getattr(check, "unsafe", None) or [])
    if not (fixed or reverted or unsafe):
        return
    outcome.autofixed, outcome.unsafe_fixes = sorted(fixed), unsafe
    sink.emit(
        CrewEvent(
            kind=EventKind.AUTOFIX_APPLIED,
            role=task.name if task else "Developer",
            card=number,
            summary=f"#{number} autofix: {len(fixed)} fixed, {len(reverted)} undone, "
            f"{len(unsafe)} unsafe",
            detail={
                "fixed": fixed,
                "reverted": reverted,
                "unsafe": unsafe,
                "rules": sorted({u.split()[0] for u in unsafe}),
            },
        )
    )


def _open_pull(outcome: DeliveryOutcome, live: dict[str, Any]) -> DeliveryOutcome:
    outcome.blocked_reason = (
        f"PR #{live['number']} is still open on `{outcome.branch}`. Close it, or let that "
        "pull request finish; re-delivering would overwrite the diff it is reviewing."
    )
    return outcome


def _unready(outcome: DeliveryOutcome, rule: Rule, why: str) -> DeliveryOutcome:
    outcome.blocked_rule = rule
    outcome.blocked_reason = f"the story's workspace isn't ready: {why}"
    outcome.ended = True
    return outcome


def _merge_base(worktree: Path) -> str:
    return _git(worktree, "merge-base", "HEAD", "origin/HEAD")


def _changes(worktree: Path, since: str, until: str = "HEAD") -> list[str]:
    """Each file the branch changes against where it started, with how."""
    words = {"A": "new", "D": "deleted", "M": "changed", "R": "renamed"}
    listed = _git(worktree, "diff", "--name-status", since, until)
    found = []
    for line in listed.splitlines():
        status, _, path = line.partition("\t")
        found.append(f"`{path.replace(chr(9), '` from `')}` ({words.get(status[:1], status)})")
    return found


def _diff(worktree: Path, since: str) -> str | None:
    """Everything the sessions wrote, committed or not: the evidence a returned story keeps."""
    _git(worktree, "add", "-A")
    return _git(worktree, "diff", "--cached", since) or None


def _quoted(text: str) -> str:
    return "\n".join(f"> {line}" for line in text.strip().splitlines())


def _git(worktree: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(worktree), *args], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else ""
