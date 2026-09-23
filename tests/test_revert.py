"""Undoing a merged change (#83).

The git half runs against real repositories: whether a squash commit and a
merge commit both revert, and whether a conflict leaves the worktree clean, are
questions about git, and a faked `_run` would only restate the assumptions.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from crew_org import git_ops
from crew_org.columns import BLOCKED, DONE, MERGING
from crew_org.columns import NEEDS_REFINEMENT as REFINEMENT
from crew_org.events import EventKind, EventSink
from crew_org.flows.revert import (
    LANDED_MARKER,
    land_reverts,
    marker,
    parse_marker,
    request_revert,
)
from crew_org.git_ops import BotIdentity, RevertConflict, Workspace
from crew_org.tools.github_project import Card

REPO = "sprint-metrics"

# --- git ------------------------------------------------------------------


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def repo_with(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    (path / "a.txt").write_text("one\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "base")
    return path


def workspace_on(path: Path) -> Workspace:
    ws = Workspace("o", "r", "tok", BotIdentity("bot", 1))
    ws.path = path
    return ws


def test_a_squash_commit_is_reverted(tmp_path):
    path = repo_with(tmp_path)
    (path / "a.txt").write_text("two\n")
    git(path, "commit", "-q", "-am", "change")
    workspace_on(path).revert(git(path, "rev-parse", "HEAD"))
    assert (path / "a.txt").read_text() == "one\n"
    assert git(path, "log", "-1", "--format=%an") == "bot"


def test_a_merge_commit_is_reverted_against_the_branch_it_merged_into(tmp_path):
    """A person who merged with a merge commit rather than a squash: git refuses
    to revert a merge without being told which parent is the mainline."""
    path = repo_with(tmp_path)
    git(path, "checkout", "-q", "-b", "feat/1-x")
    (path / "b.txt").write_text("new\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "feature")
    git(path, "checkout", "-q", "main")
    (path / "a.txt").write_text("main moved\n")
    git(path, "commit", "-q", "-am", "unrelated")
    git(path, "merge", "-q", "--no-ff", "-m", "merge", "feat/1-x")

    workspace_on(path).revert(git(path, "rev-parse", "HEAD"))
    assert not (path / "b.txt").exists()
    assert (path / "a.txt").read_text() == "main moved\n"


def test_a_conflicting_revert_is_aborted_and_names_the_paths(tmp_path):
    path = repo_with(tmp_path)
    (path / "a.txt").write_text("two\n")
    git(path, "commit", "-q", "-am", "change")
    landed = git(path, "rev-parse", "HEAD")
    (path / "a.txt").write_text("three\n")
    git(path, "commit", "-q", "-am", "moved on")
    head = git(path, "rev-parse", "HEAD")

    with pytest.raises(RevertConflict) as raised:
        workspace_on(path).revert(landed)

    assert raised.value.files == ["a.txt"]
    # Never forced, and nothing half-done left behind.
    assert git(path, "rev-parse", "HEAD") == head
    assert git(path, "status", "--porcelain") == ""
    assert (path / "a.txt").read_text() == "three\n"


def test_a_revert_branch_is_a_valid_branch():
    git_ops.validate_branch_name("revert/12-add-cycle-time")


# --- the marker -------------------------------------------------------------


def card(number: int = 31, status: str = DONE, state: str = "CLOSED") -> Card:
    return Card(
        item_id=f"I{number}",
        number=number,
        title="Add cycle time",
        status=status,
        state=state,
        work_type="Story",
        repo=REPO,
    )


def test_the_marker_round_trips():
    assert parse_marker(marker(12, card())) == (12, (REPO, 31))
    assert parse_marker(marker(12, None)) == (12, None)
    assert parse_marker("no marker here") is None


# --- asking for a revert ------------------------------------------------------


def merged_pull(number: int = 12, branch: str = "feat/31-add-cycle-time", **kw) -> dict:
    return {
        "number": number,
        "title": "Add cycle time",
        "merged": True,
        "merge_commit_sha": "abc1234def",
        "head": {"ref": branch},
        "base": {"ref": "main", "repo": {"default_branch": "main"}},
        **kw,
    }


class FakeIssues:
    def __init__(self, pulls=(), open_=(), closed=(), reviews=None, marked=()):
        self._pulls = {p["number"]: p for p in pulls}
        self._open = list(open_)
        self._closed = list(closed)
        self._reviews = reviews or {}
        self._marked = set(marked)
        self.created: list[dict] = []
        self.merged: list[int] = []
        self.reopened: list[int] = []
        self.labels: list[tuple[int, str]] = []
        self.comments: list[tuple[int, str]] = []

    def pull(self, repo, number):
        return self._pulls[number]

    def pull_for_branch(self, repo, branch):
        return next((p for p in self._open if p["head"]["ref"] == branch), None)

    def create_pull(self, repo, *, title, head, base, body):
        self.created.append({"title": title, "head": head, "base": base, "body": body})
        return {"number": 50}

    def open_pulls(self, repo):
        return self._open

    def closed_pulls(self, repo):
        return self._closed

    def pull_reviews(self, repo, number):
        return self._reviews.get(number, [])

    def review_decision(self, repo, number):
        return "APPROVED" if self._reviews.get(number) else "REVIEW_REQUIRED"

    def merge_pull(self, repo, number, *, method="squash"):
        self.merged.append(number)

    def reopen(self, repo, number):
        self.reopened.append(number)

    def add_labels(self, repo, number, labels):
        self.labels.extend((number, name) for name in labels)

    def remove_label(self, repo, number, label):
        pass

    def comment(self, repo, number, body):
        self.comments.append((number, body))
        return {}

    def has_comment_marked(self, repo, number, text):
        return (number, text) in self._marked


class FakeBoard:
    def __init__(self):
        self.moves: list[tuple[str, str]] = []

    def set_status(self, item_id, column):
        self.moves.append((item_id, column))

    def set_owner_agent(self, item_id, role):
        pass


class FakeWorkspace:
    def __init__(self, conflict: list[str] | None = None):
        self.conflict = conflict
        self.opened: list[str] = []
        self.reverted: list[str] = []
        self.pushed = False
        self.closed = False

    def for_repo(self, repo):
        return self

    def open(self, branch, *, resume=False):
        self.opened.append(branch)

    def revert(self, sha):
        if self.conflict is not None:
            raise RevertConflict(self.conflict)
        self.reverted.append(sha)

    def push(self, *, force=False):
        self.pushed = True

    def close(self, path=None):
        self.closed = True


def ask(issues, ws, cards, *, reason="it broke the table", pull=12):
    sink, seen = EventSink(None), []
    sink.subscribe(seen.append)
    board = FakeBoard()
    result = request_revert(
        board, issues, sink, ws, cards=cards, repo=REPO, pull_number=pull, reason=reason
    )
    return result, board, seen


def test_a_revert_is_opened_as_a_pull_request_on_its_own_branch():
    """Criterion 1: it goes through review like any other change, so nothing
    here merges anything."""
    issues, ws = FakeIssues(pulls=[merged_pull()]), FakeWorkspace()
    result, board, _ = ask(issues, ws, [card()])

    assert result.pr == 50
    assert ws.opened == ["revert/12-add-cycle-time"]
    assert ws.reverted == ["abc1234def"]
    assert ws.pushed and ws.closed
    assert issues.merged == []
    assert board.moves == []
    opened = issues.created[0]
    assert opened["head"] == "revert/12-add-cycle-time" and opened["base"] == "main"
    assert parse_marker(opened["body"]) == (12, (REPO, 31))
    assert "it broke the table" in opened["body"]


def test_opening_a_revert_is_on_the_audit_trail():
    """Criterion 4: what was reverted, which card, and why."""
    _, _, seen = ask(FakeIssues(pulls=[merged_pull()]), FakeWorkspace(), [card()])
    opened = [e for e in seen if e.kind is EventKind.REVERT_OPENED]
    assert len(opened) == 1
    assert opened[0].card == 31
    assert opened[0].detail["reverted"] == 12
    assert opened[0].detail["card"] == f"{REPO}#31"
    assert opened[0].detail["reason"] == "it broke the table"


def test_the_card_is_told_a_revert_is_open():
    issues = FakeIssues(pulls=[merged_pull()])
    ask(issues, FakeWorkspace(), [card()])
    assert [n for n, _ in issues.comments] == [31]


def test_a_pull_request_with_no_card_can_still_be_reverted():
    issues = FakeIssues(pulls=[merged_pull(branch="someones-branch")])
    result, _, _ = ask(issues, FakeWorkspace(), [card()])
    assert result.pr == 50 and result.card is None
    assert parse_marker(issues.created[0]["body"]) == (12, None)
    assert issues.comments == []


def test_the_card_is_matched_in_the_pull_requests_own_repository():
    other = card().model_copy(update={"repo": "crew"})
    result, _, _ = ask(FakeIssues(pulls=[merged_pull()]), FakeWorkspace(), [other])
    assert result.card is None


@pytest.mark.parametrize(
    ("pull", "reason", "why"),
    [
        (merged_pull(merged=False), "r", "never merged"),
        (merged_pull(), "  ", "needs a reason"),
        (
            merged_pull(base={"ref": "release", "repo": {"default_branch": "main"}}),
            "r",
            "not main",
        ),
    ],
)
def test_a_revert_that_should_not_be_opened_is_refused(pull, reason, why):
    ws = FakeWorkspace()
    result, board, seen = ask(FakeIssues(pulls=[pull]), ws, [card()], reason=reason)
    assert result.pr is None and why in (result.refused or "")
    assert ws.opened == [] and board.moves == []
    assert [e.kind for e in seen] == [EventKind.REVERT_REFUSED]


def test_a_second_revert_of_the_same_change_is_refused():
    already = {"number": 49, "head": {"ref": "revert/12-add-cycle-time"}, "body": ""}
    ws = FakeWorkspace()
    result, _, _ = ask(FakeIssues(pulls=[merged_pull()], open_=[already]), ws, [card()])
    assert "already open" in (result.refused or "")
    assert ws.opened == []


def test_a_revert_that_conflicts_blocks_the_card_and_names_the_conflict():
    """Criterion 3: never forced. The card stops for a person, reopened so the
    board's own workflow does not put it straight back in Done."""
    issues, ws = FakeIssues(pulls=[merged_pull()]), FakeWorkspace(conflict=["src/table.py"])
    result, board, seen = ask(issues, ws, [card()])

    assert result.pr is None and result.conflict == ["src/table.py"]
    assert issues.created == [] and not ws.pushed and ws.closed
    assert board.moves == [("I31", BLOCKED)]
    assert issues.reopened == [31]
    assert {(31, "blocked"), (31, "needs:human")} <= set(issues.labels)
    assert "src/table.py" in issues.comments[0][1]
    refused = [e for e in seen if e.kind is EventKind.REVERT_REFUSED]
    assert refused[0].detail["conflict"] == ["src/table.py"]


