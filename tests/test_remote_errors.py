"""A service stumbling isn't the story's failure (#390)."""

from __future__ import annotations

import httpx
import pytest

from crew_org.events import EventSink
from crew_org.flows import delivery
from crew_org.flows.delivery import (
    INTERRUPTED_MARKER,
    INTERRUPTIONS_BEFORE_BLOCKING,
    DeliveryResult,
    _work_one_card,
)
from crew_org.tools.github_issues import IssueError
from crew_org.tools.github_project import Card
from crew_org.tools.remote_errors import transient_remote, where

GITHUB = httpx.Request("GET", "https://api.github.com/repos/mqucifer/sprint-metrics")


def _unreadable_reply():
    """What sprint-metrics#358 hit: a GitHub reply that wasn't JSON."""
    return httpx.Response(200, text="<html>unicorn</html>", request=GITHUB).json()


# --- telling a service's failure from the card's ------------------------------------------


def test_a_dropped_connection_to_github_is_passing():
    assert transient_remote(httpx.ConnectError("reset", request=GITHUB)) == "GitHub"


def test_a_server_error_is_passing_and_a_client_error_is_not():
    bad = httpx.HTTPStatusError("502", request=GITHUB, response=httpx.Response(502))
    assert transient_remote(bad) == "GitHub"
    refused = httpx.HTTPStatusError("404", request=GITHUB, response=httpx.Response(404))
    assert transient_remote(refused) is None


def test_the_crew_s_own_github_error_with_a_server_status_is_passing():
    assert transient_remote(IssueError("POST /repos/x/pulls -> 502: Bad Gateway")) == "GitHub"
    assert transient_remote(IssueError("POST /repos/x/pulls -> 422: Validation")) is None


def test_an_unreadable_reply_is_passing():
    with pytest.raises(ValueError) as caught:
        _unreadable_reply()
    assert transient_remote(caught.value) is not None


def test_the_card_s_own_error_is_not():
    assert transient_remote(ValueError("the proposal edits a file that doesn't exist")) is None


def test_where_names_file_line_and_function():
    try:
        _unreadable_reply()
    except ValueError as exc:
        assert "in _unreadable_reply" in where(exc) or ".py:" in where(exc)


# --- the card isn't blocked for a person on the first stumbles ---------------------------


class Board:
    def __init__(self):
        self.moves = []

    def set_status(self, item_id, column):
        self.moves.append(column)

    def set_owner_agent(self, item_id, role):
        pass


class Issues:
    def __init__(self, earlier=()):
        self.posted = [{"body": b} for b in earlier]
        self.labels = []

    def comments(self, repo, number):
        return list(self.posted)

    def comment(self, repo, number, body):
        self.posted.append({"body": body})

    def add_labels(self, repo, number, labels):
        self.labels += labels


class Workspace:
    def for_repo(self, repo):
        return self

    def diff_if_open(self):
        return None

    def close(self):
        pass


CARD = Card(
    item_id="I358",
    number=358,
    title="Add GH_TOKEN to the release step",
    status="In Progress",
    state="OPEN",
    work_type="Story",
    repo="sprint-metrics",
)


def work(monkeypatch, issues):
    def interrupted(*_a, **_k):
        _unreadable_reply()

    monkeypatch.setattr(delivery, "deliver_story", interrupted)
    board, result = Board(), DeliveryResult()
    _work_one_card(
        CARD,
        board=board,
        issues=issues,
        sink=EventSink(None),
        ws=Workspace(),
        policy=None,
        ledger=None,
        result=result,
        counts={"In Progress": 1},
        sprint="Sprint 11",
        repo="sprint-metrics",
        default_branch="main",
    )
    return board, result


def test_a_first_stumble_leaves_the_card_to_be_tried_again(monkeypatch):
    issues = Issues()
    board, result = work(monkeypatch, issues)
    assert result.interrupted == [358] and result.blocked == []
    assert board.moves == [] and issues.labels == []
    assert INTERRUPTED_MARKER in issues.posted[-1]["body"]
    assert "1 of 3" in issues.posted[-1]["body"]


def test_a_streak_of_stumbles_blocks_it_naming_the_service_and_where(monkeypatch):
    issues = Issues(earlier=[INTERRUPTED_MARKER] * (INTERRUPTIONS_BEFORE_BLOCKING - 1))
    board, result = work(monkeypatch, issues)
    assert result.interrupted == []
    assert board.moves == ["Blocked"] and "blocked" in issues.labels
    body = issues.posted[-1]["body"]
    assert "failed 3 times in a row" in body and "Where: `" in body


def test_other_crew_work_since_resets_the_streak(monkeypatch):
    earlier = [INTERRUPTED_MARKER, INTERRUPTED_MARKER, "<!-- crew:review -->"]
    issues = Issues(earlier=earlier)
    _board, result = work(monkeypatch, issues)
    assert result.interrupted == [358]


# --- GitHub's side failing a push (crew#444) ------------------------------------------------


def test_a_push_github_s_side_failed_is_passing():
    from crew_org.git_ops import GitError

    seen = GitError(
        "git push failed: remote: fatal error in commit_refs\n"
        "To https://github.com/mqucifer/sprint-metrics.git\n"
        " ! [remote rejected] feat/383-show-retry-count -> feat/383-show-retry-count (failure)"
    )
    assert transient_remote(seen) == "GitHub"
    assert transient_remote(GitError("git fetch failed: error: RPC failed; HTTP 502")) == "GitHub"


def test_a_push_rejected_for_the_story_s_own_reasons_is_not():
    from crew_org.git_ops import GitError

    for reason in (
        " ! [remote rejected] main -> main (protected branch hook declined)",
        " ! [rejected] feat/6 -> feat/6 (stale info)",
        " ! [rejected] feat/6 -> feat/6 (non-fast-forward)",
        "remote: Permission to mqucifer/sprint-metrics.git denied",
    ):
        assert transient_remote(GitError(f"git push failed: {reason}")) is None, reason
