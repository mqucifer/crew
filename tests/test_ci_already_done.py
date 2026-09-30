"""A CI-only story whose work is already done can say so (#397).

sprint-metrics#368's step already met every criterion. The Developer answered
"already done", and it was refused because a workflow has no test to name.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.crews.delivery_crew import CriterionMet
from crew_org.flows.delivery import already_done_comment, ci_evidence_problems

WORKFLOW = ".github/workflows/release.yml"
STEP = f"{WORKFLOW}#Create GitHub Release"


def met(**kw):
    return CriterionMet(criterion="an existing Release is skipped", code="release.yml", **kw)


def test_a_criterion_is_proven_by_a_test_or_a_workflow_step_not_both_or_neither():
    assert met(test="tests/test_cli.py::test_help").test
    assert met(ci_step=STEP).ci_step
    with pytest.raises(ValidationError, match="exactly one"):
        met()
    with pytest.raises(ValidationError, match="exactly one"):
        met(test="tests/test_cli.py::test_help", ci_step=STEP)


class Runs:
    def __init__(self, conclusion="success"):
        self._conclusion = conclusion

    def latest_runs(self, repo, branch):
        if self._conclusion is None:
            return {}
        return {WORKFLOW: {"conclusion": self._conclusion}}


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / WORKFLOW).write_text(
        "jobs:\n  release:\n    steps:\n"
        "      - name: Create GitHub Release\n"
        "        run: gh release view\n"
    )
    return tmp_path


def test_a_step_that_exists_in_a_workflow_that_passed_holds(repo):
    assert ci_evidence_problems(repo, Runs(), "sprint-metrics", "main", [STEP]) == []


def test_a_step_that_isn_t_in_the_workflow_doesn_t_hold(repo):
    problems = ci_evidence_problems(
        repo, Runs(), "sprint-metrics", "main", [f"{WORKFLOW}#Publish the moon"]
    )
    assert problems and "no step named" in problems[0]


def test_a_workflow_that_didn_t_pass_or_never_ran_doesn_t_hold(repo):
    failed = ci_evidence_problems(repo, Runs("failure"), "sprint-metrics", "main", [STEP])
    assert failed and "didn't pass" in failed[0]
    never = ci_evidence_problems(repo, Runs(None), "sprint-metrics", "main", [STEP])
    assert never and "hasn't run" in never[0]


def test_only_a_workflow_file_is_ci_evidence(repo):
    (repo / "README.md").write_text("name: Create GitHub Release\n")
    problems = ci_evidence_problems(
        repo, Runs(), "sprint-metrics", "main", ["README.md#Create GitHub Release"]
    )
    assert problems and "isn't a workflow" in problems[0]


def test_the_evidence_comment_shows_the_step_for_qa():
    body = already_done_comment([met(ci_step=STEP)])
    assert f"CI: `{STEP}`, passed on the default branch" in body


def test_asking_for_a_dotfile_keeps_its_leading_dot(tmp_path):
    """`lstrip("./")` turned `.github/workflows/tests.yml` into a path that doesn't exist."""
    from crew_org.tools.repo_context import select_files

    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/tests.yml").write_text("name: tests\n")
    chosen, unknown = select_files(tmp_path, "", extra=[".github/workflows/tests.yml"])
    assert ".github/workflows/tests.yml" in chosen and unknown == []
    chosen, unknown = select_files(tmp_path, "", extra=["./.github/workflows/tests.yml"])
    assert ".github/workflows/tests.yml" in chosen and unknown == []