# --- landing, and returning the card ---------------------------------------


def revert_pull(number: int = 50, reverted: int = 12, the_card: Card | None = None, **kw) -> dict:
    the_card = the_card if the_card is not None else card()
    return {
        "number": number,
        "head": {"ref": f"revert/{reverted}-add-cycle-time"},
        "body": marker(reverted, the_card) + "\nReverts #12.",
        **kw,
    }


APPROVED = {50: [{"state": "APPROVED"}]}


def land(issues, cards):
    sink, seen = EventSink(None), []
    sink.subscribe(seen.append)
    board = FakeBoard()
    result = land_reverts(board, issues, sink, cards=cards, repos={REPO})
    return result, board, seen


def test_an_approved_revert_is_merged_and_its_card_returned():
    """Criterion 2: a card whose work was undone is not Done."""
    pull = revert_pull()
    issues = FakeIssues(pulls=[pull], open_=[pull], reviews=APPROVED)
    result, board, _ = land(issues, [card()])

    assert issues.merged == [50]
    assert result.merged == [(50, 12)]
    assert result.returned == [31]
    assert board.moves == [("I31", REFINEMENT)]
    assert issues.reopened == [31]
    assert (31, "needs:human") in issues.labels
    assert LANDED_MARKER.format(pr=50) in issues.comments[0][1]


