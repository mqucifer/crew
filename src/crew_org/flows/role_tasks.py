"""A role's own task: work a role owns, not an epic (ADR 0034, crew#839).

Only epics and bugs brought work to the crew, so work a role saw outside its story
had no way in: DevOps found that sprint-metrics' `requires_db` tests never ran in
CI, and could only set it aside as pre-existing (mqucifer/sprint-metrics#667).

A Task is the board's `Task` work type. Its role is a `role:` label, since the
board's Owner Agent field says who last moved a card, not whose the work is. An
event or a role's finding opens one; it's admitted like a bug and worked by its
role's session to a pull request. Every Task carries a marker naming its role, its
kind and what it's for, so the event that opened it never opens a second.
"""

from __future__ import annotations

import contextlib
import copy
import fnmatch
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from crew_org.columns import NEEDS_REFINEMENT, READY
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows import artifacts
from crew_org.flows.moves import move_card
from crew_org.llm import reraise_if_down
from crew_org.tools.github_issues import IssueClient, load_trust
from crew_org.tools.github_project import Card, ProjectClient
from crew_org.work_types import TASK


@dataclass(frozen=True)
class TaskRole:
    """A role that owns Tasks, and what its session may change."""

    key: str
    # The role's name, as agents.yaml and the board's Owner Agent field say it.
    name: str
    # Its entry in agents.yaml, whose spec its session starts from.
    spec: str
    # The paths its Task may change, as `fnmatch` patterns. Anything else it needs
    # is its own finding, a bug or another Task; the scope doesn't widen.
    scope: tuple[str, ...] = ()
    # The milestone that brings its session, or "" when it has one now.
    waits_for: str = ""

    @property
    def label(self) -> str:
        return f"role:{self.key}"

    @property
    def has_session(self) -> bool:
        return not self.waits_for


ARCHITECT = TaskRole("architect", "Architect", "architect", scope=("docs/architecture.md",))
DEVOPS = TaskRole(
    "devops",
    "DevOps Engineer",
    "devops_engineer",
    # CI, the release and what the CI side of the tests needs (a workflow's service
    # containers are in its workflow file). The version lives in `pyproject.toml`
    # in a Python project, and `package.json` in a JavaScript one.
    scope=(
        ".github/*",
        "CHANGELOG.md",
        "pyproject.toml",
        "uv.lock",
        "package.json",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        # How the tests are run, which CI runs too.
        "pytest.ini",
        "tox.ini",
        "setup.cfg",
        "noxfile.py",
        # A version file, wherever the package keeps it.
        "VERSION",
        "version.py",
        "*/version.py",
        "_version.py",
        "*/_version.py",
        "__version__.py",
        "*/__version__.py",
        "Dockerfile",
        "*/Dockerfile",
        "compose.yaml",
        "compose.yml",
        "docker-compose.yml",
        "docker-compose.yaml",
        ".dockerignore",
        # The workspace file and the services it declares (the Sponsor, 2026-10-22).
        ".devcontainer/*",
        ".devcontainer.json",
    ),
)
# Filed and held: their sessions come with M8's release review (crew#830) and
# M10's test repository (mqucifer/project-print#12).
UX = TaskRole("ux", "UX Designer", "ux_designer", waits_for="M8")
QA = TaskRole("qa", "QA Engineer", "qa_engineer", waits_for="M10")

ROLES: dict[str, TaskRole] = {r.key: r for r in (ARCHITECT, DEVOPS, UX, QA)}
LABELS = {r.label: ("0e8a16", f"A Task the {r.name} owns (ADR 0034)") for r in ROLES.values()}

# The diagram and the release are the Sponsor's to approve before they merge: the
# diagram is the Sponsor's view of the system, once a Goal, and a release's merge is
# their only approval of it (ADR 0019). A Task filed from a finding merges after the
# Code Reviewer, as a bug does (ADR 0027).
DIAGRAM = "diagram"
FIRST_DIAGRAM = "first-diagram"
FIRST_WORKSPACE = "first-workspace"
NEED = "need"
RELEASE = "release"
FINDING = "finding"
SPONSOR_APPROVES = frozenset({DIAGRAM, FIRST_DIAGRAM, RELEASE})

_MARKER = re.compile(r"<!-- crew:task role=(\S+) kind=(\S+) key=(\S+) -->")


def marker(role: TaskRole, kind: str, key: str) -> str:
    return f"<!-- crew:task role={role.key} kind={kind} key={key} -->"


@dataclass(frozen=True)
class Marked:
    role: TaskRole
    kind: str
    key: str


def marked(body: str | None) -> Marked | None:
    """What a Task's body says it is: its role, kind and key, or None."""
    found = _MARKER.search(body or "")
    if found is None or found.group(1) not in ROLES:
        return None
    return Marked(ROLES[found.group(1)], found.group(2), found.group(3))


def role_of(card: Card) -> TaskRole | None:
    """The role a Task card names, by its label, or None for any other card."""
    if card.work_type != TASK:
        return None
    return next((r for r in ROLES.values() if r.label in card.labels), None)


def outside_scope(role: TaskRole, paths: list[str]) -> list[str]:
    """The changed paths a role's Task may not change: its role's scope leaves them out."""
    return [p for p in paths if not any(fnmatch.fnmatch(p, glob) for glob in role.scope)]


def task_start(role: TaskRole, today: str = "") -> str:
    """What a role's session is told its work is, before the Task itself.

    The date is the crew's: a model never supplies a date on its own (ADR 0019).
    """
    return (
        (f"Today is {today}. " if today else "")
        + f"This is the {role.name}'s own Task (ADR 0034), not a story. You work it as the "
        f"{role.name}, inside your own area. It may change only: "
        + ", ".join(f"`{g}`" for g in role.scope)
        + ". A change anywhere else is refused at `done`. What else you find isn't this "
        "Task's: leave it, and say it in `done`'s summary."
    )


