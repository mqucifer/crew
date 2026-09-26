"""The Product Owner answers the question a story problem raises (#189, criterion 3).

sprint-metrics#97's question, opt-in or default flags, was answerable from
merged work: #100 and #102 already flagged opt-in via --thresholds. It took the
Sponsor to settle it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.crews.refinement_crew import ProductAnswer
from crew_org.events import EventSink
from crew_org.flows import board_flow
from crew_org.flows.board_flow import (
    NEEDS_HUMAN,
    NEEDS_REWORK,
    PRODUCT_ANSWER_MARKER,
    PRODUCT_QUESTION_MARKER,
    STORY_PROBLEM_MARKER,
    STORY_SPLIT_MARKER,
    TickResult,
    product_step,
    story_problem_evidence,
)
from crew_org.tools.github_project import Card

REPO = "sprint-metrics"
EVIDENCE = f"{STORY_PROBLEM_MARKER}\n#97 broke 25 merged tests pinning the default table"


def epic(labels=(NEEDS_REWORK,)) -> Card:
    return Card(item_id="E54", number=54, title="Flags", repo=REPO, labels=frozenset(labels))


class Issues:
    def __init__(self, *bodies):
        self.bodies = list(bodies)
        self.posted, self.added = [], []

    def comments(self, repo, number):
        return [{"body": b} for b in self.bodies]

    def get(self, repo, number):
        return {"body": "epic body"}

    def comment(self, repo, number, body):
        self.posted.append(body)
        self.bodies.append(body)
        return {}

    def add_labels(self, repo, number, labels):
        self.added += labels


def step(issues, monkeypatch, reply=None, card=None):
    calls = []

    def product_owner(**kw):
        calls.append(kw)
        return reply

    monkeypatch.setattr(board_flow, "answer_story_problem", product_owner)
    result = TickResult()
    go = product_step(
        issues,
        EventSink(),
        result,
        card or epic(),
        REPO,
        goal="Goal: the tool flags",
        project="record",
        delivered="- #100 opt-in flags",
    )
    return go, result, calls


def test_an_answer_the_project_has_is_given_and_the_split_goes_ahead(monkeypatch):
    issues = Issues(f"{STORY_SPLIT_MARKER}", EVIDENCE)
    reply = ProductAnswer(
        answer="Flags are opt-in via --thresholds; the default table is unchanged.",
        based_on=["#100 made markdown flags opt-in", "#102 added --thresholds"],
    )
    go, _, calls = step(issues, monkeypatch, reply)
    assert go
    assert "#97 broke 25 merged tests" in calls[0]["evidence"]
    assert "#100 opt-in flags" in calls[0]["delivered"] and calls[0]["goal"].startswith("Goal")
    (posted,) = issues.posted
    assert posted.startswith(PRODUCT_ANSWER_MARKER) and "Following: #100 made" in posted
    assert "opt-in via --thresholds" in story_problem_evidence(issues, REPO, 54), (
        "the re-split reads the answer with the evidence"
    )


def test_when_nothing_written_answers_it_the_sponsor_gets_one_question(monkeypatch):
    issues = Issues(f"{STORY_SPLIT_MARKER}", EVIDENCE)
    reply = ProductAnswer(
        question="Should flags show on the default table, or only with --thresholds?"
    )
    go, result, _ = step(issues, monkeypatch, reply)
    assert not go
    assert issues.posted[0].startswith(PRODUCT_QUESTION_MARKER)
    assert NEEDS_HUMAN in issues.added
    assert result.skipped == [(54, "waits for the Sponsor's answer on the epic")]


def test_an_open_question_waits_without_asking_again(monkeypatch):
    issues = Issues(f"{STORY_SPLIT_MARKER}", EVIDENCE, f"{PRODUCT_QUESTION_MARKER}\nopt-in?")
    go, result, calls = step(issues, monkeypatch)
    assert not go and calls == [] and result.skipped


def test_the_sponsors_reply_lets_the_split_go_ahead(monkeypatch):
    issues = Issues(
        f"{STORY_SPLIT_MARKER}", EVIDENCE, f"{PRODUCT_QUESTION_MARKER}\nopt-in?", "Opt-in, please."
    )
    go, _, calls = step(issues, monkeypatch)
    assert go and calls == []


def test_a_sponsors_own_rework_isnt_the_product_owners(monkeypatch):
    go, _, calls = step(Issues(f"{STORY_SPLIT_MARKER}", "Split it smaller."), monkeypatch)
    assert go and calls == []


def test_an_epic_not_sent_back_is_untouched(monkeypatch):
    go, _, calls = step(Issues(EVIDENCE), monkeypatch, card=epic(labels=()))
    assert go and calls == []


def test_an_answer_names_what_it_follows_and_is_never_also_a_question():
    with pytest.raises(ValidationError, match="names what it follows"):
        ProductAnswer(answer="Opt-in.")
    with pytest.raises(ValidationError, match="not both"):
        ProductAnswer(answer="Opt-in.", based_on=["#100"], question="Or not?")
    with pytest.raises(ValidationError, match="not both"):
        ProductAnswer()
