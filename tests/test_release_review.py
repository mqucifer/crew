"""A release's pull request is reviewed as a release (crew#948).

mqucifer/sprint-metrics#876, the release for epic mqucifer/sprint-metrics#853, went back
4 times for not doing its epic's rewrite, which story mqucifer/sprint-metrics#863 had
already merged. The Reviewer is told what it is from the Task's marker, not its title.
"""

from __future__ import annotations

import pytest

from crew_org.crews.review_crew import ReviewVerdict, review_diff
from crew_org.events import EventSink
from crew_org.flows import review as review_flow
from crew_org.flows.review import review_open_pulls
from crew_org.flows.role_tasks import DEVOPS
from crew_org.work_types import TASK
from tests.asked import Stop, capture
from tests.test_review import BOT, FakeBoard, FakeIssues, crew_pull, waiting_card

RELEASE_BODY = "<!-- crew:task role=devops kind=release key=epic-853 -->\n**Role:** DevOps Engineer"
STORY_BODY = "**Acceptance criteria**\n\n- The rate is shown"
TITLE = "Release for epic mqucifer/sprint-metrics#853"


def _task_card():
    """A release Task, as the board has it: labelled with its role."""
    return waiting_card().model_copy(
        update={"work_type": TASK, "labels": frozenset({DEVOPS.label})}
    )


def _issues(body: str) -> FakeIssues:
    issues = FakeIssues([crew_pull()])
    issues.get = lambda repo, number: {"body": body}  # type: ignore[attr-defined]
    return issues


def _told(monkeypatch, body: str) -> str:
    """The Code Reviewer's task as the model reads it, for a card with this body."""
    seen = capture(monkeypatch)
    with pytest.raises(Stop):
        review_diff(
            TITLE,
            "diff --git a/CHANGELOG.md b/CHANGELOG.md\n+## [4.2.0]\n",
            release=review_flow.is_release(_issues(body), _task_card(), "sprint-metrics"),
        )
    return seen["description"]


def test_a_release_pull_request_is_reviewed_as_a_release(monkeypatch):
    told = _told(monkeypatch, RELEASE_BODY)
    assert "## This pull request is a release" in told
    assert "`CHANGELOG.md`" in told and "version" in told and "CI" in told
    assert "already merged" in told


def test_a_story_pull_request_is_reviewed_as_before(monkeypatch):
    assert "is a release" not in _told(monkeypatch, STORY_BODY)
    # A title that reads like a release doesn't make one: the marker does.
    assert "is a release" not in _told(monkeypatch, TITLE)


@pytest.mark.parametrize(("body", "release"), [(RELEASE_BODY, True), (STORY_BODY, False)])
def test_the_flow_tells_the_reviewer_from_the_card_s_marker(monkeypatch, body, release):
    seen = {}

    def fake_review(title, diff, **evidence):
        seen.update(evidence)
        return ReviewVerdict(summary="ok", approve=True)

    monkeypatch.setattr(review_flow, "review_diff", fake_review)
    review_open_pulls(
        _issues(body),
        EventSink(None),
        repo="sprint-metrics",
        bot_login=BOT,
        board=FakeBoard(),
        cards=[_task_card()],
    )
    assert seen["release"] is release
