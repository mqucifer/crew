"""The Developer's output is written straight to a worktree, so the path guard
is a security boundary, not a nicety. It is tested as one."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.crews.delivery_crew import (
    MAX_FILE_BYTES,
    CriterionTest,
    FileEdit,
    FileWrite,
    FirstAttempt,
    Implementation,
)


def code(path: str = "src/pkg/mod.py") -> FileWrite:
    return FileWrite(path=path, content="def f():\n    return 1\n")


def a_test(path: str = "tests/test_mod.py") -> FileWrite:
    return FileWrite(path=path, content="def test_f():\n    assert True\n")


# A first attempt names the test proving each criterion (#172); test_f is a_test's.
PROVED = [
    CriterionTest(
        criterion="it works",
        path="tests/test_mod.py",
        test="test_g",
        source="def test_g():\n    assert True\n",
    )
]


# --- the path guard ------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "../../etc/passwd",
        "/etc/passwd",
        "a/../../b",
        "..",
        "./../x",
        "src/../../out",
        r"..\..\windows",
    ],
)
def test_paths_escaping_the_repository_are_refused(path):
    """Regression: an earlier guard used lstrip('./'), which strips any run of
    '.' and '/' — turning '../../etc/passwd' into 'etc/passwd' and passing."""
    with pytest.raises(ValidationError):
        FileWrite(path=path, content="x")


def test_writing_inside_dot_git_is_refused():
    with pytest.raises(ValidationError, match="refusing to write inside .git"):
        FileWrite(path=".git/config", content="x")


def test_a_leading_dot_slash_is_normalised_not_stripped_character_wise():
    assert FileWrite(path="./src/a.py", content="x").path == "src/a.py"


def test_ordinary_paths_are_allowed():
    assert FileWrite(path="src/pkg/mod.py", content="x").path == "src/pkg/mod.py"


def test_an_empty_path_is_refused():
    with pytest.raises(ValidationError):
        FileWrite(path="   ", content="x")


# --- content -------------------------------------------------------------


def test_empty_content_is_refused():
    """Content is written verbatim, so an empty file silently destroys the original."""
    with pytest.raises(ValidationError, match="content is empty"):
        FileWrite(path="src/a.py", content="   \n")


def test_an_enormous_file_is_refused():
    with pytest.raises(ValidationError, match="Split the work"):
        FileWrite(path="src/a.py", content="x" * (MAX_FILE_BYTES + 1))


# --- Definition of Done --------------------------------------------------


def test_a_first_attempt_without_a_test_is_refused():
    """DoD §7.1 enforced as a schema rule, so it is a SCHEMA failure the repair
    loop handles rather than something a reviewer catches later."""
    with pytest.raises(ValidationError, match="criteria_tests"):
        FirstAttempt(summary="s", new_files=[code()])


def test_a_first_attempt_with_a_test_is_accepted():
    impl = FirstAttempt(summary="s", criteria_tests=PROVED, new_files=[code(), a_test()])
    assert len(impl.new_files) == 2


@pytest.mark.parametrize("name", ["tests/test_mod.py", "src/pkg/mod_test.py"])
def test_both_test_naming_conventions_count(name):
    assert FirstAttempt(
        summary="s", criteria_tests=PROVED, new_files=[code(), a_test(name)]
    ).new_files


def test_a_repair_may_return_the_fix_alone():
    """A repair is told to return only the edits that fix the failure, and the
    test it wrote first time is already in the worktree. Requiring a test in
    every submission asks it to choose which instruction to disobey — story #11
    chose correctly, returned the fix alone, and was rejected for it twice.

    QA is the real guard here: it reads the worktree and will not accept a story
    until it can name the test proving each criterion."""
    impl = Implementation(summary="fix main()", new_files=[code()])
    assert impl.new_files


def test_a_repair_still_has_to_do_something():
    with pytest.raises(ValidationError, match="create a file or edit one"):
        Implementation(summary="nothing to do")


def test_an_empty_implementation_is_refused():
    with pytest.raises(ValidationError, match="create a file or edit one"):
        Implementation(summary="s")


def test_a_test_added_to_an_existing_file_satisfies_the_rule():
    """A story extending a module usually adds cases, not a whole test file."""

    impl = Implementation(
        summary="s",
        edits=[
            FileEdit(path="src/m.py", operation="add", target="f", source="def f():\n    pass"),
            FileEdit(
                path="tests/test_m.py",
                operation="add",
                target="test_f",
                source="def test_f():\n    assert True",
            ),
        ],
    )
    assert len(impl.edits) == 2


def test_an_edit_without_source_is_refused_unless_deleting():

    with pytest.raises(ValidationError, match="needs source"):
        FileEdit(path="src/m.py", operation="replace", target="f")
    assert FileEdit(path="src/m.py", operation="delete", target="f").target == "f"


def test_an_edit_path_cannot_escape_the_repository():

    with pytest.raises(ValidationError):
        FileEdit(path="../../etc/passwd", operation="add", target="f", source="x = 1")


# --- what a repair is told ------------------------------------------------


def _repair_prompt(feedback: str = "1 failed") -> str:
    """The task description the Developer sees on a repair."""
    import crew_org.crews.delivery_crew as dc

    captured: dict[str, str] = {}

    class FakeTask:
        def __init__(self, *, description, **_kw):
            captured["description"] = description

    original_task, original_crew = dc.Task, dc.Crew
    dc.Task = FakeTask
    try:
        dc.implement_story("a story", context="### Files", feedback=feedback)
    except Exception:  # noqa: BLE001, S110 — only the prompt is under test
        pass
    finally:
        dc.Task, dc.Crew = original_task, original_crew
    return captured.get("description", "")


def test_a_repair_is_told_its_previous_attempt_is_already_written():
    """Story #10 tried to `add` a test its own earlier attempt had added, then
    returned nothing at all. The worktree accumulates across attempts, so a
    repair that does not know this re-sends work that already landed."""
    prompt = _repair_prompt()
    assert "ALREADY BEEN WRITTEN" in prompt
    assert "replace" in prompt


def test_a_repair_is_not_asked_for_whole_files():
    """`Return the complete corrected files` survived from the whole-file
    schema (46fb8c6) through the move to editing by name (f8bb521), and
    contradicted the standing instructions on every repair."""
    prompt = _repair_prompt()
    assert "complete corrected files" not in prompt
    assert "Return ONLY the edits" in prompt


def test_a_first_attempt_carries_no_repair_block():
    """The repair text must not enter the cacheable prefix of a fresh attempt."""
    assert "ALREADY BEEN WRITTEN" not in _repair_prompt(feedback="")


# --- #172: a first attempt carries the test proving each criterion, before the code -------


def criterion_test(path="tests/test_sprint_range.py", test="test_range", source=None):
    return CriterionTest(
        criterion="a range is reported",
        path=path,
        test=test,
        source=source or f"def {test}():\n    assert True\n",
    )


def test_a_first_attempt_must_carry_its_criteria_tests():
    """sprint-metrics#75's first attempt came back without a test three times in three."""
    with pytest.raises(ValidationError, match="criteria_tests"):
        FirstAttempt(summary="s", new_files=[code(), a_test()])


