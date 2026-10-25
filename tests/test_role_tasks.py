"""A role's own Task on the board (ADR 0034, crew#839)."""

from __future__ import annotations

import pytest

from crew_org.columns import READY
from crew_org.flows import role_tasks
from crew_org.flows.role_tasks import ARCHITECT, DEVOPS, QA, UX, marked, open_task, render, role_of
from crew_org.tools.github_project import Card
from crew_org.work_types import DELIVERED, TASK
from tests.role_task_fakes import Board, Issues, events, sink


def _open(issues, board, s, cards=(), parent=None):
    return open_task(
        issues,
        board,
        s,
        repo="sm",
        role=DEVOPS,
        kind=role_tasks.RELEASE,
        key="epic-12",
        title="CI and release for #12",
        body=render(
            DEVOPS,
            kind=role_tasks.RELEASE,
            key="epic-12",
            why="#12 was split.",
            work="Release it.",
            done_when=["released"],
        ),
        points=3,
        cards=list(cards),
        parent=parent,
        event="epic.split",
    )


def test_a_task_is_delivered_work():
    assert TASK in DELIVERED


def test_a_task_names_its_role_by_label():
    card = Card(item_id="I1", number=1, work_type=TASK, labels=frozenset({"role:devops"}))
    assert role_of(card) is DEVOPS
    assert role_of(card.model_copy(update={"work_type": "Story"})) is None


def test_every_role_has_a_session_now():
    """The UX Designer's came with its release review (crew#830), QA's with M10 (crew#896)."""
    assert all(r.has_session for r in (ARCHITECT, DEVOPS, UX, QA))


def test_the_body_carries_its_marker():
    body = render(ARCHITECT, kind="diagram", key="goal-7", why="w", work="x", done_when=["d"])
    found = marked(body)
    assert found is not None
    assert (found.role, found.kind, found.key) == (ARCHITECT, "diagram", "goal-7")
    assert "**Role:** Architect" in body


