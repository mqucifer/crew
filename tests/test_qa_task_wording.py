"""A QA Task's session is told about its Task, not a story, and QA isn't the Developer
(crew#944).

mqucifer/qa-suite#8's session opened with "## The story", listed "The tests the story
declares", and was sent back with "CI failed before review: returned to the Developer"
and "this story was sent back to be re-worked".
"""

from __future__ import annotations

from crew_org.events import EventSink
from crew_org.flows import ci, delivery, session_delivery
from crew_org.session.start import start
from crew_org.tools.github_project import Card
from crew_org.work_types import STORY, SUITE, TASK
from tests.test_merge import FakeBoard, FakeIssues


def _card(work_type: str = TASK) -> Card:
    return Card(
        item_id="I8",
        number=8,
        title="Disputed: crew-presentation/test_reads_crew.py::test_a_sprints_page",
        status="Reviewing",
        state="OPEN",
        work_type=work_type,
        labels=frozenset({"role:qa", "project:crew-presentation"})
        if work_type == TASK
        else frozenset(),
        repo=SUITE if work_type == TASK else "crew-presentation",
    )


def test_a_task_s_session_opens_with_the_task_and_its_tests(tmp_path):
    opening, _, _ = start(tmp_path, "the Task's body", None, work="Task")
    assert "## The Task\n\nthe Task's body" in opening
    assert "## The tests the Task declares" in opening
    assert "story" not in opening.split("## The map")[0].lower()
    # A story's is as it was.
    story, _, _ = start(tmp_path, "the story's body", None)
    assert "## The story\n\n" in story and "## The tests the story declares" in story


def _returned(card: Card) -> str:
    issues = FakeIssues()
    ci.return_for_ci(
        FakeBoard(),
        issues,
        EventSink(None),
        card,
        repo=card.repo,
        pull={"number": 10, "head": {"sha": "abc"}},
        failed=[{"name": "suite / qa", "conclusion": "failure"}],
        where="before review",
    )
    return issues.posted[-1][1]


def test_ci_returns_a_qa_task_to_qa_not_the_developer():
    said = _returned(_card())
    assert "returned to the QA Engineer" in said and "Developer" not in said
    assert "returned to the Developer" in _returned(_card(STORY))


class Pulls:
    def __init__(self, state: str = "open") -> None:
        self.state = state

    def pulls_for_branch(self, repo, branch, state="all"):
        return [{"number": 10, "state": self.state}]


def test_an_open_pull_request_s_fate_names_the_task():
    fate = delivery.previous_fate(Pulls(), SUITE, "task/8", work="Task")
    assert fate == "PR #10 is still open, and this Task was sent back to be re-worked."


def test_a_returned_task_s_session_says_the_task_came_back(tmp_path, monkeypatch):
    monkeypatch.setattr(session_delivery, "prior_verdicts", lambda *a, **k: "CI said no")
    monkeypatch.setattr(session_delivery, "last_done_on", lambda *a: "")

    class Issues(Pulls):
        def pull_for_branch(self, repo, branch, known=None):
            return None

    told, _, _ = session_delivery.returned_start(
        Issues(), SUITE, 8, "task/8", tmp_path, known=_card()
    )
    assert "Review or CI sent the Task back" in told and "## What review or CI said" in told
    assert "story" not in told and "QA" not in told
