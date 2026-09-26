"""A story whose failures show a story problem goes back to refinement by itself (#189).

sprint-metrics#97 broke the same ~25 merged tests, ones pinning the default
table's rows, on every attempt, and blocked. The story never said whether its
flags were opt-in or a change to the table's contract.
"""

from __future__ import annotations

from crew_org.crews.delivery_crew import FileEdit, Implementation
from crew_org.escalation import Disposition, EscalationPolicy, FailureClass, LocalFailure
from crew_org.events import EventSink
from crew_org.flows.board_flow import (
    NEEDS_REWORK,
    STORY_PROBLEM_MARKER,
    STORY_SPLIT_MARKER,
    story_problem_evidence,
)
from crew_org.flows.story_problem import (
    evidence,
    failing_tests,
    pinned_failures,
    return_to_refinement,
)
from crew_org.tools.github_project import Card

REPO = "sprint-metrics"

REPORT = """\
FAILED tests/test_table.py::test_default_table_rows - AssertionError: assert '| Current |' in out
FAILED tests/test_table.py::test_range_rows[2024-01] - AssertionError: row missing
FAILED tests/test_flags.py::test_flags_default_table - AssertionError: no ⚠
3 failed, 105 passed in 2.1s
"""

MERGED = {
    "tests/test_table.py": (
        "def test_default_table_rows():\n    ...\n\n\ndef test_range_rows():\n    ...\n"
    ),
}


class Merged:
    def text(self, path):
        return MERGED.get(path)


def story(number=97, status="In Progress", parent=54, **kw) -> Card:
    return Card(
        item_id=f"S{number}",
        number=number,
        title=kw.get("title", f"Story {number}"),
        repo=REPO,
        status=status,
        state="OPEN",
        work_type="Story",
        parent=parent,
        sprint="Sprint 6",
    )


def test_the_failing_tests_are_read_from_pytests_summary():
    failing = failing_tests(REPORT)
    assert set(failing) == {
        "tests/test_table.py::test_default_table_rows",
        "tests/test_table.py::test_range_rows",
        "tests/test_flags.py::test_flags_default_table",
    }, "a parametrised id is its test"
    assert failing["tests/test_table.py::test_range_rows"] == "AssertionError: row missing"


def test_only_merged_tests_this_story_didnt_write_are_pinned():
    own = Implementation(
        summary="flags",
        edits=[
            FileEdit(
                path="tests/test_table.py",
                operation="replace",
                target="test_range_rows",
                source="def test_range_rows():\n    pass\n",
            )
        ],
    )
    pinned = pinned_failures(REPORT, Merged(), own)
    assert set(pinned) == {"tests/test_table.py::test_default_table_rows"}, (
        "test_flags is new, not merged; test_range_rows the story edited itself"
    )


def test_without_the_merged_state_nothing_is_pinned():
    assert (
        pinned_failures(
            REPORT,
            None,
            Implementation(
                summary="s", edits=[FileEdit(path="a.py", operation="delete", target="x")]
            ),
        )
        == {}
    )


def test_a_repeat_is_scope_and_goes_back_to_refinement_without_spending_the_budget():
    policy = EscalationPolicy(
        never_escalate={FailureClass.SCHEMA, FailureClass.SCOPE, FailureClass.REGRESSION},
        require_justification={FailureClass.CAPABILITY},
        local_repair_attempts=2,
        sprint_budget=1,
    )
    decision = policy.decide(
        LocalFailure(
            card=97, role="Developer", failure_class=FailureClass.SCOPE, attempts=1, detail="x"
        ),
        spent=0,
    )
    assert decision.disposition is Disposition.RETURN_TO_REFINEMENT


def test_the_evidence_names_the_tests_and_asks_the_split_to_decide():
    text = evidence(
        story(title="Flag metrics in the default table"),
        {f"tests/test_table.py::test_row_{n}": "AssertionError" for n in range(12)},
        attempts=2,
    )
    assert text.startswith(STORY_PROBLEM_MARKER)
    assert "failed 2 attempts in a row by breaking the same 12 merged tests" in text
    assert "… and 4 more" in text, "eight shown, the rest counted"
    assert "opt-in or an expected contract change" in text


class Board:
    def __init__(self):
        self.status, self.cleared = {}, []

    def set_status(self, item, to):
        self.status[item] = to

    def set_owner_agent(self, item, by):
        pass

    def clear_field(self, item, field):
        self.cleared.append((item, field))


class Issues:
    def __init__(self, branches=()):
        self._branches = branches
        self.labels, self.added, self.posted = set(), [], []

    def sub_issues(self, repo, number):
        return [{"number": n} for n in (97, 98, 99, 100)]

    def branches(self, repo):
        return [{"name": b} for b in self._branches]

    def ensure_label(self, repo, name, *, color, description):
        self.labels.add(name)

    def add_labels(self, repo, number, labels):
        self.added += [(number, label) for label in labels]

    def comment(self, repo, number, body):
        self.posted.append((number, body))
        return {}


def test_the_story_and_its_unbuilt_siblings_go_back_and_the_epic_says_why():
    """What the Sponsor did by hand for #97, #98 and #99."""
    board, issues = Board(), Issues(branches=["feat/100-already-building"])
    cards = [
        story(97),
        story(98, status="Sprint Backlog"),
        story(99, status="Sprint Backlog"),
        story(100, status="In Progress"),
    ]
    sent = return_to_refinement(
        board, issues, EventSink(), cards[0], repo=REPO, cards=cards, comment="the evidence"
    )
    assert sent
    assert board.status == {"S97": "Ready", "S98": "Ready", "S99": "Ready"}, "#100 has a branch"
    assert {item for item, field in board.cleared if field == "Sprint"} == {"S97", "S98", "S99"}
    assert NEEDS_REWORK in issues.labels and (54, NEEDS_REWORK) in issues.added
    assert issues.posted == [(54, issues.posted[0][1])] and "the evidence" in issues.posted[0][1]


def test_a_story_with_no_epic_is_not_sent_anywhere():
    assert not return_to_refinement(
        Board(), Issues(), EventSink(), story(parent=None), repo=REPO, cards=[], comment="x"
    )


class Comments:
    def __init__(self, bodies):
        self.bodies = bodies

    def comments(self, repo, number):
        return [{"body": b} for b in self.bodies]


def test_the_re_split_reads_the_evidence_given_since_the_last_split():
    issues = Comments(
        [
            f"{STORY_PROBLEM_MARKER}\nan older problem",
            f"{STORY_SPLIT_MARKER}\n## Stories",
            f"{STORY_PROBLEM_MARKER}\n#97 broke the table's rows",
        ]
    )
    assert story_problem_evidence(issues, REPO, 54) == "#97 broke the table's rows"
    assert story_problem_evidence(Comments([f"{STORY_SPLIT_MARKER}"]), REPO, 54) == ""
