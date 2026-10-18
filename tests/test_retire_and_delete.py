"""A story can delete a module whose names others still import (sprint-metrics#132).

#132 removes `crew_performance.py`, the module the split emptied. A name the deleted
module passed along must be imported from where it lives now: the gates at `done`
say which file still asks, and that a re-export can't stay in a file the branch
deletes (#273).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crew_org.tools.regression import lost_names, merged_base
from tests.test_regression import Ed, change, git

PKG = "src/sprint_metrics"
OLD = f"{PKG}/crew_performance.py"
PINS = "tests/test_split.py"
OLD_LINE = "from sprint_metrics.metrics import throughput as throughput\n"


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
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "merged")
    git(tmp_path, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    git(tmp_path, "checkout", "-qb", "feat/132")
    return tmp_path


def test_a_name_lost_from_a_deleted_module_is_repointed_not_kept(repo: Path):
    """#273: #132 was offered a re-export in the file it had to delete."""
    (repo / OLD).unlink()
    change(repo, Ed(PINS, "delete", "test_crew_performance_reexports_throughput"))
    pins = repo / PINS
    pins.write_text(pins.read_text().replace("from sprint_metrics import crew_performance\n", ""))
    _was, told = lost_names(repo, merged_base(repo))[f"{OLD}::throughput"]
    assert f"this story deletes `{OLD}`" in told
    assert f"Change `{PKG}/__init__.py` to import it from `sprint_metrics.metrics`" in told
    assert "Keep it in" not in told


def test_a_name_lost_from_a_module_that_stays_may_still_be_kept(repo: Path):
    (repo / OLD).write_text((repo / OLD).read_text().replace(OLD_LINE, ""))
    _was, told = lost_names(repo, merged_base(repo))[f"{OLD}::throughput"]
    assert f"Keep it in `{OLD}`" in told
