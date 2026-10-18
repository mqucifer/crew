"""What became of a story's last pull request, for the session that delivers it again.

sprint-metrics #31 proved that half this context is worse than none: a returned
story told of a verdict on code its worktree no longer held tried to edit code that
wasn't there. A rework session starts from the branch as it stands (ADR 0032, P2),
told what became of the last pull request.
"""

from __future__ import annotations

from crew_org.flows.acceptance import QA_MARKER
from crew_org.flows.delivery import previous_fate
from crew_org.flows.review import REVIEW_MARKER

QA_VERDICT = f"{QA_MARKER}\n**QA — not proven.** Criterion 2 is unverified."
CHANGES = f"{REVIEW_MARKER}\n**Changes requested.** `run_scrape_server` swallows the error."


class FakeIssues:
    def __init__(self, *, comments=(), reviews=(), pulls=(), open_pull=None):
        self.owner = "mqucifer"
        self._comments, self._reviews = list(comments), list(reviews)
        self._pulls, self._open = list(pulls), open_pull

    def comments(self, repo, number):
        return self._comments

    def pull_reviews(self, repo, number):
        return self._reviews

    def pull_for_branch(self, repo, branch, *, known=None):

        if known is not None:
            return {"number": known.number, "head": {"ref": known.head, "sha": known.head_sha}}
        return self._open

    def pulls_for_branch(self, repo, branch, *, state="all"):
        return self._pulls


BRANCH = "feat/31-scrape-endpoint"


# --- what happened to it -------------------------------------------------


def test_a_closed_pull_request_is_named_as_the_reason_it_came_back():
    """A pull request closed without merging carries no verdict at all, so
    nothing on the card explained why the story was back."""
    issues = FakeIssues(pulls=[{"number": 36, "state": "closed", "merged_at": None}])
    assert "closed without merging" in previous_fate(issues, "sprint-metrics", BRANCH)


def test_an_open_pull_request_reads_as_work_sent_back():
    issues = FakeIssues(pulls=[{"number": 67, "state": "open", "merged_at": None}])
    assert "still open" in previous_fate(issues, "sprint-metrics", BRANCH)


def test_a_first_delivery_has_no_fate_to_report():
    assert previous_fate(FakeIssues(), "sprint-metrics", BRANCH) == ""


def test_a_read_failure_does_not_cost_the_delivery():
    class Broken(FakeIssues):
        def pulls_for_branch(self, repo, branch, *, state="all"):
            raise RuntimeError("502")

    assert previous_fate(Broken(), "sprint-metrics", BRANCH) == ""


# --- where that leaves the files -----------------------------------------
