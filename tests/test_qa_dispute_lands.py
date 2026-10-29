"""A dispute's changed test is checked against the disputed pull request, and the two
land back to back, the story first (crew#945).

QA ruled for the Developer on mqucifer/qa-suite#8 and fixed the test in
mqucifer/qa-suite#10. Its check ran against crew-presentation's main, which didn't yet
have the change the fixed test expects, so it failed three times, and the story couldn't
merge without it.
"""

from __future__ import annotations

from crew_org.columns import BLOCKED, MERGING, REVIEWING
from crew_org.events import EventSink
from crew_org.flows import qa_tasks, role_tasks, session_delivery
from crew_org.flows.merge import merge_approved
from crew_org.flows.qa_tasks import DISPUTED, LANDS_WITH_LABEL, WAITS, WAITS_LABEL
from crew_org.tools.github_project import Card
from crew_org.work_types import STORY, SUITE, TASK
from tests.test_merge import FakeBoard, FakeIssues
from tests.test_qa_tasks import Suite, SuiteBoard, Ws, _waiting

HEAD = "a" * 40
LINE = DISPUTED.format(repo="sm", story=5, pull=40, head=HEAD)
TASK_BODY = role_tasks.marker(role_tasks.QA, role_tasks.DISPUTE, "sm-d5-x") + "\n\n" + LINE


def _story(**update) -> Card:
    fields = {
        "item_id": "S5",
        "number": 5,
        "title": "Count a card",
        "status": MERGING,
        "state": "OPEN",
        "work_type": STORY,
        "repo": "sm",
        "labels": frozenset({LANDS_WITH_LABEL}),
    }
    return Card(**{**fields, **update})


def _task(**update) -> Card:
    fields = {
        "item_id": "T901",
        "number": 901,
        "title": "Disputed: sm/test_api.py::test_counts",
        "status": MERGING,
        "state": "OPEN",
        "work_type": TASK,
        "repo": SUITE,
        "labels": frozenset({"role:qa", "project:sm"}),
    }
    return Card(**{**fields, **update})


class Paired(FakeIssues):
    """The disputed story's pull request in sm, and QA's in qa-suite."""

    def __init__(self, *, story_landed: bool = False) -> None:
        super().__init__()
        self.landed: list[tuple[str, int]] = [("sm", 40)] if story_landed else []
        self.suite_checks = [{"name": "suite", "status": "completed", "conclusion": "success"}]

    def get(self, repo, number):
        return {"body": TASK_BODY if (repo, number) == (SUITE, 901) else ""}

    def comments(self, repo, number):
        if (repo, number) == ("sm", 5):
            return [{"body": WAITS.format(task=901)}]
        return super().comments(repo, number)

    def pull_for_branch(self, repo, branch, *, known=None):
        number = 11 if repo == SUITE else 40
        return {"number": number, "head": {"ref": branch, "sha": f"{repo}-head"}}

    def pull(self, repo, number):
        return {
            "merged": (repo, number) in self.landed,
            "state": "open",
            "mergeable_state": "clean",
            "mergeable": True,
        }

    def check_runs(self, repo, sha):
        return list(self.suite_checks if repo == SUITE else self.head_checks)

    def merge_pull(self, repo, number, **kw):
        self.landed.append((repo, number))
        return {}


def _land(cards, issues):
    return merge_approved(
        FakeBoard(), issues, EventSink(None), cards=cards, default_repo="sm", repos={"sm"}
    )


def test_the_story_lands_first_and_qa_s_changed_test_straight_after():
    issues = Paired()
    # The board lists QA's Task first: it lands second all the same.
    result = _land([_task(), _story()], issues)
    assert issues.landed == [("sm", 40), (SUITE, 11)]
    assert result.merged == [(5, 40), (901, 11)] and not result.paired


def test_the_story_waits_until_qa_s_pull_request_is_approved():
    issues = Paired()
    result = _land([_task(status=REVIEWING), _story()], issues)
    assert issues.landed == []
    assert result.paired[0][0] == 5 and "isn't approved yet" in result.paired[0][1]


def test_the_story_waits_while_qa_s_check_against_it_is_red():
    issues = Paired()
    issues.suite_checks = [{"name": "suite", "status": "completed", "conclusion": "failure"}]
    result = _land([_task(), _story()], issues)
    assert issues.landed == [] and "checks failed" in result.paired[0][1]


def test_qa_s_changed_test_never_lands_before_its_story():
    issues = Paired()
    result = _land([_task()], issues)
    assert issues.landed == []
    assert result.paired == [
        (901, "lands straight after the disputed mqucifer/sm#40, which hasn't landed yet")
    ]
    # Once the story has landed, by the queue or an earlier pass, it does.
    landed = Paired(story_landed=True)
    assert _land([_task()], landed).merged == [(901, 11)]


def test_a_story_with_no_dispute_lands_as_before():
    issues = Paired()
    result = _land([_story(labels=frozenset())], issues)
    assert issues.landed == [("sm", 40)] and not result.paired


def test_qa_s_dispute_pull_request_names_the_head_its_test_runs_against():
    issues = Suite()
    issues.add(901, TASK_BODY)
    assert qa_tasks.dispute_line(issues, SUITE, 901) == LINE
    issues.add(902, "a test Task")
    assert qa_tasks.dispute_line(issues, SUITE, 902) == ""
    from types import SimpleNamespace

    reached = SimpleNamespace(said={"summary": "Fixed the test"}, sections=[])
    outcome = SimpleNamespace(attempts=1, autofixed=[], unsafe_fixes=[])
    body = session_delivery.pr_body(_task(), reached, outcome, [], by="QA Engineer", marks=LINE)
    assert LINE in body


def test_an_approved_fix_sends_the_story_on_to_land_with_it(tmp_path):
    issues, board, s = _waiting(tmp_path)
    story = board.cards()[0]
    board = SuiteBoard([story, _task()])
    ws = Ws()
    result = qa_tasks.settle_disputes(issues, board, s, ws, cards=board.cards(), repos={"sm"})
    assert result.rerun == [5] and ws.reran == ["feat/5-count-a-card"]
    assert ("status", "I5", REVIEWING) in board.done
    assert ("unlabel", 5, WAITS_LABEL) in issues.done
    assert ("label", 5, LANDS_WITH_LABEL) in issues.done
    said = issues.comments_on[40][-1]["body"]
    assert "changed the disputed test" in said and "lands first" in said


def test_a_fix_not_yet_approved_keeps_the_story_waiting(tmp_path):
    issues, board, s = _waiting(tmp_path)
    board = SuiteBoard([board.cards()[0], _task(status=REVIEWING)])
    result = qa_tasks.settle_disputes(issues, board, s, Ws(), cards=board.cards(), repos={"sm"})
    assert result.waiting == [(5, 901)] and not board.done
    assert board.cards()[0].status == BLOCKED
