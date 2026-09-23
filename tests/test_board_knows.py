"""The crew reads what the board already knows, instead of fetching it (#55)."""

from __future__ import annotations

import pytest

from crew_org.events import EventSink
from crew_org.flows.acceptance import DONE, close_finished_parents
from crew_org.flows.merge import merge_approved
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, LinkedPull, _to_card

BRANCH = "feat/31-serve-metrics"


def epic(total, closed) -> Card:
    return Card(
        item_id="E3",
        number=3,
        title="Epic",
        status="Needs Refinement",
        state="OPEN",
        work_type="Epic",
        repo="sprint-metrics",
        sub_issues_total=total,
        sub_issues_closed=closed,
    )


def story(number, status=DONE, **kw) -> Card:
    return Card(
        item_id=f"S{number}",
        number=number,
        title="Serve metrics",
        status=status,
        state="OPEN",
        work_type="Story",
        repo="sprint-metrics",
        **kw,
    )


class Board:
    def __init__(self):
        self.moves = []

    def set_status(self, item_id, column):
        self.moves.append((item_id, column))

    def set_owner_agent(self, item_id, role):
        pass


class Issues:
    def __init__(self, children=None):
        self._children = children or {}
        self.fetched: list[int] = []
        self.closed: list[int] = []

    def sub_issues(self, repo, number):
        self.fetched.append(number)
        return self._children.get(number, [])

    def close(self, repo, number, *, reason="completed"):
        self.closed.append(number)


def close_parents(cards, issues):
    return close_finished_parents(Board(), issues, EventSink(None), cards, repo="r")


# --- criteria 1 and 2: progress first, per-child only at 100% ------------------


def test_an_epic_below_100_percent_is_not_fetched():
    """Criterion 1."""
    issues = Issues({3: [{"number": 6}]})
    assert close_parents([epic(total=3, closed=2), story(6)], issues) == []
    assert issues.fetched == []


def test_an_epic_with_no_sub_issues_is_not_fetched():
    issues = Issues()
    assert close_parents([epic(total=0, closed=0)], issues) == []
    assert issues.fetched == []


def test_at_100_percent_each_child_is_still_checked():
    """Criterion 2: progress counts closed issues, not Done. A closed child
    that is not in Done keeps the epic open."""
    issues = Issues({3: [{"number": 6}, {"number": 7}]})
    cards = [epic(total=2, closed=2), story(6), story(7, status="Merging")]
    assert close_parents(cards, issues) == []
    assert issues.fetched == [3]


def test_at_100_percent_with_every_child_done_the_epic_closes():
    issues = Issues({3: [{"number": 6}]})
    assert close_parents([epic(total=1, closed=1), story(6)], issues) == [3]
    assert issues.closed == [3]


def test_progress_the_board_did_not_report_still_fetches():
    issues = Issues({3: [{"number": 6}]})
    assert close_parents([epic(total=None, closed=None), story(6)], issues) == [3]
    assert issues.fetched == [3]


# --- criterion 3: the linked pull request, not a scan ------------------------------


def linked(state="OPEN", head=BRANCH) -> LinkedPull:
    return LinkedPull(number=67, state=state, head=head, head_sha="c5077c2")


def test_a_card_knows_its_open_pull_request_on_its_branch():
    card = story(31, linked_pulls=(linked(),))
    assert card.open_pull_on(BRANCH).number == 67


@pytest.mark.parametrize("pull", [linked(state="CLOSED"), linked(head="feat/31-other")])
def test_a_closed_pull_request_or_another_branch_is_not_the_one(pull):
    assert story(31, linked_pulls=(pull,)).open_pull_on(BRANCH) is None


class ScanCountingClient(IssueClient):
    """The real `pull_for_branch`, with the scan it would make counted."""

    def __init__(self):
        self.owner = "mqucifer"
        self.scans = 0

    def open_pulls(self, repo):
        self.scans += 1
        return [{"number": 99, "head": {"ref": BRANCH, "sha": "scanned"}}]


def test_a_linked_pull_request_needs_no_scan():
    """Criterion 3."""
    client = ScanCountingClient()
    pull = client.pull_for_branch("sprint-metrics", BRANCH, known=linked())
    assert pull == {"number": 67, "head": {"ref": BRANCH, "sha": "c5077c2"}}
    assert client.scans == 0


def test_without_a_link_the_repository_is_scanned():
    """A pull request opened by hand with no `Closes #N` is still found."""
    client = ScanCountingClient()
    assert client.pull_for_branch("sprint-metrics", BRANCH)["number"] == 99
    assert client.scans == 1


def test_merging_uses_the_linked_pull_request(monkeypatch):
    card = story(31, status="Merging", linked_pulls=(linked(),))
    client = ScanCountingClient()
    looked_at: list[int] = []
    monkeypatch.setattr(client, "pull", lambda repo, n: looked_at.append(n) or {}, raising=False)
    monkeypatch.setattr(client, "pull_reviews", lambda repo, n: [], raising=False)
    result = merge_approved(
        Board(), client, EventSink(None), cards=[card], default_repo="sprint-metrics"
    )
    assert looked_at == [67] and result.awaiting_approval == [(31, 67)]
    assert client.scans == 0


# --- read off the board query --------------------------------------------------------


def test_the_item_query_carries_progress_and_linked_pull_requests():
    card = _to_card(
        {
            "id": "I1",
            "fieldValues": {"nodes": []},
            "content": {
                "number": 3,
                "repository": {"name": "sprint-metrics"},
                "subIssuesSummary": {"total": 5, "completed": 5},
                "closedByPullRequestsReferences": {
                    "nodes": [
                        {
                            "number": 67,
                            "state": "OPEN",
                            "headRefName": BRANCH,
                            "headRefOid": "c5077c2",
                        }
                    ]
                },
            },
        }
    )
    assert (card.sub_issues_total, card.sub_issues_closed) == (5, 5)
    assert card.linked_pulls == (linked(),)
