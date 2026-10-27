"""A reworking role is told the Sponsor's change request, ahead of the crew's (crew#924)."""

from __future__ import annotations

from crew_org.flows.delivery import prior_verdicts, sponsor_review
from crew_org.flows.review import REVIEW_MARKER
from tests.test_merge import FakeIssues

SPONSOR = "mquarters"
REVIEWER = {"user": {"login": "crew-reviewer[bot]"}, "body": f"{REVIEW_MARKER}\nName the file."}


def asked(body: str = "Lexical error on line 2.", *, state="CHANGES_REQUESTED", rid=7, by=SPONSOR):
    return {"id": rid, "user": {"login": by}, "state": state, "body": body, "commit_id": "old"}


class Reviewed(FakeIssues):
    """A pull request with reviews, and the comments each review left on lines."""

    def __init__(self, reviews, lines=None):
        super().__init__(reviews=reviews)
        self.lines = lines or {}

    def review_comments(self, repo, number, review_id):
        return self.lines.get(review_id, [])


def test_a_sponsor_change_request_comes_first_with_its_line_comments():
    lines = {7: [{"path": "docs/architecture.md", "line": 2, "body": "The title won't parse."}]}
    issues = Reviewed([asked(), REVIEWER], lines)
    theirs = sponsor_review(issues, "sprint-metrics", 806, SPONSOR)
    assert theirs.startswith("### The Sponsor's review")
    assert "Lexical error on line 2." in theirs
    assert "`docs/architecture.md` line 2" in theirs and "The title won't parse." in theirs
    assert "Name the file." not in theirs, "the Code Reviewer's notes stay in their own part"


def test_whichever_head_it_was_on_the_latest_sponsor_review_is_the_one():
    issues = Reviewed([asked("Old ask.", rid=1), asked("New ask.", rid=2)])
    theirs = sponsor_review(issues, "sprint-metrics", 806, SPONSOR)
    assert "New ask." in theirs and "Old ask." not in theirs


def test_an_approval_or_a_comment_from_the_sponsor_isn_t_a_finding():
    settled = Reviewed([asked(), asked("Fine.", state="APPROVED", rid=8)])
    assert sponsor_review(settled, "sprint-metrics", 806, SPONSOR) == ""
    remarked = Reviewed([asked("Nice.", state="COMMENTED")])
    assert sponsor_review(remarked, "sprint-metrics", 806, SPONSOR) == ""


def test_a_plain_comment_after_a_change_request_leaves_the_request_standing():
    issues = Reviewed([asked(), asked("Also, a typo.", state="COMMENTED", rid=8)])
    theirs = sponsor_review(issues, "sprint-metrics", 806, SPONSOR)
    assert "Lexical error" in theirs and "a typo" not in theirs


def test_anyone_else_s_change_request_or_no_sponsor_named_is_not_the_sponsor_s():
    issues = Reviewed([asked(by="quarters-claude")])
    assert sponsor_review(issues, "sprint-metrics", 806, SPONSOR) == ""
    assert sponsor_review(Reviewed([asked()]), "sprint-metrics", 806, "") == ""


def test_line_comments_that_can_t_be_read_still_leave_the_review():
    class Unread(Reviewed):
        def review_comments(self, repo, number, review_id):
            raise RuntimeError("GitHub stumbled")

    assert "Lexical error" in sponsor_review(Unread([asked()]), "sprint-metrics", 806, SPONSOR)


def test_the_crew_s_verdicts_are_as_before_without_a_sponsor_review():
    issues = Reviewed([REVIEWER])
    assert prior_verdicts(issues, "sprint-metrics", 6, "feat/6-x") == REVIEWER["body"]
