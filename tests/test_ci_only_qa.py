"""A pull request that changes only CI workflows is judged by review, not QA (crew#333).

The release stories' criteria (a tag exists, an image is in GHCR, an attestation
references the commit) can only be observed when the workflow runs on `main`.
QA could prove none of them on a pull request, so each would have been returned
until it went back to the Product Owner, who can't make a release happen either.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crew_org.events import EventSink
from crew_org.flows import acceptance as flow
from crew_org.flows.acceptance import ci_only
from crew_org.tools.github_project import Card
from tests.test_acceptance import _green, _QABoard, _QAIssues, _QAWorkspace
from tests.test_regression_merged import git


def card() -> Card:
    return Card(
        item_id="C260",
        number=260,
        title="Create the release workflow",
        status="QAing",
        state="OPEN",
        work_type="Story",
        repo="sprint-metrics",
    )


def test_a_ci_only_change_goes_to_merging_with_the_reason_recorded(monkeypatch, tmp_path):
    judged = []
    monkeypatch.setattr(flow, "ci_only", lambda worktree: True)
    monkeypatch.setattr(flow, "verify_story", lambda *a, **k: judged.append(1))
    board, issues = _QABoard(), _QAIssues(comments=[])
    result = flow.run_qa(
        board,
        issues,
        EventSink(None),
        _QAWorkspace(tmp_path),
        None,
        cards=[card()],
        repo="sprint-metrics",
    )
    assert judged == [], "QA doesn't judge what it can't observe"
    assert ("C260", "Merging") in board.moves
    assert [o.card for o in result.verified] == [260]
    [(number, body)] = issues.posted
    assert number == 260 and "## QA — not applicable: a CI-only change" in body
    assert flow.qa_marker("abc1234def56") in body, "scoped to the commit, like any verdict"


def test_any_other_change_is_still_verified(monkeypatch, tmp_path):
    judged = []

    class Stop(Exception):
        pass

    def verify(*a, **k):
        judged.append(1)
        raise Stop

    monkeypatch.setattr(flow, "ci_only", lambda worktree: False)
    monkeypatch.setattr(flow, "verify_story", verify)
    monkeypatch.setattr(flow.workspace, "check", lambda w, sandbox=None: _green())
    monkeypatch.setattr(flow, "collect_tests", lambda w: "")
    flow.run_qa(
        _QABoard(),
        _QAIssues(comments=[]),
        EventSink(None),
        _QAWorkspace(tmp_path),
        None,
        cards=[card()],
        repo="sprint-metrics",
    )
    assert judged == [1]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/tests.yml").write_text("name: tests\n")
    (tmp_path / "README.md").write_text("# x\n")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "merged")
    git(tmp_path, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    git(tmp_path, "checkout", "-qb", "feat/260")
    return tmp_path


def commit(repo: Path) -> None:
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "work")


def test_a_branch_changing_only_workflows_is_ci_only(repo: Path):
    (repo / ".github/workflows/release.yml").write_text("name: release\n")
    commit(repo)
    assert ci_only(repo)


def test_a_workflow_with_anything_else_is_not(repo: Path):
    (repo / ".github/workflows/release.yml").write_text("name: release\n")
    (repo / "README.md").write_text("# x\nrelease notes\n")
    commit(repo)
    assert not ci_only(repo)


def test_a_branch_that_changed_nothing_is_not(repo: Path):
    assert not ci_only(repo)
