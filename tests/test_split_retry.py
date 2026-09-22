"""A split that fails the same way is not attempted again.

Epic sprint-metrics#49 failed six times in one tick — reasoning_tokens 16,386,
text_tokens 0, finish=length — and was re-attempted each pass because nothing
recorded that it had already failed. The refine phase gates re-work on a
success marker, and a split that fails never posts one, so every pass saw an
epic that had simply never been split.

This is the retry half of "escalation is never the remedy for a poorly
designed task", applied one phase earlier.
"""

from __future__ import annotations

from crew_org.flows.board_flow import (
    IDENTICAL_FAILURES_BEFORE_PARKING,
    SPLIT_FAILURE_MARKER,
    SPLIT_PARKED_MARKER,
    failure_fingerprint,
    failures_since_parking,
)


class FakeIssues:
    def __init__(self, comments):
        self._comments = list(comments)

    def comments(self, repo, number):
        return self._comments


def failure_comment(fingerprint: str) -> dict:
    return {"body": f"{SPLIT_FAILURE_MARKER} {fingerprint} -->\nThe split failed."}


# --- naming a failure ----------------------------------------------------


def test_the_same_dead_end_gets_the_same_name():
    a = failure_fingerprint(RuntimeError("finish=length after 16386 reasoning tokens"))
    b = failure_fingerprint(RuntimeError("finish=length after 16412 reasoning tokens"))
    assert a == b, "token counts differ between runs of one dead end"


def test_a_different_failure_gets_a_different_name():
    a = failure_fingerprint(RuntimeError("finish=length"))
    b = failure_fingerprint(ValueError("no stories in the proposal"))
    assert a != b


def test_the_exception_type_is_part_of_it():
    assert failure_fingerprint(RuntimeError("x")) != failure_fingerprint(ValueError("x"))


# --- counting them -------------------------------------------------------


def test_an_epic_that_has_never_failed_has_no_history():
    assert failures_since_parking(FakeIssues([]), "sprint-metrics", 49) == []


def test_earlier_failures_are_found():
    issues = FakeIssues([failure_comment("abc123"), failure_comment("abc123")])
    assert failures_since_parking(issues, "sprint-metrics", 49) == ["abc123", "abc123"]


def test_ordinary_comments_are_not_failures():
    issues = FakeIssues([{"body": "Looks too big to me."}, failure_comment("abc123")])
    assert failures_since_parking(issues, "sprint-metrics", 49) == ["abc123"]


def test_parking_restarts_the_count():
    """Parking asks a person to look at the epic, and moving it back is how
    they say they have. Counting failures from before that would park it again
    on the first attempt for ever after."""
    issues = FakeIssues(
        [
            failure_comment("abc123"),
            failure_comment("abc123"),
            {"body": f"{SPLIT_PARKED_MARKER}\nBlocked."},
            failure_comment("abc123"),
        ]
    )
    assert failures_since_parking(issues, "sprint-metrics", 49) == ["abc123"]


def test_a_read_failure_does_not_park_the_epic():
    class Broken(FakeIssues):
        def comments(self, repo, number):
            raise RuntimeError("502")

    assert failures_since_parking(Broken([]), "sprint-metrics", 49) == []


# --- the threshold -------------------------------------------------------


def test_a_repeat_is_stopped_well_before_six_attempts():
    """Six identical attempts is what happened. Whatever the threshold is, it
    has to be small enough that a dead end costs a run, not a tick."""
    assert 2 <= IDENTICAL_FAILURES_BEFORE_PARKING < 6
