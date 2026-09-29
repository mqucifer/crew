"""A first attempt can answer that a story is already done, with evidence (#221).

Only rework could say "the code already satisfies this" (#161). A first
attempt had to change something, so a story duplicating delivered work, like
sprint-metrics#149 to #155, forced the Developer to write redundant code or to
fight the regression guards over what already existed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from crew_org.crews.delivery_crew import CriterionMet, FileWrite, FirstOrDone
from crew_org.crews.qa_crew import CriterionVerdict, QAVerdict
from crew_org.events import EventSink
from crew_org.flows import acceptance
from crew_org.flows.acceptance import ALREADY_DONE_MARKER, run_qa
from crew_org.flows.delivery import names_a_test
from crew_org.tools.github_project import Card
from tests.test_delivery_flow import FakeIssues, FakeWorkspace, green
from tests.test_delivery_flow import harness as harness  # noqa: F401  (the fixture)

MET = CriterionMet(
    criterion="Given cards, When --json, Then api_version is 1",
    code="src/sm/report.py::format_json_report",
    test="tests/test_report.py::test_api_version",
)
DONE = FirstOrDone(summary="#143 already added api_version.", already_done=[MET])


# --- the answer itself ---------------------------------------------------------------


def test_an_already_done_answer_changes_nothing():
    assert DONE.changes_nothing
    with pytest.raises(ValidationError, match="changes nothing"):
        FirstOrDone(
            summary="s", already_done=[MET], new_files=[FileWrite(path="a.py", content="x")]
        )


def test_without_it_a_first_attempt_still_needs_its_tests():
    with pytest.raises(ValidationError, match="must create a file or edit one|no test"):
        FirstOrDone(summary="nothing")


def test_a_named_test_is_found_by_path_and_name(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_report.py").write_text(
        "class TestIt:\n    def test_in_class(self):\n        pass\n\n"
        "def test_api_version():\n    pass\n"
    )
    assert names_a_test(tmp_path, "tests/test_report.py::test_api_version")
    assert names_a_test(tmp_path, "tests/test_report.py::TestIt::test_in_class")
    assert names_a_test(tmp_path, "tests/test_report.py::test_api_version[case-1]")
    assert not names_a_test(tmp_path, "tests/test_report.py::test_nowhere")
    assert not names_a_test(tmp_path, "tests/test_missing.py::test_api_version")


# --- through delivery ----------------------------------------------------------------


@pytest.fixture
def offered(monkeypatch):
    """A story never answered as done before, with the named test in its worktree."""
    posted: list[tuple[int, str]] = []
    monkeypatch.setattr(FakeIssues, "comments", lambda self, repo, n: [], raising=False)
    opened = FakeWorkspace.open

    def open_with_test(self, branch, *, resume=False):
        path = opened(self, branch, resume=resume)
        (path / "tests").mkdir(exist_ok=True)
        (path / "tests" / "test_report.py").write_text("def test_api_version():\n    pass\n")
        return path

    monkeypatch.setattr(FakeWorkspace, "open", open_with_test)
    return posted


def test_an_already_done_story_goes_to_qa_without_a_pull_request(harness, offered):  # noqa: F811
    result, board, issues, ws, calls, _ = harness(checks=[green()], implement=lambda n: DONE)

    assert calls["may_be_done"] == [True], "a first attempt is offered the answer"
    assert issues.prs == [], "nothing to review, so no pull request"
    assert ws.empty_commits == 1 and ws.committed[0].startswith("chore(6): already done")
    assert ("S6", "QAing") in board.moves and ("S6", "Reviewing") not in board.moves
    (evidence,) = [body for _, body in issues.comments_ if ALREADY_DONE_MARKER in body]
    assert "`tests/test_report.py::test_api_version`" in evidence
    assert not any(body.startswith("Implemented in #") for _, body in issues.comments_)
    assert result.delivered[0].already_done == [MET]


def test_a_named_test_that_does_not_exist_is_refused_naming_it(harness, offered):  # noqa: F811
    """#221, criterion 3."""
    invented = FirstOrDone(
        summary="s", already_done=[MET.model_copy(update={"test": "tests/test_report.py::test_x"})]
    )
    answers = iter([invented, DONE])
    _, board, _, _, calls, _ = harness(checks=[green()], implement=lambda n: next(answers))
    assert "tests/test_report.py::test_x" in calls["feedback"][1]
    assert "don't exist" in calls["feedback"][1]


