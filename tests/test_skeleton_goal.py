"""A new project's first Goal is its walking skeleton (crew#690, ADR 0030).

The same text for every project; `crew onboard` files it on the Sponsor's yes,
and `board.yml` travels with the record's pull request.
"""

from __future__ import annotations

from pathlib import Path

from crew_org.columns import INBOX
from crew_org.flows import skeleton
from crew_org.flows.board_flow import goal_cards, set_by_the_sponsor, unauthored_goals
from crew_org.flows.onboard import open_record_pr, validate
from crew_org.tools import regression
from crew_org.tools.github_project import Card
from tests.test_onboard import COMPLETE, FakeIssues, FakeWorkspace

# --- the standard text -------------------------------------------------------------------


def test_the_goal_is_one_text_with_the_sponsor_s_decisions():
    title, body = skeleton.goal()
    assert title.startswith("Goal: a walking skeleton")
    assert "**Sponsor decisions:**" in body
    for decision in ("| D1 |", "| D2 |", "| D3 |", "| D4 |", "| D5 |"):
        assert decision in body
    assert skeleton.PLACEHOLDER in body, "D2 names the marker the guard reads"


def test_the_goal_names_no_language_or_tool():
    """Not a Python template: what the skeleton holds comes from the design."""
    _title, body = skeleton.goal()
    for word in ("python", "pytest", "uv", "npm", "pip"):
        assert f" {word} " not in f" {body.lower()} "


def test_the_board_workflow_is_the_same_for_every_project():
    """sprint-metrics' and crew-presentation's were identical, copied by hand."""
    text = skeleton.board_workflow()
    assert "name: board" in text and "BOARD_APP_PRIVATE_KEY" in text
    assert "sprint-metrics" not in text and "crew-presentation" not in text


# --- crew onboard files it and adds board.yml ------------------------------------------


class Board:
    def __init__(self) -> None:
        self.added: list[str] = []
        self.status: list[tuple[str, str]] = []
        self.selects: list[tuple[str, str, str]] = []

    def add_issue(self, node_id):
        self.added.append(node_id)
        return "ITEM"

    def set_status(self, item, column):
        self.status.append((item, column))

    def set_select(self, item, field, option):
        self.selects.append((item, field, option))


class Issues:
    def __init__(self, labelled=()) -> None:
        self._labelled = list(labelled)
        self.created: list[tuple[str, list[str]]] = []
        self.labels: list[str] = []

    def labelled(self, repo, label):
        return self._labelled

    def ensure_label(self, repo, name, *, color, description):
        self.labels.append(name)

    def create(self, repo, title, body, labels=None):
        self.created.append((title, labels))
        return {"number": 1, "node_id": "N1"}


def test_the_skeleton_goal_lands_in_the_inbox_as_a_goal():
    issues, board = Issues(), Board()
    assert skeleton.file_goal(issues, board, "site") == 1
    ((title, labels),) = issues.created
    assert title.startswith("Goal: a walking skeleton") and labels == ["goal", "skeleton"]
    assert set(issues.labels) == {"goal", "skeleton"}
    assert board.status == [("ITEM", INBOX)]
    assert board.selects == [("ITEM", "Work Type", "Goal")]


def test_an_open_skeleton_goal_is_not_filed_twice():
    issues = Issues(labelled=[{"number": 1, "state": "open"}])
    assert skeleton.file_goal(issues, Board(), "site") is None
    assert issues.created == []


def test_a_closed_one_doesn_t_stop_a_new_one():
    issues = Issues(labelled=[{"number": 1, "state": "closed"}])
    assert skeleton.file_goal(issues, Board(), "site") == 1


def test_the_record_s_pull_request_carries_the_board_workflow(tmp_path: Path):
    ws, issues = FakeWorkspace(tmp_path), FakeIssues()
    open_record_pr(ws, issues, "site", validate(COMPLETE), base="main")
    written = (tmp_path / ".github/workflows/board.yml").read_text()
    assert written == skeleton.board_workflow()
    assert "`.github/workflows/board.yml`" in issues.pulls[0]["body"]


# --- refinement takes it from the crew, with its label --------------------------------


def goal(author: str, labels: frozenset[str] = frozenset({"goal"})) -> Card:
    return Card(
        item_id="I1",
        number=1,
        title="Goal",
        status="Inbox (Goals)",
        work_type="Goal",
        author=author,
        labels=labels,
        repo="site",
    )


def test_the_crew_s_skeleton_goal_counts_as_the_sponsor_s():
    filed = goal("mqucifer-crew", frozenset({"goal", "skeleton"}))
    assert set_by_the_sponsor(filed, "mquarters", "mqucifer-crew")
    assert goal_cards([filed], sponsor="mquarters", crew_login="mqucifer-crew") == [filed]


def test_any_other_goal_from_the_crew_still_does_not():
    """crew#60: two Goals the crew wrote redirected a day of delivery."""
    unlabelled = goal("mqucifer-crew")
    assert not set_by_the_sponsor(unlabelled, "mquarters", "mqucifer-crew")
    assert unauthored_goals([unlabelled], sponsor="mquarters", crew_login="mqucifer-crew") == [
        unlabelled
    ]


def test_a_skeleton_label_from_anyone_else_counts_for_nothing():
    stranger = goal("someone", frozenset({"goal", "skeleton"}))
    assert not set_by_the_sponsor(stranger, "mquarters", "mqucifer-crew")


# --- the first stories replace the placeholders ----------------------------------------


def test_a_merged_placeholder_test_may_be_replaced_without_a_declaration(tmp_path: Path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_scaffold.py").write_text(
        f"# {skeleton.PLACEHOLDER}: the first real story replaces this\n"
        "def test_scaffold():\n    assert True\n"
    )
    (tests / "test_real.py").write_text("def test_real():\n    assert True\n")
    broken = {
        "tests/test_scaffold.py::test_scaffold": ("passes", "removed"),
        "tests/test_real.py::test_real": ("passes", "removed"),
    }
    left, excused = regression.without_placeholders(tmp_path, broken, None)
    assert excused == ["tests/test_scaffold.py::test_scaffold"]
    assert set(left) == {"tests/test_real.py::test_real"}
