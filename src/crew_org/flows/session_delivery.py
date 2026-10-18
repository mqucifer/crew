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

Every session in one delivery shares one 2-hour budget of model time, so a loop
between a session and the gates can't run unbounded (the operator, on crew#778). A
return from review or QA is a new delivery, and a new session (P2).

**Checkpoints (P3):** after each green run the branch is committed, never pushed: a
push to an open pull request's branch would move the head under review. The clone's
branch outlives the worktree, so a crashed or stopped tick leaves the checkpoint for
the next one, which resumes from it. Any other ending drops it, so stale work is never
resumed.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from crew_org import profiles
from crew_org.crews import epic_rows
from crew_org.events import CrewEvent, EventKind, EventSink, attributed
from crew_org.flows.conclusion import story_rows
from crew_org.flows.delivery import (
    DeliveryOutcome,
    coverage_for,
    previous_fate,
    prior_verdicts,
)
from crew_org.flows.presentation_notes import story_notes
from crew_org.git_ops import CHECKPOINT, MergeConflict, Workspace, branch_name
from crew_org.llm import reraise_if_down
from crew_org.project import ProjectRecordError, read_record
from crew_org.rules import Rule
from crew_org.session import conversation
from crew_org.tools.container import Held
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient
from crew_org.tools.workspace import check

