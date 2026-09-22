"""What a second attempt is told about the first.

sprint-metrics #31 proved that half this context is worse than none. The
Developer was handed the previous QA verdict — entirely about `scrape.py` and
`run_scrape_server` — while `open` had reset the worktree to `main`, which has
no `scrape.py`. The model tried to edit `run_scrape_server`, the edit tool
refused it ("no definition named ..."), and cornered, it returned an empty
implementation. That failed the validator, classified SCHEMA, and blocked the
card.

So the three facts travel together: what the last attempt did, what became of
it, and whether its code is in the files.
"""

from __future__ import annotations

from crew_org.flows.acceptance import QA_MARKER
from crew_org.flows.delivery import previous_fate, prior_context
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

    def pull_for_branch(self, repo, branch):
        return self._open

    def pulls_for_branch(self, repo, branch, *, state="all"):
        return self._pulls


BRANCH = "feat/31-scrape-endpoint"


def context(issues, *, resumed):
    return prior_context(issues, "sprint-metrics", 31, BRANCH, resumed=resumed)


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


def test_a_resumed_attempt_is_told_its_work_is_present():
    issues = FakeIssues(
        comments=[{"body": QA_VERDICT}],
        pulls=[{"number": 36, "state": "closed", "merged_at": None}],
    )
    text = context(issues, resumed=True)
    assert "already in the repository" in text
    assert "not in the repository" not in text
    assert "continuing it" in text


def test_a_clean_branch_is_told_plainly_that_its_work_is_gone():
    issues = FakeIssues(
        comments=[{"body": QA_VERDICT}],
        pulls=[{"number": 36, "state": "closed", "merged_at": None}],
    )
    text = context(issues, resumed=False)
    assert "not in the repository" in text
    assert "written again" in text


def test_the_verdicts_still_travel_with_it():
    issues = FakeIssues(
        comments=[{"body": QA_VERDICT}],
        reviews=[{"body": CHANGES}],
        open_pull={"number": 67},
        pulls=[{"number": 67, "state": "open", "merged_at": None}],
    )
    text = context(issues, resumed=True)
    assert "Criterion 2 is unverified" in text
    assert "swallows the error" in text


def test_the_three_facts_arrive_together():
    """What it did, what happened, and where that leaves it — the failure was
    getting one without the others."""
    issues = FakeIssues(
        comments=[{"body": QA_VERDICT}],
        pulls=[{"number": 36, "state": "closed", "merged_at": None}],
    )
    text = context(issues, resumed=True)
    assert "What happened to your previous attempt" in text
    assert "What the gates said about it" in text
    assert "Where that leaves your files" in text


def test_a_first_delivery_carries_no_history_at_all():
    assert context(FakeIssues(), resumed=False) == ""