def covers_tests(role: TaskRole) -> bool:
    """Whether the role's scope takes in test files, so a test can prove its Task."""
    return any(fnmatch.fnmatch(p, g) for g in role.scope for p in ("tests/test_x.py", "test_x.py"))


def done_specs(role: TaskRole) -> list[dict[str, Any]]:
    """The Developer's tools, with `done` told as a Task's is (crew#839).

    The Developer's `done` asks a test for each criterion and says QA follows. A Task
    may change only its scope and isn't QA'd, so on mqucifer/sprint-metrics#738 the
    Architect wrote a test outside its scope to satisfy it, and the gate sent it back.
    """
    from crew_org.session.tools import SPECS  # noqa: PLC0415

    scope = ", ".join(f"`{g}`" for g in role.scope)
    tests = covers_tests(role)
    proof = (
        f"a test that proves it, or the file within {scope} that shows it"
        if tests
        else f"the file within {scope} that shows it"
    )
    specs = [copy.deepcopy(s) for s in SPECS]
    for spec in specs:
        fn = spec["function"]
        if fn["name"] != "done":
            continue
        fn["description"] = (
            f"Finish: the Task is done. Name in each criterion's `test` what proves it: {proof}. "
            "First `done` checks that each one you name exists; if one doesn't, the reply says "
            "so. Then the crew checks your branch, and returns it if any of these fails: it "
            f"changes nothing outside {scope}; "
            + ("each merged test you changed or removed is named here, with why; " if tests else "")
            + "and the project's whole checks pass. Then the Code Reviewer reviews it. QA "
            "doesn't verify a Task."
        )
        properties = fn["parameters"]["properties"]
        properties["criteria"]["items"]["properties"]["test"] = {
            "type": "string",
            "description": f"What proves the criterion: {proof}.",
        }
        properties["docs"]["description"] = (
            f"The files within {scope} you updated, or why none describe this change."
        )
        if not tests:
            # Its scope has no tests to change.
            del properties["changed_tests"]
    return specs


def waits_for(card: Card) -> str:
    """The milestone that brings a Task's role its session, or "" when it has one."""
    role = role_of(card)
    return role.waits_for if role else ""


def render(
    role: TaskRole, *, kind: str, key: str, why: str, work: str, done_when: list[str]
) -> str:
    return "\n\n".join(
        [
            marker(role, kind, key),
            f"**Role:** {role.name}. A Task this role owns and works itself (ADR 0034).",
            f"## Why\n\n{why.strip()}",
            f"## The work\n\n{work.strip()}",
            "## Done when\n\n" + "\n".join(f"- [ ] {d}" for d in done_when),
        ]
    )


def existing(
    issues: IssueClient, repo: str, role: TaskRole, kind: str, key: str
) -> dict[str, Any] | None:
    """The Task already opened for this, open or closed, or None.

    Read by the role's label, every state: a closed one was done, and opening it again
    would do the work twice.
    """
    wanted = marker(role, kind, key)
    return next(
        (i for i in issues.labelled(repo, role.label) if wanted in (i.get("body") or "")), None
    )


@dataclass
class Opened:
    number: int
    new: bool


def open_task(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    repo: str,
    role: TaskRole,
    kind: str,
    key: str,
    title: str,
    body: str,
    points: int | None,
    cards: list[Card],
    parent: int | None = None,
    column: str = READY,
    by: str | None = None,
    event: str = "",
    rule: str = "",
) -> Opened:
    """Open the Task once, or finish placing one an earlier pass opened.

    Each step is checked from what's there, so one that failed part-way (the issue
    created, then the board or the link refused) is completed by the next pass
    rather than opened again. Raises if GitHub refuses: the caller logs it and the
    next pass tries again.
    """
    found = existing(issues, repo, role, kind, key)
    new = found is None
    if found is None:
        issues.ensure_label(
            repo, role.label, color=LABELS[role.label][0], description=LABELS[role.label][1]
        )
        found = issues.create(repo, title, body, labels=[role.label])
    number = int(found["number"])
    if not any(c.repo == repo and c.number == number for c in cards):
        item = board.add_issue(found["node_id"])
        board.set_select(item, "Work Type", TASK)
        if points is not None:
            board.set_number(item, "Points", points)
        move_card(
            board,
            sink,
            item_id=item,
            to=column,
            by=by,
            card=number,
            summary=f"{role.name}'s task — {title}"[:100],
        )
    if parent is not None:
        linked = {int(c["number"]) for c in issues.sub_issues(repo, parent)}
        if number not in linked:
            issues.add_sub_issue(repo, parent, found["id"])
    if new:
        sink.emit(
            CrewEvent(
                kind=EventKind.ROLE_TASK_OPENED,
                role=by,
                card=number,
                summary=f"{role.name}: {title}"[:100],
                detail={
                    "repo": repo,
                    "task_role": role.name,
                    "task_kind": kind,
                    "parent": parent,
                    "event": event or kind,
                    **({"rule": rule} if rule else {}),
                },
            )
        )
    return Opened(number, new)


def not_opened(
    sink: EventSink, *, repo: str, role: TaskRole, kind: str, card: int | None, why: Any
) -> str:
    """A Task that couldn't be opened: recorded, and said as a held line. The next
    pass tries again, since nothing marks it as opened."""
    reason = f"{role.name}'s {kind} task not opened: {why}"[:200]
    with contextlib.suppress(Exception):
        sink.note(
            EventKind.NOTE, reason, card=card, repo=repo, rule="decision.role_tasks.not_opened"
        )
    return reason


