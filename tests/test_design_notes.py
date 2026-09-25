"""An epic labelled needs:design gets its Architect's note before its stories (#155).

The case: sprint-metrics#50, four stories extending one report path to a range
of sprints, built with no shared approach. #73's rebuild absorbed #74's scope.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from crew_org.crews.design_crew import Conflict, DesignReview
from crew_org.crews.design_note_crew import DesignNote, StoryDirection, render
from crew_org.events import EventSink
from crew_org.flows.design_notes import (
    NOTE_MARKER,
    awaiting_design,
    note_for,
    story_note,
    write_notes,
)
from crew_org.flows.sprint import plan_sprint
from crew_org.project import RECORD_PATH
from crew_org.tools.github_project import Card
from tests.test_delivery_flow import harness  # noqa: F401
from tests.test_sprint import REPO, epic, parents, story

RECORD = (
    "version: 2\nintent:\n  scope:\n    purpose: Delivery metrics for the crew.\n"
    "  release:\n    deploys: false\n  done:\n    bar: tests pass\n"
)


def design_epic(number=50, labels=frozenset({"needs:design"})) -> Card:
    return epic(number).model_copy(update={"labels": labels, "title": "Report a range"})


def note(**overrides) -> DesignNote:
    params = dict(
        approach="Build a range model once; every format renders from it.",
        interfaces=["SprintRange: label -> Metrics", "format_sprint_range_table(range, ...)"],
        expensive_to_reverse=["The range model's shape: every format depends on it"],
        risks=["#74's WIP handling belongs in the model, not the table"],
        stories=[
            StoryDirection(story=73, direction="the range model and the table"),
            StoryDirection(story=74, direction="WIP limits and escalations, in the model"),
        ],
        looked_at=["src/sprint_metrics/crew_performance.py", "#73", "#74"],
    )
    params.update(overrides)
    return DesignNote(**params)


class Issues:
    """Comments per issue, sub-issues per epic, and what was labelled."""

    def __init__(self, comments=None, subs=None) -> None:
        self._comments = comments or {}
        self._subs = (
            subs
            if subs is not None
            else {50: [{"number": 73, "title": "Table"}, {"number": 74, "title": "WIP"}]}
        )
        self.posted: list[tuple[int, str]] = []
        self.labels: list[tuple[int, str]] = []

    def comments(self, repo, number):
        return self._comments.get(number, []) + [{"body": b} for n, b in self.posted if n == number]

    def comment(self, repo, number, body):
        self.posted.append((number, body))

    def sub_issues(self, repo, number):
        return self._subs.get(number, [])

    def get(self, repo, number):
        return {"body": "A user can ask for a range of past sprints."}

    def add_labels(self, repo, number, labels):
        self.labels += [(number, label) for label in labels]


class Clone:
    def __init__(self, root: Path) -> None:
        self.root = root

    def for_repo(self, repo):
        return self

    def current(self):
        (self.root / ".crew").mkdir(parents=True, exist_ok=True)
        (self.root / RECORD_PATH).write_text(RECORD)
        (self.root / "README.md").write_text("# sprint-metrics\n")
        return self.root


class Architect:
    def __init__(self, *notes) -> None:
        self.notes, self.calls = list(notes), []

    def __call__(self, **context):
        self.calls.append(context)
        return self.notes.pop(0)


class Reviewer:
    def __init__(self, *reviews) -> None:
        self.reviews = list(reviews)

    def __call__(self, **context):
        return self.reviews.pop(0) if self.reviews else DesignReview()


def run(tmp_path, issues, architect, reviewer=None, cards=None):
    return write_notes(
        issues,
        EventSink(None),
        Clone(tmp_path),
        cards if cards is not None else [design_epic()],
        default_repo=REPO,
        repos={REPO},
        write=architect,
        review=reviewer or Reviewer(),
        render=render,
    )


CONFLICT = DesignReview(
    conflicts=[Conflict(guideline="§19 rule 3", choice="add pandas", why="no dependency needed")]
)


# --- 1: the note is written, from the epic, its stories, the record and the code --------------


def test_an_epic_with_one_story_still_open_gets_its_note(tmp_path):
    issues = Issues(
        subs={
            50: [
                {"number": 73, "title": "Table", "state": "closed"},
                {"number": 74, "title": "WIP", "state": "open"},
            ]
        }
    )
    assert run(tmp_path, issues, Architect(note())).written == [50]


def test_an_epic_needing_design_gets_its_note_on_the_epic(tmp_path):
    issues, architect = Issues(), Architect(note())
    result = run(tmp_path, issues, architect)

    assert result.written == [50]
    ((number, body),) = issues.posted
    assert number == 50 and NOTE_MARKER in body
    assert "Build a range model once" in body and "- #74: WIP limits" in body
    assert "_Looked at: src/sprint_metrics/crew_performance.py" in body
    assert body.rstrip().endswith("— *Architect*")
    shown = architect.calls[0]
    assert "A user can ask for a range" in shown["epic"]
    assert "#73 Table" in shown["stories"] and "#74 WIP" in shown["stories"]
    assert "**Purpose:** Delivery metrics for the crew." in shown["project"]
    assert "README.md" in shown["repository"]


def test_a_note_gives_each_story_its_direction():
    with pytest.raises(ValidationError, match="gives each story its direction"):
        note(stories=[])


@pytest.mark.parametrize(
    "case, cards, issues",
    [
        ("already has a note", [design_epic()], Issues(comments={50: [{"body": NOTE_MARKER}]})),
        ("no stories split yet", [design_epic()], Issues(subs={})),
        (
            "every story already built",
            [design_epic()],
            Issues(
                subs={
                    50: [
                        {"number": 73, "title": "Table", "state": "closed"},
                        {"number": 74, "title": "WIP", "state": "closed"},
                    ]
                }
            ),
        ),
        ("not labelled needs:design", [design_epic(labels=frozenset())], Issues()),
        (
            "not the crew's repository",
            [design_epic().model_copy(update={"repo": "other"})],
            Issues(),
        ),
    ],
)
def test_no_note_is_written_when_none_is_due(tmp_path, case, cards, issues):
    architect = Architect()
    result = run(tmp_path, issues, architect, cards=cards)
    assert result.written == [] and architect.calls == [], case


# --- 2: checked against the guidelines by the Code Reviewer --------------------------------------


def test_a_note_that_conflicts_is_retried_with_the_guideline_named(tmp_path):
    issues, architect = Issues(), Architect(note(), note())
    result = run(tmp_path, issues, architect, Reviewer(CONFLICT))

    assert "§19 rule 3" in architect.calls[1]["feedback"]
    assert result.written == [50]


def test_a_note_still_in_conflict_blocks_the_epic_for_a_person(tmp_path):
    issues = Issues()
    result = run(tmp_path, issues, Architect(note(), note()), Reviewer(CONFLICT, CONFLICT))

    ((number, why),) = result.blocked
    assert number == 50 and "§19 rule 3" in why
    assert {(50, "blocked"), (50, "needs:human")} <= set(issues.labels)
    assert not any(NOTE_MARKER in body for _, body in issues.posted)


def test_a_decision_beyond_the_architect_goes_to_a_person_naming_it(tmp_path):
    issues = Issues()
    result = run(tmp_path, issues, Architect(note(beyond_reach="whether ranges span years")))
    assert "whether ranges span years" in result.blocked[0][1]


# --- 3: stories wait for the note --------------------------------------------------------------


def test_a_needs_design_epics_stories_wait_for_its_note():
    cards = [design_epic(), story(73, 5), story(74, 3), epic(49), story(70, 3)]
    plan = plan_sprint(
        cards,
        parents({73: 50, 74: 50, 70: 49}),
        sprint="S1",
        capacity=20,
        awaiting_design={(REPO, 50)},
    )
    assert [c.number for c in plan.admitted] == [70]
    assert [c.number for c in plan.waiting_on_design] == [73, 74]


def test_an_epic_waits_only_until_it_has_a_note():
    cards = [design_epic()]
    assert awaiting_design(Issues(), cards, REPO) == {(REPO, 50)}
    with_note = Issues(comments={50: [{"body": f"{NOTE_MARKER}\n## Design note"}]})
    assert awaiting_design(with_note, cards, REPO) == set()


# --- 4: the Developer and the Reviewer are shown it ----------------------------------------------


def test_a_story_finds_its_epics_note():
    issues = Issues(comments={50: [{"body": "first"}, {"body": f"{NOTE_MARKER}\nthe note"}]})
    child = story(74, 3).model_copy(update={"parent": 50})
    assert story_note(issues, child, REPO).endswith("the note")
    assert story_note(issues, story(99, 3), REPO) == ""
    assert note_for(issues, REPO, 50).endswith("the note")


def test_the_developer_is_shown_its_epics_note(harness, monkeypatch):  # noqa: F811
    from tests.test_delivery_flow import FakeIssues, green
    from tests.test_delivery_flow import story as delivery_story

    monkeypatch.setattr(
        FakeIssues,
        "comments",
        lambda self, repo, n: (
            [{"body": f"{NOTE_MARKER}\nBuild a range model once."}] if n == 50 else []
        ),
        raising=False,
    )
    child = delivery_story(6).model_copy(update={"parent": 50})
    _, _, _, _, calls, _ = harness(checks=[green()], cards=[child])

    context = calls["context"][0]
    assert "# The design note for this story's epic" in context
    assert "Build a range model once." in context


def test_the_reviewer_is_shown_the_stories_note(monkeypatch):
    from crew_org.flows import review as review_flow
    from tests.test_review import APPROVAL
    from tests.test_review import FakeIssues as ReviewIssues

    issues = ReviewIssues(
        [
            {
                "number": 88,
                "user": {"login": "crew[bot]"},
                "draft": False,
                "title": "t",
                "head": {"sha": "h", "ref": "feat/74-wip-limits"},
            }
        ]
    )
    issues.comments = lambda repo, n: [{"body": f"{NOTE_MARKER}\nBuild a range model once."}]
    shown = {}

    def reviewer(title, diff, **kwargs):
        shown.update(kwargs)
        return APPROVAL

    monkeypatch.setattr(review_flow, "review_diff", reviewer)
    reviewing = Card(
        item_id="S74",
        number=74,
        title="WIP limits",
        status="Reviewing",
        state="OPEN",
        work_type="Story",
        repo=REPO,
        parent=50,
    )

    class Board:
        """Takes the card's move after the review; only what it's shown is under test."""

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    review_flow.review_open_pulls(
        issues,
        EventSink(None),
        repo=REPO,
        bot_login="reviewer[bot]",
        board=Board(),
        cards=[reviewing],
    )
    assert "Build a range model once." in shown["design_note"]


# --- 5: nothing waits without the label ----------------------------------------------------------


def test_an_epic_without_needs_design_holds_nothing_back():
    cards = [epic(49), story(70, 3)]
    assert awaiting_design(Issues(), cards, REPO) == set()
    plan = plan_sprint(cards, parents({70: 49}), sprint="S1", capacity=20, awaiting_design=set())
    assert [c.number for c in plan.admitted] == [70] and plan.waiting_on_design == []