def test_a_task_is_opened_once_placed_and_linked(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.add(12)
    first = _open(issues, board, s, parent=12)
    assert first.new
    assert ("select", "item-N901", "Work Type", TASK) in board.done
    assert ("status", "item-N901", READY) in board.done
    assert issues.subs[12] == [901]
    opened = [e for e in events(tmp_path) if e["kind"] == "role_task.opened"]
    assert opened[0]["detail"]["task_kind"] == "release"

    on_board = Card(item_id="item-N901", number=901, repo="sm", work_type=TASK)
    again = _open(issues, board, s, cards=[on_board], parent=12)
    assert (again.number, again.new) == (901, False)
    assert [d for d in issues.done if d[0] == "create"] == [
        ("create", 901, "CI and release for #12")
    ]
    assert issues.subs[12] == [901]
    assert len([e for e in events(tmp_path) if e["kind"] == "role_task.opened"]) == 1


def test_a_task_closed_is_not_opened_again(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    _open(issues, board, s)
    issues.issues[901]["state"] = "closed"
    assert _open(issues, board, s, cards=[Card(item_id="x", number=901, repo="sm")]).new is False


def test_a_task_half_placed_is_finished_by_the_next_pass(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.add(12)
    board.fail = "add"
    with pytest.raises(RuntimeError):
        _open(issues, board, s, parent=12)
    board.fail = None
    again = _open(issues, board, s, parent=12)
    assert again.number == 901
    assert ("add", "N901") in board.done
    assert issues.subs[12] == [901]


def _task(number: int, status: str, role: str, parent: int | None = None, points: int = 2) -> Card:
    return Card(
        item_id=f"I{number}",
        number=number,
        title=f"Task {number}",
        status=status,
        state="OPEN",
        work_type=TASK,
        labels=frozenset({f"role:{role}"}),
        repo="sm",
        points=points,
        parent=parent,
    )


def _story(number: int, status: str, parent: int) -> Card:
    return Card(
        item_id=f"I{number}",
        number=number,
        title=f"Story {number}",
        status=status,
        state="OPEN",
        work_type="Story",
        repo="sm",
        points=2,
        parent=parent,
    )


def test_a_task_for_a_role_with_no_session_is_held_and_says_which_milestone(monkeypatch):
    from dataclasses import replace

    from crew_org.columns import READY as R
    from crew_org.flows import role_tasks
    from crew_org.flows.role_tasks import waits_for
    from crew_org.flows.sprint import plan_sprint

    # Every role has its session now, so one is made to wait, as QA's did for M10.
    monkeypatch.setitem(role_tasks.ROLES, "ux", replace(UX, waits_for="M10"))
    ux, qa = _task(5, R, "ux"), _task(6, R, "qa")
    plan = plan_sprint([ux, qa], {}, sprint="S1", capacity=10)
    assert [c.number for c in plan.admitted] == [6]
    assert [c.number for c in plan.no_session] == [5]
    assert waits_for(ux) == "M10" and waits_for(qa) == ""


def test_the_release_task_waits_for_every_story_of_its_epic_whatever_its_number():
    from crew_org.columns import DONE
    from crew_org.columns import READY as R
    from crew_org.flows.siblings import unlanded_earlier_siblings

    release = _task(20, R, "devops", parent=10)
    # 21 came from a re-split after the Task was opened.
    cards = [release, _story(11, DONE, 10), _story(12, R, 10), _story(21, R, 10)]
    assert [c.number for c in unlanded_earlier_siblings(cards, release)] == [12, 21]
    # A story still waits only for earlier ones, as before.
    assert [c.number for c in unlanded_earlier_siblings(cards, cards[3])] == [12]


class _Triage:
    def __init__(self, outcome: str, **kw):
        self.outcome = outcome
        self.reason = kw.get("reason", "ci.yml has no postgres service")
        self.done_when = kw.get("done_when", [])
        self.points = kw.get("points")
        self.duplicate_of = kw.get("duplicate_of")


def test_an_accepted_task_gets_its_done_when_and_goes_to_ready(tmp_path):
    from crew_org.columns import NEEDS_REFINEMENT

    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.edit_issue = lambda repo, number, *, body: issues.done.append(("edit", body))
    card = _task(7, NEEDS_REFINEMENT, "devops")
    triage = _Triage("accepted", done_when=["CI runs the requires_db tests"], points=2)
    role_tasks.carry_out(board, issues, s, card, repo="sm", filed="Filed.", triage=triage)
    edited = next(d[1] for d in issues.done if d[0] == "edit")
    assert "- [ ] CI runs the requires_db tests" in edited
    assert ("status", "I7", READY) in board.done


def test_a_task_that_is_not_one_is_closed_with_why(tmp_path):
    from crew_org.columns import NEEDS_REFINEMENT

    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.add(7)
    card = _task(7, NEEDS_REFINEMENT, "devops")
    role_tasks.carry_out(
        board, issues, s, card, repo="sm", filed="Filed.", triage=_Triage("not_a_task")
    )
    assert ("close", 7) in issues.done
    assert any(d[0] == "comment" and "Not a Task" in d[2] for d in issues.done)


def test_only_filed_tasks_wait_for_triage():
    from crew_org.columns import NEEDS_REFINEMENT

    waiting = _task(7, NEEDS_REFINEMENT, "devops")
    asked = waiting.model_copy(update={"number": 8, "labels": waiting.labels | {"needs:human"}})
    ready = _task(9, READY, "architect")
    assert [c.number for c in role_tasks.tasks_to_triage([waiting, asked, ready])] == [7]


def test_the_task_triage_form_fits_its_outcome():
    from pydantic import ValidationError

    from crew_org.crews.bug_triage_crew import TaskTriage

    fields = {"reason": "r", "duplicate_of": None}
    TaskTriage(outcome="accepted", done_when=["CI runs it"], points=2, **fields)
    with pytest.raises(ValidationError):
        TaskTriage(outcome="accepted", done_when=[], points=2, **fields)
    with pytest.raises(ValidationError):
        TaskTriage(outcome="not_a_task", done_when=["x"], points=None, **fields)


def test_a_task_changes_only_its_roles_area():
    from crew_org.flows.role_tasks import outside_scope

    assert outside_scope(ARCHITECT, ["docs/architecture.md"]) == []
    assert outside_scope(ARCHITECT, ["docs/architecture.md", "src/app.py"]) == ["src/app.py"]
    changed = [".github/workflows/ci.yml", "pyproject.toml", "CHANGELOG.md", "src/x.py"]
    assert outside_scope(DEVOPS, changed) == ["src/x.py"]
    # How tests run, the version file and every lockfile are the release's too.
    release = [
        "pytest.ini",
        "tox.ini",
        "setup.cfg",
        "noxfile.py",
        "src/sprint_metrics/version.py",
        "src/sprint_metrics/__version__.py",
        "pnpm-lock.yaml",
        "yarn.lock",
        "npm-shrinkwrap.json",
    ]
    assert outside_scope(DEVOPS, release) == []
    assert outside_scope(DEVOPS, ["src/sprint_metrics/__init__.py"]) == [
        "src/sprint_metrics/__init__.py"
    ]


def test_devops_in_infra_changes_only_what_runs():
    """An alert's Task proposes in infra's own areas, not its CI or a project's files
    (crew#893); a project's DevOps Task is as it was."""
    from crew_org.flows.role_tasks import done_specs, outside_scope, task_start

    infra = ["grafana/alerts/a.yaml", "inventory/services/x.yaml", "runbooks/x.md"]
    assert outside_scope(DEVOPS, infra + ["compose/mac/x/compose.yaml"], "infra") == []
    assert outside_scope(DEVOPS, [".github/workflows/guard.yml", "src/x.py"], "infra") == [
        ".github/workflows/guard.yml",
        "src/x.py",
    ]
    assert outside_scope(DEVOPS, ["grafana/a.json"], "sprint-metrics") == ["grafana/a.json"]
    assert outside_scope(DEVOPS, ["pyproject.toml"], "sprint-metrics") == []
    assert "`runbooks/*`" in task_start(DEVOPS, repo="infra")
    assert "`runbooks/*`" not in task_start(DEVOPS, repo="sprint-metrics")
    done = next(s for s in done_specs(DEVOPS, repo="infra") if s["function"]["name"] == "done")
    assert "`grafana/*`" in done["function"]["description"]


def test_the_scope_is_said_where_the_gate_says_it():
    from crew_org.rulebook import says

    said = says("guard.role_tasks.out_of_scope", role="Architect", paths="`a`", scope="`b`")
    assert "Architect" in said and "`a`" in said


def test_a_roles_session_starts_as_that_role():
    from crew_org.flows.role_tasks import task_start
    from crew_org.session.start import system_text

    assert "DevOps Engineer" in system_text(DEVOPS.spec)
    assert "Architect" in system_text(ARCHITECT.spec)
    assert "`docs/architecture.md`" in task_start(ARCHITECT)


def test_qa_passes_a_task_to_merging(tmp_path):
    from crew_org.columns import MERGING, QAING
    from crew_org.flows.acceptance import run_qa

    board, s = Board(), sink(tmp_path)
    task = _task(30, QAING, "devops")
    result = run_qa(board, Issues(), s, None, None, cards=[task], repo="sm")
    assert ("status", "I30", MERGING) in board.done
    assert [o.card for o in result.verified] == [30]


def test_a_task_is_never_sent_to_be_split_again(tmp_path):
    from crew_org.flows.story_problem import return_to_refinement

    task = _task(30, READY, "devops", parent=10)
    assert not return_to_refinement(
        Board(), Issues(), sink(tmp_path), task, repo="sm", cards=[task], comment="", reason=""
    )


def test_a_rework_leaves_the_epics_task_alone():
    from crew_org.flows.board_flow import unstarted_children

    issues = Issues()
    issues.add(11)
    issues.add(30)
    issues.subs[10] = [11, 30]
    issues.pull_for_branch = lambda *a, **k: None
    cards = [_story(11, READY, 10), _task(30, READY, "devops", parent=10)]
    closable, _started = unstarted_children(issues, cards, "sm", 10)
    assert 30 not in closable


def test_the_diagram_and_the_release_wait_for_the_sponsor():
    from crew_org.flows.role_tasks import for_the_sponsor

    issues = Issues()
    issues.add(
        30, render(DEVOPS, kind="release", key="epic-10", why="w", work="x", done_when=["d"])
    )
    issues.add(31, render(DEVOPS, kind="finding", key="k", why="w", work="x", done_when=["d"]))
    assert for_the_sponsor(issues, "sm", _task(30, READY, "devops"))
    assert not for_the_sponsor(issues, "sm", _task(31, READY, "devops"))


def _review(login: str, state: str = "APPROVED", at: str = "h2", kind: str = "User") -> dict:
    return {"state": state, "commit_id": at, "user": {"login": login, "type": kind}}


def test_only_the_sponsors_approval_counts_never_another_persons():
    from crew_org.flows.role_tasks import sponsor_approved

    # Claude's account is a person with Write access: its approval is not the Sponsor's.
    others = [_review("crew-reviewer[bot]", kind="Bot"), _review("quarters-claude")]
    assert not sponsor_approved(others, "mquarters", "h2")
    assert sponsor_approved([*others, _review("mquarters")], "mquarters", "h2")
    # No Sponsor named: nobody's approval counts.
    assert not sponsor_approved([*others, _review("mquarters")], "", "h2")


def test_an_approval_of_an_older_commit_doesnt_count():
    from crew_org.flows.role_tasks import sponsor_approved

    assert not sponsor_approved([_review("mquarters", at="h1")], "mquarters", "h2")
    # Their latest word on the head is what counts.
    later = [_review("mquarters"), _review("mquarters", state="CHANGES_REQUESTED")]
    assert not sponsor_approved(later, "mquarters", "h2")


def test_with_no_sponsor_set_the_gate_reads_orgs_trust(monkeypatch):
    from crew_org.flows import role_tasks as rt

    assert rt.the_sponsor("someone") == "someone"
    assert rt.the_sponsor(None) == "mquarters"

    def missing():
        raise ValueError("org.yaml has no `trust` section naming the Sponsor")

    monkeypatch.setattr(rt, "load_trust", missing)
    assert rt.the_sponsor(None) == ""


class _ReleasePull:
    """A release Task's pull request at head `h2`, for the merge step."""

    def __init__(self, reviews):
        from tests.test_merge import FakeIssues

        self.issues = FakeIssues(reviews=reviews)
        body = render(DEVOPS, kind="release", key="epic-10", why="w", work="x", done_when=["d"])
        self.issues.get = lambda repo, number: {"body": body}
        self.issues.has_comment_marked = lambda repo, number, marker: False
        self.issues.pull_for_branch = lambda repo, branch, known=None: {
            "number": 100,
            "head": {"ref": branch, "sha": "h2"},
        }
        self.card = _task(30, "Merging", "devops").model_copy(update={"repo": "sprint-metrics"})


def _land(pull, sponsor, tmp_path):
    from crew_org.flows.merge import merge_approved
    from tests.test_merge import FakeBoard

    return merge_approved(
        FakeBoard(),
        pull.issues,
        sink(tmp_path),
        cards=[pull.card],
        default_repo="sprint-metrics",
        sponsor=sponsor,
    )


def test_a_release_task_approved_only_by_the_crew_waits_for_the_sponsor(tmp_path):
    pull = _ReleasePull([_review("crew-reviewer[bot]", kind="Bot"), _review("quarters-claude")])
    result = _land(pull, None, tmp_path)
    assert result.awaiting_sponsor == [(30, 100)]
    assert pull.issues.merged == []


def test_a_release_task_with_no_sponsor_named_never_merges(tmp_path, monkeypatch):
    from crew_org.flows import role_tasks as rt

    monkeypatch.setattr(rt, "load_trust", lambda: (_ for _ in ()).throw(ValueError("none")))
    pull = _ReleasePull([_review("crew-reviewer[bot]", kind="Bot"), _review("mquarters")])
    result = _land(pull, None, tmp_path)
    assert result.no_sponsor == [(30, 100)]
    assert pull.issues.merged == []


def test_a_push_after_the_sponsors_approval_needs_a_new_one(tmp_path):
    pull = _ReleasePull([_review("crew-reviewer[bot]", kind="Bot"), _review("mquarters", at="h1")])
    assert _land(pull, "mquarters", tmp_path).awaiting_sponsor == [(30, 100)]
    pull = _ReleasePull([_review("crew-reviewer[bot]", kind="Bot"), _review("mquarters")])
    assert _land(pull, "mquarters", tmp_path).awaiting_sponsor == []


def _found(owner: str, what: str = "CI never runs the requires_db tests"):
    from crew_org.crews.outside import OutsideFinding

    return OutsideFinding(
        what=what, where=".github/workflows/ci.yml", evidence="skipped", owner=owner
    )


def test_a_finding_outside_the_story_is_filed_for_its_role(tmp_path):
    from crew_org.columns import NEEDS_REFINEMENT

    issues, board, s = Issues(), Board(), sink(tmp_path)
    story = _story(40, "Reviewing", 10)
    filed = role_tasks.file_findings(
        issues,
        board,
        s,
        repo="sm",
        story=story,
        found=[_found("devops")],
        by="DevOps Engineer",
        round_key="abc1234",
        cards=[],
    )
    assert filed == [901]
    task = issues.issues[901]
    assert {lab["name"] for lab in task["labels"]} == {"role:devops"}
    assert marked(task["body"]).kind == "finding"
    assert ("status", "item-N901", NEEDS_REFINEMENT) in board.done
    assert [e["detail"]["filed"] for e in events(tmp_path) if e["kind"] == "finding.filed"] == [901]


def test_one_finding_per_owner_each_round_and_the_rest_recorded(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    story = _story(40, "Reviewing", 10)
    found = [_found("devops"), _found("devops", "the image runs as root"), _found("bug")]
    filed = role_tasks.file_findings(
        issues,
        board,
        s,
        repo="sm",
        story=story,
        found=found,
        by="Code Reviewer",
        round_key="abc1234",
        cards=[],
    )
    assert filed == [901, 902]
    assert {lab["name"] for lab in issues.issues[902]["labels"]} == {"bug"}
    recorded = [e["detail"]["filed"] for e in events(tmp_path) if e["kind"] == "finding.filed"]
    assert recorded == [901, None, 902]
    # The same round again files nothing new.
    on_board = [Card(item_id="x", number=n, repo="sm") for n in (901, 902)]
    again = role_tasks.file_findings(
        issues,
        board,
        s,
        repo="sm",
        story=story,
        found=found,
        by="Code Reviewer",
        round_key="abc1234",
        cards=on_board,
    )
    assert again == [901, 902]
    assert len([d for d in issues.done if d[0] == "create"]) == 2


def test_a_finding_that_cant_be_filed_doesnt_stop_the_review(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.fail = "create"
    story = _story(40, "Reviewing", 10)
    assert (
        role_tasks.file_findings(
            issues,
            board,
            s,
            repo="sm",
            story=story,
            found=[_found("architect")],
            by="Code Reviewer",
            round_key="abc1234",
            cards=[],
        )
        == []
    )


def test_the_verdicts_carry_findings_outside_the_story():
    from crew_org.crews.qa_crew import QAVerdict
    from crew_org.crews.review_crew import ReviewVerdict

    assert "outside" in ReviewVerdict.model_fields
    assert "outside" in QAVerdict.model_fields
    assert ReviewVerdict(summary="s", approve=True).outside == []


def _goal_board():
    from crew_org.columns import DONE

    goal = Card(item_id="IG", number=7, repo="sm", work_type="Goal", status="Ready", state="OPEN")
    epics = [
        Card(
            item_id=f"IE{n}",
            number=n,
            repo="sm",
            work_type="Epic",
            status=DONE,
            state="CLOSED",
            parent=7,
        )
        for n in (10, 11)
    ]
    issues = Issues()
    for n in (10, 11):
        issues.add(n, state="closed")
    issues.subs[7] = [10, 11]
    return goal, epics, issues


def test_a_goal_whose_epics_are_done_opens_the_architects_task_and_stays_open(tmp_path):
    from crew_org.flows.acceptance import close_finished_parents

    goal, epics, issues = _goal_board()
    board, s = Board(), sink(tmp_path)
    closed = close_finished_parents(board, issues, s, [goal, *epics], repo="sm", repos={"sm"})
    assert closed == []
    task = issues.issues[901]
    found = marked(task["body"])
    assert (found.role, found.kind, found.key) == (ARCHITECT, "diagram", "goal-7")
    assert issues.subs[7] == [10, 11, 901]
    assert ("number", "item-N901", "Points", role_tasks.DIAGRAM_POINTS) in board.done
    assert "#10, #11" in task["body"]
    opened = [e for e in events(tmp_path) if e["kind"] == "role_task.opened"]
    assert opened[0]["detail"]["event"] == "goal.epics_done"

    # Next pass: the Task is open, so the Goal waits for it and nothing is opened twice.
    on_board = Card(
        item_id="item-N901",
        number=901,
        repo="sm",
        work_type=TASK,
        status=READY,
        state="OPEN",
        parent=7,
    )
    close_finished_parents(board, issues, s, [goal, *epics, on_board], repo="sm", repos={"sm"})
    assert len([d for d in issues.done if d[0] == "create"]) == 1


def test_the_goal_closes_once_the_architects_task_is_done(tmp_path):
    from crew_org.columns import DONE
    from crew_org.flows.acceptance import close_finished_parents

    goal, epics, issues = _goal_board()
    body = render(ARCHITECT, kind="diagram", key="goal-7", why="w", work="x", done_when=["d"])
    issues.add(901, body, ("role:architect",), state="closed")
    issues.subs[7].append(901)
    done = Card(
        item_id="IT", number=901, repo="sm", work_type=TASK, status=DONE, state="CLOSED", parent=7
    )
    closed = close_finished_parents(
        Board(), issues, sink(tmp_path), [goal, *epics, done], repo="sm", repos={"sm"}
    )
    assert closed == [7]


def test_a_task_the_goal_event_couldnt_open_is_tried_next_pass(tmp_path):
    from crew_org.flows.acceptance import close_finished_parents

    goal, epics, issues = _goal_board()
    issues.fail = "create"
    s = sink(tmp_path)
    assert close_finished_parents(Board(), issues, s, [goal, *epics], repo="sm", repos={"sm"}) == []
    assert any("not opened" in e["summary"] for e in events(tmp_path))
    issues.fail = None
    close_finished_parents(Board(), issues, s, [goal, *epics], repo="sm", repos={"sm"})
    assert 901 in issues.issues


def test_a_repository_with_no_diagram_gets_one_task_to_draw_it(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.files["docs/architecture.md"] = "```mermaid\nC4Context\n```"
    drawn = role_tasks.first_diagrams(issues, board, s, repos={"sm", "infra"}, cards=[])
    # The fake serves one file for every repository: both have it.
    assert drawn.opened == []
    issues.files.clear()
    drawn = role_tasks.first_diagrams(issues, board, s, repos={"sm"}, cards=[])
    assert drawn.opened == [("sm", 901)]
    assert marked(issues.issues[901]["body"]).kind == "first-diagram"
    again = role_tasks.first_diagrams(
        issues, board, s, repos={"sm"}, cards=[Card(item_id="x", number=901, repo="sm")]
    )
    assert again.opened == []


def test_a_repository_with_no_workspace_file_gets_devops_one_task_to_write_it(tmp_path):
    """The Sponsor (2026-10-22): DevOps keeps the workspace file true; none was ever
    written, so a first tick opens one per project (crew#860)."""
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.files[".devcontainer.json"] = '{"image": "i"}'
    assert role_tasks.first_workspaces(issues, board, s, repos={"sm"}, cards=[]).opened == []
    issues.files.clear()
    housed = role_tasks.first_workspaces(issues, board, s, repos={"sm"}, cards=[])
    assert housed.opened == [("sm", 901)]
    task = issues.issues[901]
    assert {lab["name"] for lab in task["labels"]} == {"role:devops"}
    assert marked(task["body"]).kind == "first-workspace"
    # The workspace file and its services only: CI is the second Task (the Sponsor,
    # 2026-10-23, crew#839).
    assert "image_digest" in task["body"] and "CI isn't changed here" in task["body"]
    assert "- [ ] CI's test job" not in task["body"]
    again = role_tasks.first_workspaces(
        issues, board, s, repos={"sm"}, cards=[Card(item_id="x", number=901, repo="sm")]
    )
    assert again.opened == []


def test_a_workspace_task_that_cant_be_opened_is_held_for_next_pass(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.fail = "create"
    housed = role_tasks.first_workspaces(issues, board, s, repos={"sm"}, cards=[])
    assert housed.opened == [] and "first-workspace task not opened" in housed.failed[0][1]


def test_devops_may_change_the_workspace_file_and_its_services():
    assert (
        role_tasks.outside_scope(
            DEVOPS, [".devcontainer/devcontainer.json", ".devcontainer/compose.yaml"]
        )
        == []
    )


def test_each_need_of_a_goal_is_one_devops_task_under_it(tmp_path):
    from crew_org.columns import NEEDS_REFINEMENT

    issues, board, s = Issues(), Board(), sink(tmp_path)
    board.cards = lambda: []
    needs = ["a Postgres for the tests", "Node for the site build"]
    opened = role_tasks.need_tasks(issues, board, s, repo="sm", goal=666, needs=needs)
    assert opened == [901, 902]
    assert marked(issues.issues[901]["body"]).kind == "need"
    assert issues.subs[666] == [901, 902]
    assert ("status", "item-N901", NEEDS_REFINEMENT) in board.done
    again = role_tasks.need_tasks(issues, board, s, repo="sm", goal=666, needs=needs[:1])
    assert again == [901] and len(issues.issues) == 2


def test_a_need_that_cant_be_opened_is_recorded_and_the_rest_go_on(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    board.cards = lambda: []
    issues.fail = "create"
    assert role_tasks.need_tasks(issues, board, s, repo="sm", goal=666, needs=["x"]) == []
    assert any("need task not opened" in e["summary"] for e in events(tmp_path))


def test_each_pass_opens_a_goals_needs_from_its_record_once(tmp_path, monkeypatch):
    """A need Task GitHub refused when the look ran is opened by a later pass (crew#860)."""
    from crew_org.flows import record as record_flow

    held = {"body": record_flow.render(record_flow.Record(on_goal=True, needs=("a Postgres",)))}
    monkeypatch.setattr(record_flow, "goal_record_comment", lambda issues, repo, goal: held)
    monkeypatch.setattr(record_flow, "comment_text", lambda body: body)
    issues, board, s = Issues(), Board(), sink(tmp_path)
    board.cards = lambda: []
    goal = Card(item_id="G", number=666, repo="sm", work_type="Goal", state="OPEN")
    closed = Card(item_id="C", number=600, repo="sm", work_type="Goal", state="CLOSED")
    issues.fail = "create"
    assert role_tasks.goals_needs(issues, board, s, repos={"sm"}, cards=[goal, closed]) == []
    issues.fail = None
    assert role_tasks.goals_needs(issues, board, s, repos={"sm"}, cards=[goal, closed]) == [901]
    assert role_tasks.goals_needs(issues, board, s, repos={"sm"}, cards=[goal]) == [901]
    assert len(issues.issues) == 1 and issues.subs[666] == [901]


def test_the_architects_diagram_must_render():
    from crew_org.flows.role_tasks import diagram_problems

    good = 'x\n\n```mermaid\nC4Context\nPerson(u, "User")\nSystem(s, "S")\nRel(u, s, "uses")\n```\n'
    assert diagram_problems(good) == []
    assert diagram_problems(None)
    assert diagram_problems("no diagram here")
    assert diagram_problems("```mermaid\ngraph TD\n```")


def test_every_block_of_the_architects_diagram_must_render():
    """mqucifer/sprint-metrics#740 passed with its second and third blocks broken: only
    the first was checked (crew#839)."""
    from crew_org.flows.role_tasks import diagram_problems

    context = 'C4Context\nPerson(u, "User")\nSystem(s, "S")\nRel(u, s, "uses")'
    containers = (
        'C4Container\nPerson(u, "User")\n'
        'Container_Boundary(b, "S")\nContainer(c, "C", "py", "does")\nEnd_Boundary\n'
        'Rel(u, c, "uses")'
    )
    sequence = "sequenceDiagram\nA->>B: asks"
    fenced = "\n\n".join(f"```mermaid\n{b}\n```" for b in (context, sequence, containers))

    found = diagram_problems(fenced)

    assert found and all(p.startswith("block 3, C4Container: ") for p in found)
    assert any("`Container_Boundary` opens no `{`" in p for p in found)
    assert any("`End_Boundary` isn't a C4 macro" in p for p in found)
    assert diagram_problems(f"```mermaid\n{context}\n```\n\n```mermaid\n{sequence}\n```") == []
    broken_sequence = f"```mermaid\n{context}\n```\n\n```mermaid\nsequenceDiagram\nA->>B: a; b\n```"
    assert diagram_problems(broken_sequence)[0].startswith("block 2, sequenceDiagram: ")
    flowchart = f"```mermaid\n{context}\n```\n\n```mermaid\ngraph TD\nA-->B\n```"
    assert diagram_problems(flowchart)[0].startswith("block 2, graph TD: ")
    only_sequence = f"```mermaid\n{sequence}\n```"
    assert diagram_problems(only_sequence) == [
        "no block is a C4 diagram ("
        + ", ".join(("C4Context", "C4Container", "C4Component", "C4Dynamic", "C4Deployment"))
        + ")"
    ]


def _epic(number: int = 10) -> Card:
    return Card(
        item_id=f"IE{number}",
        number=number,
        title="Serve the history",
        repo="sm",
        work_type="Epic",
        status="Ready",
        state="OPEN",
    )


def _split_now(number: int, parent: int) -> Card:
    from datetime import UTC, datetime

    return _story(number, READY, parent).model_copy(
        update={"created": datetime(2026, 10, 23, tzinfo=UTC)}
    )


def test_a_split_opens_devops_task_for_the_epics_ci_and_release(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.add(10)
    why = role_tasks.epic_release_task(
        issues, board, s, repo="sm", epic=_epic(), stories=[11, 12], cards=[]
    )
    assert why == ""
    task = issues.issues[901]
    found = marked(task["body"])
    assert (found.role, found.kind, found.key) == (DEVOPS, "release", "epic-10")
    assert "mqucifer/crew#860" in task["body"]
    assert issues.subs[10] == [901]
    assert ("number", "item-N901", "Points", role_tasks.RELEASE_POINTS) in board.done


@pytest.mark.parametrize("refused", ["label", "create", "add", "link"])
def test_any_refusal_at_the_split_leaves_the_release_task_for_the_next_pass(tmp_path, refused):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.add(10)
    issues.fail = refused if refused != "add" else None
    board.fail = "add" if refused == "add" else None
    why = role_tasks.epic_release_task(
        issues, board, s, repo="sm", epic=_epic(), stories=[11], cards=[]
    )
    assert "not opened" in why

    issues.fail = board.fail = None
    on_board = [c for c in [_epic(), _split_now(11, 10)]]
    failed = role_tasks.pending_release_tasks(issues, board, s, cards=on_board, repos={"sm"})
    assert failed == []
    assert marked(issues.issues[901]["body"]).key == "epic-10"
    assert "#11" in issues.issues[901]["body"]
    assert issues.subs[10] == [901]
    assert len([d for d in issues.done if d[0] == "create"]) == 1


def test_an_epic_split_before_this_build_gets_no_release_task(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    before = _story(11, READY, 10)  # no creation date on or after RELEASE_TASKS_FROM
    assert (
        role_tasks.pending_release_tasks(issues, board, s, cards=[_epic(), before], repos={"sm"})
        == []
    )
    assert 901 not in issues.issues


def test_an_epic_with_its_release_task_on_the_board_opens_nothing(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    cards = [_epic(), _split_now(11, 10), _task(30, READY, "devops", parent=10)]
    role_tasks.pending_release_tasks(issues, board, s, cards=cards, repos={"sm"})
    assert issues.done == []


def _split_story(title: str):
    from crew_org.crews.refinement_crew import AcceptanceCriterion, Story

    criterion = AcceptanceCriterion(given="g", when="w", then="t")
    return Story(
        title=title,
        as_a="a user",
        i_want="it",
        so_that="s",
        acceptance_criteria=[criterion, criterion],
        points=2,
    )


# What the split has written for the project's own release (mqucifer/sprint-metrics).
RELEASE_STORIES = [
    "Cut release 1.3.2 with the escalation-rate fix in the changelog",
    "Cut the 1.0.0 release",
    "Cut and publish the first versioned release",
    "Release 1.3.1: the service counts superseded stories",
    "Bump the version to 1.0.1 and date the changelog section for the next release",
    "Bump version to 1.1.0 and date the changelog section",
]
# Stories that show releases as data, as sprint-metrics and crew-presentation do
# (the operator's review), and the release check's own epics.
NOT_RELEASE_STORIES = [
    "Tag each release in the replay timeline",
    "Show escalations since release 1.3.2 on the dashboard",
    "Detect a missing version bump in the release check",
    "Publish the latest release notes on the site",
    "Ship the release badge on the home page",
    "Mark the MAJOR release entries in the changelog view",
    "Release v1.0.1 published less than the design asks",
    "Date the 1.3.2 and 1.3.3 changelog sections 2026-10-19",
]


@pytest.mark.parametrize("title", RELEASE_STORIES)
def test_the_split_refuses_a_story_that_cuts_the_release(title):
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="DevOps"):
        _split_story(title)


@pytest.mark.parametrize("title", NOT_RELEASE_STORIES)
def test_a_story_about_releases_as_data_is_not_the_release(title):
    _split_story(title)


def _done(specs):
    return next(s["function"] for s in specs if s["function"]["name"] == "done")


def test_a_tasks_done_says_its_proof_is_its_scope_and_no_qa_follows():
    """On mqucifer/sprint-metrics#738 the Developer's `done` asked the Architect for a
    test per criterion, and it wrote one outside its scope (crew#839)."""
    from crew_org.session.tools import SPECS

    done = _done(role_tasks.done_specs(ARCHITECT))
    assert "`docs/architecture.md`" in done["description"]
    assert "review and QA follow" not in done["description"]
    assert "QA doesn't verify a Task" in done["description"]
    assert "changed_tests" not in done["parameters"]["properties"]
    proof = done["parameters"]["properties"]["criteria"]["items"]["properties"]["test"]
    assert "`docs/architecture.md`" in proof["description"]
    # The Developer's own `done` is told as before.
    assert "Then review and QA follow." in _done(SPECS)["description"]
    assert "changed_tests" in _done(SPECS)["parameters"]["properties"]


def test_neither_tasks_scope_takes_in_tests():
    assert not role_tasks.covers_tests(ARCHITECT)
    assert not role_tasks.covers_tests(DEVOPS)


def test_done_takes_a_file_in_the_tasks_scope_as_a_criterions_proof(tmp_path):
    from crew_org.flows import gates

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "architecture.md").write_text("# Architecture\n")
    said = {"criteria": [{"criterion": "draws it", "test": "docs/architecture.md"}]}
    assert gates.missing_named_tests(tmp_path, said, scope=ARCHITECT.scope) == []
    # Outside a Task, or a file it doesn't have, it's still a missing test.
    assert gates.missing_named_tests(tmp_path, said) != []
    gone = {"criteria": [{"criterion": "draws it", "test": "docs/other.md"}]}
    assert gates.missing_named_tests(tmp_path, gone, scope=ARCHITECT.scope) != []


def test_a_tasks_session_is_given_its_own_done(tmp_path, monkeypatch):
    from crew_org.session import conversation

    monkeypatch.setattr(conversation, "record", lambda *a, **k: None)
    monkeypatch.setattr(conversation, "start", lambda *a: ("start", {"total": 5}, []))
    monkeypatch.setattr(conversation, "Tools", lambda *a, **k: None)
    specs = role_tasks.done_specs(ARCHITECT)
    task = conversation.session(tmp_path, "s", None, None, specs=specs, scope=ARCHITECT.scope)
    assert task.specs is specs
    story = conversation.session(tmp_path, "s", None, None)
    assert story.specs is conversation.SPECS
