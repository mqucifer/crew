"""The missing middle: a story the reviewer sent back is picked up again.

Proven on sprint-metrics#31, PR #67, Sprint 3:

    03:09:15  Developer      delivered — PR #67             [In Progress -> Reviewing]
    03:10:04  Code Reviewer  changes requested — 4 findings [Reviewing -> In Progress]
    03:11:03  (reconcile)    already has PR #67             [In Progress -> Reviewing]

and then forever. Three things have to hold for "changes requested" to be
answered, and only the first existed: review returns the card (crew#15),
delivery re-claims and re-works it (this), and the re-pushed head is judged
again (crew#45). The second was missing, so the first never led anywhere.
"""

from __future__ import annotations

from crew_org.events import EventSink
from crew_org.flows.delivery import awaiting_rework, needs_rework, reconcile_orphans
from crew_org.flows.review import already_reviewed
from crew_org.tools.github_project import Card

HEAD = "abc123def456"
OLD_HEAD = "0000111122223333"
BOT = "mqucifer-crew-approver[bot]"

REFUSED_NOW = [{"state": "CHANGES_REQUESTED", "commit_id": HEAD}]
REFUSED_BEFORE = [{"state": "CHANGES_REQUESTED", "commit_id": OLD_HEAD}]
APPROVED = [{"state": "APPROVED", "commit_id": HEAD}]


def card(number: int, status: str) -> Card:
    return Card(
        item_id=f"C{number}",
        number=number,
        title="Scrape endpoint",
        status=status,
        state="OPEN",
        work_type="Story",
        repo="sprint-metrics",
    )


class FakeIssues:
    def __init__(self, *, reviews, pull=True, story_comments=()):
        self.owner = "mqucifer"
        self._reviews = reviews
        self._story_comments = list(story_comments)
        self._pull = (
            {"number": 67, "head": {"ref": "feat/31-scrape-endpoint", "sha": HEAD}}
            if pull
            else None
        )

    def pull_for_branch(self, repo, branch, *, known=None):

        if known is not None:
            return {"number": known.number, "head": {"ref": known.head, "sha": known.head_sha}}
        return self._pull

    def open_pulls(self, repo):
        return [self._pull] if self._pull else []

    def pull_reviews(self, repo, number):
        return self._reviews

    def comments(self, repo, number):
        # The story (31) carries QA's verdicts; the pull request (67) nothing.
        return [{"body": b} for b in self._story_comments] if number == 31 else []


class FakeBoard:
    def __init__(self):
        self.moves = []

    def set_status(self, item_id, column):
        self.moves.append((item_id, column))

    def set_owner_agent(self, item_id, role):
        pass


# --- is this pull request waiting for the Developer? ---------------------


def test_a_refusal_of_the_current_head_is_work_for_the_developer():
    assert awaiting_rework(FakeIssues(reviews=REFUSED_NOW), "sprint-metrics", "feat/31-x")


def test_a_refusal_of_a_head_already_repaired_is_not():
    """The repair pushed a new commit. The card is now waiting to be judged
    again, not to be worked again — this is what stops the claim repeating."""
    assert (
        awaiting_rework(FakeIssues(reviews=REFUSED_BEFORE), "sprint-metrics", "feat/31-x") is None
    )


def test_an_approved_pull_request_is_not_rework():
    assert awaiting_rework(FakeIssues(reviews=APPROVED), "sprint-metrics", "feat/31-x") is None


def test_a_story_with_no_pull_request_is_not_rework():
    assert (
        awaiting_rework(FakeIssues(reviews=[], pull=False), "sprint-metrics", "feat/31-x") is None
    )


def test_a_read_failure_does_not_claim_the_card():
    class Broken(FakeIssues):
        def pull_for_branch(self, repo, branch, *, known=None):
            if known is not None:
                return {"number": known.number, "head": {"ref": known.head, "sha": known.head_sha}}
            raise RuntimeError("502")

    assert awaiting_rework(Broken(reviews=[]), "sprint-metrics", "feat/31-x") is None


# --- which cards need it -------------------------------------------------


def test_a_card_stuck_in_reviewing_is_found():
    """sprint-metrics#31 as it sits right now: Reviewing, open PR #67, changes
    requested. Nothing else was ever going to move it."""
    issues = FakeIssues(reviews=REFUSED_NOW)
    assert [
        c.number for c in needs_rework(issues, [card(31, "Reviewing")], repo="sprint-metrics")
    ] == [31]


def test_a_card_left_in_progress_is_found_too():
    issues = FakeIssues(reviews=REFUSED_NOW)
    assert [
        c.number for c in needs_rework(issues, [card(31, "In Progress")], repo="sprint-metrics")
    ] == [31]