def test_criteria_tests_come_before_the_code_in_the_answer():
    schema = FirstAttempt.model_json_schema()
    fields = list(schema["properties"])
    assert fields.index("criteria_tests") < fields.index("new_files") < fields.index("edits")
    assert schema["properties"]["criteria_tests"]["minItems"] == 1


def test_a_criterion_test_is_written_not_just_named():
    """Named in one place and written in another, three of six first attempts wrote none."""
    with pytest.raises(ValidationError, match="doesn't define it"):
        criterion_test(source="def something_else():\n    pass\n")


def test_a_criterion_test_goes_in_a_test_file():
    with pytest.raises(ValidationError, match="isn't a test file"):
        criterion_test(path="src/sprint_metrics/crew_performance.py")


def test_a_criterion_test_is_applied_as_an_add_after_the_edits():
    impl = FirstAttempt(summary="s", criteria_tests=[criterion_test()], new_files=[code()])
    (edit,) = impl.all_edits
    assert (edit.path, edit.operation, edit.target) == (
        "tests/test_sprint_range.py",
        "add",
        "test_range",
    )
    assert not impl.changes_nothing


def test_criteria_tests_land_in_an_existing_and_a_new_test_file(tmp_path):
    from crew_org.tools import workspace

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_old.py").write_text("def test_there():\n    assert True\n")
    impl = FirstAttempt(
        summary="s",
        criteria_tests=[
            criterion_test(path="tests/test_old.py", test="test_added"),
            criterion_test(path="tests/test_new.py", test="test_fresh"),
        ],
        new_files=[FileWrite(path="tests/test_new.py", content="import json\n")],
    )
    workspace.apply_implementation(tmp_path, impl)
    old = (tmp_path / "tests" / "test_old.py").read_text()
    new = (tmp_path / "tests" / "test_new.py").read_text()
    assert "def test_there" in old and "def test_added" in old
    assert new.startswith("import json") and "def test_fresh" in new