# Triage, as a bug's (ADR 0027): the Business Analyst writes what done means and sizes it.
TRIAGED_BY = "Business Analyst"
NEEDS_HUMAN = "needs:human"


def tasks_to_triage(cards: list[Card]) -> list[Card]:
    """Tasks a role filed from its finding, waiting in refinement for their triage.

    A Task an event opens is written whole and goes straight to Ready.
    """
    return sorted(
        (
            c
            for c in cards
            if c.work_type == TASK
            and c.status == NEEDS_REFINEMENT
            and c.state != "CLOSED"
            and NEEDS_HUMAN not in c.labels
        ),
        key=lambda c: (c.repo or "", c.number or 0),
    )


def accepted_body(filed: str, triage: Any) -> str:
    """The Task as filed, then what done means and its estimate."""
    return "\n".join(
        [
            filed.rstrip(),
            "",
            "## Done when (triaged)",
            "",
            *[f"- [ ] {d}" for d in triage.done_when],
            "",
            f"**Estimate** — {triage.points} points",
            "",
            "---",
            "",
            f"Triaged as a Task by the {TRIAGED_BY}: {triage.reason}",
        ]
    )


def triage_tasks(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    cards: list[Card],
    *,
    default_repo: str,
    context: Any,
    failed: list[tuple[int, str]],
) -> list[tuple[int, str]]:
    """Triage each filed Task waiting, and carry out its outcome. Returns (number, outcome)."""
    from crew_org.crews.bug_triage_crew import triage_task  # noqa: PLC0415

    done: list[tuple[int, str]] = []
    for card in tasks_to_triage(cards):
        repo = card.repo or default_repo
        number = card.number or 0
        role = role_of(card)
        filed = issues.get(repo, number).get("body") or ""
        sink.emit(
            CrewEvent(
                kind=EventKind.AGENT_STARTED,
                role=TRIAGED_BY,
                card=number,
                summary="triage a Task",
                detail={"repo": repo},
            )
        )
        try:
            shown, focus = context.focused_for(repo, f"{card.title}\n\n{filed}")
            triage = triage_task(
                card.title,
                filed,
                number=number,
                role=role.name if role else "role it names",
                repository=shown,
                reader=context.reader_for(repo, focus),
            )
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            failed.append((number, f"{type(exc).__name__}: {exc}"))
            sink.emit(
                CrewEvent(
                    kind=EventKind.AGENT_FAILED,
                    role=TRIAGED_BY,
                    card=number,
                    summary=str(exc)[:100],
                    detail={"repo": repo},
                )
            )
            continue
        carry_out(board, issues, sink, card, repo=repo, filed=filed, triage=triage)
        done.append((number, triage.outcome))
        sink.emit(
            CrewEvent(
                kind=EventKind.AGENT_FINISHED,
                role=TRIAGED_BY,
                card=number,
                summary=f"Task triaged: {triage.outcome}",
                detail={"repo": repo},
            )
        )
    return done


def carry_out(
    board: ProjectClient,
    issues: IssueClient,
    sink: EventSink,
    card: Card,
    *,
    repo: str,
    filed: str,
    triage: Any,
) -> None:
    """Do what the triage decided, on the issue and the board."""
    number = card.number or 0
    if triage.outcome == "accepted":
        issues.edit_issue(repo, number, body=accepted_body(filed, triage))
        board.set_number(card.item_id, "Points", float(triage.points or 0))
        move_card(
            board,
            sink,
            item_id=card.item_id,
            to=READY,
            by=TRIAGED_BY,
            card=number,
            frm=card.status,
            summary=f"Task accepted, {triage.points} points",
            rule="decision.role_tasks.carry_out",
        )
        return
    said = (
        f"A duplicate of #{triage.duplicate_of}: {triage.reason}"
        if triage.outcome == "duplicate"
        else f"Not a Task: {triage.reason}"
    )
    artifacts.comment(issues, sink, repo=repo, number=number, body=said, by=TRIAGED_BY)
    issues.close(
        repo, number, reason="duplicate" if triage.outcome == "duplicate" else "not_planned"
    )


# On the pull request, once: it asks the Sponsor for their approval.
ASK_MARKER = "<!-- crew:task-for-the-sponsor -->"


def for_the_sponsor(issues: IssueClient, repo: str, card: Card) -> bool:
    """Is this a Task whose work the Sponsor approves before it merges?"""
    if card.work_type != TASK:
        return False
    found = marked(issues.get(repo, card.number or 0).get("body"))
    return found is not None and found.kind in SPONSOR_APPROVES


def the_sponsor(configured: str | None) -> str:
    """The Sponsor's login: `GITHUB_SPONSOR`, else org.yaml's `trust.sponsor`, else "".

    Never "anyone": Claude's account is a person with Write access, so a gate that
    took any person's approval would take Claude's (the operator's review, crew#839).
    """
    if configured:
        return configured
    try:
        return load_trust().sponsor
    except Exception:  # noqa: BLE001 - no Sponsor named: the gate stays shut
        return ""


def sponsor_approved(reviews: list[dict[str, Any]], sponsor: str, head: str) -> bool:
    """The Sponsor's latest review on the pull request's current head is an approval.

    An approval of an older commit doesn't count, whatever branch protection does
    with stale reviews: a push after it needs a new one. No Sponsor, no approval.
    """
    if not sponsor or not head:
        return False
    theirs = [
        r
        for r in reviews
        if (r.get("user") or {}).get("login") == sponsor
        and r.get("commit_id") == head
        and r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")
    ]
    return bool(theirs) and theirs[-1].get("state") == "APPROVED"


