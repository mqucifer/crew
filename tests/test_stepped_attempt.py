"""A second attempt in steps: a plan, then one small answer per file (#276).

sprint-metrics#268's one answer (rewrite the README, move ~20 tests, delete a
file) stopped mid-thought 3 times in 5 even on a focused prompt (#312). After a
first attempt with no usable answer, the next plans the work and answers one
file at a time; the pieces merge into one ordinary answer the gates judge.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from crew_org.crews import stepped
from crew_org.crews.delivery_crew import FileEdit, FileWrite, TextEdit
from crew_org.crews.stepped import (
    CriterionPlan,
    Plan,
    PlannedFile,
    StepAnswer,
    check_plan,
    merge,
    render_plan,
)
from crew_org.events import EventSink
from crew_org.flows import delivery
from tests.test_delivery_flow import green, harness  # noqa: F401


def plan(*files: PlannedFile, criteria=()) -> Plan:
    return Plan(
        summary="Move the README tests to test_docs.py.", files=list(files), criteria=list(criteria)
    )


README = PlannedFile(path="README.md", kind="edit", intent="Trim to the entry-point sections.")
DOCS_TESTS = PlannedFile(
    path="tests/test_docs.py", kind="edit", intent="Add the moved README tests."
)
OLD_TESTS = PlannedFile(
    path="tests/test_readme.py", kind="delete", intent="Removed, its tests moved."
)


# --- the plan ----------------------------------------------------------------------------------


def test_a_plan_names_at_least_one_file():
    with pytest.raises(ValidationError, match="at least one file"):
        Plan(summary="s", files=[])


def test_a_plan_is_one_story_not_several():
    many = [
        PlannedFile(path=f"src/m{i}.py", kind="new", intent="a new module here") for i in range(9)
    ]
    with pytest.raises(ValidationError, match="at most 8 files"):
        plan(*many)


def test_each_file_appears_once():
    with pytest.raises(ValidationError, match="each file appears once"):
        plan(README, README)


def test_every_criterions_test_is_in_a_planned_file():
    with pytest.raises(ValidationError, match="isn't among the planned files"):
        plan(README, criteria=[CriterionPlan(criterion="c", test="tests/test_docs.py::test_x")])
    assert plan(
        README,
        DOCS_TESTS,
        criteria=[CriterionPlan(criterion="c", test="tests/test_docs.py::test_x")],
    )


def test_the_plan_is_checked_against_the_repository(tmp_path: Path):
    (tmp_path / "README.md").write_text("# x\n")
    wrong = plan(
        PlannedFile(path="README.md", kind="new", intent="Write a new README file."),
        PlannedFile(
            path="tests/test_gone.py", kind="edit", intent="Change a file that isn't there."
        ),
    )
    problems = check_plan(wrong, tmp_path)
    assert "README.md already exists: plan it as an edit" in problems
    assert any("tests/test_gone.py doesn't exist" in p for p in problems)
    assert check_plan(plan(README), tmp_path) == []


def test_the_plan_reads_as_the_steps_will_see_it():
    text = render_plan(
        plan(
            README,
            DOCS_TESTS,
            criteria=[CriterionPlan(criterion="c1", test="tests/test_docs.py::t")],
        )
    )
    assert "1. `README.md` (edit): Trim" in text and "2. `tests/test_docs.py` (edit)" in text
    assert "- c1: `tests/test_docs.py::t`" in text


# --- the steps ---------------------------------------------------------------------------------


def test_a_step_that_reaches_into_another_file_is_refused(monkeypatch):
    stray = StepAnswer(
        summary="s",
        text_edits=[
            TextEdit(path="README.md", find="# x\n", replace="# y\n"),
            TextEdit(path="docs/usage.md", find="a", replace="b"),
        ],
    )
    monkeypatch.setattr(stepped, "_run", lambda description, output: stray)
    with pytest.raises(ValueError, match="also changes `docs/usage.md`"):
        stepped.implement_file(
            "story", plan=plan(README, DOCS_TESTS), step=0, context="", earlier=""
        )


def test_a_delete_step_answers_with_the_deletion_alone():
    assert StepAnswer(summary="gone", deleted_files=["tests/test_readme.py"]).touched() == {
        "tests/test_readme.py"
    }


def test_the_pieces_merge_into_one_ordinary_answer():
    answers = [
        StepAnswer(
            summary="a", text_edits=[TextEdit(path="README.md", find="# x\n", replace="# y\n")]
        ),
        StepAnswer(
            summary="b",
            edits=[
                FileEdit(
                    path="tests/test_docs.py",
                    operation="add",
                    target="test_x",
                    source="def test_x():\n    assert True",
                )
            ],
        ),
        StepAnswer(summary="c", deleted_files=["tests/test_readme.py"]),
    ]
    merged = merge(plan(README, DOCS_TESTS, OLD_TESTS), answers)
    assert merged.summary.startswith("Move the README tests")
    assert [t.path for t in merged.text_edits] == ["README.md"]
    assert [e.target for e in merged.edits] == ["test_x"]
    assert merged.deleted_files == ["tests/test_readme.py"]


def test_later_steps_are_shown_what_earlier_ones_wrote():
    earlier = stepped.describe(
        StepAnswer(summary="n", new_files=[FileWrite(path="docs/new.md", content="# New\n")])
    )
    assert "`docs/new.md` (new)" in earlier and "# New" in earlier


def test_the_whole_stepped_attempt(monkeypatch, tmp_path: Path):
    (tmp_path / "README.md").write_text("# x\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_docs.py").write_text("def test_a():\n    assert True\n")
    the_plan = plan(README, DOCS_TESTS)
    seen_steps: list[tuple[int, str]] = []

    def fake_step(story, *, plan, step, context, earlier, written=frozenset()):
        seen_steps.append((step, earlier))
        if step == 0:
            return StepAnswer(
                summary="r", text_edits=[TextEdit(path="README.md", find="# x\n", replace="# y\n")]
            )
        return StepAnswer(
            summary="t",
            edits=[
                FileEdit(
                    path="tests/test_docs.py",
                    operation="add",
                    target="test_b",
                    source="def test_b():\n    assert True",
                )
            ],
        )

    monkeypatch.setattr(stepped, "plan_story", lambda story, *, context, why="": the_plan)
    monkeypatch.setattr(stepped, "implement_file", fake_step)
    sink = EventSink(None)
    notes = []
    sink.subscribe(notes.append)
    merged = delivery._deliver_in_steps(
        "story",
        worktree=tmp_path,
        header="",
        about="story",
        why="empty",
        sink=sink,
        number=268,
        repo="sprint-metrics",
        sprint="Sprint 9",
        attempt=2,
    )
    assert [s for s, _ in seen_steps] == [0, 1]
    assert "`README.md`: replaced" in seen_steps[1][1], "step 2 sees what step 1 wrote"
    assert [t.path for t in merged.text_edits] == ["README.md"] and merged.edits[
        0
    ].target == "test_b"
    summaries = [n.summary for n in notes]
    assert any("planned 2 files" in s for s in summaries)
    assert any("step 2/2: tests/test_docs.py" in s for s in summaries)


def test_a_plan_that_doesnt_fit_the_repository_fails_the_attempt(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        stepped,
        "plan_story",
        lambda story, *, context, why="": plan(
            PlannedFile(path="nope.py", kind="edit", intent="edit a missing file")
        ),
    )
    with pytest.raises(ValueError, match="doesn't fit the repository"):
        delivery._deliver_in_steps(
            "s",
            worktree=tmp_path,
            header="",
            about="s",
            why="",
            sink=EventSink(None),
            number=1,
            repo="r",
            sprint="S",
            attempt=2,
        )


# --- when it's used ----------------------------------------------------------------------------


def test_no_usable_first_answer_sends_the_next_attempt_into_steps(harness):  # noqa: F811
    def first_fails(n):
        if n == 1:
            raise ValueError("Invalid response from LLM call - None or empty.")
        return delivery.Implementation(
            summary="done", new_files=[FileWrite(path="x.py", content="x = 1\n")]
        )

    _, _, _, _, calls, seen = harness(checks=[green()], implement=first_fails)
    assert calls["implement"] == 2
    assert any("the next attempt goes in steps" in (e.summary or "") for e in seen)
    assert calls["feedback"][1].startswith("Your output did not validate"), (
        "the step path is told why"
    )


def test_an_answer_that_arrived_and_failed_a_check_is_repaired_as_before(harness):  # noqa: F811
    bad = delivery.Implementation(
        summary="x", new_files=[FileWrite(path="x.py", content="x = 1\n")]
    )
    _, _, _, _, _, seen = harness(checks=[green()], implement=lambda n: bad)
    assert not any("goes in steps" in (e.summary or "") for e in seen)


def test_a_later_step_citing_an_earlier_steps_test_as_proof_is_not_refused(monkeypatch):
    """sprint-metrics#268's delete step listed three of step 2's tests as criteria proof."""
    from crew_org.crews.delivery_crew import CriterionTest

    cites = StepAnswer(
        summary="delete",
        deleted_files=["tests/test_readme.py"],
        criteria_tests=[
            CriterionTest(
                criterion="c",
                path="tests/test_docs.py",
                test="test_moved",
                source="def test_moved():\n    assert True",
            )
        ],
    )
    monkeypatch.setattr(stepped, "_run", lambda description, output: cites)
    answer = stepped.implement_file(
        "s",
        plan=plan(DOCS_TESTS, OLD_TESTS),
        step=1,
        context="",
        earlier="",
        written=frozenset({("tests/test_docs.py", "test_moved")}),
    )
    assert answer.criteria_tests == [] and answer.deleted_files == ["tests/test_readme.py"]


