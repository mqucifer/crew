"""A QA test Task's pull request is judged and found where it lives (crew#971).

mqucifer/qa-suite#17 adds tests of mqucifer/crew-presentation#215 to qa-suite. Its
Task's body quotes the story, so the Code Reviewer was handed the story's criteria
as the pull request's own, and asked for changes in files qa-suite doesn't have. The
stranded-card check then looked for the pull request in crew-presentation, the
tick's repository, and returned the card to the backlog; claimed again, it was
blocked for having an open pull request.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from crew_org.crews import review_crew
from crew_org.crews.review_crew import Finding, ReviewVerdict
from crew_org.events import EventSink
from crew_org.flows import review as review_flow
from crew_org.flows import qa_tasks, session_delivery
from crew_org.flows.delivery import (
    DeliveryOutcome,
    DeliveryResult,
    _work_one_card,
    awaiting_rework,
    needs_rework,
    reconcile_orphans,
)
from crew_org.flows.review import (
    JUDGED_AS_TASK,
    REVIEW_MARKER,
    review_open_pulls,
    story_criteria,
    what_tests_cover,
)
from crew_org.git_ops import branch_name
from crew_org.rules import Rule
from crew_org.tools.github_project import Card

BODY = (Path(__file__).parent / "fixtures" / "qa_suite_16_body.md").read_text()
TITLE = "The test of crew-presentation#215: Accept delivery-history 2.0.0 (schema major 2)"
BRANCH = branch_name(16, TITLE)
HEAD = "95dbd88f44724344306b060f5023cf7aa4531b1d"
BOT = "mqucifer-crew-approver[bot]"
TICK = "crew-presentation"
SUITE = "qa-suite"


def task(status: str = "Reviewing") -> Card:
    return Card(
        item_id="T16",
        number=16,
        title=TITLE,
        status=status,
        state="OPEN",
        work_type="Task",
        repo=SUITE,
        labels=frozenset({"role:qa", "project:crew-presentation"}),
    )


def verdict(state: str, *, as_task: bool = False, commit: str = HEAD) -> dict:
    marks = f"{JUDGED_AS_TASK}\n" if as_task else ""
    return {
        "user": {"login": BOT},
        "state": state,
        "commit_id": commit,
        "body": f"{marks}{REVIEW_MARKER}\nsaid",
    }


MISJUDGED = verdict("CHANGES_REQUESTED")


class Issues:
    """qa-suite holds pull request 17; crew-presentation holds another project's."""

    owner = "mqucifer"

    def __init__(self, reviews=()):
        self.reviews = list(reviews)
        self.submitted: list[tuple[str, int, str, str]] = []
        self.labels: list[tuple[str, int, list[str]]] = []
        self.comments_posted: list[tuple[str, int]] = []

    def open_pulls(self, repo):
        if repo != SUITE:
            return []
        return [
            {
                "number": 17,
                "title": TITLE,
                "user": {"login": "mqucifer-crew[bot]"},
                "body": "",
                "head": {"ref": BRANCH, "sha": HEAD},
                "base": {"ref": "main"},
            }
        ]

    def pull_for_branch(self, repo, branch, *, known=None):
        return next((p for p in Issues.open_pulls(self, repo) if p["head"]["ref"] == branch), None)

    def pull_reviews(self, repo, number):
        return self.reviews if repo == SUITE else []

    def comments(self, repo, number):
        return []

    def get(self, repo, number):
        assert (repo, number) == (SUITE, 16), "read in the wrong repository"
        return {"body": BODY, "labels": []}

    def check_runs(self, repo, sha):
        return []

    def pull_diff(self, repo, number):
        return "diff --git a/crew-presentation/test_x.py b/crew-presentation/test_x.py\n+x\n"

    def file_at(self, repo, path, ref):
        return None

    def create_review(self, repo, number, *, event, body):
        self.submitted.append((repo, number, event, body))
        return {}

    def remove_label(self, repo, number, name):
        self.labels.append((repo, number, [f"-{name}"]))

    def add_labels(self, repo, number, labels):
        self.labels.append((repo, number, list(labels)))

    def comment(self, repo, number, body):
        self.comments_posted.append((repo, number))
        return {}