def ask_the_sponsor(
    issues: IssueClient, sink: EventSink, *, repo: str, pull: int, card: int
) -> None:
    """Say once on the pull request that it waits for the Sponsor's approval."""
    if issues.has_comment_marked(repo, pull, ASK_MARKER):
        return
    artifacts.comment(
        issues,
        sink,
        repo=repo,
        number=pull,
        body=f"{ASK_MARKER}\n**For the Sponsor: approve to merge.** The Code Reviewer approved "
        "this role's Task. Its work, the repository's diagram or a release, is yours to "
        "approve before it merges (ADR 0034).",
        by=None,
    )


# A bug a role filed from its finding, so the same round doesn't file it twice.
_FINDING_BUG = "<!-- crew:finding key={key} -->"
BUG_LABEL = "bug"


def file_findings(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    repo: str,
    story: Card,
    found: list[Any],
    by: str,
    round_key: str,
    cards: list[Card],
) -> list[int]:
    """File a role's findings outside its story: a Task for the role it names, or a bug.

    At most one per owner for each story and round (a pull request's head), and each
    filing recorded, so a finding said again in other words in a later round shows in
    the events rather than as a flood of issues (the operator, crew#839). Filed or
    not, the story under review goes on: a failure is recorded, never raised.
    """
    filed: list[int] = []
    seen: set[str] = set()
    number = story.number or 0
    for finding in found:
        if finding.owner in seen:
            sink.emit(
                CrewEvent(
                    kind=EventKind.FINDING_FILED,
                    role=by,
                    card=number,
                    summary=f"{finding.owner} finding not filed: one per round"[:100],
                    detail={
                        "repo": repo,
                        "owner": finding.owner,
                        "filed": None,
                        "round": round_key,
                    },
                )
            )
            continue
        seen.add(finding.owner)
        key = f"s{number}-{finding.owner}-{round_key}"
        title = f"{finding.what.strip().rstrip('.')}"[:120]
        why = (
            f"The {by} found this while judging #{number}, outside what that story changes.\n\n"
            f"**Where:** {finding.where}\n\n**Evidence:** {finding.evidence}"
        )
        try:
            if finding.owner == "bug":
                filed_as = _file_bug(
                    issues,
                    board,
                    sink,
                    repo=repo,
                    key=key,
                    title=title,
                    why=why,
                    by=by,
                    cards=cards,
                )
            else:
                role = ROLES[finding.owner]
                filed_as = open_task(
                    issues,
                    board,
                    sink,
                    repo=repo,
                    role=role,
                    kind=FINDING,
                    key=key,
                    title=title,
                    body=render(
                        role,
                        kind=FINDING,
                        key=key,
                        why=why,
                        work=finding.what,
                        done_when=["Triaged: the Business Analyst writes what done means."],
                    ),
                    points=None,
                    cards=cards,
                    column=NEEDS_REFINEMENT,
                    by=by,
                    event="finding",
                ).number
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            sink.emit(
                CrewEvent(
                    kind=EventKind.FINDING_FILED,
                    role=by,
                    card=number,
                    summary=f"{finding.owner} finding not filed: {exc}"[:100],
                    detail={
                        "repo": repo,
                        "owner": finding.owner,
                        "filed": None,
                        "round": round_key,
                        "error": str(exc)[:200],
                    },
                )
            )
            continue
        filed.append(filed_as)
        sink.emit(
            CrewEvent(
                kind=EventKind.FINDING_FILED,
                role=by,
                card=number,
                summary=f"{finding.owner} finding filed as #{filed_as}"[:100],
                detail={
                    "repo": repo,
                    "owner": finding.owner,
                    "filed": filed_as,
                    "round": round_key,
                },
            )
        )
    return filed


def workspace_finding(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    repo: str,
    story: int,
    skipped: dict[str, str],
    down: dict[str, str],
    cards: list[Card],
) -> int | None:
    """DevOps's finding when the gate at `done` finds the workspace isn't what CI runs.

    Merged tests that skipped for a reason the project doesn't declare CI-only, and
    declared services that wouldn't start (crew#860). Neither is the story's: the
    workspace file is DevOps's (the Sponsor, 2026-10-22). Keyed by the reasons and the
    services, not by story, so every story that meets the same cause finds the one
    Task, open or closed, instead of filing its own. None when there's nothing to file.
    """
    if not skipped and not down:
        return None
    reasons = sorted(set(skipped.values()))
    cause = "\n".join([*reasons, *sorted(down)])
    key = "workspace-" + hashlib.sha256(cause.encode()).hexdigest()[:12]
    lines = [f"- `{name}`: {why}" for name, why in sorted(down.items())]
    for reason in reasons:
        tests = sorted(t for t, r in skipped.items() if r == reason)
        shown = ", ".join(f"`{t}`" for t in tests[:5]) + (
            f" and {len(tests) - 5} more" if len(tests) > 5 else ""
        )
        lines.append(f'- {len(tests)} skipped for "{reason}": {shown}')
    why = (
        f"The gates at `done` on #{story} found the workspace doesn't run what CI runs:\n\n"
        + "\n".join(lines)
        + "\n\nNone of it is that story's: the workspace file and its services are DevOps's."
    )
    opened = open_task(
        issues,
        board,
        sink,
        repo=repo,
        role=DEVOPS,
        kind=FINDING,
        key=key,
        title="The workspace doesn't run what CI runs",
        body=render(
            DEVOPS,
            kind=FINDING,
            key=key,
            why=why,
            work="Make the workspace match CI: declare each service the tests need in "
            "`.devcontainer/` so they run there, or, where CI skips them too, declare the "
            "reason in `devcontainer.json`'s `customizations.crew.ciOnlySkips`.",
            done_when=["Triaged: the Business Analyst writes what done means."],
        ),
        points=None,
        cards=cards,
        column=NEEDS_REFINEMENT,
        by="Developer",
        event="finding",
        rule="check.gates.these_tests_skipped" if skipped else "check.gates.a_service_wont_start",
    )
    sink.emit(
        CrewEvent(
            kind=EventKind.FINDING_FILED,
            role="Developer",
            card=story,
            summary=f"devops finding filed as #{opened.number}"[:100],
            detail={"repo": repo, "owner": DEVOPS.key, "filed": opened.number, "round": key},
        )
    )
    return opened.number


