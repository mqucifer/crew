"""QA's Tasks: the tests in qa-suite, and a Developer's dispute of one (crew#896, M10).

QA's functional tests live in mqucifer/qa-suite, a folder per project, and its main
runs against every pull request of that project (ADR 0006 in mqucifer/project-print).
Three things open a QA Task there, each the test of behaviour on the project's main:

- **A story merges:** the test of its criteria, through the surface the project's
  folder names. Not at the split: qa-suite's main runs against every pull request, so a
  test of work not yet merged would fail everyone else's. Before the merge, QA's
  session verifies the story (crew#895), so the story isn't held for its test.
- **An escaped bug's fix merges:** the test that would have caught it. Triage accepts a
  bug only when it contradicts what the project accepted (ADR 0027), so each is one.
- **A Developer disputes a test** its pull request fails: QA fixes the test, or
  justifies it. The story waits, blocked on the Task; it isn't sent back.

A project with no folder in qa-suite opens none: its surface isn't named yet.
"""

from __future__ import annotations

import copy
import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crew_org.columns import BLOCKED, DONE, IN_PROGRESS, READY, REVIEWING
from crew_org.events import CrewEvent, EventKind, EventSink, held
from crew_org.flows import artifacts
from crew_org.flows import role_tasks as rt
from crew_org.flows.moves import move_card
from crew_org.llm import reraise_if_down
from crew_org.rulebook import says
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient
from crew_org.work_types import BUG, STORY

SUITE = rt.SUITE
BY = rt.QA.name
STORY_TEST = rt.STORY_TEST
ESCAPED = rt.ESCAPED
DISPUTE = rt.DISPUTE
TEST_POINTS = 2
# Work merged before QA's Tasks were built has no Task: the suite starts from what
# merges after (crew#896).
FROM = datetime(2026, 10, 26, tzinfo=UTC)
# Where a QA Task's session reads the project it tests, inside its qa-suite worktree:
# never committed, and never in its scope.
REFERENCE = ".project"


def folder_readme(project: str) -> str:
    return f"{project}/README.md"


def merged_work(cards: list[Card], repos: set[str]) -> list[Card]:
    """Stories and bugs whose pull request merged since `FROM`, in a project the crew
    works: Done and closed as completed, never one closed as superseded."""
    return sorted(
        (
            c
            for c in cards
            if c.work_type in (STORY, BUG)
            and c.status == DONE
            and c.state == "CLOSED"
            and c.state_reason != "NOT_PLANNED"
            and c.closed is not None
            and c.closed >= FROM
            and c.repo in repos
            and c.repo != SUITE
        ),
        key=lambda c: (c.repo or "", c.number or 0),
    )


def test_key(card: Card) -> tuple[str, str]:
    """The kind and key of the Task a merged story or bug opens."""
    if card.work_type == BUG:
        return ESCAPED, f"{card.repo}-b{card.number}"
    return STORY_TEST, f"{card.repo}-s{card.number}"