def test_returning_the_card_is_on_the_audit_trail():
    pull = revert_pull()
    _, _, seen = land(FakeIssues(pulls=[pull], open_=[pull], reviews=APPROVED), [card()])
    landed = [e for e in seen if e.kind is EventKind.REVERT_LANDED]
    assert len(landed) == 1
    assert landed[0].detail == {"repo": REPO, "pr": 50, "reverted": 12, "card": f"{REPO}#31"}


def test_an_unapproved_revert_waits():
    pull = revert_pull()
    issues = FakeIssues(pulls=[pull], open_=[pull])
    result, board, _ = land(issues, [card()])
    assert issues.merged == [] and board.moves == []
    assert result.awaiting_approval == [50]


def test_a_revert_that_no_longer_merges_cleanly_is_reported_not_merged():
    pull = revert_pull()
    issues = FakeIssues(
        pulls=[{**pull, "mergeable_state": "dirty"}], open_=[pull], reviews=APPROVED
    )
    result, board, _ = land(issues, [card()])
    assert issues.merged == [] and board.moves == []
    assert result.conflicted == [50]


def test_a_revert_a_person_merged_still_returns_its_card():
    """However it lands. A revert merged by hand is still a revert."""
    pull = revert_pull(merged_at="2026-09-23T12:00:00Z")
    issues = FakeIssues(closed=[pull])
    result, board, _ = land(issues, [card()])
    assert issues.merged == []
    assert result.returned == [31]
    assert board.moves == [("I31", REFINEMENT)]


def test_a_card_is_returned_once_per_revert():
    """Re-delivered and Done again, it must not be pulled back by the old revert."""
    pull = revert_pull(merged_at="2026-09-23T12:00:00Z")
    issues = FakeIssues(closed=[pull], marked=[(31, LANDED_MARKER.format(pr=50))])
    result, board, _ = land(issues, [card()])
    assert result.returned == [] and board.moves == []


def test_a_revert_closed_without_merging_returns_nothing():
    pull = revert_pull(merged_at=None)
    result, board, _ = land(FakeIssues(closed=[pull]), [card()])
    assert result.returned == [] and board.moves == []


def test_an_ordinary_pull_request_is_not_mistaken_for_a_revert():
    """A story's own pull request waits in Merging; it is `merge_approved`'s."""
    pull = {"number": 12, "head": {"ref": "feat/31-add-cycle-time"}, "body": "Closes #31"}
    issues = FakeIssues(pulls=[pull], open_=[pull], reviews={12: [{"state": "APPROVED"}]})
    result, board, _ = land(issues, [card(status=MERGING, state="OPEN")])
    assert issues.merged == [] and result.merged == []