def changes_workspace(role: TaskRole | None, worktree: Any, paths: list[str]) -> bool:
    """A DevOps Task that changes the workspace file or the compose file it names: its
    gate at `done` runs the services and setup its own branch declares (crew#839)."""
    from crew_org.tools import bounds, devcontainer  # noqa: PLC0415

    return role is DEVOPS and any(
        p in devcontainer.PATHS or bounds.is_compose(worktree, p) for p in paths
    )


# The pull request's section naming the merged tests a workspace Task's services turn
# on that fail: none is the Task's, and they're filed for the Developer as one bug when
# it merges, since only then do they fail on main (the operator, crew#839).
TURNED_ON_HEADING = "## Merged tests the workspace turns on, which fail"
_TURNED_ON = re.compile(r"^- `([^`]+)`: ?(.*)$", re.M)


def turned_on(body: str | None) -> dict[str, str]:
    """The tests a merged pull request's `TURNED_ON_HEADING` names, with each one's line."""
    text = body or ""
    at = text.find(TURNED_ON_HEADING)
    if at == -1:
        return {}
    section = text[at + len(TURNED_ON_HEADING) :].split("\n## ", 1)[0]
    return dict(_TURNED_ON.findall(section))


def turned_on_bug(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    repo: str,
    task: int,
    tests: dict[str, str],
    cards: list[Card],
) -> int | None:
    """The Developer's bug for the merged tests a workspace Task turned on that fail, filed
    once its pull request merged (crew#839): a finding filed as a bug, as a role's is.

    Keyed by the tests, so the same set is filed once. None when there are none.
    """
    if not tests:
        return None
    key = "turned-on-" + hashlib.sha256("\n".join(sorted(tests)).encode()).hexdigest()[:12]
    shown = [f"- `{t}`: {line}" if line else f"- `{t}`" for t, line in sorted(tests.items())]
    why = (
        f"#{task} merged the workspace's services, so the workspace now runs tests that "
        f"used to skip there, and these {len(tests)} fail or error:\n\n"
        + "\n".join(shown)
        + f"\n\nNone was #{task}'s: its branch changed none of them, nor a module or "
        "`conftest.py` they're in. Fixing them is the Developer's."
    )
    title = f"{len(tests)} merged tests fail now the workspace runs its services"
    return _file_bug(
        issues, board, sink, repo=repo, key=key, title=title, why=why, by=DEVOPS.name, cards=cards
    )


def _file_bug(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    repo: str,
    key: str,
    title: str,
    why: str,
    by: str,
    cards: list[Card],
) -> int:
    """A bug, filed the way a collaborator files one (ADR 0027): triage takes it from
    refinement with no work type."""
    wanted = _FINDING_BUG.format(key=key)
    found = next(
        (i for i in issues.labelled(repo, BUG_LABEL) if wanted in (i.get("body") or "")), None
    )
    if found is None:
        found = issues.create(
            repo,
            title,
            f"{wanted}\n\n**What happens:** {title}\n\n{why}\n\n"
            "**Expected:** what the project's criteria and docs say.",
            labels=[BUG_LABEL],
        )
    number = int(found["number"])
    if not any(c.repo == repo and c.number == number for c in cards):
        item = board.add_issue(found["node_id"])
        move_card(
            board,
            sink,
            item_id=item,
            to=NEEDS_REFINEMENT,
            by=by,
            card=number,
            summary=f"bug filed from a finding — {title}"[:100],
        )
    return number


# The repository's own C4 diagram: what's built now (ADR 0036). Each Goal's target
# diagram becomes it, corrected to what was built, when the Goal's epics are done.
DIAGRAM_PATH = "docs/architecture.md"
DIFFERS_HEADING = "## Where the build differs from the target"
_MERMAID = re.compile(r"```mermaid\n(.*?)\n```", re.S)
DIAGRAM_POINTS = 2
FIRST_DIAGRAM_POINTS = 3


def diagram_problems(text: str | None) -> list[str]:
    """What's wrong with the repository's diagram file as the Architect's Task leaves it."""
    from crew_org import mermaid  # noqa: PLC0415

    if text is None:
        return [f"`{DIAGRAM_PATH}` doesn't exist"]
    blocks = _MERMAID.findall(text)
    if not blocks:
        return [f"`{DIAGRAM_PATH}` has no Mermaid block"]
    # Every block, since GitHub renders each: on mqucifer/sprint-metrics#740 only the
    # first was checked, and the second and third didn't render (crew#839).
    problems: list[str] = []
    kinds: list[str] = []
    for n, block in enumerate(blocks, 1):
        lines = block.strip().splitlines()
        kind = lines[0].strip() if lines else ""
        kinds.append(kind)
        if kind in mermaid.C4_TYPES:
            found = mermaid.c4_problems(block)
        elif kind == "sequenceDiagram":
            found = mermaid.sequence_problems(block)
        else:
            found = [
                f"`{kind[:40]}` isn't one the crew checks: {', '.join(mermaid.C4_TYPES)} "
                "or sequenceDiagram"
            ]
        problems += [f"block {n}, {kind[:40] or 'empty'}: {p}" for p in found]
    if not any(k in mermaid.C4_TYPES for k in kinds):
        problems.append(f"no block is a C4 diagram ({', '.join(mermaid.C4_TYPES)})")
    return problems