@dataclass
class QATasks:
    opened: list[tuple[str, int]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def open_tests(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    cards: list[Card],
    repos: set[str],
) -> QATasks:
    """QA's Task for each story and bug merged in a project with a folder in qa-suite,
    once. One GitHub refused is recorded and opened by the next pass: nothing marks it
    opened until it is."""
    result = QATasks()
    work = merged_work(cards, repos)
    if not work:
        return result
    try:
        opened = {
            found.key
            for issue in issues.labelled(SUITE, rt.QA.label)
            if (found := rt.marked(issue.get("body"))) is not None
        }
        branch = issues.default_branch(SUITE)
    except Exception as exc:  # noqa: BLE001 - read again next pass
        reraise_if_down(exc)
        result.failed.append(
            (
                SUITE,
                rt.not_opened(sink, repo=SUITE, role=rt.QA, kind=STORY_TEST, card=None, why=exc),
            )
        )
        return result
    folders: dict[str, bool] = {}
    for card in work:
        kind, key = test_key(card)
        if key in opened:
            continue
        project = card.repo or ""
        try:
            if project not in folders:
                folders[project] = issues.file_at(SUITE, folder_readme(project), branch) is not None
            if not folders[project]:
                continue
            number = test_task(issues, board, sink, card=card, kind=kind, key=key, cards=cards)
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            result.failed.append(
                (
                    f"{project}#{card.number}",
                    rt.not_opened(
                        sink, repo=SUITE, role=rt.QA, kind=kind, card=card.number, why=exc
                    ),
                )
            )
            continue
        result.opened.append((f"{project}#{card.number}", number))
    return result


def test_task(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    card: Card,
    kind: str,
    key: str,
    cards: list[Card],
) -> int:
    """Open QA's Task for one merged story or bug, with what it merged as."""
    project, number = card.repo or "", card.number or 0
    ref = f"{issues.owner}/{project}#{number}"
    filed = issues.get(project, number).get("body") or ""
    readme = folder_readme(project)
    if kind == ESCAPED:
        why = (
            f"The fix for the bug {ref} merged. The bug broke behaviour {project} had "
            "accepted, and no test of QA's caught it."
        )
        work = (
            f"Add the test to `{project}/` that would have caught {ref}: it fails on the "
            "behaviour the bug describes, and passes on the fix."
        )
        title = f"The test that would have caught {project}#{number}: {card.title}"
        done_when = [f"A test in `{project}/` fails on the bug {ref} describes and passes now"]
    else:
        why = (
            f"The story {ref} merged, so what it built is on {project}'s main, and a test "
            "of it runs on every pull request there (ADR 0006 in mqucifer/project-print). "
            "Its test comes after the merge: one of work not yet merged would fail every "
            "other pull request."
        )
        work = (
            f"Add a test of the story's acceptance criteria to `{project}/`, one per "
            "criterion the surface reaches."
        )
        title = f"The test of {project}#{number}: {card.title}"
        done_when = [f"Each criterion of {ref} the surface reaches has a test in `{project}/`"]
    work += (
        f"\n\nThrough the surface `{readme}` names, and nothing else: no imports of "
        f"{project}'s code, no reads of its database. Add each test to the README's table. "
        f"{project} as it is now is at `{REFERENCE}/{project}/` in your worktree, to read and "
        "never to change: its docs say how the surface behaves. Your tests run in CI against "
        f"{project}'s main when your pull request opens, and a red one comes back to you."
        "\n\nIf no criterion can be reached through the surface, change nothing and say why "
        "in `done`'s `no_test`: that's recorded, so the retro sees where the surface falls "
        f"short.\n\n## {'The bug' if kind == ESCAPED else 'The story'} as it merged\n\n"
        f"**{card.title}**\n\n{filed.strip()}"
    )
    body = rt.render(rt.QA, kind=kind, key=key, why=why, work=work, done_when=done_when)
    return rt.open_task(
        issues,
        board,
        sink,
        repo=SUITE,
        role=rt.QA,
        kind=kind,
        key=key,
        title=title[:120],
        body=body,
        points=TEST_POINTS,
        cards=cards,
        column=READY,
        by=BY,
        event="story.merged" if kind == STORY_TEST else "bug.fixed",
        labels=(rt.project_label(project),),
        detail={"project": project},
    ).number


def place_reference(ws: Any, worktree: Path, project: str, ref: str = "") -> None:
    """Put `project` at `ref` (its default branch when "") under `REFERENCE` in a QA
    Task's worktree, for its session to read: never committed, since the clone's
    `info/exclude` names it, and outside the Task's scope either way."""
    source = ws.for_repo(project)
    clone = source.current()
    target = worktree / REFERENCE / project
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    _exclude(worktree)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", ref or "HEAD"],
        cwd=clone,
        check=True,
        capture_output=True,
    )
    subprocess.run(["tar", "-x", "-C", str(target)], input=archive.stdout, check=True)


def _exclude(worktree: Path) -> None:
    """Name `REFERENCE` in the git directory's `info/exclude`, which every worktree of
    the clone shares, once."""
    common = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    path = (worktree / common).resolve() / "info" / "exclude"
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    line = f"/{REFERENCE}/"
    if line in current.splitlines():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        current + ("\n" if current and not current.endswith("\n") else "") + line + "\n"
    )


# On QA's Task when it closes with no change, saying why: for the record, and for the
# dispute's story, which reads the justification from it.
NO_TEST_MARKER = "<!-- crew:qa-no-test -->"
JUSTIFIED_MARKER = "<!-- crew:qa-justified -->"


def settle(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    task: Card,
    repo: str,
    why: str,
    justified: bool,
) -> str:
    """Close QA's Task with no change, saying why on it, and record it. Returns its rule.

    A dispute's story is returned by `settle_disputes`, from the closed Task, on this
    pass or the next.
    """
    if justified:
        _close(
            issues,
            board,
            sink,
            task=task,
            repo=repo,
            said=f"{JUSTIFIED_MARKER}\n**The test stands, unchanged.** {why}",
            summary="the disputed test stands",
            rule="decision.qa_tasks.justified",
        )
        return "decision.qa_tasks.justified"
    _close(
        issues,
        board,
        sink,
        task=task,
        repo=repo,
        said=f"{NO_TEST_MARKER}\n**No test: not reachable through the surface.** {why}",
        summary="no test: not reachable through the surface",
        rule="decision.qa_tasks.no_test",
    )
    return "decision.qa_tasks.no_test"


