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
work.

**Resuming kept work:** a delivery with no checkpoint and no returned pull request
starts from the card's newest kept branch, when the crew's own App pushed it and it
hasn't moved since (`kept_work`), brought up to date with main as a checkpoint is. Its
session is told where the last delivery stopped (`kept_start`). One that conflicts with
main, or can't be shown to be the crew's, isn't used: the delivery starts from main, and
the start says why (the Sponsor, 2026-10-24: "Resume first", crew#839). Each delivery
still gets its own 3 hours.
"""

from __future__ import annotations

import re
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
    sponsor_review,
)
from crew_org.flows.epic_notes import story_notes
from crew_org.flows.merge import returned_for_conflict
from crew_org.flows.qa_tasks import disputable as qa_disputable
from crew_org.flows.qa_tasks import dispute_line, dispute_specs, place_reference, reference_ref
from crew_org.git_ops import (
    CHECKPOINT,
    DONE_TRAILER,
    GitError,
    MergeConflict,
    Workspace,
    beyond_the_merge,
    branch_name,
    conflicts_in,
    kept_name,
)
from crew_org.llm import reraise_if_down
from crew_org.project import ProjectRecordError, read_record
from crew_org.rules import Rule
from crew_org.session import conversation, skills
from crew_org.tools import reproduction
from crew_org.tools.container import Held, own_services
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient
from crew_org.tools.regression import merged_base
from crew_org.tools.workspace import CheckResult, CommandResult, check, shown_test
from crew_org.work_types import TASK

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
    sponsor: str | None = None,
) -> DeliveryOutcome:
    """Take one story to a pull request by Developer sessions, or to where they end it.

    A role's Task goes the same way, worked by its role's session in the Developer's
    place, with the Developer's tools (crew#839). `sponsor` is `GITHUB_SPONSOR`, as the
    merge reads it: a returned story is told their change request first (crew#924).
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
    # A checkpoint or a returned story's branch is newer than any kept work.
    kept, unused = (
        kept_work(ws, issues, sink, repo, number)
        if checkpoint is None and live is None
        else (None, "")
    )
    worktree = ws.open(
        branch,
        resume=live is not None,
        checkpoint=checkpoint is not None,
        kept=kept.sha if kept else None,
    )
    rebuilt = False
    conflicted: list[str] = []
    earlier = ""
    tried = ""
    if ws.resumed:
        try:
            ws.catch_up()
        except MergeConflict as conflict:
            # A returned story's last `done`, read before the rebuild drops it: what it
            # tried, beside the findings it was sent back with (crew#919).
            earlier = last_done_on(worktree, number) if live is not None else ""
            if live is not None and role is None and returned_for_conflict(issues, repo, live):
                # Approved, and returned only for conflicting with main: its conflicts
                # are resolved, and it's rebuilt only if that fails (crew#933).
                resolution = resolve(
                    card,
                    ws=ws,
                    worktree=worktree,
                    story_text=story_text,
                    earlier=earlier,
                    issues=issues,
                    sink=sink,
                    sprint=sprint,
                    repo=repo,
                    outcome=outcome,
                )
                if resolution.reached is not None:
                    return _land(
                        resolution.reached,
                        card,
                        outcome=outcome,
                        ws=ws,
                        issues=issues,
                        sink=sink,
                        repo=repo,
                        default_branch=default_branch,
                        live=live,
                        rework=True,
                        rebuilt=False,
                        since=resolution.approved,
                        worktree=worktree,
                        resolution=resolution,
                    )
                tried = resolution_failed(resolution)
            # A checkpoint's or a returned story's branch, either way being rewritten:
            # rebuilt on current main, as today's rework is (#158).
            worktree = ws.open(branch, resume=False)
            checkpoint, rebuilt, conflicted = None, True, list(conflict.files)
            if kept is not None:
                unused = not_resumed(
                    kept,
                    "it conflicts with main in "
                    + (", ".join(f"`{f}`" for f in conflict.files) or "unknown files"),
                )
                kept = None
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
    marked = role_tasks.marked(body) if role else None
    project = role_tasks.project_of(card.labels)
    if role is not None and role.key == role_tasks.QA.key and repo == role_tasks.SUITE and project:
        # The project QA's test is for, to read beside its folder (crew#896).
        place_reference(ws, worktree, project, reference_ref(body))
    try:
        record = read_record(worktree)
    except ProjectRecordError as exc:
        outcome.blocked_rule = Rule.RECORD_UNREADABLE
        outcome.blocked_reason = f"the project's record can't be read: {exc}"
        outcome.ended = True
        return outcome
    profiles.set_project(record)
    offered = skills.for_task(role, body, worktree, _branch_paths(worktree)) if role else []
    note, rows = story_notes(issues, card, repo), story_rows(issues, card, repo)
    diagram = story_diagram(issues, card, repo)
    story = story_section(
        (
            f"{role_tasks.task_start(role, _today(), offered, repo)}\n\n{story_text}"
            if role
            else story_text
        ),
        note,
        rows,
        diagram,
    )
    if unused:
        story += "\n\n" + unused
    if tried:
        story += "\n\n" + tried
    # The story's part of the start, measured by what it's made of (the operator, crew#778).
    measured = {
        "card": len(story_text),
        "decided": len(rows or ""),
        "note": len(note or ""),
        "diagram": len(diagram),
    }
    # After a catch-up, the branch's base is main as it is now.
    since = _merge_base(worktree) or "origin/HEAD"
    findings: int | None = None
    asked: int | None = None
    if checkpoint is not None:
        instead = resumed_start(checkpoint)
    elif live is not None:
        # Rebuilt or not, the findings it was sent back with: without them each session
        # sent the same diff back (crew#919).
        instead, findings, asked = returned_start(
            issues,
            repo,
            number,
            branch,
            worktree,
            known=card,
            rebuilt=conflicted if rebuilt else None,
            earlier=earlier,
            sponsor=role_tasks.the_sponsor(sponsor),
        )
    elif kept is not None:
        instead = kept_start(issues, repo, number, kept, worktree, since, link=ws)
    else:
        instead = None
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
            ran = f"$ {setup.command}\n(exit {setup.code})\n{setup.output[-1500:]}"
            if not _own_workspace(role, worktree):
                # As a failed setup is reported today, so a design's fault is still found
                # as one (crew#690).
                return _unready(outcome, Rule.WORKSPACE_UNREADY, ran)
            # The setup is the Task's own work, as at `done`: told to its session, which
            # still has to pass it there (crew#839, mqucifer/sprint-metrics#739).
            story += "\n\n" + session_end.setup_failed_at_start(ran)
            sink.note(
                EventKind.NOTE,
                f"#{number}'s own setup failed at the start: told to its session",
                repo=repo,
                card=number,
                rule="check.role_tasks.setup_failed_at_start",
            )
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
            findings=findings,
            sponsor=asked,
            returned=live is not None,
            rebuilt=rebuilt,
            role=role.spec if role else conversation.ROLE,
            task=role,
            skills=offered,
            kind=marked.kind if marked else "",
            disputable=role is None and qa_disputable(issues, repo, live),
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


def _branch_paths(worktree: Path) -> list[str]:
    """The paths the branch changes against the default branch it left."""
    merged = merged_base(worktree)
    return reproduction._changed(worktree, merged.sha) if merged is not None else []


def _own_workspace(task: role_tasks.TaskRole | None, worktree: Path) -> bool:
    """Is the worktree's workspace file the Task's own work: a DevOps Task whose branch
    changes it, or the compose file it names (`role_tasks.owns_workspace_file`)?"""
    return role_tasks.owns_workspace_file(task, worktree, _branch_paths(worktree))


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
    findings: int | None = None,
    sponsor: int | None = None,
    returned: bool = False,
    rebuilt: bool = False,
    role: str = conversation.ROLE,
    task: role_tasks.TaskRole | None = None,
    skills: list[Any] | None = None,
    kind: str = "",
    disputable: bool = False,
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
            # The returned start's findings are the first round's; later rounds start
            # from the gates' (crew#919).
            findings=findings if rounds == 1 else None,
            sponsor=sponsor if rounds == 1 else None,
            returned=returned,
            rebuilt=rebuilt,
            role=role,
            step=task.step if task else None,
            specs=(
                role_tasks.done_specs(task, skills or (), repo, kind)
                if task
                else dispute_specs()
                if disputable
                else None
            ),
            scope=task.scope_for(repo) if task else (),
            skills=tuple(s.name for s in skills or ()),
            work="Task" if task else "story",
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
    resolution: Resolution | None = None,
) -> DeliveryOutcome:
    """Push and open for review, or for QA, as today; a story the route moved is done here.

    A `resolution`'s push is said with its marker, for review and QA (crew#933)."""
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
            for c in gates.entries(reached.said, "criteria")
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
    if live is not None and resolution is not None:
        outcome.pr = live["number"]
        _say_resolved(issues, sink, repo, card, live["number"], worktree, resolution, outcome)
    elif live is not None:
        outcome.pr = live["number"]
        _say_reworked(issues, sink, repo, number, live["number"], summary, reached, rebuilt, by)
    else:
        try:
            pr = issues.create_pull(
                repo,
                title=card.title,
                head=outcome.branch or "",
                base=default_branch,
                body=pr_body(
                    card,
                    reached,
                    outcome,
                    _changes(worktree, since),
                    by=by,
                    # QA's changed test runs against the disputed pull request (crew#945).
                    marks=dispute_line(issues, repo, number)
                    if task is not None and task.key == role_tasks.QA.key
                    else "",
                ),
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


@dataclass
class Resolution:
    """A session's resolution of an approved branch's conflicts with main (crew#933).

    `reached` is where its gates sent it: review, ready to land. None when it failed,
    and `why` says how, for the rebuild that follows.
    """

    approved: str
    files: list[str]
    reached: Delivered | None = None
    why: str = ""
    calls: int = 0
    model_s: float = 0.0


# How much of the story's approved diff a resolution's start shows. The rest is read
# with `git diff`, as the start says.
APPROVED_DIFF_CHARS = 20_000


def resolve(
    card: Card,
    *,
    ws: Workspace,
    worktree: Path,
    story_text: str,
    earlier: str,
    issues: IssueClient,
    sink: EventSink,
    sprint: str,
    repo: str,
    outcome: DeliveryOutcome,
) -> Resolution:
    """Merge main into an approved branch and give its conflicts alone to one session.

    sprint-metrics#809 was rebuilt from scratch for a shared line in four files: 135
    calls and 95 minutes, then review and QA again (crew#933). Here the merge's conflicts
    stay in the files, and the session may edit only those files. Its `done` commits the
    merge, and the gates run on the branch as at any `done`. Anything else, an ending
    that isn't `done`, a gate's finding, a file it changed beyond the conflicts, fails
    it: the caller rebuilds, as before. The worktree is left as the caller reopens it.
    """
    from crew_org.flows import session_end  # noqa: PLC0415

    number = card.number or 0
    approved = _git(worktree, "rev-parse", "HEAD")
    try:
        files = ws.merge_leaving_conflicts()
    except GitError as exc:
        return Resolution(approved, [], why=f"main couldn't be merged in: {exc}"[:300])
    resolution = Resolution(approved, files)
    if not files:
        resolution.why = "main merged in with nothing to resolve"
        return resolution
    try:
        record = read_record(worktree)
    except ProjectRecordError as exc:
        resolution.why = f"the project's record can't be read mid-merge: {exc}"[:300]
        return resolution
    profiles.set_project(record)
    start, measured = resolution_start(worktree, files, approved, earlier)
    sink.note(
        EventKind.NOTE,
        f"#{number} resolves its conflicts with main in {', '.join(files)}",
        repo=repo,
        card=number,
        paths=list(files),
        rule=Rule.RESOLVE_CONFLICTS,
    )
    held = Held(worktree, f"{repo}-{number}")
    try:
        started = held.start()
        if not started.ok and started.code != 2:
            raise SessionInterrupted("the container engine", started.output[-300:])
        if not started.ok:
            resolution.why = f"its workspace couldn't start: {started.output[-300:]}"
            return resolution
        setup = held.setup()
        if setup is not None and not setup.ok:
            setup = held.setup()
        if setup is not None and not setup.ok:
            resolution.why = f"its setup failed mid-merge: `{setup.command}` exited {setup.code}"
            return resolution
        session = conversation.session(
            worktree,
            story_text,
            held,
            held.tools,
            instead_of_hints=start,
            base=approved,
            round=1,
            story_parts=measured,
            returned=True,
            resolving=tuple(files),
        )
        ending = attributed(
            session.run, card=number, repo=repo, sprint=sprint, attempt=1, purpose="resolution"
        )()
        resolution.calls, resolution.model_s = session.calls, session.model_s
        outcome.attempts += 1
        if ending.how == "interrupted":
            if ending.error is not None:
                reraise_if_down(ending.error)
            service = (
                "the workspace container" if ending.why == "workspace_gone" else "a model call"
            )
            raise SessionInterrupted(service, str(ending.said.get("error") or ending.why))
        if ending.how != "done":
            said = ending.said.get("missing") or ending.why or "no reason given"
            resolution.why = f"its session ended `{ending.how}`: {said}"[:400]
            return resolution
        try:
            ws.commit(
                f"merge({number}): main into {card.title}, its conflicts resolved\n\n"
                f"Resolved: {', '.join(files)}",
                allow_empty=True,
            )
            beyond = sorted(set(beyond_the_merge(worktree, approved)) - set(files))
        except GitError as exc:
            raise SessionInterrupted("git", f"the merge's commit failed: {exc}") from exc
        if beyond:
            # The tools refuse these edits; this is the gate's own look, whatever made them.
            resolution.why = "it changed files main didn't conflict in: " + ", ".join(
                f"`{f}`" for f in beyond
            )
            return resolution

        def note(text: str) -> None:
            sink.note(EventKind.NOTE, f"#{number} {text}"[:300], card=number)

        def run_checks(at: Path) -> Any:
            checked = check(at, held=held, since=approved)
            if held.lost:
                raise SessionInterrupted("the workspace container", "lost while the gates ran")
            return checked

        verdict = gates.at_done(
            worktree,
            ending.said,
            card=card,
            story=story_text,
            record=record,
            run_checks=run_checks,
            note=note,
        )
        rules = [f.rule for f in verdict.findings]
        sink.emit(
            CrewEvent(
                kind=EventKind.GATE_CHECKED,
                role="Developer",
                card=number,
                summary=f"#{number} gates at the resolution's done: "
                + (", ".join(rules) if rules else "passed"),
                detail={"repo": repo, "passed": verdict.passed, "rules": rules, "resolution": True},
            )
        )
        if verdict.findings:
            resolution.why = "its gates sent it back: " + session_end.first_lines(
                verdict.findings[0].says
            )
            return resolution
        if verdict.already_done or verdict.disagree:
            resolution.why = "its gates found it changes nothing main doesn't have, or ruled on it"
            return resolution
        resolution.reached = Delivered(
            session_end.REVIEW,
            ending.said,
            verdict,
            "decision.delivery.outcome",
            sections=gates.pr_sections(verdict),
            how="done",
        )
        return resolution
    finally:
        held.stop()
        if resolution.reached is None:
            ws.abort_merge()


def resolution_start(
    worktree: Path, files: list[str], approved: str, earlier: str
) -> tuple[str, dict[str, int]]:
    """What a session resolving a merge's conflicts is told, and the size of each part.

    Each conflict with both sides, the base they share, and the commits on main that
    changed the file; the story's approved diff, to read; and its last `done`, which
    its own `done` repeats (crew#933).
    """
    base = _git(worktree, "merge-base", approved, "origin/HEAD")
    parts = [
        "## Why this is a new session\n\n"
        "Review and QA approved this story, and since then other work has landed on main "
        "in the same lines, so it can't merge. Main is merged into the branch, and git "
        "left its conflicts in the files: resolve them, and change nothing else. Only "
        "the files below can be edited.\n\n"
        "Resolve each conflict so both changes hold: where both sides moved the same "
        "value, make one move to the newer value; where both added entries, keep both. "
        "Take out every marker line. Then run the checks, and call `done` with the "
        "story's criteria and tests as its last `done` names them, below. Review and QA "
        "then judge only what your resolution changes.\n\n"
        "If a conflict can't be resolved so that both changes hold, call `blocked` and "
        "say why: the story is then rebuilt on main instead."
    ]
    shown = []
    for name in files:
        landed = _git(worktree, "log", "--format=- %s", f"{base}..origin/HEAD", "--", name)
        text = (worktree / name).read_text(encoding="utf-8", errors="replace")
        lines = [f"### `{name}`", ""]
        if landed:
            lines += ["Main's side came from:", "", *landed.splitlines()[:5], ""]
        for i, (story, both, main) in enumerate(conflicts_in(text), 1):
            lines += [
                f"**Conflict {i}.** The story's side:",
                "",
                _fenced(story),
                "",
                "What both sides started from:",
                "",
                _fenced(both),
                "",
                "Main's side:",
                "",
                _fenced(main),
                "",
            ]
        shown.append("\n".join(lines).rstrip())
    conflicts = "## The conflicts\n\n" + "\n\n".join(shown)
    diff = _git(worktree, "diff", base, approved)
    if len(diff) > APPROVED_DIFF_CHARS:
        diff = diff[:APPROVED_DIFF_CHARS] + (
            f"\n(cut at {APPROVED_DIFF_CHARS} characters: read the rest with "
            f"`git diff {base[:12]} {approved[:12]}`)"
        )
    change = (
        "## The story's approved change, to read\n\n"
        f"What review and QA approved, against main as it was then:\n\n{_fenced(diff, 'diff')}"
    )
    parts += [conflicts, change]
    if earlier:
        parts.append(earlier)
    measured = {"conflicts": len(conflicts), "approved_diff": len(change), "done": len(earlier)}
    return "\n\n".join(parts), measured


def resolution_failed(resolution: Resolution) -> str:
    """The rebuild's line on the resolution tried first, so its session knows why."""
    return (
        "## The conflicts couldn't be resolved\n\n"
        "This story was approved, and conflicted with main in "
        + (", ".join(f"`{f}`" for f in resolution.files) or "unknown files")
        + f". A session tried to resolve only those conflicts, and {resolution.why}. So it "
        "is rebuilt on main."
    )


def _say_resolved(
    issues: IssueClient,
    sink: EventSink,
    repo: str,
    card: Card,
    pull: int,
    worktree: Path,
    resolution: Resolution,
    outcome: DeliveryOutcome,
) -> None:
    """The resolution, said on its pull request with the marker review and QA read, and
    recorded as `merge.resolved`. The push already moved its head."""
    from crew_org.flows.artifacts import signed  # noqa: PLC0415
    from crew_org.flows.merge import RESOLVED_MARKER  # noqa: PLC0415

    number = card.number or 0
    head = _git(worktree, "rev-parse", "HEAD")
    try:
        # What the gates' fixers changed lands with it too, so it's judged with it.
        changed = sorted(set(beyond_the_merge(worktree, resolution.approved)))
    except GitError:
        changed = list(resolution.files)
    marker = RESOLVED_MARKER.format(
        approved=resolution.approved, head=head, files=",".join(changed)
    )
    try:
        issues.comment(
            repo,
            pull,
            signed(
                f"{marker}\n**Conflicts with `main` resolved.** Main is merged in, and the "
                "conflicts in "
                + ", ".join(f"`{f}`" for f in resolution.files)
                + " are resolved so both sides' changes hold. Nothing else of the story "
                "changed. Lint and the full test suite pass in the story's workspace; "
                f"review and QA judge only what changed in those files since "
                f"{resolution.approved[:7]}, which they approved.",
                "Developer",
            ),
        )
    except Exception as exc:  # noqa: BLE001 - unmarked, review judges the whole pull request
        reraise_if_down(exc)
        sink.note(
            EventKind.NOTE,
            f"#{number} resolved on PR #{pull}, and its comment failed: {exc}"[:300],
            card=number,
        )
    sink.emit(
        CrewEvent(
            kind=EventKind.MERGE_RESOLVED,
            role="Developer",
            card=number,
            summary=f"#{number} PR #{pull}: conflicts with main resolved in "
            + ", ".join(resolution.files),
            detail={
                "repo": repo,
                "pr": pull,
                "files": list(resolution.files),
                "changed": changed,
                "calls": resolution.calls,
                "model_s": round(resolution.model_s, 1),
                "sessions": outcome.attempts,
            },
        )
    )


def _fenced(text: str, lang: str = "") -> str:
    """`text` in a fence longer than any it holds."""
    longest = max((len(m) for m in re.findall(r"`{3,}", text)), default=2)
    fence = "`" * (longest + 1)
    return f"{fence}{lang}\n{text.rstrip(chr(10))}\n{fence}"


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
    marks: str = "",
) -> str:
    """The pull request: what `done` said, how it was checked, and what the gates noted,
    signed by the role whose sessions did the work. `marks` are for the workflows that
    read the body: a QA dispute's names the pull request its test runs against."""
    from crew_org.flows import session_end  # noqa: PLC0415
    from crew_org.flows.artifacts import signed  # noqa: PLC0415

    lines = [
        str(reached.said.get("summary") or ""),
        "",
        "## Verification",
        "",
        "Lint and the full test suite pass in its workspace container.",
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
    if marks:
        lines += ["", marks]
    return signed("\n".join(lines), by)


def test_task_start(
    issues: IssueClient,
    *,
    project: str,
    number: int,
    worktree: Path,
    today: str = "",
    bug: bool = False,
) -> str:
    """The first session's start of QA's test Task for a merged story, rendered without
    opening the Task or calling a model: its system prompt, its first message and its
    tools (crew#944). For the Sponsor's and the operator's review before test Tasks open.

    `worktree` is qa-suite, with `project` placed at `.project/<project>/`, as delivery
    places it. Built from the pieces a delivery builds it from: the Task's body as
    `qa_tasks.test_body` writes it, the role's start, and the session's own start. The
    hints are read without coverage, so they can differ from a tick's. A `bug`'s fix opens
    the test that would have caught it: its type is the board's, not the issue's.
    """
    from crew_org.flows import qa_tasks  # noqa: PLC0415
    from crew_org.session.start import start, system_text  # noqa: PLC0415
    from crew_org.work_types import BUG, STORY  # noqa: PLC0415

    filed = issues.get(project, number)
    if filed.get("pull_request"):
        raise ValueError(
            f"{project}#{number} is a pull request: name the story it closes, whose test this is"
        )
    story = Card(
        item_id="",
        number=number,
        title=str(filed.get("title") or ""),
        repo=project,
        work_type=BUG if bug else STORY,
        state=str(filed.get("state") or "").upper(),
    )
    kind, key = qa_tasks.test_key(story)
    title, body = qa_tasks.test_body(issues, card=story, kind=kind, key=key)
    task = Card(
        item_id="",
        number=None,
        title=title,
        repo=role_tasks.SUITE,
        work_type=TASK,
        labels=frozenset({role_tasks.QA.label, role_tasks.project_label(project)}),
    )
    role = role_tasks.role_of(task)
    assert role is not None
    offered = skills.for_task(role, body, worktree, [])
    text = story_section(
        f"{role_tasks.task_start(role, today, offered, role_tasks.SUITE)}\n\n{title}\n\n{body}",
        story_notes(issues, task, role_tasks.SUITE),
        story_rows(issues, task, role_tasks.SUITE),
        story_diagram(issues, task, role_tasks.SUITE),
    )
    opening, _, _ = start(worktree, text, None, None, "Task")
    specs = role_tasks.done_specs(role, offered, role_tasks.SUITE, kind)
    done = next(s for s in specs if s["function"]["name"] == "done")["function"]
    return "\n\n".join(
        [
            f"# The session start of QA's test Task for {issues.owner}/{project}#{number}",
            f"## System prompt\n\n{system_text(role.spec, role.step)}",
            f"## First message\n\n{opening}",
            "## Tools\n\n" + "\n".join(f"- `{s['function']['name']}`" for s in specs),
            f"## `done`, as it's described\n\n{done['description']}",
        ]
    )


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


@dataclass(frozen=True)
class KeptWork:
    """A kept branch a delivery resumes: its name, and the tip the crew pushed."""

    branch: str
    sha: str


def kept_work(
    ws: Workspace, issues: IssueClient, sink: EventSink, repo: str, number: int
) -> tuple[KeptWork | None, str]:
    """The card's newest kept branch to resume, or why it isn't used (crew#839).

    Used only when its newest push is by the crew's own App (`trust.crew`) and left it
    at the tip it has now: a branch anyone else moved is code no gate has read. A check
    that fails or finds no push trusts nothing: the delivery starts from main, as for a
    branch someone else pushed, and never fails over it (the operator, crew#839).
    """
    found = ws.kept_for(number)
    if found is None:
        return None, ""
    name, sha = found
    crew = getattr(getattr(issues, "trust", None), "crew", None) or frozenset()
    try:
        push = issues.last_push(repo, name)
    except Exception as exc:  # noqa: BLE001 - a failed check starts from main
        why = f"who pushed it couldn't be checked: {str(exc)[:200]}"
    else:
        actor = ((push or {}).get("actor") or {}).get("login") or ""
        if push is None:
            why = "GitHub has no record of who pushed it"
        elif actor not in crew:
            why = f"its last push was by `{actor or 'someone unnamed'}`, not the crew"
        elif push.get("after") != sha:
            why = "it has moved since the crew pushed it"
        else:
            sink.note(
                EventKind.NOTE,
                f"#{number} resumes its kept work on {name}",
                repo=repo,
                card=number,
                kept=name,
                rule=Rule.RESUME_KEPT,
            )
            return KeptWork(name, sha), ""
    kept = KeptWork(name, sha)
    sink.note(
        EventKind.NOTE,
        f"#{number} starts from main, not its kept work on {name}: {why}",
        repo=repo,
        card=number,
        kept=name,
        rule=Rule.RESUME_KEPT,
    )
    return None, not_resumed(kept, why)


def not_resumed(kept: KeptWork, why: str) -> str:
    """The start's line on kept work that exists and wasn't used, so nothing is lost."""
    return (
        "## Kept work this session doesn't start from\n\n"
        f"The last delivery's work is kept on `{kept.branch}`, but this session starts "
        f"from main: {why}. Its files aren't below; read them with "
        f"`git show {kept.sha[:12]}:<path>` if they help."
    )


def kept_start(
    issues: IssueClient,
    repo: str,
    number: int,
    kept: KeptWork,
    worktree: Path,
    since: str,
    *,
    link: Workspace,
) -> str:
    """What a session resuming kept work is told, in place of the hints (crew#839).

    The branch, the files it changes against main, and the last gates' findings and
    run from the crew's own note on the card that links it, so it doesn't find them
    again. The note's findings are already cut to `session_end.FINDING_CHARS`.
    """
    url = f"https://github.com/{link.owner}/{link.repo}/compare/{since}...{kept.branch}"
    parts = [
        "## Where the last delivery stopped\n\n"
        f"It left its work kept on [`{kept.branch}`]({url}), and this session starts "
        "from it, brought up to date with main. Its work is in the files below, "
        "committed: carry on from it, and leave what's done."
    ]
    if _git(worktree, "log", "-1", "--format=%s", kept.sha).startswith(STOPPED.split("{", 1)[0]):
        parts.append(
            "Its last commit holds edits made after its last green run, which no run "
            "proved: run the checks before you build on them."
        )
    files = _changes(worktree, since)
    parts.append(
        "**What it changes against main:** " + (", ".join(files) if files else "nothing") + "."
    )
    said = kept_note(issues, repo, number, kept.branch)
    if said:
        parts.append(said)
    else:
        parts.append(
            "The crew's note on why it stopped couldn't be read, so the gates' findings "
            "aren't here: run the checks to see where it stands."
        )
    return "\n\n".join(parts)


# A round of findings in a note on work that left (`session_end.evidence`).
_ROUND = re.compile(r"^- At `done` (\d+): (.+)$", re.MULTILINE)
_LAST_RUN = re.compile(r"^\*\*Its last run:\*\* .+$", re.MULTILINE)


def kept_note(issues: IssueClient, repo: str, number: int, branch: str) -> str:
    """The last gates' findings and last run, from the crew's newest note linking `branch`.

    Only a comment by the crew's own App (`trust.crew`) counts. Empty when none is found
    or the comments can't be read: the start says so.
    """
    crew = getattr(getattr(issues, "trust", None), "crew", None) or frozenset()
    try:
        comments = issues.comments(repo, number)
    except Exception:  # noqa: BLE001 - the start says the note wasn't read
        return ""
    linked = f"[`{branch}`]("
    notes = [
        c.get("body") or ""
        for c in comments
        if ((c.get("user") or {}).get("login") or "") in crew and linked in (c.get("body") or "")
    ]
    if not notes:
        return ""
    note = notes[-1]
    rounds = [(int(n), said) for n, said in _ROUND.findall(note)]
    parts = []
    if rounds:
        last = max(n for n, _ in rounds)
        listed = "\n".join(f"- {said}" for n, said in rounds if n == last)
        parts.append(f"**The gates' findings at its last `done`, the {_nth(last)}:**\n\n{listed}")
    ran = _LAST_RUN.search(note)
    if ran:
        parts.append(ran.group(0))
    return "\n\n".join(parts)


def _nth(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def returned_start(
    issues: IssueClient,
    repo: str,
    number: int,
    branch: str,
    worktree: Path,
    *,
    known: Card,
    rebuilt: list[str] | None = None,
    earlier: str = "",
    sponsor: str = "",
) -> tuple[str, int, int]:
    """What a session reworking a story review or QA sent back is told (P2), the size of
    its findings section, and of the Sponsor's part of it: each 0 without one.

    A branch `rebuilt` on main, for conflicting in those files, has no previous work in
    the files: its last `done`, read before the rebuild, is `earlier` (crew#919). The
    Sponsor's change request goes first: their words outrank the crew's (crew#924).
    """
    linked = known.open_pull_on(branch)
    theirs = ""
    try:
        pull = issues.pull_for_branch(repo, branch, known=linked)
        if pull:
            theirs = sponsor_review(issues, repo, pull["number"], sponsor)
    except Exception:  # noqa: BLE001, S110 - best effort, as the crew's verdicts are
        pass
    crews = prior_verdicts(issues, repo, number, branch, known=linked)
    verdicts = "\n\n---\n\n".join(p for p in (theirs, crews) if p)
    # A role's Task isn't a story, and QA doesn't verify it: review or CI sends it back
    # (crew#944).
    work, by = ("Task", "Review or CI") if known.work_type == TASK else ("story", "Review or QA")
    fate = previous_fate(issues, repo, branch, work=work)
    if rebuilt is None:
        why = (
            f"{by} sent the {work} back. Its previous work is in the files below, on "
            "its branch: change what the findings ask, and leave the rest."
        )
        done = last_done_on(worktree, number)
    else:
        why = (
            f"{by} sent the {work} back, and its branch conflicted with main in "
            + (", ".join(f"`{f}`" for f in rebuilt) or "unknown files")
            + ", so it was rebuilt on current main: its previous work isn't in the files. "
            f"Do the {work} again on main, with the findings in view; its last `done` is "
            "below, to read."
        )
        done = earlier
    parts = [f"## Why this is a new session\n\n{why}"]
    if fate:
        parts.append(f"## What happened to it\n\n{fate}")
    said = f"## What {by[0].lower()}{by[1:]} said\n\n{verdicts}" if verdicts else ""
    if said:
        parts.append(said)
    if done:
        parts.append(done)
    return "\n\n".join(parts), len(said), len(theirs)


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