def goal_diagram_task(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    repo: str,
    goal: Card,
    epics: list[int],
    cards: list[Card],
) -> Opened:
    """The Architect's Task once a Goal's epics are done: its target diagram becomes the
    repository's, corrected to what was built (ADR 0036, crew#839)."""
    from crew_org.flows.goal_look import diagram_comment, diagram_of  # noqa: PLC0415

    number = goal.number or 0
    target = diagram_of(issues, repo, number)
    link = str((diagram_comment(issues, repo, number) or {}).get("html_url") or "")
    shown = (
        f"The Goal's target diagram ([its comment]({link})):\n\n```mermaid\n{target}\n```"
        if target
        else "The Goal has no target diagram (its look came before ADR 0036): draw the "
        "system as the code has it."
    )
    body = render(
        ARCHITECT,
        kind=DIAGRAM,
        key=f"goal-{number}",
        why=f"All of Goal #{number}'s epics are done: "
        + ", ".join(f"#{n}" for n in epics)
        + ". The repository's diagram changes only when the work is done.",
        work=f"Make `{DIAGRAM_PATH}` the system as built: the Goal's target diagram, "
        "corrected to what the code shows. Read the code; where it differs from the "
        f"target, the code wins, and the difference is written under `{DIFFERS_HEADING}`.\n\n"
        + shown,
        done_when=[
            f"`{DIAGRAM_PATH}` draws the system as built, as a C4 diagram in Mermaid",
            f"`{DIFFERS_HEADING}` names each difference from the target, or says there's none",
        ],
    )
    return open_task(
        issues,
        board,
        sink,
        repo=repo,
        role=ARCHITECT,
        kind=DIAGRAM,
        key=f"goal-{number}",
        title=f"The repository's diagram after Goal #{number}",
        body=body,
        points=DIAGRAM_POINTS,
        cards=cards,
        parent=number,
        event="goal.epics_done",
    )


def first_diagram_task(
    issues: IssueClient, board: ProjectClient, sink: EventSink, *, repo: str, cards: list[Card]
) -> Opened | None:
    """The Architect's first Task in a repository with no diagram: draw it (crew#839).

    None when the repository has one. The roadmap's reset has every project's C4
    diagram drawn as the Architect's first task, so a first tick opens one per project.
    """
    if issues.file_at(repo, DIAGRAM_PATH, issues.default_branch(repo)) is not None:
        return None
    body = render(
        ARCHITECT,
        kind=FIRST_DIAGRAM,
        key="repository",
        why=f"`{repo}` has no diagram of its system yet. Each Goal's target diagram is "
        "drawn from it, and becomes it once the Goal is done (ADR 0036).",
        work=f"Draw `{DIAGRAM_PATH}`: the system as the code has it, as a C4 diagram in "
        "Mermaid (C4 context and containers, components where they help). Read the code, "
        "the project's record and its docs; draw what's there, not what's planned.",
        done_when=[
            f"`{DIAGRAM_PATH}` draws the system as the code has it, as a C4 diagram in Mermaid",
        ],
    )
    return open_task(
        issues,
        board,
        sink,
        repo=repo,
        role=ARCHITECT,
        kind=FIRST_DIAGRAM,
        key="repository",
        title="Draw the repository's C4 diagram",
        body=body,
        points=FIRST_DIAGRAM_POINTS,
        cards=cards,
        event="repository.no_diagram",
    )


@dataclass
class FirstDiagrams:
    opened: list[tuple[str, int]]
    failed: list[tuple[str, str]]


def first_diagrams(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    repos: set[str],
    cards: list[Card],
) -> FirstDiagrams:
    """Each repository with no diagram gets the Architect's Task to draw it, once.

    A repository that can't be read, or whose Task GitHub refused, is said as held and
    tried again next pass.
    """
    return _first_tasks(
        first_diagram_task, ARCHITECT, FIRST_DIAGRAM, issues, board, sink, repos, cards
    )


def _first_tasks(
    open_one: Any,
    role: TaskRole,
    kind: str,
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    repos: set[str],
    cards: list[Card],
) -> FirstDiagrams:
    """A role's first Task in each repository that lacks what it makes, once."""
    result = FirstDiagrams([], [])
    for repo in sorted(repos):
        try:
            opened = open_one(issues, board, sink, repo=repo, cards=cards)
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            result.failed.append(
                (repo, not_opened(sink, repo=repo, role=role, kind=kind, card=None, why=exc))
            )
            continue
        if opened is not None and opened.new:
            result.opened.append((repo, opened.number))
    return result


# What DevOps's first workspace Task makes, by the Development Container specification.
WORKSPACE_PATH = ".devcontainer/devcontainer.json"
FIRST_WORKSPACE_POINTS = 3


