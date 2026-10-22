"""A story whose problem is the story goes back to refinement by itself (#189).

sprint-metrics#97 broke the same ~25 merged tests, ones pinning the default
table's rows, on every attempt, and blocked. The story never said whether its
flags were opt-in or a change to the table's contract. Unblocking it took the
Sponsor three manual steps, which `return_to_refinement` now takes.
"""

from __future__ import annotations

from crew_org.events import EventSink
from crew_org.flows.board_flow import (
    NEEDS_REWORK,
    STORY_PROBLEM_MARKER,
    STORY_SPLIT_MARKER,
    story_problem_evidence,
)
from crew_org.flows.story_problem import return_to_refinement
from crew_org.tools.github_project import Card

REPO = "sprint-metrics"


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


class Board:
    def __init__(self):
        self.status, self.cleared = {}, []

    def repo_of(self, item_id):
        return None

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
        board,
        issues,
        EventSink(),
        cards[0],
        repo=REPO,
        cards=cards,
        comment="the evidence",
        reason="gate round trips",
    )
    assert sent
    assert board.status == {"S97": "Ready", "S98": "Ready", "S99": "Ready"}, "#100 has a branch"
    assert {item for item, field in board.cleared if field == "Sprint"} == {"S97", "S98", "S99"}
    assert NEEDS_REWORK in issues.labels and (54, NEEDS_REWORK) in issues.added
    assert issues.posted == [(54, issues.posted[0][1])] and "the evidence" in issues.posted[0][1]


def test_a_story_with_no_epic_is_not_sent_anywhere():
    assert not return_to_refinement(
        Board(),
        Issues(),
        EventSink(),
        story(parent=None),
        repo=REPO,
        cards=[],
        comment="x",
        reason="gate round trips",
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


def test_an_earlier_sibling_stays_in_its_sprint():
    """Only the stories after it build on it, so only they are replaced (crew#859)."""
    board, issues = Board(), Issues()
    cards = [story(97, status="Sprint Backlog"), story(98), story(99, status="Sprint Backlog")]
    return_to_refinement(
        board,
        issues,
        EventSink(),
        cards[1],
        repo=REPO,
        cards=cards,
        comment="the evidence",
        reason="gate round trips",
    )
    assert board.status == {"S98": "Ready", "S99": "Ready"}
