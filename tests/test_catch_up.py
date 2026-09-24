"""A resumed branch is brought up to date with main before work starts (#119).

Against real repositories, with a real `origin`: whether main's new commits
arrive, whether the branch's own history survives for the reviewer, and whether
a conflict leaves nothing half-merged are questions about git.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from crew_org.git_ops import BotIdentity, MergeConflict, Workspace


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repos(tmp_path):
    """An upstream with a feature branch, and a clone checked out on it."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-q", "-b", "main")
    (upstream / "app.py").write_text("def a():\n    return 1\n")
    git(upstream, "add", "-A")
    git(upstream, "commit", "-q", "-m", "base")
    git(upstream, "checkout", "-q", "-b", "feat/32-x")
    (upstream / "extra.py").write_text("x = 1\n")
    git(upstream, "add", "-A")
    git(upstream, "commit", "-q", "-m", "the story's earlier attempt")
    git(upstream, "checkout", "-q", "main")

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True)
    git(clone, "checkout", "-q", "-b", "feat/32-x", "origin/feat/32-x")
    ws = Workspace("o", "r", "tok", BotIdentity("bot", 1))
    ws.path = clone
    return upstream, clone, ws


def land_on_main(upstream: Path, clone: Path, name: str, text: str) -> None:
    git(upstream, "checkout", "-q", "main")
    (upstream / name).write_text(text)
    git(upstream, "add", "-A")
    git(upstream, "commit", "-q", "-m", f"sibling landed: {name}")
    git(clone, "fetch", "-q", "origin")


def test_work_that_landed_on_main_is_brought_in(repos):
    """#32 was resumed on a branch cut before its sibling #31 landed, and could
    not see #31's code at all."""
    upstream, clone, ws = repos
    land_on_main(upstream, clone, "scrape.py", "def serve():\n    pass\n")

    assert ws.catch_up() is True
    assert (clone / "scrape.py").exists()


def test_the_branchs_own_history_survives_for_the_reviewer(repos):
    """Merged in, not rebased: the commit the reviewer read is still there, so
    the push that follows is a fast-forward."""
    upstream, clone, ws = repos
    before = git(clone, "rev-parse", "HEAD")
    land_on_main(upstream, clone, "scrape.py", "def serve():\n    pass\n")

    ws.catch_up()
    assert (clone / "extra.py").exists()
    git(clone, "merge-base", "--is-ancestor", before, "HEAD")  # raises if not


def test_a_branch_already_up_to_date_is_left_alone(repos):
    _upstream, clone, ws = repos
    before = git(clone, "rev-parse", "HEAD")
    assert ws.catch_up() is False
    assert git(clone, "rev-parse", "HEAD") == before


def test_a_conflict_is_aborted_and_names_the_paths(repos):
    """Never forced, and nothing half-merged left for the Developer to trip on."""
    upstream, clone, ws = repos
    (clone / "app.py").write_text("def a():\n    return 2\n")
    git(clone, "commit", "-q", "-am", "the branch changed a()")
    head = git(clone, "rev-parse", "HEAD")
    land_on_main(upstream, clone, "app.py", "def a():\n    return 3\n")

    with pytest.raises(MergeConflict) as raised:
        ws.catch_up()

    assert raised.value.files == ["app.py"]
    assert git(clone, "rev-parse", "HEAD") == head
    assert git(clone, "status", "--porcelain") == ""
