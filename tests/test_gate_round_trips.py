"""A story the gates keep returning goes back to refinement, not round again (#242).

sprint-metrics#145 went round six times in 45 minutes: QA wanted the test to
assert stdout was empty, as its criterion said; the reviewer wanted that
assertion gone, as the epic's design note said.
"""

from __future__ import annotations

from crew_org.events import EventSink
from crew_org.flows import delivery
from crew_org.flows.board_flow import NEEDS_REWORK, STORY_PROBLEM_MARKER
from crew_org.flows.delivery import DeliveryResult
from crew_org.flows.story_problem import MAX_ROUND_TRIPS, round_trips
from crew_org.tools.github_project import Card

REPO = "sprint-metrics"
QA_RETURN = (
    "<!-- crew:qa -->\n## QA — not accepted\n\n- **not proven** — Given only sprint 2024-02 "
    "… Then the exit code is 2 … and stdout is empty"
)
REVIEW = (
    'summary\n\n## Findings\n\n**`tests/test_prior_sprint.py`** — asserts captured.out == ""; '
    "the design note says not to.\n→ Remove the line.\n\n<!-- crew:by Code Reviewer -->"
)


def story() -> Card:
    return Card(
        item_id="S145",
        number=145,
        title="Prior sprint in JSON",
        repo=REPO,
        status="In Progress",
        state="OPEN",
        work_type="Story",
        parent=59,
        sprint="Sprint 7",
    )


class Issues:
    def __init__(self, deliveries: int):
        self.story = [
            {"body": f"Implemented in #164 on `b`. Lint and tests pass. ({i})"}
            for i in range(deliveries)
        ]
        self.story.append({"body": QA_RETURN})
        self.closed_pulls, self.pr_comments, self.epic_comments, self.labels = [], [], [], []

    def comments(self, repo, number):
        return self.story if number == 145 else []

    def pull_for_branch(self, repo, branch, *, known=None):
        return {"number": 164, "head": {"sha": "abc"}}

    def pull_reviews(self, repo, number):
        return [{"state": "CHANGES_REQUESTED", "body": REVIEW}, {"state": "APPROVED", "body": "ok"}]

    def sub_issues(self, repo, number):
        return [{"number": 145}, {"number": 146}]

    def branches(self, repo):
        return [{"name": "feat/145-prior-sprint"}]

    def ensure_label(self, repo, name, *, color, description):
        pass

    def add_labels(self, repo, number, labels):
        self.labels += [(number, label) for label in labels]

    def comment(self, repo, number, body):
        (self.epic_comments if number == 59 else self.pr_comments).append(body)
        return {}

    def close_pull(self, repo, number):
        self.closed_pulls.append(number)


class Board:
    def __init__(self):
        self.status = {}

    def cards(self):
        return [
            story(),
            Card(
                item_id="S146",
                number=146,
                repo=REPO,
                status="Sprint Backlog",
                state="OPEN",
                work_type="Story",
            ),
        ]

    def set_status(self, item, to):
        self.status[item] = to

    def set_owner_agent(self, item, by):
        pass

    def clear_field(self, item, field):
        pass


def gates_disagree(issues, board=None):
    result = DeliveryResult()
    went = delivery._gates_disagree(
        story(),
        board=board or Board(),
        issues=issues,
        sink=EventSink(None),
        repo=REPO,
        result=result,
    )
    return went, result


def test_deliveries_are_counted_from_the_developers_own_comments():
    assert round_trips(Issues(deliveries=6), REPO, 145) == 6


def test_under_the_limit_the_story_is_repaired_as_usual():
    went, result = gates_disagree(Issues(deliveries=MAX_ROUND_TRIPS - 1))
    assert not went and result.returned == []


def test_past_the_limit_it_goes_back_with_both_gates_word_and_its_pr_closed():
    issues, board = Issues(deliveries=6), Board()
    went, result = gates_disagree(issues, board)
    assert went and result.returned == [(145, 59)]
    assert board.status == {"S145": "Ready", "S146": "Ready"}
    (evidence,) = issues.epic_comments
    assert evidence.startswith(STORY_PROBLEM_MARKER) and "delivered 6 times" in evidence
    assert "stdout is empty" in evidence, "QA's side"
    assert "the design note says not to" in evidence, "the reviewer's side"
    assert (59, NEEDS_REWORK) in issues.labels
    assert issues.closed_pulls == [164] and "went back to refinement" in issues.pr_comments[0]
