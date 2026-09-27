"""A criterion like 'existing behaviour is unchanged' can be proven by existing tests (#217).

A first attempt had to write a new test per criterion (#172), so the split's
"the full existing test suite continues to pass" got invented
`test_full_suite_passes` bodies: on sprint-metrics#127 the source didn't define
the test it named, and on #126 the draft test was then stuck behind the guard.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.crews.delivery_crew import (
    CriterionTest,
    ExistingProof,
    FileWrite,
    FirstAttempt,
    FirstOrDone,
)
from crew_org.flows.acceptance import EXISTING_PROOF_MARKER
from tests.test_already_done import offered, qa  # noqa: F401  (the fixture)
from tests.test_delivery_flow import green
from tests.test_delivery_flow import harness as harness  # noqa: F401  (the fixture)

UNCHANGED = ExistingProof(
    criterion="The full existing test suite continues to pass",
    tests=["tests/test_report.py::test_api_version"],
)
CODE = FileWrite(path="src/sm/split.py", content="def f():\n    return 1\n")
NEW = CriterionTest(
    criterion="f returns 1",
    path="tests/test_split.py",
    test="test_f",
    source="def test_f():\n    assert f() == 1\n",
)


def test_a_criterion_can_rest_on_named_existing_tests():
    """#217, criterion 1: either kind of proof is accepted."""
    both = FirstAttempt(summary="s", criteria_tests=[NEW], proven_by_existing=[UNCHANGED])
    assert both.proven_by_existing == [UNCHANGED]
    refactor = FirstAttempt(
        summary="s", criteria_tests=[], proven_by_existing=[UNCHANGED], new_files=[CODE]
    )
    assert refactor.criteria_tests == [], "a pure refactor rests on the suite alone"


def test_at_least_one_criterion_still_needs_a_new_or_named_test():
    with pytest.raises(ValidationError, match="no test"):
        FirstAttempt(summary="s", criteria_tests=[], new_files=[CODE])
    with pytest.raises(ValidationError, match="no test"):
        FirstOrDone(summary="s", new_files=[CODE])
    with pytest.raises(ValidationError):
        ExistingProof(criterion="c", tests=[])


# --- through delivery ----------------------------------------------------------------


def test_the_proof_is_checked_carried_to_the_pr_and_left_for_qa(harness, offered):  # noqa: F811
    answer = FirstOrDone(
        summary="Split f out.",
        criteria_tests=[NEW],
        proven_by_existing=[UNCHANGED],
        new_files=[CODE],
    )
    result, board, issues, ws, calls, _ = harness(checks=[green()], implement=lambda n: answer)
    assert result.delivered and result.delivered[0].pr is not None
    (story,) = [b for _, b in issues.comments_ if b.startswith("Implemented in #")]
    assert EXISTING_PROOF_MARKER in story
    assert "`tests/test_report.py::test_api_version`" in story


def test_a_named_test_that_does_not_exist_is_refused_naming_it(harness, offered):  # noqa: F811
    """#217, criterion 3."""
    invented = UNCHANGED.model_copy(update={"tests": ["tests/test_report.py::test_nowhere"]})
    answers = iter(
        [
            FirstOrDone(
                summary="s", criteria_tests=[], proven_by_existing=[invented], new_files=[CODE]
            ),
            FirstOrDone(summary="s", criteria_tests=[NEW], new_files=[CODE]),
        ]
    )
    _, _, _, _, calls, _ = harness(checks=[green()], implement=lambda n: next(answers))
    assert "tests/test_report.py::test_nowhere" in calls["feedback"][1]


# --- through QA ----------------------------------------------------------------------


def test_qa_is_shown_the_criteria_existing_tests_prove(monkeypatch, tmp_path):
    """#217, criterion 2."""
    delivered = [
        {
            "body": "Implemented in #170 on `feat/132`. Lint and tests pass.\n\n"
            f"{EXISTING_PROOF_MARKER}\n**Proven by existing tests:**\n"
            "- The full existing test suite continues to pass: `tests/test_report.py::test_x`"
        }
    ]
    seen, _, board = qa(monkeypatch, tmp_path, delivered, accepted=True)
    assert "## Criteria the Developer says the existing tests prove" in seen["story"]
    assert "`tests/test_report.py::test_x`" in seen["story"]
    assert board.moves == [("S143", "Merging")], "a story with a PR still merges"


def test_a_later_delivery_without_the_proof_shows_qa_none(monkeypatch, tmp_path):
    comments = [
        {"body": f"Implemented in #170.\n\n{EXISTING_PROOF_MARKER}\n- old: `t::x`"},
        {"body": "Implemented in #171. Lint and tests pass."},
    ]
    seen, _, _ = qa(monkeypatch, tmp_path, comments, accepted=True)
    assert "existing tests prove" not in seen["story"]