def first_workspace_task(
    issues: IssueClient, board: ProjectClient, sink: EventSink, *, repo: str, cards: list[Card]
) -> Opened | None:
    """DevOps's first Task in a repository with no workspace file: write it (crew#860).

    The Sponsor (2026-10-22): DevOps keeps each project's workspace file true, since
    the workspace has to match CI and DevOps owns CI. ADR 0032 made the file the
    workspace's one source, but no step ever wrote one. None when the repository has it.
    """
    from crew_org.tools.devcontainer import PATHS  # noqa: PLC0415

    branch = issues.default_branch(repo)
    if any(issues.file_at(repo, path, branch) is not None for path in PATHS):
        return None
    body = render(
        DEVOPS,
        kind=FIRST_WORKSPACE,
        key="repository",
        why=f"`{repo}` has no workspace file. The Developer's workspace runs on the crew's "
        "fallback image, with no services, so tests that need one (a database) skip there "
        "and the gate at `done` can't tell a skip CI makes from one it doesn't. The "
        "workspace has to run what CI runs.",
        work=f"Write `{WORKSPACE_PATH}` (the Development Container specification), as the "
        "crew reads it: the workspace's image by digest, one setup command as an array of "
        "words, and `containerEnv`. Declare each service the tests need in a compose file "
        "beside it, named by `dockerComposeFile`, with the workspace's own `service`: each "
        "service's image by digest, a `user` that isn't root, a `healthcheck` in "
        '["CMD", ...] form and `tmpfs` for its data. `image_digest` reads each digest from '
        "its registry. At `done` the gate starts the services and setup your branch "
        "declares and runs the whole checks with them. A merged test they turn on that "
        "fails, in a file your branch doesn't change, is the Developer's: it's named in "
        "your pull request and filed as a bug when it merges. CI isn't changed here: "
        "a later Task makes CI's test job run the same services, once the tests pass "
        "in the workspace on main (the Sponsor, 2026-10-23).",
        done_when=[
            f"`{WORKSPACE_PATH}` names the workspace's image by digest and its setup",
            "Each service the tests need is declared beside it and starts in the workspace",
        ],
    )
    return open_task(
        issues,
        board,
        sink,
        repo=repo,
        role=DEVOPS,
        kind=FIRST_WORKSPACE,
        key="repository",
        title="Write the workspace file and the services its tests need",
        body=body,
        points=FIRST_WORKSPACE_POINTS,
        cards=cards,
        event="repository.no_workspace",
    )


def need_tasks(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    repo: str,
    goal: int,
    needs: list[str],
) -> list[int]:
    """DevOps's Task under the Goal for each service or toolchain its look says it needs.

    The Sponsor (2026-10-22): the Architect names what the Goal needs, at the level of
    intent ("a Postgres for the tests"), and DevOps's Task works out the image, the
    declaration and CI's side (ADR 0035). Keyed by the Goal and the need, so a look
    read again opens none twice. One that can't be opened is recorded, and the look
    goes on: the Task is DevOps's, not the Goal's record.
    """
    opened: list[int] = []
    for need in needs:
        key = f"goal-{goal}-" + hashlib.sha256(need.strip().lower().encode()).hexdigest()[:12]
        body = render(
            DEVOPS,
            kind=NEED,
            key=key,
            why=f"The Goal's look on #{goal} says it needs {need.strip().rstrip('.')}, and "
            "the project's workspace doesn't have it yet.",
            work="Give the workspace and CI what the Goal needs: declare it in "
            "`.devcontainer/`, and make CI's test job run the same, so its tests run in "
            "both.",
            done_when=["Triaged: the Business Analyst writes what done means."],
        )
        try:
            found = open_task(
                issues,
                board,
                sink,
                repo=repo,
                role=DEVOPS,
                kind=NEED,
                key=key,
                title=f"The workspace for Goal #{goal}: {need.strip().rstrip('.')}"[:120],
                body=body,
                points=None,
                cards=board.cards(),
                parent=goal,
                column=NEEDS_REFINEMENT,
                by="Architect",
                event="goal.needs",
            )
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            not_opened(sink, repo=repo, role=DEVOPS, kind=NEED, card=goal, why=exc)
            continue
        opened.append(found.number)
    return opened


def goals_needs(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    repos: set[str],
    cards: list[Card],
) -> list[int]:
    """Each open Goal's needs, from its record, opened as DevOps's Tasks where they aren't.

    The look runs once per epic, so a need Task GitHub refused then would never be
    opened again: each pass tries, as for the other Tasks, and `open_task` opens none
    twice (crew#860).
    """
    from crew_org.flows import record as record_flow  # noqa: PLC0415
    from crew_org.work_types import GOAL  # noqa: PLC0415

    opened: list[int] = []
    for card in cards:
        repo = card.repo or ""
        if card.work_type != GOAL or card.state == "CLOSED" or repo not in repos or not card.number:
            continue
        try:
            held = record_flow.goal_record_comment(issues, repo, card.number)
        except Exception as exc:  # noqa: BLE001 - read again next pass
            reraise_if_down(exc)
            not_opened(sink, repo=repo, role=DEVOPS, kind=NEED, card=card.number, why=exc)
            continue
        if not held:
            continue
        needs = record_flow.parse(record_flow.comment_text(held.get("body") or "")).needs
        if needs:
            opened += need_tasks(
                issues, board, sink, repo=repo, goal=card.number, needs=list(needs)
            )
    return opened


def first_workspaces(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    repos: set[str],
    cards: list[Card],
) -> FirstDiagrams:
    """Each repository with no workspace file gets DevOps's Task to write it, once.

    Held and tried again next pass, as the first diagram is.
    """
    return _first_tasks(
        first_workspace_task, DEVOPS, FIRST_WORKSPACE, issues, board, sink, repos, cards
    )


RELEASE_POINTS = 3
# Splits from this build on plan no release story, so only their epics get a release
# Task: one split before it may already have its release story. Nothing in the worked
# repositories was created on or after this day when it was set (crew#839).
RELEASE_TASKS_FROM = datetime(2026, 10, 22, tzinfo=UTC)