def _close(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    task: Card,
    repo: str,
    said: str,
    summary: str,
    rule: str,
) -> None:
    number = task.number or 0
    found = rt.marked(issues.get(repo, number).get("body"))
    artifacts.comment(issues, sink, repo=repo, number=number, body=said, by=BY)
    move_card(
        board,
        sink,
        item_id=task.item_id,
        to=DONE,
        by=BY,
        card=number,
        frm=task.status,
        summary=summary,
        rule=rule,
    )
    issues.close(repo, number)
    sink.emit(
        CrewEvent(
            kind=EventKind.ROLE_TASK_SETTLED,
            role=BY,
            card=number,
            summary=f"{BY}: {summary}"[:100],
            detail={
                "repo": repo,
                "task_role": BY,
                "task_kind": found.kind if found is not None else "",
                "project": rt.project_of(task.labels),
                "rule": rule,
            },
        )
    )


def reference_ref(body: str) -> str:
    """The commit of the project a QA Task's session reads: a dispute's pull request's
    head, or "" for the project's default branch."""
    found = _DISPUTED.search(body or "")
    return found.group("head") if found else ""


# --- the dispute ----------------------------------------------------------------------

# The required check a delivery repository's `qa.yml` makes (mqucifer/project-template#7).
QA_CHECK = "qa-suite / qa"
# On a story that waits for QA's dispute Task: neither for a person nor sent back.
WAITS_LABEL = "waits:qa"
DISPUTE_POINTS = 1
CI_SHOWN = 4_000
# In a dispute Task's body: the story and the pull request head it was disputed on.
DISPUTED = "<!-- crew:qa-dispute repo={repo} story={story} pull={pull} head={head} -->"
_DISPUTED = re.compile(
    r"<!-- crew:qa-dispute repo=(?P<repo>\S+) story=(?P<story>\d+) pull=(?P<pull>\d+) "
    r"head=(?P<head>[0-9a-f]+) -->"
)
# On the story, naming the Task it waits for.
WAITS = "<!-- crew:qa-dispute-waits task={task} -->"
_WAITS = re.compile(r"<!-- crew:qa-dispute-waits task=(\d+) -->")
# On the story's pull request when the Task is settled, once per Task.
ANSWERED = "<!-- crew:qa-dispute-answered task={task} -->"


def disputable(issues: IssueClient, repo: str, pull: dict[str, Any] | None) -> bool:
    """Did QA's check fail on the pull request's current head, as CI's return said?"""
    if pull is None or repo == SUITE:
        return False
    from crew_org.flows.ci import CI_MARKER, latest_ci_verdict  # noqa: PLC0415

    head = (pull.get("head") or {}).get("sha", "")
    said = latest_ci_verdict(issues, repo, pull["number"])
    return bool(head) and CI_MARKER.format(head=head) in said and f"`{QA_CHECK}`" in said


def dispute_specs() -> list[dict[str, Any]]:
    """The Developer's tools, with `blocked` able to dispute QA's failing test."""
    from crew_org.session.tools import SPECS  # noqa: PLC0415

    specs = [copy.deepcopy(s) for s in SPECS]
    for spec in specs:
        fn = spec["function"]
        if fn["name"] != "blocked":
            continue
        fn["description"] += (
            f" Or, with `kind` `test`, dispute a test of QA's that `{QA_CHECK}` failed on your "
            "pull request, when you think the test is wrong, not your code: name it in `test` "
            "as `<project>/<file>::<test>`, and say why in `missing`. QA's tests are in "
            "qa-suite, which you can't change. The story waits while QA fixes the test or "
            "says why it stands; it isn't sent back."
        )
        properties = fn["parameters"]["properties"]
        properties["kind"]["enum"] = [*properties["kind"]["enum"], "test"]
        properties["test"] = {
            "type": "string",
            "description": "With `kind` `test`: QA's test you dispute, `<project>/<file>::<test>`.",
        }
    return specs


@dataclass
class Disputed:
    """Where a dispute leaves the story: waiting on QA's Task, or back in a session
    with `start` (the test already justified, or one QA doesn't have)."""

    rule: str
    task: int = 0
    start: str = ""


