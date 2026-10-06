"""The never-push-to-main rail is enforced here in code because branch
protection needs GitHub Pro on a private repo. Until that changes, these tests
are the rail."""

from __future__ import annotations

import pytest

from crew_org.git_ops import (
    BranchNameError,
    ProtectedBranchError,
    assert_writable,
    branch_name,
    validate_branch_name,
)


@pytest.mark.parametrize("branch", ["main", "master", "trunk", "release", "develop"])
def test_protected_branches_are_refused(branch):
    with pytest.raises(ProtectedBranchError, match="protected"):
        assert_writable(branch)


@pytest.mark.parametrize("branch", ["MAIN", " main ", "Master"])
def test_protection_is_not_defeated_by_case_or_whitespace(branch):
    with pytest.raises(ProtectedBranchError):
        assert_writable(branch)


def test_a_feature_branch_is_writable():
    assert_writable("feat/42-cycle-time-metric")  # must not raise


def test_branch_name_follows_the_convention():
    assert branch_name(42, "Cycle time metric") == "feat/42-cycle-time-metric"
    assert branch_name(7, "Fix off-by-one", kind="fix") == "fix/7-fix-off-by-one"


def test_branch_name_slugifies_punctuation():
    assert branch_name(3, "Report: cycle time (p50/p90)") == "feat/3-report-cycle-time-p50-p90"


def test_unknown_branch_type_is_refused():
    with pytest.raises(BranchNameError, match="not one of"):
        branch_name(1, "thing", kind="hotfix")


def test_nonsense_issue_number_is_refused():
    with pytest.raises(BranchNameError, match="positive"):
        branch_name(0, "thing")


def test_summary_that_slugifies_to_nothing_is_refused():
    with pytest.raises(BranchNameError, match="empty slug"):
        branch_name(1, "!!!")


def test_valid_branch_names_pass_validation():
    for branch in ("feat/1-a", "fix/22-two-words", "spike/9-investigate-pagination"):
        validate_branch_name(branch)


@pytest.mark.parametrize(
    "branch",
    ["feat-42-no-slash", "feat/no-issue-number", "42-missing-type", "feat/42-Has-Capitals"],
)
def test_malformed_branch_names_are_refused(branch):
    with pytest.raises(BranchNameError):
        validate_branch_name(branch)


def test_validation_checks_protection_before_shape():
    """'main' is refused as protected, not merely as badly shaped."""
    with pytest.raises(ProtectedBranchError):
        validate_branch_name("main")


# --- worktrees -----------------------------------------------------------


def test_the_registry_is_pruned_before_a_worktree_is_opened(monkeypatch, tmp_path):
    """Git records worktrees in the clone, and that record outlives the
    directory. Without a prune, a tree deleted from disk still holds its branch
    and the next run cannot claim it."""
    from crew_org import git_ops

    calls: list[list[str]] = []

    def fake_run(args, *, cwd=None, token=None):
        calls.append(args)
        if args[0] == "symbolic-ref":
            return "refs/remotes/origin/main"
        return ""

    monkeypatch.setattr(git_ops, "_run", fake_run)
    monkeypatch.setattr(git_ops.Workspace, "_ensure_clone", lambda self: None)

    ws = git_ops.Workspace("o", "r", "tok", git_ops.BotIdentity("bot", 1))
    monkeypatch.setattr(git_ops, "WORKTREES", tmp_path / "wt")
    ws.open("feat/6-a-story")

    subcommands = [c[0] for c in calls]
    assert "prune" in [c[1] for c in calls if c[0] == "worktree"]
    assert subcommands.index("worktree") < len(subcommands)
    # The prune must come before the add, or it prunes nothing useful.
    worktree_ops = [c[1] for c in calls if c[0] == "worktree"]
    assert worktree_ops.index("prune") < worktree_ops.index("add")


# --- resuming a previous attempt -----------------------------------------


def _resume_workspace(monkeypatch, tmp_path, *, remote_branch: bool, merged: bool):
    """A Workspace whose git answers are scripted, and the commands it ran."""
    from crew_org import git_ops

    calls: list[list[str]] = []

    def fake_run(args, *, cwd=None, token=None):
        calls.append(args)
        if args[0] == "rev-parse" and not remote_branch:
            raise git_ops.GitError("unknown revision")
        if args[0] == "merge-base" and not merged:
            raise git_ops.GitError("not an ancestor")
        if args[0] == "symbolic-ref":
            return "refs/remotes/origin/main"
        return ""

    monkeypatch.setattr(git_ops, "_run", fake_run)
    monkeypatch.setattr(git_ops.Workspace, "_ensure_clone", lambda self: None)
    monkeypatch.setattr(git_ops, "WORKTREES", tmp_path / "wt")
    return git_ops.Workspace("o", "r", "tok", git_ops.BotIdentity("bot", 1)), calls


def _base_of(calls):
    add = next(c for c in calls if c[:2] == ["worktree", "add"])
    return add[-1]


def test_a_first_delivery_starts_from_the_default_branch(monkeypatch, tmp_path):
    ws, calls = _resume_workspace(monkeypatch, tmp_path, remote_branch=False, merged=False)
    ws.open("feat/31-scrape-endpoint", resume=True)
    assert _base_of(calls) == "origin/HEAD"
    assert ws.resumed is False


def test_a_re_delivery_builds_on_the_attempt_it_is_repairing(monkeypatch, tmp_path):
    """`open` reset the branch to origin/HEAD unconditionally, so the accepted
    implementation a verdict was *about* was discarded while the verdict
    describing it was still handed to the Developer. sprint-metrics #31 spent
    an attempt trying to edit a function that no longer existed."""
    ws, calls = _resume_workspace(monkeypatch, tmp_path, remote_branch=True, merged=False)
    ws.open("feat/31-scrape-endpoint", resume=True)
    assert _base_of(calls) == "origin/feat/31-scrape-endpoint"
    assert ws.resumed is True


def test_a_merged_branch_is_not_resumed(monkeypatch, tmp_path):
    """Its commits are already in the default branch; resuming onto them would
    re-apply work that has landed."""
    ws, calls = _resume_workspace(monkeypatch, tmp_path, remote_branch=True, merged=True)
    ws.open("feat/31-scrape-endpoint", resume=True)
    assert _base_of(calls) == "origin/HEAD"
    assert ws.resumed is False


def test_without_resume_nothing_is_carried_over(monkeypatch, tmp_path):
    ws, calls = _resume_workspace(monkeypatch, tmp_path, remote_branch=True, merged=False)
    ws.open("feat/31-scrape-endpoint")
    assert _base_of(calls) == "origin/HEAD"
    assert ws.resumed is False


# --- what landed in a conflicting file first (#436) ----------------------------------------------


def test_the_last_commit_to_land_in_the_files_is_named(tmp_path):
    import subprocess

    from crew_org.git_ops import last_landed

    def git(*args):
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    for name, subject in (
        ("a.txt", "feat: first"),
        ("b.txt", "feat: second"),
        ("a.txt", "fix: third"),
    ):
        (tmp_path / name).write_text(subject)
        git("add", name)
        git("commit", "-q", "-m", subject)
    git("update-ref", "refs/remotes/origin/HEAD", "HEAD")

    assert last_landed(tmp_path, ["a.txt"]) == "fix: third"
    assert last_landed(tmp_path, ["b.txt"]) == "feat: second"
    assert last_landed(tmp_path, []) == ""


def test_what_landed_is_empty_when_it_cannot_be_read(tmp_path):
    from crew_org.git_ops import last_landed

    assert last_landed(tmp_path, ["a.txt"]) == ""