# One delivery's model time, shared by its sessions (decision 3; the operator, crew#778).
BACKSTOP_S = conversation.BACKSTOP_S


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
    **_today: Any,
) -> DeliveryOutcome:
    """Take one story to a pull request by Developer sessions, or to where they end it.

    The keyword arguments only today's one-call Developer reads (its escalation
    policy and ledger) are accepted and unused.
    """
    from crew_org.flows import session_end  # noqa: PLC0415

    number = card.number or 0
    outcome = DeliveryOutcome(card=number)
    body = issues.get(repo, number).get("body") or ""
    story_text = f"{card.title}\n\n{body}"
    branch = branch_name(number, card.title)
    outcome.branch = branch
    live = issues.pull_for_branch(repo, branch, known=card.open_pull_on(branch))
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
    story = story_section(story_text, note, rows)
    # The story's part of the start, measured by what it's made of (the operator, crew#778).
    measured = {"card": len(story_text), "decided": len(rows or ""), "note": len(note or "")}
    if checkpoint is not None:
        instead = resumed_start(checkpoint)
    elif live is not None and not rebuilt:
        instead = returned_start(issues, repo, number, branch, worktree, known=card)
    else:
        instead = None
    since = _merge_base(worktree)
    sink.emit(
        CrewEvent(kind=EventKind.AGENT_STARTED, role="Developer", card=number, summary=branch)
    )

    unfit = conversation.unfit(worktree)
    if unfit:
        return _unready(outcome, unfit)
    held = Held(worktree, f"{repo}-{number}")
    try:
        started = held.start()
        if not started.ok:
            return _unready(outcome, f"the workspace didn't start: {started.output[-500:]}")
        setup = held.setup()
        if setup is not None and not setup.ok:
            return _unready(
                outcome, f"$ {setup.command}\n(exit {setup.code})\n{setup.output[-1500:]}"
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
    )


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
) -> Delivered:
    """Sessions until one ends somewhere other than back in a session, within the budget."""
    from crew_org.flows import session_end  # noqa: PLC0415

    number = card.number or 0
    spent = 0.0
    rounds = 0

    def run_checks(at: Path) -> Any:
        return check(at, held=held, since=since)

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
            # Always a commit, so the next delivery can read what this `done` said.
            ws.commit(
                f"feat({number}): {card.title}\n\n{session_end.last_done(ending.said)}\n"
                f"Closes #{number}",
                allow_empty=True,
            )
        routed = route(
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
        )
        if routed.to == session_end.SESSION and spent >= BACKSTOP_S:
            # The budget's spent with the gates' findings still open: a stop, as the
            # backstop is inside a session, back to the Business Analyst.
            stopped = SimpleNamespace(
                how="blocked",
                said={
                    "missing": f"the gates' findings, still open after {round(spent / 60)} "
                    "minutes of model time, the backstop:\n\n" + routed.start,
                    "kind": "stop",
                },
                stop="backstop",
                why="",
                evidence=ending.evidence,
                error=None,
            )
            routed = route(
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
            )
        if routed.to != session_end.SESSION:
            return Delivered(routed.to, ending.said, routed.verdict, routed.rule)
        instead = routed.start


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
) -> DeliveryOutcome:
    """Push and open for review, or for QA, as today; a story the route moved is done here."""
    from crew_org.flows import session_end  # noqa: PLC0415
    from crew_org.flows.artifacts import signed  # noqa: PLC0415
    from crew_org.flows.delivery import REWORK_MARKER  # noqa: PLC0415
    from crew_org.tools.remote_errors import transient_remote  # noqa: PLC0415

    number = card.number or 0
    outcome.ended = True
    if reached.to not in (session_end.REVIEW, session_end.QA):
        # Back to refinement, or blocked for a person: the route has moved the card.
        outcome.routed = reached.to
        outcome.blocked_rule = reached.rule
        outcome.rejected_diff = _diff(worktree, since)
        return outcome
    if live is not None and not rework:
        outcome.blocked_rule = Rule.OPEN_PULL_REQUEST
        outcome.blocked_reason = (
            f"PR #{live['number']} is still open on `{outcome.branch}`. Close it, or let that "
            "pull request finish; re-delivering would overwrite the diff it is reviewing."
        )
        return outcome
    try:
        # A rework's commits go on top of what the review read; a fresh branch or a
        # rebuild replaces the crew's own dead branch, with a lease.
        ws.push(force=not rework or rebuilt)
    except Exception as exc:  # noqa: BLE001
        if transient_remote(exc):
            raise
        outcome.blocked_rule = Rule.PUSH_FAILED
        outcome.blocked_reason = f"could not push `{outcome.branch}`: {exc}"[:400]
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
                role="Developer",
                card=number,
                summary=f"already done: {len(outcome.already_done)} criteria met by existing code",
                detail={"rule": "decision.delivery.outcome"},
            )
        )
        return outcome
    if live is not None:
        outcome.pr = live["number"]
        issues.comment(
            repo,
            live["number"],
            signed(
                f"{REWORK_MARKER}\n**Re-worked.** {summary}\n\n"
                + (
                    "Rebuilt from main, where other work had changed the same lines. "
                    if rebuilt
                    else ""
                )
                + "Lint and the full test suite pass in the story's workspace.",
                "Developer",
            ),
        )
    else:
        pr = issues.create_pull(
            repo,
            title=card.title,
            head=outcome.branch,
            base=default_branch,
            body=pr_body(card, reached, outcome, _changes(worktree, since)),
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


def pr_body(card: Card, reached: Delivered, outcome: DeliveryOutcome, changes: list[str]) -> str:
    """The pull request: what `done` said, how it was checked, and what the gates noted."""
    from crew_org.flows import session_end  # noqa: PLC0415
    from crew_org.flows.artifacts import signed  # noqa: PLC0415

    lines = [
        str(reached.said.get("summary") or ""),
        "",
        "## Verification",
        "",
        "Lint and the full test suite pass in the story's workspace container.",
        "",
        f"- Developer sessions: {outcome.attempts}",
        "",
        "## Changes",
        "",
        *(f"- {change}" for change in changes),
        "",
        session_end.last_done(reached.said).rstrip(),
    ]
    sections = session_end.pr_sections(reached.verdict) if reached.verdict is not None else ""
    if sections:
        lines += ["", sections.rstrip()]
    lines += ["", f"Closes #{card.number}"]
    return signed("\n".join(lines), "Developer")


def story_section(story_text: str, note: str, rows: str) -> str:
    """The story as the session starts with it: the card, then what its epic decided."""
    parts = [story_text.strip()]
    decided = epic_rows.block(
        rows,
        "These are decided for the epic. Build to them: don't contradict one, and don't "
        "settle in code what the design note leaves open.",
        level=3,
    )
    if decided:
        parts.append(decided.strip())
    if note:
        parts.append(f"### The design note for this story's epic\n\n{note.strip()}")
    return "\n\n".join(parts)


def resumed_start(checkpoint: str) -> str:
    """What a session resuming an interrupted one is told, in place of the hints (P3)."""
    return (
        "## Where the last session stopped\n\n"
        "It was interrupted: a model call failed, its container was lost, or the tick "
        "stopped. Its work up to its last green run is in the files below, committed. "
        "Anything it changed after that run is gone. Carry on from here. Its last "
        "checkpoint:\n\n" + _quoted(checkpoint)
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
    return body.replace(f"Closes #{number}", "").strip()


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


def _unready(outcome: DeliveryOutcome, why: str) -> DeliveryOutcome:
    outcome.blocked_rule = Rule.WORKSPACE_UNREADY
    outcome.blocked_reason = f"the story's workspace isn't ready: {why}"
    outcome.ended = True
    return outcome


def _merge_base(worktree: Path) -> str:
    return _git(worktree, "merge-base", "HEAD", "origin/HEAD")


def _changes(worktree: Path, since: str) -> list[str]:
    """Each file the branch changes against where it started, with how."""
    words = {"A": "new", "D": "deleted", "M": "changed", "R": "renamed"}
    listed = _git(worktree, "diff", "--name-status", since, "HEAD")
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
