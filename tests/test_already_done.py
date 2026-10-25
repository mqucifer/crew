"""A story the code already satisfies goes to QA with its evidence (#221).

Only rework could say "the code already satisfies this" (#161). A first
attempt had to change something, so a story duplicating delivered work, like
sprint-metrics#149 to #155, forced the Developer to write redundant code or to
fight the regression guards over what already existed. A session now calls `done`
with no change, naming the test for each criterion, and the gates send it to QA
(crew#778).
"""

from __future__ import annotations

from types import SimpleNamespace

from crew_org.crews.qa_crew import CriterionVerdict, QAVerdict, Ran
from crew_org.events import EventSink
from crew_org.flows.acceptance import ALREADY_DONE_MARKER, run_qa
from crew_org.flows.delivery import already_done_comment, names_a_test
from crew_org.tools.github_project import Card
from tests.test_acceptance import in_a_session

# --- the answer itself ---------------------------------------------------------------


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


def test_the_evidence_comment_names_each_criterion_s_test_for_qa():
    met = SimpleNamespace(
        criterion="api_version is 1", code="the code as it stands", test="tests/t.py::test_v"
    )
    body = already_done_comment([met])
    assert body.startswith(ALREADY_DONE_MARKER)
    assert "| api_version is 1 | the code as it stands | `tests/t.py::test_v` |" in body


# --- through QA ----------------------------------------------------------------------


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

    def repo_of(self, item_id):
        return None

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
                by="run",
                ran=[Ran(command="uv run pytest -q tests/test_report.py", exit_code=0)],
                evidence="tests/test_report.py::test_api_version passes and checks it",
            )
        ],
    )
    seen = in_a_session(monkeypatch, verdict)
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
    assert "## The Developer answered that this is already done" in seen["answered"]
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
    assert "already done" not in seen["answered"] + seen["story"]
    assert board.moves == [("S143", "Merging")] and issues.closed == []
