"""Claude's guard hook refuses what CLAUDE.md forbids, and nothing else (crew#458)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "claude_pretool_guard.py"


def run_guard(command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    return subprocess.run(
        [sys.executable, str(SCRIPT)], input=json.dumps(payload), capture_output=True, text=True
    )


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(
        path,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
    )
    return path


@pytest.fixture
def guard(monkeypatch):
    spec = importlib.util.spec_from_file_location("claude_pretool_guard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- pushes ----------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git push origin HEAD:main",
        "git push -f origin +feature:refs/heads/main",
        "git push --all origin",
        "git push",
        "git push origin HEAD",
        "uv run pytest -q && git push origin main",
    ],
)
def test_a_push_to_main_is_refused(repo, command):
    done = run_guard(command, repo)
    assert done.returncode == 2
    assert "PR" in done.stderr


@pytest.mark.parametrize(
    "command", ["git push -u origin feat/458-claude-standards", "git push origin HEAD:feat/x"]
)
def test_a_push_of_a_branch_is_allowed(repo, command):
    assert run_guard(command, repo).returncode == 0


def test_a_bare_push_from_a_branch_is_allowed(repo):
    git(repo, "checkout", "-q", "-b", "feat/x")
    assert run_guard("git push", repo).returncode == 0


# --- pulls during a tick ---------------------------------------------------


def test_a_pull_in_the_main_checkout_during_a_tick_is_refused(guard, repo, monkeypatch):
    monkeypatch.setattr(guard, "tick_running", lambda: True)
    with pytest.raises(SystemExit) as stopped:
        guard.check_pull(["pull"], repo)
    assert stopped.value.code == 2


def test_merging_origin_counts_as_pulling(guard, repo, monkeypatch):
    monkeypatch.setattr(guard, "tick_running", lambda: True)
    with pytest.raises(SystemExit):
        guard.check_pull(["merge", "--ff-only", "origin/main"], repo)


def test_a_pull_with_no_tick_running_is_allowed(guard, repo, monkeypatch):
    monkeypatch.setattr(guard, "tick_running", lambda: False)
    guard.check_pull(["pull"], repo)


def test_a_pull_in_a_worktree_during_a_tick_is_allowed(guard, repo, tmp_path, monkeypatch):
    """The tick runs from the main checkout; a worktree is a separate directory."""
    worktree = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "feat/y", str(worktree))
    monkeypatch.setattr(guard, "tick_running", lambda: True)
    guard.check_pull(["pull"], worktree)


# --- bare issue numbers ----------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        'gh issue create --title "x" --body "see #12"',
        'gh pr create --title "fix: y (#458)" --body "Closes crew#458"',
        "gh pr comment 5 --body \"$(cat <<'EOF'\nFollows #301.\nEOF\n)\"",
    ],
)
def test_a_bare_issue_number_in_a_gh_body_is_refused(repo, command):
    done = run_guard(command, repo)
    assert done.returncode == 2
    assert "owner/repo#N" in done.stderr


@pytest.mark.parametrize(
    "command",
    [
        'gh issue create --title "x" --body "see crew#12 and mqucifer/sprint-metrics#3"',
        'gh pr create --body "https://github.com/mqucifer/crew/issues/12\n## Why"',
        "gh pr view 455 --json body",  # reading isn't posting
        'echo "#12"',
    ],
)
def test_scoped_references_and_other_commands_pass(repo, command):
    assert run_guard(command, repo).returncode == 0


def test_a_body_file_is_checked_too(repo):
    (repo / "body.md").write_text("Closes #9\n")
    assert run_guard("gh pr create --body-file body.md", repo).returncode == 2


def test_input_it_cannot_read_is_allowed(repo):
    done = subprocess.run(
        [sys.executable, str(SCRIPT)], input="not json", capture_output=True, text=True
    )
    assert done.returncode == 0