class Board:
    def __init__(self, cards=()):
        self._cards = list(cards)
        self.moves: list[tuple[str, str]] = []

    def cards(self):
        return self._cards

    def repo_of(self, item_id):
        return SUITE

    def set_status(self, item_id, column):
        self.moves.append((item_id, column))

    def set_owner_agent(self, item_id, role):
        pass


def review(monkeypatch, issues, cards, answer, seen=None):
    def judge(*args, **kwargs):
        if seen is not None:
            seen.update(kwargs)
        return answer

    monkeypatch.setattr(review_flow, "review_diff", judge)
    board = Board(cards)
    result = review_open_pulls(
        issues,
        EventSink(None),
        repo=SUITE,
        bot_login=BOT,
        board=board,
        cards=cards,
        only_cards=True,
    )
    return result, board


APPROVAL = ReviewVerdict(summary="Sound.", approve=True)
REFUSAL = ReviewVerdict(
    summary="Not yet.",
    approve=False,
    findings=[
        Finding(
            file="crew-presentation/test_x.py",
            concern="asserts nothing",
            action="assert the page's title",
        )
    ],
)


# --- what the Task is judged against ------------------------------------------------


def test_the_task_is_judged_on_its_own_criteria_not_the_storys_it_quotes():
    criteria = story_criteria(Issues(), task(), TICK)
    assert "Each criterion of mqucifer/crew-presentation#215 your tests can reach" in criteria
    assert "to read and never to change" in criteria
    assert "no imports of crew-presentation's code" in criteria
    # The two findings of the review on mqucifer/qa-suite#17 came from these.
    assert "SUPPORTED_MAJOR_VERSIONS" not in criteria
    assert "Existing tests" not in criteria
    assert "**Given**" not in criteria


def test_the_story_is_kept_as_what_the_what_tests_cover():
    cover = what_tests_cover(Issues(), task(), TICK)
    assert "**Given** a delivery-history package" in cover
    assert "## Done when" not in cover
    assert "Add a test of the story's acceptance criteria" not in cover


def test_a_story_has_only_its_own_criteria():
    class Story(Issues):
        def get(self, repo, number):
            return {"body": "Intro.\n\n## Acceptance criteria\n\n1. It works\n\n**Estimate** — 1"}

    card = Card(
        item_id="S",
        number=16,
        title="A story",
        status="Reviewing",
        state="OPEN",
        work_type="Story",
        repo=TICK,
    )
    assert story_criteria(Story(), card, TICK) == "1. It works"
    assert what_tests_cover(Story(), card, TICK) == ""


def test_a_task_with_nothing_quoted_still_has_its_own_criteria():
    """A dispute Task quotes no story."""

    class Dispute(Issues):
        def get(self, repo, number):
            return {
                "body": "## Why\n\nWhy.\n\n## The work\n\nDecide.\n\n## Done when\n\n- [ ] Fixed\n"
            }

    criteria = story_criteria(Dispute(), task(), TICK)
    assert "- [ ] Fixed" in criteria and "Decide." in criteria
    assert what_tests_cover(Dispute(), task(), TICK) == ""


def test_the_reviewer_is_told_the_story_is_not_this_pull_requests_criteria(monkeypatch):
    issues = Issues()
    seen: dict = {}
    _result, board = review(monkeypatch, issues, [task()], APPROVAL, seen)
    assert "SUPPORTED_MAJOR_VERSIONS" not in seen["acceptance_criteria"]
    assert "SUPPORTED_MAJOR_VERSIONS" in seen["tests_cover"]
    assert ("T16", "QAing") in board.moves


def test_the_prompt_labels_the_story_as_what_the_tests_cover(monkeypatch):
    asked: dict = {}

    def ask(role, parts, *args, **kwargs):
        asked["text"] = parts[0].text
        return APPROVAL

    monkeypatch.setattr(review_crew.calls, "ask", ask)
    review_crew.review_diff(
        TITLE,
        "diff --git a/x b/x\n+x\n",
        acceptance_criteria="- [ ] the Task's own",
        tests_cover="**Given** the story's",
    )
    text = asked["text"]
    own = text.index("## Acceptance criteria this must satisfy")
    cover = text.index("## What the tests should cover")
    assert own < text.index("the Task's own") < cover < text.index("the story's")
    assert "`conflicts_with`" not in text


