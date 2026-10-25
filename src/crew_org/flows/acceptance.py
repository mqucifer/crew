"""Acceptance: verifying delivered work, and closing out what is finished.

QA judges behaviour against the acceptance criteria, which is a different
question from the Reviewer's. A story only leaves QAing when every
criterion is proven by a test that actually exercises it.

The columns say what a card is waiting for, not what is happening to it. A
card sits in QAing until QA has finished with it — QA does not pull it
into a lane of its own — and lands in Merging already verified, where
what it waits for is the Sponsor.

Parent completion is bookkeeping the crew should not make a human do: an epic
whose stories are all Done is done, and so is a goal whose epics are.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crew_org import log, profiles, sendbacks
from crew_org.columns import DONE, IN_PROGRESS, MERGING, QAING
from crew_org.crews.qa_crew import CriterionVerdict, QAVerdict, Recorded, is_doc, verify_story
from crew_org.events import CrewEvent, EventKind, EventSink, attributed, held
from crew_org.flows import artifacts, role_tasks
from crew_org.flows.board_flow import NEEDS_REWORK
from crew_org.flows.conclusion import story_diagram, story_rows
from crew_org.flows.history import past_qa
from crew_org.flows.moves import move_card
from crew_org.git_ops import Workspace, branch_name
from crew_org.llm import reraise_if_down
from crew_org.profiles import profile_for
from crew_org.project import read_record
from crew_org.rules import Rule
from crew_org.session import conversation
from crew_org.tools.container import Held
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient, within
from crew_org.tools.regression import merged_base
from crew_org.tools.sandbox import Sandbox
from crew_org.work_types import BUG, TASK, open_in

STORY_TYPE = "Story"
EPIC_TYPE = "Epic"
GOAL_TYPE = "Goal"

QA_MARKER = "<!-- crew:qa -->"


def qa_marker(revision: str) -> str:
    """The marker for a verdict on one revision.

    `has_comment_marked` matches *any* comment carrying the bare marker, so the
    rejection QA itself wrote permanently disqualified the card: the Developer
    repaired, the card came back to QAing, and QA skipped it in silence — for
    good, while the command printed "Nothing awaiting QA".

    Scoping the marker to the commit keeps the idempotency the guard was written
    for — a re-run on unchanged code posts nothing — and lets new commits be
    judged. Bare `QA_MARKER` stays in the body so older verdicts remain findable.
    """
    return f"<!-- crew:qa {revision[:12]} -->"


def qa_returns(issues: Any, repo: str, number: int, revision: str) -> int | None:
    """QA's returns of a card, this revision's included; None if they can't be read.

    Its earlier revisions' are counted from their verdicts, so the count holds whether
    or not the verdict just posted is read back yet.
    """
    try:
        bodies = [c.get("body") or "" for c in issues.comments(repo, number)]
    except Exception:  # noqa: BLE001
        return None
    this = qa_marker(revision)
    earlier = (b for b in bodies if QA_MARKER in b and this not in b)
    return sum("## QA — not accepted" in b for b in earlier) + 1


@dataclass
class QAOutcome:
    card: int
    accepted: bool
    unproven: int = 0
    reason: str | None = None


@dataclass
class AcceptanceResult:
    verified: list[QAOutcome] = field(default_factory=list)
    returned: list[QAOutcome] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)
    # Cards QA deliberately did not judge again. Reported, because a silent
    # `continue` is how a card sat in QAing for good while the run said there
    # was nothing to do.
    skipped: list[tuple[int, str]] = field(default_factory=list)
    parents_closed: list[int] = field(default_factory=list)


def awaiting_qa(cards: list[Card]) -> list[Card]:
    return open_in(cards, QAING)


def _cited(item: CriterionVerdict) -> list[str]:
    """What a criterion rests on, as the verdict cites it: each run, check and quote."""
    lines = [f"  - ran `{r.command}`: exit {r.exit_code}" for r in item.ran]
    if item.check:
        lines.append(f"  - check `{item.check}`")
    lines += [f'  - `{q.path}`: "{q.text}"' for q in item.quoted]
    return lines


def render_qa(verdict: QAVerdict, revision: str = "") -> str:
    lines = [
        # Both: the bare marker keeps every verdict findable, the revision one
        # says which commit this verdict is about.
        QA_MARKER,
        qa_marker(revision) if revision else "",
        f"## QA — {'accepted' if verdict.accepted else 'not accepted'}",
        "",
        verdict.summary,
        "",
        "### Criteria",
        "",
    ]
    for item in verdict.criteria:
        mark = f"proven by {item.by}" if item.proven else "**not proven**"
        lines += [f"- {mark} — {item.criterion}", *_cited(item), f"  - {item.evidence}"]
    return "\n".join(lines)


def changed_paths(worktree: Path) -> list[str]:
    """Every file the branch changed since it left the default branch."""
    import subprocess  # noqa: PLC0415

    from crew_org.tools.regression import merged_base  # noqa: PLC0415

    merged = merged_base(worktree)
    if merged is None:
        return []
    diff = subprocess.run(
        ["git", "diff", "--name-only", merged.sha],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    return diff.stdout.split()


def ci_only(worktree: Path) -> bool:
    """The branch changes CI workflows, and at most the docs that describe them (crew#333).

    A deploy workflow comes with a README note on how the site deploys
    (crew-presentation#74); the Code Reviewer reads both.
    """
    from crew_org.tools.ci_guard import is_workflow  # noqa: PLC0415

    changed = changed_paths(worktree)
    return (
        bool(changed)
        and all(is_workflow(p) or is_doc(p) for p in changed)
        and any(is_workflow(p) for p in changed)
    )


def ci_checks(issues: IssueClient, repo: str, sha: str) -> dict[str, str]:
    """The CI checks on the commit QA judges (#325), each with its outcome, or none where
    they can't be read.

    What only CI runs (an image build, a workflow) has no test in the workspace: its
    passing check is the evidence, and `done` holds a citation of one to this list.
    """
    try:
        runs = issues.check_runs(repo, sha)
    except Exception:  # noqa: BLE001
        return {}
    return {
        str(r.get("name")): str(r.get("conclusion") or r.get("status") or "unknown")
        for r in runs or []
    }


def unguarded(changed: Sequence[str]) -> list[str]:
    """The changed files no regression guard read (#404): QA is told to judge them by
    their tests."""
    return [p for p in changed if not profile_for(p).guarded]


class WorkspaceUnready(RuntimeError):
    """The story's workspace couldn't be held for QA: not the story's verdict."""


def hold(worktree: Path, name: str, sandbox: Sandbox | None = None) -> Recorded:
    """The story's workspace for QA, as delivery holds it: its container from the
    project's image, with the project's services, set up (crew#895). QA's own sandbox had
    no services, so the database tests failed there (mqucifer/sprint-metrics#785).

    One that can't be started or set up is WorkspaceUnready, and the card is judged on a
    later pass: delivery already ran the same setup on this branch.
    """
    unfit = conversation.unfit(worktree)
    if unfit:
        raise WorkspaceUnready(unfit)
    held = Held(worktree, name, sandbox)
    started = held.start()
    setup = held.setup() if started.ok else None
    if setup is not None and not setup.ok:
        # Once more: a fetch can fail for a moment.
        setup = held.setup()
    failed = started if not started.ok else setup if setup is not None and not setup.ok else None
    if failed is not None:
        held.stop()
        raise WorkspaceUnready(f"$ {failed.command}\n(exit {failed.code})\n{failed.output[-1500:]}")
    return Recorded(held)


# A story the Developer answered is already done (#221): no pull request, the
# evidence on the story. QA judges it like any story; accepted, it closes.
ALREADY_DONE_MARKER = "<!-- crew:already-done -->"
# Criteria a delivery says the existing tests prove (#217): QA runs and cites them.
EXISTING_PROOF_MARKER = "<!-- crew:existing-proof -->"


def _existing_proof(issues: IssueClient, repo: str, number: int) -> str:
    """The criteria the latest delivery named existing tests for, as it wrote them."""
    try:
        bodies = [c.get("body") or "" for c in issues.comments(repo, number)]
    except Exception:  # noqa: BLE001
        return ""
    latest = [b for b in bodies if b.startswith("Implemented in #")]
    if not latest or EXISTING_PROOF_MARKER not in latest[-1]:
        return ""
    block = latest[-1].split(EXISTING_PROOF_MARKER, 1)[1]
    return block.split("<!-- crew:by")[0].strip()


def _already_done(issues: IssueClient, repo: str, number: int) -> str:
    """The Developer's already-done evidence, if the story's latest delivery was that."""
    try:
        bodies = [c.get("body") or "" for c in issues.comments(repo, number)]
    except Exception:  # noqa: BLE001
        return ""
    latest = [b for b in bodies if ALREADY_DONE_MARKER in b or b.startswith("Implemented in #")]
    if not latest or ALREADY_DONE_MARKER not in latest[-1]:
        return ""
    return latest[-1].replace(ALREADY_DONE_MARKER, "").split("<!-- crew:by")[0].strip()


def run_qa(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    ws: Workspace,
    sandbox: Sandbox,
    *,
    cards: list[Card],
    repo: str,
    repos: set[str] | None = None,
) -> AcceptanceResult:
    """Verify everything sitting in QAing.

    `repo` is a fallback for a card that names none, never the answer. QA read
    the story, checked its own marker and posted its verdict against this
    argument whatever card it was judging — so a card outside the pilot got its
    body read from the wrong repository and its verdict posted there. Delivery
    has always used `card.repo or repo`; QA was the one flow that did not.
    """
    result = AcceptanceResult()

    for card in awaiting_qa(within(cards, repos)):
        # One QA run per card (crew#449).
        with log.run("QA Engineer", card=card.number, repo=card.repo or repo):
            number = card.number or 0
            card_repo = card.repo or repo
            # A role's Task has no story criteria for QA to prove: its role's checks ran
            # at `done`, the Code Reviewer judged it, and a release is proven by the
            # release check once it's out (crew#839). QA's own Task is its test, which the Code
            # Reviewer reads and qa-suite's CI runs against the project's main (crew#896).
            if card.work_type == TASK:
                move_card(
                    board,
                    sink,
                    item_id=card.item_id,
                    to=MERGING,
                    by="QA Engineer",
                    card=number,
                    frm=QAING,
                    summary="a role's Task: nothing for QA to prove",
                    rule="decision.role_tasks.no_qa",
                )
                result.verified.append(QAOutcome(card=number, accepted=True))
                continue
            branch = branch_name(number, card.title)
            # The worktree has to come from the card's own repository too, or QA
            # verifies a branch of the same name in a different codebase.
            card_ws = ws.for_repo(card_repo)

            try:
                worktree = card_ws.open_existing(branch)
                revision = card_ws.head()
            except Exception as exc:  # noqa: BLE001
                result.failed.append((number, f"{type(exc).__name__}: {exc}"))
                continue

            if issues.has_comment_marked(card_repo, number, qa_marker(revision)):
                held("skip.acceptance.already_judged_at", repo=card.repo or repo, card=number)
                result.skipped.append((number, f"already judged at {revision[:7]}"))
                continue

            # Only CI workflows: QA can observe nothing of what
            # they do until they run on `main`. The Code Reviewer judged the
            # workflow; its run is the proof (§7.1, crew#333).
            if ci_only(worktree):
                card_ws.close()
                artifacts.comment(
                    issues,
                    sink,
                    repo=card_repo,
                    number=number,
                    body=f"{QA_MARKER}\n{qa_marker(revision)}\n"
                    "## QA — not applicable: a CI-only change\n\n"
                    "This pull request changes CI workflows, and at most the docs that "
                    "describe them, which the Code Reviewer read. What a workflow "
                    "does can only be observed when it runs, mostly on `main`, so it is judged "
                    "by the Code Reviewer and proven by its own run (constitution §7.1). "
                    "A check that fails on the pull request or in the merge queue still sends "
                    "it back with the log.",
                    by="QA Engineer",
                )
                move_card(
                    board,
                    sink,
                    item_id=card.item_id,
                    to=MERGING,
                    by="QA Engineer",
                    card=number,
                    frm=QAING,
                    summary="CI-only change: judged by review, proven by its run",
                    rule="decision.acceptance.ci_only",
                )
                result.verified.append(QAOutcome(card=number, accepted=True))
                continue

            sink.emit(
                CrewEvent(
                    kind=EventKind.AGENT_STARTED,
                    role="QA Engineer",
                    card=number,
                    summary=f"verify {card.title[:46]}",
                )
            )
            holding = None
            try:
                done = _already_done(issues, card_repo, number)
                proof = _existing_proof(issues, card_repo, number)
                if proof:
                    done_or_proof = (
                        "## Criteria the Developer says the existing tests prove\n\n"
                        f"{proof}\n\nThese have no new test. Run the named tests, and cite "
                        "those runs as the evidence."
                    )
                else:
                    done_or_proof = ""
                answered = (
                    "## The Developer answered that this is already done\n\n"
                    f"{done}\n\nNothing was changed. Judge each criterion against the code "
                    "and the tests named, as for any story."
                    if done
                    else done_or_proof
                )
                # Read first: it sets this project's parts, which `unguarded` needs (#404).
                _read_project(worktree)
                # A bug's body is its report, then the criteria that reproduce it
                # (crew#407): judged the same, said so the report isn't read as scope.
                bug = (
                    "This is a bug fix. The report above the criteria says what was wrong; "
                    "judge the criteria, which reproduce it.\n\n"
                    if card.work_type == BUG
                    else ""
                )
                body = issues.get(card_repo, number).get("body") or ""
                changed = changed_paths(worktree)
                holding = hold(worktree, f"{card_repo}-{number}-qa", sandbox)
                merged = merged_base(worktree)
                verdict = attributed(verify_story, card=number, repo=card_repo)(
                    f"{bug}{card.title}\n\n{body}",
                    worktree=worktree,
                    repo=card_repo,
                    github=issues,
                    workspace=holding,
                    base=merged.sha if merged is not None else "",
                    checks=ci_checks(issues, card_repo, revision),
                    prior_verdicts=past_qa(issues, card_repo, number, marker=QA_MARKER),
                    decisions=story_rows(issues, card, card_repo),
                    diagram=story_diagram(issues, card, card_repo),
                    answered=answered,
                    changed=changed,
                    unguarded=unguarded(changed),
                )
                if holding.held.lost:
                    raise WorkspaceUnready("the workspace container was lost during QA")
            except Exception as exc:  # noqa: BLE001
                reraise_if_down(exc)
                result.failed.append((number, f"{type(exc).__name__}: {exc}"))
                sink.emit(
                    CrewEvent(
                        kind=EventKind.AGENT_FAILED,
                        role="QA Engineer",
                        card=number,
                        summary=str(exc)[:80],
                    )
                )
                continue
            finally:
                if holding is not None:
                    holding.stop()
                card_ws.close()

            artifacts.comment(
                issues,
                sink,
                repo=card_repo,
                number=number,
                body=render_qa(verdict, revision),
                by="QA Engineer",
            )
            # What QA saw outside this story is filed for its owner (crew#839).
            if verdict.outside:
                role_tasks.file_findings(
                    issues,
                    board,
                    sink,
                    repo=card_repo,
                    story=card,
                    found=verdict.outside,
                    by="QA Engineer",
                    round_key=revision[:7],
                    cards=cards,
                )

            if verdict.accepted and done:
                # Nothing to merge (#221): the story closes, proven against main.
                move_card(
                    board,
                    sink,
                    item_id=card.item_id,
                    to=DONE,
                    by="QA Engineer",
                    card=number,
                    frm=QAING,
                    summary="already done — every criterion proven, no pull request",
                    rule="gate.qa_unproven",
                )
                issues.close(card_repo, number)
                result.verified.append(QAOutcome(card=number, accepted=True))
            elif verdict.accepted:
                move_card(
                    board,
                    sink,
                    item_id=card.item_id,
                    to=MERGING,
                    by="QA Engineer",
                    card=number,
                    frm=QAING,
                    summary="every criterion proven",
                    rule="gate.qa_unproven",
                )
                result.verified.append(QAOutcome(card=number, accepted=True))
            else:
                move_card(
                    board,
                    sink,
                    item_id=card.item_id,
                    to=IN_PROGRESS,
                    by="QA Engineer",
                    card=number,
                    frm=QAING,
                    summary=f"returned — {len(verdict.unproven)} unproven",
                    rule=Rule.QA_UNPROVEN,
                )
                sendbacks.record(
                    sink,
                    sendbacks.QA_UNPROVEN,
                    card=number,
                    repo=card_repo,
                    role="QA Engineer",
                    round_=qa_returns(issues, card_repo, number, revision),
                    unproven=len(verdict.unproven),
                )
                outcome = QAOutcome(
                    card=number,
                    accepted=False,
                    unproven=len(verdict.unproven),
                    reason=verdict.unproven[0].evidence if verdict.unproven else None,
                )
                result.returned.append(outcome)

            sink.emit(
                CrewEvent(
                    kind=EventKind.AGENT_FINISHED,
                    role="QA Engineer",
                    card=number,
                    summary=f"{'accepted' if verdict.accepted else 'returned'} — "
                    f"{len(verdict.unproven)} unproven",
                    detail={
                        "accepted": verdict.accepted,
                        "unproven": [c.criterion for c in verdict.unproven],
                        # Which criteria, by their number on the card (crew#449).
                        "unproven_numbers": [
                            n for n, c in enumerate(verdict.criteria, start=1) if not c.proven
                        ],
                    },
                )
            )

    return result


def close_finished_parents(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    cards: list[Card],
    *,
    repo: str,
    repos: set[str] | None = None,
) -> list[int]:
    """Close an epic when its stories are Done, and a goal when its epics are.

    Bookkeeping a human should not have to do. Done means every child is Done —
    a parent with one story still open is not finished, however close it looks.

    A child closed as not planned was superseded, not delivered: GitHub moves it
    to Done all the same. It doesn't count either way, and a parent with nothing
    delivered isn't finished. sprint-metrics#184, #185 and #187 were closed as
    completed when their superseded stories were, and Goal #174 would have
    followed. A parent being planned again (`needs:rework`) never closes here.

    Closing means both: the card moves to Done and the issue closes. Moving the
    card alone left finished epics open in the repository, and a parent already
    in Done with its issue open is closed here rather than skipped.
    """
    closed: list[int] = []
    # By repository and number: the board spans repositories, and crew#33 is
    # not sprint-metrics#33. Now that this closes issues, confusing them would
    # close one whose children are not done.
    by_key = {(c.repo, c.number): c for c in cards}

    for parent_type in (EPIC_TYPE, GOAL_TYPE):
        # Parents only in the crew's repositories; their children are read
        # from the whole board.
        for card in within(cards, repos):
            if card.work_type != parent_type or card.state == "CLOSED":
                continue
            if NEEDS_REWORK in card.labels:
                continue
            # "Not finished" is settled without a fetch (#55). The children on
            # the board say it first: one that isn't Done. GitHub's sub-issue
            # count is only the fallback for a parent with none on the board,
            # because it can be wrong: it read 1 of 2 for sprint-metrics#304 with
            # both children closed and Done, and the epic, and everything held
            # on it, stayed open (crew#381). 100% either way only earns the
            # per-child check below; neither closes anything alone.
            on_board = [c for c in cards if c.parent == card.number and c.repo == card.repo]
            if on_board:
                if any(c.status != DONE for c in on_board):
                    continue
            else:
                total, closed_count = card.sub_issues_total, card.sub_issues_closed
                if total is not None and (total == 0 or (closed_count or 0) < total):
                    continue
            parent_repo = card.repo or repo
            try:
                children = issues.sub_issues(parent_repo, card.number or 0)
            except Exception:  # noqa: BLE001
                continue
            if not children:
                continue

            statuses = []
            for child in children:
                if child.get("state_reason") == "not_planned":
                    continue
                child_repo = child.get("repository_url", parent_repo).rsplit("/", 1)[-1]
                on_board = by_key.get((child_repo, child["number"]))
                if on_board:
                    statuses.append(on_board.status)
                else:
                    # A child off the board has no status to read; its issue
                    # closing is the only sign it is finished.
                    statuses.append(DONE if child.get("state") == "closed" else None)
            if not statuses or any(status != DONE for status in statuses):
                continue
            # A Goal's epics done is the Architect's event: its target diagram becomes
            # the repository's, and the Goal closes once that Task is done too (crew#839).
            if parent_type == GOAL_TYPE and not _diagram_task_among(children):
                epics = [c["number"] for c in children if c.get("state_reason") != "not_planned"]
                try:
                    role_tasks.goal_diagram_task(
                        issues, board, sink, repo=parent_repo, goal=card, epics=epics, cards=cards
                    )
                except Exception as exc:  # noqa: BLE001
                    reraise_if_down(exc)
                    role_tasks.not_opened(
                        sink,
                        repo=parent_repo,
                        role=role_tasks.ARCHITECT,
                        kind=role_tasks.DIAGRAM,
                        card=card.number,
                        why=exc,
                    )
                continue

            # Bookkeeping, not judgement: no role decided this, so the parent
            # keeps whichever role last worked on it.
            if card.status != DONE:
                move_card(
                    board,
                    sink,
                    item_id=card.item_id,
                    to=DONE,
                    by=None,
                    card=card.number,
                    summary=f"all {len(statuses)} children done — closing {parent_type.lower()}",
                    rule="decision.acceptance.close_finished_parents",
                )
            issues.close(parent_repo, card.number or 0)
            closed.append(card.number or 0)
    return closed


def _diagram_task_among(children: list[dict[str, Any]]) -> bool:
    """Is the Architect's diagram Task already one of this Goal's children?"""
    for child in children:
        found = role_tasks.marked(child.get("body"))
        if found is not None and found.kind == role_tasks.DIAGRAM:
            return True
    return False


def _read_project(worktree: Path) -> None:
    """The project's record, read before QA judges (#131): the session's start says it.

    Read from the branch under test, where it can't differ from main's:
    delivery refuses any change to the record (`bounds`). A record that can't
    be read raises, which fails this card's verification with the reason
    rather than judging it against rules nobody can see.
    """
    # Which changed files a regression guard reads is by its parts' profiles (#404).
    profiles.set_project(read_record(worktree))