def test_a_story_answered_before_is_not_offered_it_again(harness, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        FakeIssues,
        "comments",
        lambda self, repo, n: [{"body": f"{ALREADY_DONE_MARKER}\nAlready done."}],
        raising=False,
    )
    _, _, _, _, calls, _ = harness(checks=[green()])
    assert calls["may_be_done"] == [False], "QA refused it once; now it's built"


# --- through QA ----------------------------------------------------------------------


class QAIssues:
    def __init__(self, comments):
        self._comments = comments
        self.posted, self.closed = [], []

    def has_comment_marked(self, repo, number, marker):
        return False

    def get(self, repo, number):
        return {"body": "**Given** cards **When** --json **Then** api_version is 1"}

    def comments(self, repo, number):
        return self._comments

    def comment(self, repo, number, body):
        self.posted.append((number, body))

    def close(self, repo, number, *, reason="completed"):
        self.closed.append(number)


class QABoard:
    def __init__(self):
        self.moves = []

    def set_status(self, item_id, column):
        self.moves.append((item_id, column))

    def set_owner_agent(self, item_id, role):
        pass


class QAWorkspace:
    def __init__(self, root):
        self.root = root

    def for_repo(self, repo):
        return self

    def open_existing(self, branch):
        return self.root

    def head(self):
        return "abc1234"

    def close(self, path=None):
        pass


def qa(monkeypatch, tmp_path, comments, accepted):
    seen: dict = {}
    verdict = QAVerdict(
        summary="judged",
        accepted=accepted,
        criteria=[
            CriterionVerdict(
                criterion="api_version is 1",
                proven=accepted,
                evidence="tests/test_report.py::test_api_version passes and checks it",
            )
        ],
    )

    def verify(story, **kwargs):
        seen["story"] = story
        return verdict

    monkeypatch.setattr(acceptance, "verify_story", verify)
    monkeypatch.setattr(
        acceptance.workspace, "check", lambda w, sandbox=None: SimpleNamespace(results=[])
    )
    card = Card(
        item_id="S143",
        number=143,
        title="Add api_version",
        status="QAing",
        state="OPEN",
        work_type="Story",
        repo="sprint-metrics",
    )
    # The test the verdict cites exists: QA's verdict rests on tests it read (#231).
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_report.py").write_text("def test_api_version():\n    pass\n")
    issues, board = QAIssues(comments), QABoard()
    run_qa(board, issues, EventSink(None), QAWorkspace(tmp_path), None, cards=[card], repo="sm")
    return seen, issues, board


def test_qa_accepting_an_already_done_story_closes_it(monkeypatch, tmp_path):
    """#221, criterion 2."""
    evidence = [{"body": f"{ALREADY_DONE_MARKER}\n**Already done.** | a | b | c |"}]
    seen, issues, board = qa(monkeypatch, tmp_path, evidence, accepted=True)
    assert "## The Developer answered that this is already done" in seen["story"]
    assert board.moves == [("S143", "Done")] and issues.closed == [143]


def test_qa_refusing_it_returns_the_story_as_any_refusal_does(monkeypatch, tmp_path):
    evidence = [{"body": f"{ALREADY_DONE_MARKER}\n**Already done.**"}]
    _, issues, board = qa(monkeypatch, tmp_path, evidence, accepted=False)
    assert board.moves == [("S143", "In Progress")] and issues.closed == []


def test_a_story_built_since_its_already_done_answer_merges_as_usual(monkeypatch, tmp_path):
    later = [
        {"body": f"{ALREADY_DONE_MARKER}\n**Already done.**"},
        {"body": "Implemented in #170 on `feat/143`. Lint and tests pass."},
    ]
    seen, issues, board = qa(monkeypatch, tmp_path, later, accepted=True)
    assert "already done" not in seen["story"]
    assert board.moves == [("S143", "Merging")] and issues.closed == []
