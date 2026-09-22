"""What a gate is shown about its own earlier verdicts.

QA and the Code Reviewer judged every attempt cold. A story returned for one
unproven criterion could come back for a different one nobody had mentioned —
a moving target, and a whole delivery cycle spent each time it moved.

Bounded, because §16's rule cuts both ways: a limit is allowed, a silent or
partial one is not. Whole verdicts are dropped, oldest first, and the omission
is stated.
"""

from __future__ import annotations

from crew_org.flows.history import MAX_VERDICTS, past_qa, past_reviews, recent_verdicts

MARKER = "<!-- crew:qa -->"
REVIEW = "<!-- crew:review -->"


class FakeIssues:
    def __init__(self, *, comments=(), reviews=()):
        self._comments, self._reviews = list(comments), list(reviews)

    def comments(self, repo, number):
        return self._comments

    def pull_reviews(self, repo, number):
        return self._reviews


# --- the bound -----------------------------------------------------------


def test_the_trail_is_newest_last():
    assert recent_verdicts(["first", "second"]) == "first\n\n---\n\nsecond"


def test_only_the_most_recent_verdicts_are_carried():
    bodies = [f"verdict {i}" for i in range(MAX_VERDICTS + 2)]
    trail = recent_verdicts(bodies)
    assert "verdict 0" not in trail
    assert f"verdict {MAX_VERDICTS + 1}" in trail


def test_what_was_dropped_is_said_rather_than_silently_omitted():
    bodies = [f"verdict {i}" for i in range(MAX_VERDICTS + 2)]
    assert "2 earlier verdict(s) omitted" in recent_verdicts(bodies)


def test_a_verdict_is_dropped_whole_rather_than_cut_in_half():
    """Half a finding reads as a finding about half a file."""
    trail = recent_verdicts(["a" * 100, "b" * 100], budget=120)
    assert "a" * 100 not in trail
    assert "b" * 100 in trail


def test_one_oversized_verdict_is_still_shown():
    """Better the whole of the only thing there is than nothing at all."""
    assert recent_verdicts(["x" * 500], budget=100) == "x" * 500


def test_no_history_is_no_text():
    assert recent_verdicts([]) == ""
    assert recent_verdicts(["", "   "]) == ""


# --- QA's own trail ------------------------------------------------------


def test_qa_sees_its_earlier_verdicts_and_nothing_else():
    issues = FakeIssues(
        comments=[
            {"body": f"{MARKER} criterion 2 unproven"},
            {"body": "**Blocked.** something else entirely"},
        ]
    )
    trail = past_qa(issues, "sprint-metrics", 31, marker=MARKER)
    assert "criterion 2 unproven" in trail
    assert "something else entirely" not in trail


def test_a_first_verification_has_no_trail():
    assert past_qa(FakeIssues(), "sprint-metrics", 31, marker=MARKER) == ""


def test_a_read_failure_does_not_cost_the_verdict():
    class Broken(FakeIssues):
        def comments(self, repo, number):
            raise RuntimeError("502")

    assert past_qa(Broken(), "sprint-metrics", 31, marker=MARKER) == ""


# --- the Reviewer's own trail --------------------------------------------


def test_the_reviewer_is_not_shown_its_verdict_on_the_head_it_is_judging():
    """A gate handed its own answer is not judging."""
    issues = FakeIssues(
        reviews=[
            {"body": f"{REVIEW} earlier finding", "commit_id": "old"},
            {"body": f"{REVIEW} this very pass", "commit_id": "head"},
        ]
    )
    trail = past_reviews(issues, "sprint-metrics", 67, marker=REVIEW, head="head")
    assert "earlier finding" in trail
    assert "this very pass" not in trail


def test_another_reviewers_comments_are_not_the_crews_trail():
    issues = FakeIssues(reviews=[{"body": "looks fine to me", "commit_id": "old"}])
    assert past_reviews(issues, "sprint-metrics", 67, marker=REVIEW, head="head") == ""
