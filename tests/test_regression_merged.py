"""The guards protect what's merged, not a story's own draft.

A failed attempt's changes stay in the worktree for the repair to build on.
Judged against that draft, the guards went wrong both ways on the night of
the split: sprint-metrics#126 couldn't delete a test its own first attempt had
added, and #129's first attempt removed imports `__init__.py` needed, after
which nothing was "lost" again and the tests failed without the guard saying
why.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from crew_org.crews.delivery_crew import FileEdit, Implementation
from crew_org.tools.regression import (
    REMOVED,
    broken_contracts,
    draft_breaks,
    lost_names,
    merged_base,
    without_moves,
)

PKG = "src/sprint_metrics"
OLD = f"{PKG}/crew_performance.py"


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A clone whose default branch has one merged module, and a story branch off it."""
    (tmp_path / PKG).mkdir(parents=True)
    (tmp_path / PKG / "metrics.py").write_text(
        "def calculate_blocked_aging(cards):\n    return 0\n"
    )
    (tmp_path / OLD).write_text(
        "from sprint_metrics.metrics import calculate_blocked_aging\n\n\n"
        "def main():\n    return calculate_blocked_aging([])\n\n\n"
        "def throughput(cards):\n    return 0\n"
    )
    (tmp_path / PKG / "__init__.py").write_text(
        "from sprint_metrics.crew_performance import calculate_blocked_aging, main\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_metrics.py").write_text("def test_merged():\n    pass\n")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "merged")
    git(tmp_path, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    git(tmp_path, "checkout", "-qb", "feat/129")
    return tmp_path


# A change somewhere else: the repair doesn't touch the damaged file again.
ANOTHER_TEST = FileEdit(
    path="tests/test_metrics.py",
    operation="add",
    target="test_another",
    source="def test_another():\n    pass\n",
)


def impl(**parts) -> Implementation:
    return Implementation(summary="Repair the previous attempt", **parts)


def test_the_merged_state_is_found_and_absent_outside_git(repo: Path, tmp_path_factory):
    assert merged_base(repo) is not None
    assert merged_base(tmp_path_factory.mktemp("plain")) is None


def test_a_test_only_the_draft_added_may_be_deleted(repo: Path):
    """#126: its first attempt added `test_full_suite_passes`; the repair removed it."""
    with (repo / "tests/test_metrics.py").open("a") as fh:
        fh.write("\n\ndef test_full_suite_passes():\n    pass\n")
    delete = FileEdit(
        path="tests/test_metrics.py", operation="delete", target="test_full_suite_passes"
    )
    merged = merged_base(repo)
    assert broken_contracts(repo, [delete], merged) == {}
    assert broken_contracts(repo, [delete]), "judged against the draft, it was refused"


def test_a_merged_test_is_still_protected(repo: Path):
    delete = FileEdit(path="tests/test_metrics.py", operation="delete", target="test_merged")
    assert set(broken_contracts(repo, [delete], merged_base(repo))) == {
        "tests/test_metrics.py::test_merged"
    }


def test_an_import_the_draft_already_removed_is_still_lost(repo: Path):
    """#129: the first attempt removed it; the repair didn't touch the file again."""
    text = (
        (repo / OLD)
        .read_text()
        .replace("from sprint_metrics.metrics import calculate_blocked_aging\n", "")
    )
    (repo / OLD).write_text(text)
    elsewhere = ANOTHER_TEST
    lost = lost_names(repo, impl(edits=[elsewhere]), merged_base(repo))
    assert set(lost) == {f"{OLD}::calculate_blocked_aging"}
    assert lost_names(repo, impl(edits=[elsewhere])) == {}, "the draft hid it"


def test_a_definition_the_draft_already_deleted_is_still_broken(repo: Path):
    text = (repo / OLD).read_text().replace("def throughput(cards):\n    return 0\n", "")
    (repo / OLD).write_text(text)
    elsewhere = ANOTHER_TEST
    broken = draft_breaks(repo, impl(edits=[elsewhere]), merged_base(repo))
    assert broken == {f"{OLD}::throughput": ("def(cards)", REMOVED)}


def test_a_draft_move_is_still_a_move(repo: Path):
    """An earlier attempt moved `throughput` properly; nothing here is broken."""
    text = (repo / OLD).read_text().replace("def throughput(cards):\n    return 0\n", "")
    (repo / OLD).write_text("from sprint_metrics.flow import throughput\n" + text)
    (repo / PKG / "flow.py").write_text("def throughput(cards):\n    return 0\n")
    merged = merged_base(repo)
    elsewhere = ANOTHER_TEST
    implementation = impl(edits=[elsewhere])
    broken = draft_breaks(repo, implementation, merged)
    assert without_moves(repo, implementation, broken, merged) == {}