def dispute(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    card: Card,
    repo: str,
    pull: dict[str, Any],
    said: dict[str, Any],
) -> Disputed:
    """Open QA's Task for a disputed test, once per test as it stands on qa-suite's main,
    and leave the story waiting on it (crew#896).

    Keyed by the test and its file's text: a test QA justified is the same key while the
    file is unchanged, so disputing it again ends "already justified", with its link; one
    QA has since changed is a new dispute.
    """
    test = str(said.get("test") or "").strip()
    why = str(said.get("missing") or "").strip()
    path, _, name = test.partition("::")
    branch = issues.default_branch(SUITE)
    text = issues.file_at(SUITE, path, branch) if path.startswith(f"{repo}/") else None
    if text is None or not name or not re.search(rf"\bdef {re.escape(name)}\b", text):
        return Disputed(
            "check.qa_tasks.dispute_names_no_test",
            start=says(
                "check.qa_tasks.dispute_names_no_test",
                test=test or "(none)",
                project=repo,
                check=QA_CHECK,
            ),
        )
    key = f"{repo}-d{card.number}-" + digest(f"{test}\n{text}")
    found = rt.existing(issues, SUITE, rt.QA, DISPUTE, key)
    if found is not None and found.get("state") == "closed":
        justified = _justification(issues, int(found["number"]))
        if justified is not None:
            rule = "decision.qa_tasks.already_justified"
            return Disputed(
                rule,
                task=int(found["number"]),
                start=says(
                    rule,
                    test=test,
                    task=f"{issues.owner}/{SUITE}#{found['number']}",
                    why=justified,
                ),
            )
    head = (pull.get("head") or {}).get("sha", "")
    number = card.number or 0
    ref = f"{issues.owner}/{repo}"
    from crew_org.flows.ci import latest_ci_verdict  # noqa: PLC0415

    ci = latest_ci_verdict(issues, repo, pull["number"])
    body = (
        rt.render(
            rt.QA,
            kind=DISPUTE,
            key=key,
            why=f"The Developer disputes `{test}`, which `{QA_CHECK}` failed on "
            f"{ref}#{pull['number']}, the pull request of {ref}#{number}. A failing QA "
            "test blocks the merge, and the Developer can't change qa-suite, so the story "
            "waits on this Task (ADR 0006 in mqucifer/project-print).",
            work=f"Decide whether the test is wrong. If it is, fix it in `{repo}/`: your pull "
            f"request runs it against {repo}'s main, and once it merges the story's check runs "
            "again. If it's right, change nothing and say why in `done`'s `justified`, for the "
            "Developer: the story goes back to them with the test unchanged, to change the code. "
            f"{repo} as the disputed pull request has it is at `{REFERENCE}/{repo}/` in your "
            f"worktree, to read and never to change.\n\n## The Developer's dispute\n\n"
            + "\n".join(f"> {line}" for line in (why or "(nothing said)").splitlines())
            + (f"\n\n## What CI said\n\n{ci[-CI_SHOWN:]}" if ci else ""),
            done_when=[f"`{test}` is fixed, or justified with why"],
        )
        + "\n\n"
        + DISPUTED.format(repo=repo, story=number, pull=pull["number"], head=head)
    )
    task = rt.open_task(
        issues,
        board,
        sink,
        repo=SUITE,
        role=rt.QA,
        kind=DISPUTE,
        key=key,
        title=f"Disputed: {test}"[:120],
        body=body,
        points=DISPUTE_POINTS,
        cards=board.cards(),
        column=READY,
        by="Developer",
        event="qa.disputed",
        labels=(rt.project_label(repo),),
        detail={"project": repo},
    ).number
    _wait(issues, board, sink, card=card, repo=repo, task=task, test=test)
    return Disputed("decision.qa_tasks.disputed", task=task)


def _wait(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    card: Card,
    repo: str,
    task: int,
    test: str,
) -> None:
    """The story waits, blocked on QA's Task: labelled so it's no person's to unblock."""
    number = card.number or 0
    move_card(
        board,
        sink,
        item_id=card.item_id,
        to=BLOCKED,
        by="Developer",
        card=number,
        frm=card.status,
        summary=f"waits for QA's dispute Task {SUITE}#{task}"[:100],
        kind=EventKind.CARD_BLOCKED,
        rule="decision.qa_tasks.disputed",
    )
    artifacts.label(issues, sink, repo=repo, number=number, by="Developer", add=[WAITS_LABEL])
    artifacts.comment(
        issues,
        sink,
        repo=repo,
        number=number,
        body=f"{WAITS.format(task=task)}\n**Waits for QA.** The Developer disputes `{test}`, "
        f"which QA's check failed on this story's pull request: {issues.owner}/{SUITE}#{task} "
        "fixes the test or says why it stands. Then the story goes on.",
        by="Developer",
    )


