"""A story can delete a file and retire the tests that pinned it (sprint-metrics#132).

#132 removes `crew_performance.py`, the module the split emptied. The Developer
had no way to delete a file, so it tried to blank it with one quoted edit,
refused because it reached into a definition. And earlier split stories had
merged tests checking the module's pass-throughs, which the regression guard
protected. Ending those pass-throughs is the story's job, and nothing let it
say so.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from crew_org.crews.delivery_crew import FileEdit, Implementation, RetiredTest, TextEdit
from crew_org.tools.ast_edit import EditError
from crew_org.tools.bounds import touched
from crew_org.tools.regression import (
    broken_contracts,
    draft_breaks,
    lost_names,
    merged_base,
    without_moves,
    without_retired,
)
from crew_org.tools.workspace import apply_implementation
from tests.test_regression_merged import git

PKG = "src/sprint_metrics"
OLD = f"{PKG}/crew_performance.py"
PINS = "tests/test_split.py"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The split near its end: the old module only passes names along."""
    (tmp_path / PKG).mkdir(parents=True)
    (tmp_path / PKG / "metrics.py").write_text("def throughput(cards):\n    return 0\n")
    (tmp_path / OLD).write_text("from sprint_metrics.metrics import throughput as throughput\n")
    (tmp_path / PKG / "__init__.py").write_text(
        "from sprint_metrics.crew_performance import throughput\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / PINS).write_text(
        "from sprint_metrics import crew_performance\n\n\n"
        "def test_crew_performance_reexports_throughput():\n"
        "    assert crew_performance.throughput\n\n\n"
        "def test_throughput():\n    assert True\n"
    )
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "merged")
    git(tmp_path, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    git(tmp_path, "checkout", "-qb", "feat/132")
    return tmp_path


REPOINT = TextEdit(
    path=f"{PKG}/__init__.py",
    find="from sprint_metrics.crew_performance import throughput\n",
    replace="from sprint_metrics.metrics import throughput\n",
)
UNPIN = FileEdit(path=PINS, operation="delete", target="test_crew_performance_reexports_throughput")
IMPORT_LINE = "from sprint_metrics import crew_performance\n\n\n"
DROP_IMPORT = TextEdit(path=PINS, find=IMPORT_LINE, replace="")
RETIRE = RetiredTest(
    path=PINS,
    test="test_crew_performance_reexports_throughput",
    why="#132 removes crew_performance.py, whose pass-throughs this pinned",
)


def removal(**parts) -> Implementation:
    return Implementation(summary="Remove crew_performance.py", **parts)


def judge(repo: Path, implementation: Implementation) -> dict:
    """Every guard, in delivery's order."""
    merged = merged_base(repo)
    broken = broken_contracts(repo, implementation.all_edits, merged)
    broken = draft_breaks(repo, implementation, merged) | broken
    broken = without_moves(repo, implementation, broken, merged)
    broken |= lost_names(repo, implementation, merged)
    return without_retired(broken, implementation)


def test_the_whole_removal_passes_when_it_retires_what_pinned_the_module(repo: Path):
    """What #132 should have been able to send."""
    implementation = removal(
        deleted_files=[OLD],
        edits=[UNPIN],
        text_edits=[REPOINT, DROP_IMPORT],
        retired_tests=[RETIRE],
    )
    assert judge(repo, implementation) == {}


def test_a_merged_test_deleted_without_retiring_it_is_refused(repo: Path):
    implementation = removal(deleted_files=[OLD], edits=[UNPIN], text_edits=[REPOINT, DROP_IMPORT])
    assert set(judge(repo, implementation)) == {f"{PINS}::{RETIRE.test}"}


def test_retiring_a_test_does_not_excuse_anything_else(repo: Path):
    """__init__.py still imports from the deleted module: that's still broken."""
    implementation = removal(
        deleted_files=[OLD], edits=[UNPIN], text_edits=[DROP_IMPORT], retired_tests=[RETIRE]
    )
    assert f"{OLD}::throughput" in judge(repo, implementation)


def test_only_a_test_can_be_retired():
    with pytest.raises(ValidationError, match="only tests can be retired"):
        RetiredTest(path=OLD, test="throughput", why="the module is going away now")


def test_a_retirement_says_why():
    with pytest.raises(ValidationError, match="say what this story ends"):
        RetiredTest(path=PINS, test="test_x", why="gone")


def test_a_deleted_file_is_removed_and_a_missing_one_refused(repo: Path):
    apply_implementation(repo, removal(deleted_files=[OLD]))
    assert not (repo / OLD).exists()
    with pytest.raises(EditError, match="isn't a file in the repository"):
        apply_implementation(repo, removal(deleted_files=[OLD]))


def test_a_deleted_file_counts_as_touched_for_the_projects_protections():
    assert ".crew/project.yaml" in touched(removal(deleted_files=[".crew/project.yaml"]))


def test_a_deletion_alone_is_a_change():
    assert not removal(deleted_files=[OLD]).changes_nothing


def test_retiring_a_test_deletes_it(repo: Path):
    """#132 retired tests and sent no delete for them; the one still asking was retired."""
    implementation = removal(
        deleted_files=[OLD], text_edits=[REPOINT, DROP_IMPORT], retired_tests=[RETIRE]
    )
    assert judge(repo, implementation) == {}
    apply_implementation(repo, implementation)
    assert RETIRE.test not in (repo / PINS).read_text()
    assert "def test_throughput" in (repo / PINS).read_text()


def test_a_retired_test_already_deleted_by_name_is_fine(repo: Path):
    implementation = removal(
        deleted_files=[OLD],
        edits=[UNPIN],
        text_edits=[REPOINT, DROP_IMPORT],
        retired_tests=[RETIRE],
    )
    apply_implementation(repo, implementation)
    assert RETIRE.test not in (repo / PINS).read_text()
