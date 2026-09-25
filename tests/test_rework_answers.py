"""A returned story can answer that the work is already done (#161).

The case: sprint-metrics#74 was asked to add an implementation #73 had already
landed. Its Developer said so three times, and each answer was refused as a
SCHEMA failure because an implementation had to change something.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.crews.delivery_crew import (
    FileWrite,
    FirstAttempt,
    Implementation,
    Rework,
    Satisfied,
)
from crew_org.flows.history import ANSWERED_MARKER
from tests.test_delivery_flow import (  # noqa: F401
    REFUSED_HEAD,
    FakeIssues,
    FakeWorkspace,
    _returned_card,
    green,
    harness,
    red,
    refused,
)

EVIDENCE = [
    Satisfied(
        finding="The diff adds tests but no implementation",
        evidence=(
            "format_sprint_range_table in src/sprint_metrics/crew_performance.py already "
            "applies wip_limits and escalations per sprint; the new tests exercise it"
        ),
    )
]
ANSWER = Rework(summary="Already implemented by #73; the tests pin it.", already_satisfied=EVIDENCE)


# --- the answer itself -----------------------------------------------------------------


def test_returned_work_may_answer_with_evidence_and_no_change():
    """Criterion 1."""
    assert ANSWER.changes_nothing and ANSWER.already_satisfied


def test_returned_work_with_no_change_and_no_evidence_is_refused():
    with pytest.raises(ValidationError, match="must create a file or edit one"):
        Rework(summary="Nothing to do.")


def test_a_first_attempt_with_nothing_to_deliver_is_still_refused():
    """Criterion 3."""
    with pytest.raises(ValidationError, match="must create a file or edit one"):
        FirstAttempt(summary="Nothing to do.")


def test_a_repair_with_no_change_is_still_refused():
    with pytest.raises(ValidationError, match="must create a file or edit one"):
        Implementation(summary="Nothing to do.")


def test_returned_work_is_not_asked_for_a_new_test():
    """Its first attempt's tests are already in the branch, as for a repair."""
    fix = Rework(summary="s", new_files=[FileWrite(path="src/m.py", content="x = 1\n")])
    assert not fix.changes_nothing


# --- 2: through delivery ---------------------------------------------------------------------


def test_an_answer_is_verified_committed_empty_and_posted_on_the_pull_request(
    harness,  # noqa: F811
    refused,  # noqa: F811
):
    result, _, issues, ws, calls, _ = harness(
        checks=[green()], cards=[_returned_card("In Progress")], implement=lambda n: ANSWER
    )

    assert len(result.delivered) == 1 and not result.blocked
    assert ws.empty_commits == 1, "the head moves, so the review it answers stops applying"
    assert ws.committed[0].startswith("chore(31): answer the review, no change needed")
    assert ws.forced is False, "added on top of the reviewed history, not replacing it"
    (answer,) = [body for _, body in issues.comments_ if ANSWERED_MARKER in body]
    assert "**Answered without a change:**" in answer
    assert "format_sprint_range_table" in answer
    assert "pass on this head" in answer


def test_an_answer_the_checks_contradict_is_sent_back_to_the_developer(
    harness,  # noqa: F811
    refused,  # noqa: F811
):
    answers = iter(
        [ANSWER, Implementation(summary="fix", new_files=[FileWrite(path="a.py", content="x\n")])]
    )
    result, _, _, ws, calls, _ = harness(
        checks=[red("1 failed"), green()],
        cards=[_returned_card("In Progress")],
        implement=lambda n: next(answers),
    )
    assert "Lint or tests failed" in calls["feedback"][1]
    assert ws.empty_commits == 0, "a claim the checks contradict is never committed"


def test_a_second_answer_without_a_change_goes_to_a_person_with_both_sides(
    harness,  # noqa: F811
    refused,  # noqa: F811
    monkeypatch,
):
    """Criterion 4: not looped."""
    earlier = f"{ANSWERED_MARKER}\n**Re-worked.** Already implemented by #73."
    monkeypatch.setattr(
        FakeIssues, "comments", lambda self, repo, n: [{"body": earlier}], raising=False
    )
    monkeypatch.setattr(
        FakeIssues,
        "pull_reviews",
        lambda self, repo, n: [
            {
                "state": "CHANGES_REQUESTED",
                "commit_id": REFUSED_HEAD,
                "body": "Still no implementation.",
            }
        ],
        raising=False,
    )
    result, _, _, ws, _, _ = harness(
        checks=[green()], cards=[_returned_card("In Progress")], implement=lambda n: ANSWER
    )

    (blocked,) = result.blocked
    assert "the Code Reviewer and the Developer disagree" in blocked.blocked_reason
    assert "Still no implementation." in blocked.failure_detail
    assert "Already implemented by #73." in blocked.failure_detail
    assert ws.committed == []


def test_the_developer_is_offered_the_answer_only_for_returned_work(
    harness,  # noqa: F811
    refused,  # noqa: F811
):
    _, _, _, _, calls, _ = harness(
        checks=[green()], cards=[_returned_card("In Progress")], implement=lambda n: ANSWER
    )
    assert calls["returned"] == [True]


def test_a_story_delivered_the_first_time_is_not_returned(harness):  # noqa: F811
    _, _, _, _, calls, _ = harness(checks=[green()])
    assert calls["returned"] == [False]


def test_the_reviewer_is_shown_the_answer_when_it_reviews_again(monkeypatch):
    from crew_org.events import EventSink
    from crew_org.flows import review as review_flow
    from tests.test_review import APPROVAL
    from tests.test_review import FakeIssues as ReviewIssues

    issues = ReviewIssues(
        [
            {
                "number": 88,
                "user": {"login": "crew[bot]"},
                "draft": False,
                "title": "t",
                "head": {"sha": "answer-head"},
            }
        ]
    )
    issues.comments = lambda repo, n: [
        {"body": f"{ANSWERED_MARKER}\n**Answered without a change:** format_sprint_range_table"}
    ]
    shown = {}

    def reviewer(title, diff, **kwargs):
        shown.update(kwargs)
        return APPROVAL

    monkeypatch.setattr(review_flow, "review_diff", reviewer)
    review_flow.review_open_pulls(
        issues, EventSink(None), repo="sprint-metrics", bot_login="reviewer[bot]"
    )
    assert "## The author's answer to your last review" in shown["prior_verdicts"]
    assert "format_sprint_range_table" in shown["prior_verdicts"]