def _justification(issues: IssueClient, task: int) -> str | None:
    """Why QA said a test stands, from its closed Task, or None if it didn't."""
    said = [
        c.get("body") or ""
        for c in issues.comments(SUITE, task)
        if (c.get("body") or "").startswith(JUSTIFIED_MARKER)
    ]
    if not said:
        return None
    return said[-1].removeprefix(JUSTIFIED_MARKER).strip()


@dataclass
class Settled:
    returned: list[int] = field(default_factory=list)
    rerun: list[int] = field(default_factory=list)
    waiting: list[tuple[int, int]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def settle_disputes(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    ws: Any,
    *,
    cards: list[Card],
    repos: set[str],
) -> Settled:
    """Each story waiting on a dispute goes on once QA's Task is closed (crew#896).

    Justified: back to the Developer, with the test unchanged and QA's why, as CI's
    verdict on the same head, so its rework reads it. Fixed: the pull request's head
    moves by an empty commit, so CI runs the fixed test, and the story goes to review.
    One that fails is retried next pass: the label stays until it's done.
    """
    result = Settled()
    for card in sorted(
        (
            c
            for c in cards
            if c.status == BLOCKED
            and WAITS_LABEL in c.labels
            and c.state != "CLOSED"
            and c.repo in repos
        ),
        key=lambda c: (c.repo or "", c.number or 0),
    ):
        repo, number = card.repo or "", card.number or 0
        try:
            waits = [
                int(m.group(1))
                for c in issues.comments(repo, number)
                if (m := _WAITS.search(c.get("body") or "")) is not None
            ]
            if not waits:
                continue
            task = issues.get(SUITE, waits[-1])
            if task.get("state") != "closed":
                held("decision.qa_tasks.dispute_settled", repo=repo, card=number)
                result.waiting.append((number, waits[-1]))
                continue
            _go_on(issues, board, sink, ws, card=card, repo=repo, task=task)
        except Exception as exc:  # noqa: BLE001 - tried again next pass
            reraise_if_down(exc)
            result.failed.append((f"{repo}#{number}", f"dispute not settled: {exc}"[:200]))
            continue
        (result.returned if _justification(issues, waits[-1]) else result.rerun).append(number)
    return result


def _go_on(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    ws: Any,
    *,
    card: Card,
    repo: str,
    task: dict[str, Any],
) -> None:
    from crew_org.flows.ci import CI_MARKER  # noqa: PLC0415

    number = card.number or 0
    found = _DISPUTED.search(task.get("body") or "")
    pull = int(found.group("pull")) if found else 0
    if not pull:
        raise ValueError(f"{SUITE}#{task['number']} names no pull request")
    link = f"{issues.owner}/{SUITE}#{task['number']}"
    answered = ANSWERED.format(task=task["number"])
    live = issues.pull(repo, pull)
    head = (live.get("head") or {}).get("sha", "")
    justified = _justification(issues, int(task["number"]))
    if not issues.has_comment_marked(repo, pull, answered):
        if justified is not None:
            # As CI's verdict on this head, so the rework reads QA's answer with the log.
            body = (
                f"{CI_MARKER.format(head=head)}\n{answered}\n**QA's answer to the dispute: "
                f"the test stands, unchanged** ({link}). {justified}\n\nChange the code so "
                f"`{QA_CHECK}` passes."
            )
        else:
            _rerun(ws, repo, live, task=link)
            body = (
                f"{answered}\n**QA changed the disputed test** ({link}). `{QA_CHECK}` runs again."
            )
        artifacts.comment(issues, sink, repo=repo, number=pull, body=body, by=BY)
    move_card(
        board,
        sink,
        item_id=card.item_id,
        to=IN_PROGRESS if justified is not None else REVIEWING,
        by=BY,
        card=number,
        frm=BLOCKED,
        summary=(
            f"the disputed test stands ({link}): back to the Developer"
            if justified is not None
            else f"QA changed the disputed test ({link}): CI runs again"
        )[:100],
        rule="decision.qa_tasks.dispute_settled",
    )
    artifacts.label(issues, sink, repo=repo, number=number, by=BY, remove=[WAITS_LABEL])


def _rerun(ws: Any, repo: str, pull: dict[str, Any], *, task: str) -> None:
    """Move the pull request's head by an empty commit, so its checks run again."""
    branch = (pull.get("head") or {}).get("ref") or ""
    work = ws.for_repo(repo)
    path = work.open(branch, resume=True)
    try:
        work.commit(f"ci: run QA's check again, now {task} changed the test", allow_empty=True)
        work.push()
    finally:
        work.close(path)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]
