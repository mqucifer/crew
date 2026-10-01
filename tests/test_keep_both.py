"""Approved stories that only added to the same file keep both additions (#436).

sprint-metrics #380, #381, #382 and #384 each appended tests to one file. #384
was rebuilt from scratch twice for a conflict between two additions, then
blocked for a person. Against real repositories, as #119's catch-up tests are.
"""

from __future__ import annotations

import pytest

from crew_org.git_ops import MergeConflict
from tests.test_catch_up import git, land_on_main, repos  # noqa: F401

BASE = "def test_a():\n    assert True\n"


@pytest.fixture
def tests_file(repos):  # noqa: F811
    """Both sides start from one test file on main and the branch."""
    upstream, clone, ws = repos
    land_on_main(upstream, clone, "test_it.py", BASE)
    git(clone, "merge", "-q", "origin/main")
    return upstream, clone, ws


def test_two_additions_at_the_same_place_are_both_kept(tests_file):
    upstream, clone, ws = tests_file
    (clone / "test_it.py").write_text(BASE + "\n\ndef test_branch():\n    assert True\n")
    git(clone, "commit", "-q", "-am", "the story's test")
    land_on_main(upstream, clone, "test_it.py", BASE + "\n\ndef test_main():\n    assert True\n")

    assert ws.catch_up_keeping_both() == ["test_it.py"]

    text = (clone / "test_it.py").read_text()
    assert "def test_main" in text and "def test_branch" in text and "<<<<<<<" not in text
    assert text.index("test_main") < text.index("test_branch"), "main's first"
    assert git(clone, "status", "--porcelain") == ""
    assert len(git(clone, "log", "-1", "--format=%P").split()) == 2, "a merge, not a rewrite"


def test_a_line_both_sides_changed_still_conflicts_and_leaves_nothing_half_merged(tests_file):
    upstream, clone, ws = tests_file
    (clone / "test_it.py").write_text(BASE.replace("True", "1 == 1"))
    git(clone, "commit", "-q", "-am", "the branch changed test_a")
    head = git(clone, "rev-parse", "HEAD")
    land_on_main(upstream, clone, "test_it.py", BASE.replace("True", "2 == 2"))

    with pytest.raises(MergeConflict) as raised:
        ws.catch_up_keeping_both()

    assert raised.value.files == ["test_it.py"]
    assert git(clone, "rev-parse", "HEAD") == head
    assert git(clone, "status", "--porcelain") == ""


def test_two_tests_of_the_same_name_are_not_merged(tests_file):
    """pytest would run only the second: a silent loss, not a merge."""
    upstream, clone, ws = tests_file
    (clone / "test_it.py").write_text(BASE + "\n\ndef test_new():\n    assert 1\n")
    git(clone, "commit", "-q", "-am", "the branch's test_new")
    head = git(clone, "rev-parse", "HEAD")
    land_on_main(upstream, clone, "test_it.py", BASE + "\n\ndef test_new():\n    assert 2\n")

    with pytest.raises(MergeConflict):
        ws.catch_up_keeping_both()
    assert git(clone, "rev-parse", "HEAD") == head


def test_a_clean_merge_keeps_nothing_by_hand(tests_file):
    upstream, clone, ws = tests_file
    land_on_main(upstream, clone, "other.py", "y = 2\n")
    assert ws.catch_up_keeping_both() == []
    assert (clone / "other.py").exists()