CI_SERVICES = "ci-services"
CI_SERVICES_POINTS = 3


def ci_services_task(
    issues: IssueClient, board: ProjectClient, sink: EventSink, *, repo: str, cards: list[Card]
) -> Opened | None:
    """DevOps's second workspace Task: CI's test job runs the workspace's services
    (the Sponsor, 2026-10-23, crew#839).

    Its caller opens it when the tests that need them pass in the workspace on main: a
    story's whole checks passed at `done` with the default branch's services up. Only
    once the first workspace Task is closed, so it follows the workspace onto main
    rather than racing it. None until then, and once it's opened.
    """
    first = existing(issues, repo, DEVOPS, FIRST_WORKSPACE, "repository")
    if first is None or first.get("state") != "closed":
        return None
    body = render(
        DEVOPS,
        kind=CI_SERVICES,
        key="repository",
        why=f"`{repo}`'s workspace runs the services its tests need, and the tests that need "
        "them pass there. CI's test job doesn't run them yet, so CI skips what the "
        "workspace runs.",
        work="Make CI's test job run the same services as the workspace file's compose "
        "file: the same images by digest, as the workflow's service containers. Name the "
        "skip reasons CI still makes, and the workspace makes too, in "
        "`customizations.crew.ciOnlySkips`.",
        done_when=[
            "CI's test job runs the same services, and the tests that need them run there",
            "Every skip left in the workspace is one CI makes too, declared in `ciOnlySkips`",
        ],
    )
    return open_task(
        issues,
        board,
        sink,
        repo=repo,
        role=DEVOPS,
        kind=CI_SERVICES,
        key="repository",
        title="CI's test job runs the workspace's services",
        body=body,
        points=CI_SERVICES_POINTS,
        cards=cards,
        event="workspace.tests_pass",
    )


def epic_release_task(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    repo: str,
    epic: Card,
    stories: list[int],
    cards: list[Card],
    rule: str = "decision.role_tasks.epic_split",
) -> str:
    """DevOps's Task for a split epic's CI and release, one per epic (crew#839).

    Epics release themselves (ADR 0019): this replaces the release story the split
    used to plan. It waits for every story of the epic, whatever their numbers. Returns
    "" once it's open, or why it isn't. Nothing has to be written for the next pass to
    try again: an epic split since `RELEASE_TASKS_FROM` with no release Task under it on
    the board is what `pending_release_tasks` looks for.
    """
    number = epic.number or 0
    body = render(
        DEVOPS,
        kind=RELEASE,
        key=f"epic-{number}",
        why=f"Epic #{number} was split"
        + (" into " + ", ".join(f"#{n}" for n in stories) if stories else "")
        + ". Each epic releases itself (ADR 0019), and its CI and release are DevOps's.",
        work="This waits until every story of the epic is done.\n\n"
        "**CI:** CI runs what the epic's stories need, with nothing skipped that it should "
        "run: services the tests need, jobs, the image's platforms. CI gaps a story hit "
        "along the way may already have been worked: as a Task or a bug filed from a "
        "finding, or through the gate at `done` that refuses a skipped test CI runs "
        "(mqucifer/crew#860, step 4). Read the epic's stories and their pull requests first.\n\n"
        "**Release:** release what `[Unreleased]` holds, another epic's merged entries "
        "included (ADR 0019). The version is the one the most significant entry needs "
        "(Keep a Changelog, SemVer), or the Product Owner's call where the epic's record "
        "makes one. The section is dated today, as this Task's start says.",
        done_when=[
            "CI runs every test the epic's stories added, and none it should run is skipped",
            "`CHANGELOG.md` moves `[Unreleased]` to the new version, dated today",
            "The version is bumped where the project keeps it",
        ],
    )
    try:
        open_task(
            issues,
            board,
            sink,
            repo=repo,
            role=DEVOPS,
            kind=RELEASE,
            key=f"epic-{number}",
            title=f"CI and release for #{number}: {epic.title}"[:120],
            body=body,
            points=RELEASE_POINTS,
            cards=cards,
            parent=number,
            event="epic.split",
            rule=rule,
        )
    except Exception as exc:  # noqa: BLE001
        reraise_if_down(exc)
        return not_opened(sink, repo=repo, role=DEVOPS, kind=RELEASE, card=number, why=exc)
    return ""


def pending_release_tasks(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    cards: list[Card],
    repos: set[str] | None,
) -> list[tuple[int, str]]:
    """Open the release Tasks an earlier split couldn't. Returns (epic, why) for each
    still not open.

    Read from the board alone: an open epic with stories split since
    `RELEASE_TASKS_FROM` and no DevOps Task under it. A Task opened but not yet linked
    to its epic is linked by `open_task` from its marker, not opened twice.
    """
    failed: list[tuple[int, str]] = []
    for epic in cards:
        if (
            epic.work_type != "Epic"
            or epic.state == "CLOSED"
            or (repos is not None and epic.repo not in repos)
        ):
            continue
        under = [c for c in cards if c.parent == epic.number and c.repo == epic.repo]
        split_since = any(
            c.work_type == "Story" and c.created is not None and c.created >= RELEASE_TASKS_FROM
            for c in under
        )
        if not split_since or any(role_of(c) is DEVOPS for c in under):
            continue
        stories = sorted(
            c.number or 0
            for c in cards
            if c.parent == epic.number and c.repo == epic.repo and c.work_type == "Story"
        )
        why = epic_release_task(
            issues, board, sink, repo=epic.repo or "", epic=epic, stories=stories, cards=cards
        )
        if why:
            failed.append((epic.number or 0, why))
    return failed