def test_a_new_test_in_another_file_is_still_refused(monkeypatch):
    from crew_org.crews.delivery_crew import CriterionTest

    sneaks = StepAnswer(
        summary="delete",
        deleted_files=["tests/test_readme.py"],
        criteria_tests=[
            CriterionTest(
                criterion="c",
                path="tests/test_docs.py",
                test="test_brand_new",
                source="def test_brand_new():\n    assert True",
            )
        ],
    )
    monkeypatch.setattr(stepped, "_run", lambda description, output: sneaks)
    with pytest.raises(ValueError, match="also changes `tests/test_docs.py`"):
        stepped.implement_file(
            "s", plan=plan(DOCS_TESTS, OLD_TESTS), step=1, context="", earlier=""
        )


def test_the_tests_earlier_steps_wrote_are_known():
    answers = [
        StepAnswer(
            summary="n",
            new_files=[
                FileWrite(path="tests/test_new.py", content="def test_one():\n    assert True\n")
            ],
        ),
        StepAnswer(
            summary="e",
            edits=[
                FileEdit(
                    path="tests/test_docs.py",
                    operation="add",
                    target="test_two",
                    source="def test_two():\n    assert True",
                )
            ],
        ),
    ]
    assert stepped.written_tests(answers) == {
        ("tests/test_new.py", "test_one"),
        ("tests/test_docs.py", "test_two"),
    }
