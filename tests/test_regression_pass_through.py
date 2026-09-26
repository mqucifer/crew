"""A name a module passes along is a promise too (sprint-metrics#129).

`crew_performance.py` imported `calculate_blocked_aging` from `metrics.py`,
and the package's `__init__.py` imported it from `crew_performance.py`. #129
removed the import as unused, which broke the package; putting it back
failed lint. It went round between the two and blocked, never told which
file still asked for the name or how to keep it and satisfy lint.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crew_org.crews.delivery_crew import FileEdit, Implementation, TextEdit
from crew_org.tools.regression import lost_names

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
    return tmp_path


def impl(**parts) -> Implementation:
    return Implementation(summary="Tidy crew_performance.py's imports", **parts)


def drop_import() -> TextEdit:
    return TextEdit(path=OLD, find=IMPORT, replace="")


def test_dropping_a_name_another_file_imports_is_refused_with_both_ways_out(repo: Path):
    """#129's shape."""
    lost = lost_names(repo, impl(text_edits=[drop_import()]))
    (key,) = lost
    assert key == f"{OLD}::calculate_blocked_aging"
    was, now = lost[key]
    assert was == "imported from sprint_metrics.metrics"
    assert f"`{PKG}/__init__.py` still imports it from `{OLD}`" in now
    assert (
        "from sprint_metrics.metrics import calculate_blocked_aging as calculate_blocked_aging"
        in now
    )
    assert "change" in now and "where it lives now" in now


def test_pointing_the_asker_at_the_new_home_first_is_fine(repo: Path):
    repoint = TextEdit(
        path=f"{PKG}/__init__.py",
        find=FRONT,
        replace=(
            "from sprint_metrics.crew_performance import THRESHOLD, main\n"
            "from sprint_metrics.metrics import calculate_blocked_aging\n"
        ),
    )
    assert lost_names(repo, impl(text_edits=[drop_import(), repoint])) == {}


def test_keeping_it_as_a_pass_through_is_fine(repo: Path):
    keep = TextEdit(
        path=OLD,
        find=IMPORT,
        replace=(
            "from sprint_metrics.metrics import "
            "calculate_blocked_aging as calculate_blocked_aging\n"
        ),
    )
    assert lost_names(repo, impl(text_edits=[keep])) == {}


def test_a_pass_through_nobody_imports_may_go(repo: Path):
    (repo / PKG / "__init__.py").write_text("from sprint_metrics.crew_performance import main\n")
    assert lost_names(repo, impl(text_edits=[drop_import()])) == {}


def test_a_constant_another_file_imports_is_protected(repo: Path):
    delete = FileEdit(path=OLD, operation="delete", target="THRESHOLD")
    lost = lost_names(repo, impl(edits=[delete]))
    assert set(lost) == {f"{OLD}::THRESHOLD"}
    assert lost[f"{OLD}::THRESHOLD"][0] == "a constant assigned here"


def test_definitions_are_left_to_the_contract_check(repo: Path):
    """A removed function is `broken_contracts`' finding; it isn't reported twice."""
    delete = FileEdit(path=OLD, operation="delete", target="main")
    assert lost_names(repo, impl(edits=[delete])) == {}


def test_a_change_that_wont_apply_reports_nothing_here(repo: Path):
    missing = TextEdit(path=OLD, find="import nothing_like_this\n", replace="")
    assert lost_names(repo, impl(text_edits=[missing])) == {}
