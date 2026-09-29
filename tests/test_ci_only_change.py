"""A CI workflow change needs no new test: its own run is the proof (§7.1, crew#331).

sprint-metrics#260 (create release.yml) was refused both ways: a test beside the
workflow broke "a CI change travels alone", and none broke "every change carries
a test". The test rule made room for it; since crew#335 a workflow may also change
with the code it runs.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.crews.delivery_crew import (
    STANDING_INSTRUCTIONS,
    FileEdit,
    FileWrite,
    FirstAttempt,
    FirstOrDone,
    TextEdit,
)

RELEASE = FileWrite(path=".github/workflows/release.yml", content="name: release\non: push\n")
TESTS_JOB = TextEdit(
    path=".github/workflows/tests.yml", find="jobs:\n", replace="jobs:\n  image:\n    x: 1\n"
)


@pytest.mark.parametrize("answer", [FirstAttempt, FirstOrDone])
def test_a_new_workflow_alone_needs_no_test(answer):
    assert answer(criteria_tests=[], summary="Release", new_files=[RELEASE]).ci_only


@pytest.mark.parametrize("answer", [FirstAttempt, FirstOrDone])
def test_a_changed_workflow_alone_needs_no_test(answer):
    assert answer(criteria_tests=[], summary="Image check", text_edits=[TESTS_JOB]).ci_only


@pytest.mark.parametrize("answer", [FirstAttempt, FirstOrDone])
def test_a_workflow_with_code_still_needs_a_test(answer):
    code = FileEdit(
        path="src/sprint_metrics/cli.py",
        operation="replace",
        target="main",
        source="def main():\n    return 0\n",
    )
    with pytest.raises(ValidationError, match="no test"):
        answer(criteria_tests=[], summary="Both", new_files=[RELEASE], edits=[code])


@pytest.mark.parametrize("answer", [FirstAttempt, FirstOrDone])
def test_a_yaml_file_outside_the_workflows_is_not_ci(answer):
    other = FileWrite(path="config/release.yml", content="x: 1\n")
    with pytest.raises(ValidationError, match="no test"):
        answer(criteria_tests=[], summary="Config", new_files=[other])


def test_the_developer_is_told_how_a_workflow_change_is_proven():
    assert "A change to CI workflows alone needs no test" in STANDING_INSTRUCTIONS
    assert "may change together with the code or tests" in STANDING_INSTRUCTIONS
    assert "judged by review, and proven when it runs" in STANDING_INSTRUCTIONS
    # sprint-metrics#261 named "CI run of release.yml …" as an existing test.
    assert "Leave `criteria_tests` and `proven_by_existing` empty" in STANDING_INSTRUCTIONS