def test_a_story_review_has_no_such_section(monkeypatch):
    asked: dict = {}

    def ask(role, parts, *args, **kwargs):
        asked["text"] = parts[0].text
        return APPROVAL

    monkeypatch.setattr(review_crew.calls, "ask", ask)
    review_crew.review_diff(TITLE, "diff --git a/x b/x\n+x\n", acceptance_criteria="1. It works")
    assert "## What the tests should cover" not in asked["text"]
    assert "`conflicts_with`" in asked["text"]


def test_a_tasks_review_says_it_was_judged_as_a_task(monkeypatch):
    issues = Issues()
    review(monkeypatch, issues, [task()], REFUSAL)
    assert JUDGED_AS_TASK in issues.submitted[0][3]


def test_a_storys_review_does_not(monkeypatch):
    """Only QA's Tasks have a story's criteria to be mistaken for their own."""

    class Story(Issues):
        def get(self, repo, number):
            return {"body": "## Acceptance criteria\n\n1. It works"}

    card = Card(
        item_id="T16",
        number=16,
        title=TITLE,
        status="Reviewing",
        state="OPEN",
        work_type="Story",
        repo=SUITE,
    )
    issues = Story()
    review(monkeypatch, issues, [card], REFUSAL)
    assert JUDGED_AS_TASK not in issues.submitted[0][3]


# --- finding the card's pull request ------------------------------------------------


def test_a_card_whose_open_pull_request_is_in_its_own_repo_is_not_stranded():
    """Card 16 at 18:34Z: In Progress, with mqucifer/qa-suite#17 open, in a tick on
    crew-presentation."""
    board = Board()
    recovered = reconcile_orphans(
        board, Issues(), EventSink(None), [task("In Progress")], repo=TICK
    )
    assert recovered == []
    assert board.moves == [("T16", "Reviewing")]


def test_a_card_refused_in_its_own_repo_waits_for_its_rework():
    board = Board()
    issues = Issues([verdict("CHANGES_REQUESTED", as_task=True)])
    assert reconcile_orphans(board, issues, EventSink(None), [task("In Progress")], repo=TICK) == []
    assert board.moves == []


def test_a_card_with_no_pull_request_in_its_own_repo_is_still_stranded():
    class NoPull(Issues):
        def open_pulls(self, repo):
            return []

    board = Board()
    assert reconcile_orphans(
        board, NoPull(), EventSink(None), [task("In Progress")], repo=TICK
    ) == [16]
    assert board.moves == [("T16", "Sprint Backlog")]


def test_each_repository_is_listed_once_a_pass():
    class Counting(Issues):
        listed: list[str] = []

        def open_pulls(self, repo):
            self.listed.append(repo)
            return super().open_pulls(repo)

    other = Card(
        item_id="T2",
        number=2,
        title="Another",
        status="In Progress",
        state="OPEN",
        work_type="Task",
        repo=SUITE,
    )
    issues = Counting()
    reconcile_orphans(Board(), issues, EventSink(None), [task("In Progress"), other], repo=TICK)
    assert issues.listed.count(SUITE) == 1
    assert TICK not in issues.listed


def _deliver(monkeypatch, outcome, issues, board):
    monkeypatch.setattr(session_delivery, "deliver_by_session", lambda *a, **k: outcome)
    ws = SimpleNamespace(
        for_repo=lambda repo: ws,
        diff_if_open=lambda: None,
        close=lambda: None,
        forget=lambda branch: None,
    )
    _work_one_card(
        task("In Progress"),
        board=board,
        issues=issues,
        sink=EventSink(None),
        ws=ws,
        result=DeliveryResult(),
        counts={"In Progress": 1},
        sprint="Sprint 37",
        repo=TICK,
        default_branch="main",
    )


@pytest.mark.parametrize(
    "outcome",
    [
        DeliveryOutcome(card=16, branch=BRANCH, pr=17),
        DeliveryOutcome(
            card=16,
            branch=BRANCH,
            blocked_rule=Rule.OPEN_PULL_REQUEST,
            blocked_reason="PR #17 is still open",
        ),
    ],
    ids=["delivered", "blocked"],
)
def test_delivery_writes_on_the_cards_own_issue(monkeypatch, outcome):
    """Card 16's block landed on mqucifer/crew-presentation#16, an old closed issue."""
    issues = Issues()
    _deliver(monkeypatch, outcome, issues, Board())
    assert {repo for repo, _n in issues.comments_posted} == {SUITE}
    assert {repo for repo, _n, _l in issues.labels} <= {SUITE}