def test_a_card_in_qa_is_not_the_developers_problem():
    issues = FakeIssues(reviews=REFUSED_NOW)
    assert needs_rework(issues, [card(31, "QAing")], repo="sprint-metrics") == []


# --- reconciliation stops bouncing it ------------------------------------


def test_a_returned_card_is_left_where_review_put_it():
    """`reconcile_orphans` saw In Progress plus an open pull request and called
    it "already reviewed — moved to review". That move is the second half of
    the ping-pong: review then skips it, and the card never comes back."""
    board = FakeBoard()
    reconcile_orphans(
        board,
        FakeIssues(reviews=REFUSED_NOW),
        EventSink(None),
        [card(31, "In Progress")],
        repo="sprint-metrics",
    )
    assert board.moves == []


def test_a_card_genuinely_waiting_for_review_is_still_moved():
    """The healing this was written for still has to work."""
    board = FakeBoard()
    reconcile_orphans(
        board,
        FakeIssues(reviews=APPROVED),
        EventSink(None),
        [card(31, "In Progress")],
        repo="sprint-metrics",
    )
    assert board.moves == [("C31", "Reviewing")]


def test_a_card_with_no_pull_request_still_goes_back_to_the_backlog():
    board = FakeBoard()
    recovered = reconcile_orphans(
        board,
        FakeIssues(reviews=[], pull=False),
        EventSink(None),
        [card(31, "In Progress")],
        repo="sprint-metrics",
    )
    assert recovered == [31]
    assert board.moves == [("C31", "Sprint Backlog")]


# --- and the repaired head is judged again (crew#45) ---------------------


def _verdict(sha):
    return {
        "user": {"login": BOT},
        "body": "<!-- crew:review -->\nChanges requested.",
        "commit_id": sha,
    }


def test_a_verdict_belongs_to_the_commit_it_judged():
    assert already_reviewed([_verdict(HEAD)], BOT, HEAD) is True


def test_a_repaired_branch_is_reviewed_again():
    """The docstring said "this revision" and the code meant "ever", so the
    crew requested changes and then refused to read the answer."""
    assert already_reviewed([_verdict(OLD_HEAD)], BOT, HEAD) is False


def test_a_pull_request_nobody_has_judged_is_new_work():
    assert already_reviewed([], BOT, HEAD) is False


def test_another_reviewers_verdict_is_not_the_crews():
    other = dict(_verdict(HEAD), user={"login": "someone-else"})
    assert already_reviewed([other], BOT, HEAD) is False


# --- QA's return is work for the Developer too (sprint-metrics#145) --------------


def qa_verdict(sha: str, accepted: bool) -> str:
    from crew_org.flows.acceptance import QA_MARKER, qa_marker

    word = "accepted" if accepted else "not accepted"
    return f"{QA_MARKER}\n{qa_marker(sha)}\n## QA — {word}\n\n1 unproven"


def test_a_qa_return_of_the_current_head_is_work_for_the_developer():
    """#145: QA returned it, and reconciliation moved it back to review with its
    approval standing. The Developer never fixed the unproven criterion."""
    issues = FakeIssues(reviews=APPROVED, story_comments=[qa_verdict(HEAD, accepted=False)])
    assert awaiting_rework(
        issues, "sprint-metrics", "feat/31-scrape-endpoint", card(31, "In Progress")
    )


def test_a_qa_returned_card_is_not_bounced_back_to_review():
    board = FakeBoard()
    issues = FakeIssues(reviews=APPROVED, story_comments=[qa_verdict(HEAD, accepted=False)])
    reconcile_orphans(
        board, issues, EventSink(None), [card(31, "In Progress")], repo="sprint-metrics"
    )
    assert board.moves == []


def test_a_card_already_bounced_to_review_by_a_qa_return_is_found():
    """Where #145 sits now: Reviewing, approved, QA's return unanswered."""
    issues = FakeIssues(reviews=APPROVED, story_comments=[qa_verdict(HEAD, accepted=False)])
    assert [
        c.number for c in needs_rework(issues, [card(31, "Reviewing")], repo="sprint-metrics")
    ] == [31]


def test_a_qa_return_of_an_earlier_head_or_an_acceptance_is_not():
    for comments in ([qa_verdict(OLD_HEAD, accepted=False)], [qa_verdict(HEAD, accepted=True)]):
        issues = FakeIssues(reviews=APPROVED, story_comments=comments)
        assert not awaiting_rework(
            issues, "sprint-metrics", "feat/31-scrape-endpoint", card(31, "In Progress")
        )