def test_a_repair_is_not_asked_for_criteria_tests_again():
    assert Implementation(summary="fix", new_files=[code()]).criteria_tests == []


def test_a_test_already_in_a_new_test_file_is_not_added_twice(tmp_path):
    """Every first attempt at sprint-metrics#73 wrote its new test file with the tests in it."""
    from crew_org.tools import workspace

    fresh = "import json\n\n\ndef test_fresh():\n    assert True\n"
    impl = FirstAttempt(
        summary="s",
        criteria_tests=[criterion_test(path="tests/test_new.py", test="test_fresh")],
        new_files=[FileWrite(path="tests/test_new.py", content=fresh)],
    )
    assert impl.all_edits == []
    workspace.apply_implementation(tmp_path, impl)
    assert (tmp_path / "tests" / "test_new.py").read_text().count("def test_fresh") == 1


# --- #183: a criterion's test is applied once, whatever else carries it ------------------


def test_a_repair_resending_its_first_attempts_test_replaces_it(tmp_path):
    """sprint-metrics#93: a repair sent its criteria tests again; add refused them."""
    from crew_org.tools import workspace

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sprint_range.py").write_text(
        "def test_range():\n    assert False\n"
    )
    repair = Implementation(
        summary="fix",
        criteria_tests=[criterion_test(source="def test_range():\n    assert 1 + 1 == 2\n")],
    )
    workspace.apply_implementation(tmp_path, repair)
    text = (tmp_path / "tests" / "test_sprint_range.py").read_text()
    assert text.count("def test_range") == 1 and "assert 1 + 1 == 2" in text


def test_a_test_in_both_edits_and_criteria_is_applied_once(tmp_path):
    """sprint-metrics#95: the same test as an add edit and a criterion's test."""
    from crew_org.tools import workspace

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sprint_range.py").write_text("def test_there():\n    pass\n")
    source = "def test_range():\n    assert True\n"
    impl = FirstAttempt(
        summary="s",
        criteria_tests=[criterion_test(source=source)],
        edits=[
            FileEdit(
                path="tests/test_sprint_range.py",
                operation="add",
                target="test_range",
                source=source,
            )
        ],
    )
    assert len(impl.all_edits) == 1
    workspace.apply_implementation(tmp_path, impl)
    assert (tmp_path / "tests" / "test_sprint_range.py").read_text().count("def test_range") == 1


def test_a_new_criterion_test_is_still_added(tmp_path):
    from crew_org.tools import workspace

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sprint_range.py").write_text("def test_there():\n    pass\n")
    workspace.apply_implementation(
        tmp_path, FirstAttempt(summary="s", criteria_tests=[criterion_test()], new_files=[code()])
    )
    text = (tmp_path / "tests" / "test_sprint_range.py").read_text()
    assert "def test_there" in text and "def test_range" in text
