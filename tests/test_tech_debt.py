"""Technical debt a role finds is filed and paid by the crew, with no Sponsor step
(mqucifer/crew#1033).

The fixture is mqucifer/sprint-metrics#995: `_attach_range_working_time` takes a
`has_events` parameter it never uses. The Code Reviewer found it on
mqucifer/sprint-metrics#952; crew#1018's route filed it as a bug, since no role's Task
may change `src/`; and the Business Analyst closed it, fairly by the bug rule, as "a
Task, not a Bug". Dead code fails no test on main, so the bug gate couldn't have taken
its fix either. Debt is its own route: a technical epic marked `debt`, split and built
like any technical epic, proven by the project's tests passing unchanged.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from crew_org.columns import NEEDS_REFINEMENT, READY
from crew_org.crews.bug_triage_crew import Triage
from crew_org.crews.outside import OutsideFinding
from crew_org.crews.refinement_crew import AcceptanceCriterion
from crew_org.flows import bugs, debt, gates, role_tasks
from crew_org.flows.sprint import plan_sprint
from crew_org.tools.github_project import Card
from crew_org.tools.workspace import CheckResult, CommandResult
from tests.role_task_fakes import Board, Issues, events, sink
from tests.test_sprint import REPO, epic, story

# mqucifer/sprint-metrics#995's finding, as the Code Reviewer would name it now.
FINDING = OutsideFinding(
    what="_attach_range_working_time carries a has_events parameter it never uses.",
    where="src/sprint_metrics/time_splits.py",
    evidence="def _attach_range_working_time(result, conn, labels, has_events): — has_events "
    "never appears in the function body",
    owner="debt",
)
# mqucifer/sprint-metrics#995 as filed, and the Business Analyst's reason for closing it.
REPORT = (
    "## What happens\n`_attach_range_working_time(result, conn, labels, has_events)` in "
    "`src/sprint_metrics/time_splits.py` takes a `has_events` parameter its body never "
    "uses.\n\n**Where:** `src/sprint_metrics/time_splits.py`\n\n## Expected\n- The "
    "parameter is gone and its callers updated. No answer changes: the existing tests "
    "pass unchanged."
)
REASON = (
    "In src/sprint_metrics/time_splits.py, _attach_range_working_time declares has_events "
    "but never references it. The observable output is correct in every case. This is a "
    "code-quality observation (dead parameter, misleading signature), not a contradiction "
    "of accepted behaviour."
)


def _labels(issue: dict[str, Any]) -> set[str]:
    return {lab["name"] for lab in issue["labels"]}


def _review(tmp_path: Path, found: list[OutsideFinding], issues: Issues | None = None):
    issues = issues or Issues()
    board = Board()
    on = Card(
        item_id="S952",
        number=952,
        repo="sprint-metrics",
        status="Reviewing",
        work_type="Story",
        points=3,
    )
    filed = role_tasks.file_findings(
        issues,
        board,
        sink(tmp_path),
        repo="sprint-metrics",
        story=on,
        found=found,
        by="Code Reviewer",
        round_key="614a909",
        cards=[],
    )
    return issues, board, filed


# --- a finding that names debt is a technical debt epic ------------------------------------


def test_a_dead_code_finding_no_role_may_change_is_filed_as_a_debt_epic_not_a_bug(tmp_path):
    """No role's Task may change `src/`, so crew#1018 filed it as a bug. Named as debt, it's
    a technical epic, marked as debt, in refinement: no Goal, and no Sponsor step."""
    assert role_tasks.finding_role("architect", Card(item_id="x"), "sm", FINDING.where) is None
    issues, board, [number] = _review(tmp_path, [FINDING])
    filed = issues.issues[number]
    assert _labels(filed) == {"technical", "debt"}
    assert "bug" not in _labels(filed)
    for said in (FINDING.what, f"**Where:** {FINDING.where}", f"**Evidence:** {FINDING.evidence}"):
        assert said in filed["body"]
    assert "the project's tests pass unchanged" in filed["body"]
    item = f"item-N{number}"
    assert ("select", item, "Work Type", "Epic") in board.done
    assert ("status", item, NEEDS_REFINEMENT) in board.done
    assert ("status", item, READY) not in board.done
    assert issues.subs == {}, "it belongs to no Goal"
    filings = [e["detail"] for e in events(tmp_path) if e["kind"] == "finding.filed"]
    assert [(d["filed"], d.get("rule")) for d in filings] == [
        (number, "decision.role_tasks.finding_as_debt")
    ]


def test_a_debt_finding_said_again_is_filed_once(tmp_path):
    issues, _, [first] = _review(tmp_path, [FINDING])
    _, _, [again] = _review(tmp_path, [FINDING], issues=issues)
    assert again == first
    assert [d for d in issues.done if d[0] == "create"] == [
        ("create", first, FINDING.what.rstrip("."))
    ]


def test_a_defect_stays_a_bug(tmp_path):
    """Behaviour that contradicts what was accepted is still a bug."""
    defect = FINDING.model_copy(update={"owner": "bug"})
    issues, _, [number] = _review(tmp_path, [defect])
    assert _labels(issues.issues[number]) == {"bug"}


# --- triage sends a bug that's debt to debt, instead of closing it -------------------------


class TriageIssues(Issues):
    def edit_issue(self, repo: str, number: int, *, body: str) -> None:
        self.issues[number]["body"] = body
        self.done.append(("edit", number))

    def add_labels(self, repo: str, number: int, labels: list[str]) -> None:
        self.issues[number]["labels"] += [{"name": n} for n in labels]
        super().add_labels(repo, number, labels)

    def remove_label(self, repo: str, number: int, label: str) -> None:
        self.issues[number]["labels"] = [
            lab for lab in self.issues[number]["labels"] if lab["name"] != label
        ]
        super().remove_label(repo, number, label)


def _triage(outcome: str, **kw: Any) -> Triage:
    fields: dict[str, Any] = {
        "outcome": outcome,
        "reason": REASON,
        "acceptance_criteria": [],
        "points": None,
        "duplicate_of": None,
        "question": "",
    }
    return Triage(**{**fields, **kw})


def test_triage_sends_a_bug_that_changes_no_behaviour_to_technical_debt(tmp_path):
    """mqucifer/sprint-metrics#995 was closed as "a Task, not a Bug". Now it becomes a
    technical debt epic, the same issue, and stays open in refinement for its split."""
    issues, board = TriageIssues(), Board()
    issues.add(995, REPORT, ("bug",), title="_attach_range_working_time takes has_events")
    card = Card(
        item_id="I995",
        number=995,
        title="_attach_range_working_time takes has_events",
        status=NEEDS_REFINEMENT,
        state="OPEN",
        repo="sprint-metrics",
        labels=frozenset({"bug"}),
    )
    bugs.carry_out(
        board,
        issues,
        sink(tmp_path),
        card,
        repo="sprint-metrics",
        report=REPORT,
        triage=_triage("debt"),
    )
    filed = issues.issues[995]
    assert filed["state"] == "open"
    assert ("close", 995) not in issues.done
    assert _labels(filed) == {"technical", "debt"}
    assert ("select", "I995", "Work Type", "Epic") in board.done
    assert not [d for d in board.done if d[0] == "status"], "it stays in refinement"
    assert filed["body"].startswith(REPORT)
    assert REASON in filed["body"]
    assert "the project's tests pass unchanged" in filed["body"]
    assert any("technical debt" in d[2] for d in issues.done if d[0] == "comment")
    # Its triage is done: it's an epic now, not a bug waiting.
    typed = card.model_copy(update={"work_type": "Epic", "labels": frozenset(_labels(filed))})
    assert bugs.bugs_to_triage([typed]) == []


def test_debt_carries_no_criteria_its_split_writes_them():
    _triage("debt")
    criterion = AcceptanceCriterion(given="the module", when="it's read", then="no has_events")
    with pytest.raises(ValidationError, match="only an accepted bug has criteria"):
        _triage("debt", acceptance_criteria=[criterion])


# --- debt never holds a Goal's epics, and is still paid each sprint ------------------------


def _debt_epic(number: int) -> Card:
    return epic(number, priority=None).model_copy(
        update={"labels": frozenset({"technical", "debt"})}
    )


def _story(number: int, points: int, *, parent: int, **kw: Any) -> Card:
    labels = frozenset({"debt"}) if kw.pop("debt", False) else frozenset()
    return story(number, points, **kw).model_copy(update={"parent": parent, "labels": labels})


def _plan(cards: list[Card], capacity: int):
    stories = [c for c in cards if c.work_type == "Story"]
    parents = {(REPO, c.number): (REPO, c.parent) for c in stories if c.parent}
    return plan_sprint(cards, parents, sprint="Sprint 10", capacity=capacity)


def _admitted(cards: list[Card], capacity: int) -> list[int]:
    return [c.number for c in _plan(cards, capacity).admitted]


GOAL_AND_DEBT = [
    epic(179, "P0"),
    _story(283, 3, parent=179),
    _story(284, 3, parent=179),
    _debt_epic(303),
    _story(307, 1, parent=303, debt=True),
    _story(308, 1, parent=303, debt=True),
]


def test_one_debt_story_goes_ahead_of_goal_work_and_the_rest_after_it():
    # Reported by epic, so a debt epic's stories are listed together.
    assert sorted(_admitted(GOAL_AND_DEBT, capacity=8)) == [283, 284, 307, 308]
    assert _admitted(GOAL_AND_DEBT, capacity=7) == [307, 283, 284], "Goal work keeps its place"


def test_a_sprint_already_paying_debt_takes_goal_work_first():
    under_way = _story(305, 1, parent=303, debt=True, status="In Progress")
    under_way = under_way.model_copy(update={"sprint": "Sprint 10"})
    assert _admitted([*GOAL_AND_DEBT, under_way], capacity=7) == [283, 284]


def test_a_debt_story_that_cant_go_ahead_still_has_its_turn_after_goal_work():
    """One that builds on a Goal's story waits for it, and isn't dropped for it."""
    stories = [c for c in GOAL_AND_DEBT if c.work_type == "Story"]
    parents = {(REPO, c.number): (REPO, c.parent) for c in stories}
    plan = plan_sprint(
        GOAL_AND_DEBT,
        parents,
        sprint="Sprint 10",
        capacity=7,
        builds_on={(REPO, 307): {283}},
    )
    assert [c.number for c in plan.admitted] == [283, 284, 307]


def test_other_technical_work_still_goes_first():
    red = epic(400, priority=None).model_copy(update={"labels": frozenset({"technical"})})
    cards = [*GOAL_AND_DEBT, red, _story(401, 2, parent=400)]
    assert _admitted(cards, capacity=6) == [401, 307, 283]


def test_a_debt_epic_is_marked_apart_from_other_technical_work():
    assert debt.is_debt(_debt_epic(303))
    assert not debt.is_debt(epic(400).model_copy(update={"labels": frozenset({"technical"})}))


# --- a debt story's proof: the project's tests pass unchanged ------------------------------

MODULE = "src/sprint_metrics/time_splits.py"
TESTS = "tests/test_time_splits.py"
MERGED_MODULE = (
    "def _attach_range_working_time(result, conn, labels, has_events):\n"
    "    result['working_time'] = len(labels)\n"
    "    return result\n\n\n"
    "def range_answer(conn, labels, has_events):\n"
    "    return _attach_range_working_time({}, conn, labels, has_events)\n"
)
PAID_MODULE = (
    "def _attach_range_working_time(result, conn, labels):\n"
    "    result['working_time'] = len(labels)\n"
    "    return result\n\n\n"
    "def range_answer(conn, labels, has_events):\n"
    "    return _attach_range_working_time({}, conn, labels)\n"
)
MERGED_TESTS = (
    "from sprint_metrics.time_splits import range_answer\n\n\n"
    "def test_working_time_counts_labels():\n"
    "    assert range_answer(None, ['a', 'b'], True)['working_time'] == 2\n\n\n"
    "def test_no_labels_no_time():\n"
    "    assert range_answer(None, [], False)['working_time'] == 0\n"
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """sprint-metrics with the dead parameter merged, and the debt story's branch."""
    (tmp_path / "src/sprint_metrics").mkdir(parents=True)
    (tmp_path / MODULE).write_text(MERGED_MODULE)
    (tmp_path / "tests").mkdir()
    (tmp_path / TESTS).write_text(MERGED_TESTS)
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "merged")
    _git(tmp_path, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    _git(tmp_path, "checkout", "-qb", "chore/1040-has-events")
    (tmp_path / MODULE).write_text(PAID_MODULE)
    return tmp_path


def _green(worktree: Path) -> CheckResult:
    return CheckResult(results=[CommandResult("uv run pytest -q", 0, "2 passed")])


def _debt_story() -> Card:
    return Card(
        item_id="S1040",
        number=1040,
        title="Remove the unused has_events parameter",
        repo="sprint-metrics",
        status="In Progress",
        state="OPEN",
        work_type="Story",
        parent=1039,
        labels=frozenset({"debt"}),
    )


STORY = "**Existing tests** — unchanged: no behaviour changes"
SAID = {
    "summary": "Removed has_events from _attach_range_working_time and its caller.",
    "criteria": [
        {
            "criterion": "range answers are unchanged",
            "test": f"{TESTS}::test_working_time_counts_labels",
        }
    ],
    "changed_tests": [],
    "docs": "",
}


def _gate(repo: Path, said: dict[str, Any] = SAID, card: Card | None = None) -> gates.Verdict:
    return gates.at_done(
        repo,
        said,
        card=card or _debt_story(),
        story=STORY,
        record=None,
        run_checks=_green,
        permissions={},
    )


def test_a_debt_story_passes_its_gate_with_the_projects_tests_unchanged(repo):
    verdict = _gate(repo)
    assert verdict.passed, verdict.findings


def test_a_debt_story_that_removes_a_test_goes_back(repo):
    (repo / TESTS).write_text(MERGED_TESTS.split("\n\n\ndef test_no_labels_no_time")[0] + "\n")
    said = {**SAID, "changed_tests": [{"test": f"{TESTS}::test_no_labels_no_time", "why": "dead"}]}
    verdict = _gate(repo, said)
    rules = [f.rule for f in verdict.findings]
    assert "guard.debt.tests_kept" in rules
    (finding,) = [f for f in verdict.findings if f.rule == "guard.debt.tests_kept"]
    assert f"`{TESTS}::test_no_labels_no_time`" in finding.says


@pytest.mark.parametrize(
    "weaker",
    [
        "    range_answer(None, [], False)\n",  # its assertion removed
        "    assert True\n",  # an assertion that can't fail
    ],
)
def test_a_debt_story_that_weakens_a_test_goes_back(repo, weaker):
    (repo / TESTS).write_text(
        MERGED_TESTS.replace(
            "    assert range_answer(None, [], False)['working_time'] == 0\n", weaker
        )
    )
    said = {**SAID, "changed_tests": [{"test": f"{TESTS}::test_no_labels_no_time", "why": "x"}]}
    verdict = _gate(repo, said)
    (finding,) = [f for f in verdict.findings if f.rule == "guard.debt.tests_kept"]
    assert "range_answer(None, [], False)['working_time'] == 0" in finding.says


def test_only_a_debt_story_is_held_to_it(repo):
    """A story with no debt mark removing a test it declares is ruled on as before."""
    (repo / TESTS).write_text(MERGED_TESTS.split("\n\n\ndef test_no_labels_no_time")[0] + "\n")
    said = {**SAID, "changed_tests": [{"test": f"{TESTS}::test_no_labels_no_time", "why": "dead"}]}
    plain = _debt_story().model_copy(update={"labels": frozenset()})
    assert "guard.debt.tests_kept" not in [f.rule for f in _gate(repo, said, plain).findings]