# --- a refusal made against the story's criteria (cards 16 and 18) ------------------


def test_a_refusal_made_against_the_story_is_not_work_for_qa():
    issues = Issues([MISJUDGED])
    assert awaiting_rework(issues, SUITE, BRANCH, task("In Progress")) is None
    assert needs_rework(issues, [task("In Progress")], repo=TICK) == []


def test_a_refusal_made_against_the_task_is():
    issues = Issues([verdict("CHANGES_REQUESTED", as_task=True)])
    assert awaiting_rework(issues, SUITE, BRANCH, task("In Progress")) is not None


def test_another_cards_refusal_without_the_mark_still_counts():
    """The mark is QA's Tasks' alone: a story's review never carries it."""
    story = Card(
        item_id="S",
        number=16,
        title=TITLE,
        status="In Progress",
        state="OPEN",
        work_type="Story",
        repo=SUITE,
    )
    assert awaiting_rework(Issues([MISJUDGED]), SUITE, BRANCH, story) is not None


@pytest.mark.parametrize("status", ["Blocked", "In Progress", "Sprint Backlog"])
def test_a_misjudged_task_is_reviewed_again_and_goes_on(monkeypatch, status):
    """Card 16, wherever the wrong review and the stranded check left it."""
    issues = Issues([MISJUDGED])
    result, board = review(monkeypatch, issues, [task(status)], APPROVAL)
    assert board.moves == [("T16", "Reviewing"), ("T16", "QAing")]
    assert [s[2] for s in issues.submitted] == ["APPROVE"]
    assert result.reviewed[0].approved


def test_a_misjudged_task_still_in_review_is_reviewed_again(monkeypatch):
    issues = Issues([MISJUDGED])
    _result, board = review(monkeypatch, issues, [task()], APPROVAL)
    assert board.moves == [("T16", "QAing")]


def test_a_misjudged_tasks_blocked_label_goes_when_it_has_one(monkeypatch):
    labelled = Card(**{**task("Blocked").__dict__, "labels": task().labels | {"blocked"}})
    issues = Issues([MISJUDGED])
    review(monkeypatch, issues, [labelled], APPROVAL)
    assert (SUITE, 16, ["-blocked"]) in issues.labels


def test_a_refusal_at_a_task_with_a_real_review_isnt_reviewed_again(monkeypatch):
    issues = Issues([verdict("CHANGES_REQUESTED", as_task=True)])
    _result, board = review(monkeypatch, issues, [task("In Progress")], APPROVAL)
    assert board.moves == []
    assert issues.submitted == []


def test_a_blocked_task_with_no_misjudged_review_stays_blocked(monkeypatch):
    issues = Issues()
    _result, board = review(monkeypatch, issues, [task("Blocked")], APPROVAL)
    assert board.moves == []
    assert issues.submitted == []


def test_an_approval_from_before_stands(monkeypatch):
    """Only a refusal stopped anything. An approval made it on, whatever it read."""
    issues = Issues([verdict("APPROVED")])
    _result, board = review(monkeypatch, issues, [task()], APPROVAL)
    assert board.moves == [("T16", "QAing")]
    assert issues.submitted == []


def test_a_refusal_of_an_earlier_head_moves_nothing(monkeypatch):
    issues = Issues([verdict("CHANGES_REQUESTED", commit="0" * 40)])
    _result, board = review(monkeypatch, issues, [task("Blocked")], APPROVAL)
    assert board.moves == []


def test_a_done_when_in_the_quoted_story_isnt_the_tasks():
    body = (
        "## The work\n\nAdd tests.\n\n## The story as it merged\n\n**A story**\n\n"
        "## Done when\n\n- the story's\n\n## Done when\n\n- [ ] the Task's"
    )
    own, quoted = qa_tasks.own_and_quoted(body)
    assert own.startswith("- [ ] the Task's") and "the story's" not in own
    assert "the story's" in quoted and "the Task's" not in quoted
