"""A name a module passes along is a promise too (sprint-metrics#129).

`crew_performance.py` imported `calculate_blocked_aging` from `metrics.py`,
and the package's `__init__.py` imported it from `crew_performance.py`. #129
removed the import as unused, which broke the package; putting it back
failed lint. It went round between the two and blocked, never told which
file still asked for the name or how to keep it and satisfy lint. The gate at
`done` reads the branch against what's merged (crew#778).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crew_org.tools.regression import lost_names, merged_base
from tests.test_regression import Ed, change, git

PKG = "src/sprint_metrics"
OLD = f"{PKG}/crew_performance.py"
IMPORT = "from sprint_metrics.metrics import calculate_blocked_aging\n"
FRONT = "from sprint_metrics.crew_performance import THRESHOLD, calculate_blocked_aging, main\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / PKG).mkdir(parents=True)
    (tmp_path / PKG / "metrics.py").write_text(
        "def calculate_blocked_aging(cards):\n    return 0\n"
    )
    (tmp_path / OLD).write_text(
        IMPORT + "\nTHRESHOLD: float = 3.0\n\n\ndef main():\n    return 0\n"
    )
    (tmp_path / PKG / "__init__.py").write_text(FRONT)
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "merged")
    git(tmp_path, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    git(tmp_path, "checkout", "-qb", "feat/129")
    return tmp_path


def quote(repo: Path, path: str, find: str, replace: str) -> None:
    """A `replace_text` on the branch."""
    target = repo / path
    target.write_text(target.read_text().replace(find, replace, 1))


def lost(repo: Path) -> dict:
    return lost_names(repo, merged_base(repo))


def drop_import(repo: Path) -> None:
    quote(repo, OLD, IMPORT, "")


def test_dropping_a_name_another_file_imports_is_refused_with_both_ways_out(repo: Path):
    """#129's shape."""
    drop_import(repo)
    found = lost(repo)
    (key,) = found
    assert key == f"{OLD}::calculate_blocked_aging"
    was, now = found[key]
    assert was == "imported from sprint_metrics.metrics"
    assert f"`{PKG}/__init__.py` still imports it from `{OLD}`" in now
    assert (
        "from sprint_metrics.metrics import calculate_blocked_aging as calculate_blocked_aging"
        in now
    )
    assert "change" in now and "where it lives now" in now


def test_pointing_the_asker_at_the_new_home_first_is_fine(repo: Path):
    drop_import(repo)
    quote(
        repo,
        f"{PKG}/__init__.py",
        FRONT,
        "from sprint_metrics.crew_performance import THRESHOLD, main\n"
        "from sprint_metrics.metrics import calculate_blocked_aging\n",
    )
    assert lost(repo) == {}


def test_keeping_it_as_a_pass_through_is_fine(repo: Path):
    quote(
        repo,
        OLD,
        IMPORT,
        "from sprint_metrics.metrics import calculate_blocked_aging as calculate_blocked_aging\n",
    )
    assert lost(repo) == {}


def test_a_pass_through_nobody_imports_may_go(repo: Path):
    (repo / PKG / "__init__.py").write_text("from sprint_metrics.crew_performance import main\n")
    drop_import(repo)
    assert lost(repo) == {}


def test_a_constant_another_file_imports_is_protected(repo: Path):
    change(repo, Ed(OLD, "delete", "THRESHOLD"))
    found = lost(repo)
    assert set(found) == {f"{OLD}::THRESHOLD"}
    assert found[f"{OLD}::THRESHOLD"][0] == "a constant assigned here"


def test_definitions_are_left_to_the_contract_check(repo: Path):
    """A removed function is `draft_breaks`' finding; it isn't reported twice."""
    change(repo, Ed(OLD, "delete", "main"))
    assert lost(repo) == {}
