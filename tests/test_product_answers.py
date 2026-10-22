"""The Product Owner answers the question a story problem raises (#189, criterion 3).

sprint-metrics#97's question, opt-in or default flags, was answerable from
merged work: #100 and #102 already flagged opt-in via --thresholds. It took the
Sponsor to settle it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.crews.refinement_crew import AnswerRow, ProductAnswer
from crew_org.events import EventSink
from crew_org.flows import board_flow, record
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


RECORD = (
    "epic body\n\n## Refinement conclusion\n\n"
    "| ID | Status | Context | Decision | Consequences | Source |\n|---|---|---|---|---|---|\n"
    "| R1 | accepted | Flags | Shown with --thresholds | One flag column | #100 |\n"
)


def rows(*decisions, replaces=""):
    return [
        AnswerRow(
            context="Flags", decision=d, consequences="The table is unchanged", replaces=replaces
        )
        for d in decisions
    ] or [AnswerRow(context="Flags", decision="Opt-in", consequences="Unchanged table")]


class Issues:
    owner = "mqucifer"

    def __init__(self, *bodies):
        self.bodies = list(bodies)
        self.posted, self.added = [], []
        self.body = RECORD

    def comments(self, repo, number):
        return [{"body": b} for b in self.bodies]

    def get(self, repo, number):
        return {"body": self.body}

    def edit_issue(self, repo, number, *, body):
        self.body = body

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
        rows=rows("Flags only with --thresholds"),
        based_on=["#100 made markdown flags opt-in", "#102 added --thresholds"],
    )
    go, _, calls = step(issues, monkeypatch, reply)
    assert go
    assert "#97 broke 25 merged tests" in calls[0]["evidence"]
    assert "#100 opt-in flags" in calls[0]["delivered"] and calls[0]["goal"].startswith("Goal")
    (posted,) = issues.posted
    assert record.has_change([posted], "answer")
    assert PRODUCT_ANSWER_MARKER in posted and "Following: #100 made" in posted
    found = record.parse(record.split(issues.body)[1])
    assert found.row("R2").decision == "Flags only with --thresholds"
    assert found.row("R2").set_by == "Product Owner"
    assert story_problem_evidence(issues, REPO, 54).startswith("#97 broke 25 merged tests")
    assert "opt-in via --thresholds" not in story_problem_evidence(issues, REPO, 54), (
        "the re-split reads the answer in the record, not from the comments"
    )


def test_the_product_owner_is_shown_the_record_with_its_earlier_answers(monkeypatch):
    issues = Issues(f"{STORY_SPLIT_MARKER}", EVIDENCE)
    reply = ProductAnswer(answer="Opt-in.", rows=rows("Opt-in"), based_on=["#100"])
    _, _, calls = step(issues, monkeypatch, reply)
    assert "| R1 | accepted | Flags | Shown with --thresholds |" in calls[0]["epic"]


def test_a_row_the_record_refuses_is_asked_for_again_with_the_reason(monkeypatch):
    issues = Issues(f"{STORY_SPLIT_MARKER}", EVIDENCE)
    issues.body = (
        RECORD.replace("| R1 | accepted |", "| R1 | binding |")
        .replace("| #100 |", "| Architect | 2026-10-09 | — | #100 |")
        .replace(
            "| Source |\n|---|---|---|---|---|---|",
            "| Set by | Date | Replaces | Source |\n|---|---|---|---|---|---|---|---|---|",
        )
    )
    bad = ProductAnswer(answer="Opt-in.", rows=rows("Opt-in", replaces="R1"), based_on=["#100"])
    good = ProductAnswer(answer="Opt-in.", rows=rows("Opt-in"), based_on=["#100"])
    replies = [bad, good]
    calls = []

    def product_owner(**kw):
        calls.append(kw)
        return replies.pop(0)

    monkeypatch.setattr(board_flow, "answer_story_problem", product_owner)
    go = product_step(
        issues, EventSink(), TickResult(), epic(), REPO, goal="g", project="p", delivered="d"
    )
    assert go and len(calls) == 2
    assert "only its author or the Sponsor" in calls[1]["feedback"]
    assert record.parse(record.split(issues.body)[1]).row("R1").binding


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


def test_an_answer_names_the_story_whose_return_it_answers(monkeypatch):
    """So the retro pairs it with that return and no other (crew#583, step A6)."""
    returned = (
        f"{STORY_PROBLEM_MARKER}\n**#97 went back to refinement: its failures are the story's"
    )
    issues = Issues(f"{STORY_SPLIT_MARKER}", returned)
    reply = ProductAnswer(answer="Opt-in.", rows=rows("Opt-in"), based_on=["#100"])
    step(issues, monkeypatch, reply)
    assert "for mqucifer/sprint-metrics#97." in issues.posted[0]
    assert board_flow.returned_story(EVIDENCE) is None, "raised in refinement: no story"


# --- the stop: an epic's rounds are counted, and after the stop its problem is the
# Sponsor's (crew#859) -----------------------------------------------------------------


def rounds(n: int) -> list[str]:
    out = []
    for k in range(1, n + 1):
        out += [
            f"{STORY_PROBLEM_MARKER}\n**#{100 + k} went back to refinement: round {k}.**",
            f"{PRODUCT_ANSWER_MARKER}\n**Decided:** answer {k}\n\nFollowing: #100",
            f"{STORY_SPLIT_MARKER}\nsplit\n\n<!-- crew:resplit round={k} -->\nRound {k}.\n\n"
            f"Replaced, closed as not planned: #{100 + k}.",
        ]
    return out


def stopped_step(issues, monkeypatch, stop):
    calls = []
    monkeypatch.setattr(board_flow, "answer_story_problem", lambda **kw: calls.append(kw))
    sink = EventSink()
    seen = []
    sink.subscribe(seen.append)
    result = TickResult()
    go = product_step(
        issues, sink, result, epic(), REPO, goal="g", project="p", delivered="d", stop=stop
    )
    return go, result, calls, seen


def test_a_problem_after_the_third_round_is_the_sponsors_one_question(monkeypatch):
    issues = Issues(f"{STORY_SPLIT_MARKER}", *rounds(3), EVIDENCE)
    go, result, calls, seen = stopped_step(issues, monkeypatch, stop=3)
    assert not go and calls == []
    (question,) = issues.posted
    lines = question.splitlines()
    assert lines[0] == PRODUCT_QUESTION_MARKER
    assert lines[1].startswith("**Epic #54 has been split again 3 times")
    assert "#97 broke 25 merged tests" in lines[1]
    assert lines[2].startswith("**Your call:**")
    assert "<details>" in question and "**Round 2.** The problem: #102 went back" in question
    assert "- The Product Owner decided: answer 3" in question
    assert "- Replaced, closed as not planned: #103." in question
    assert NEEDS_HUMAN in issues.added
    assert result.skipped == [(54, "re-split 3 times: the Sponsor's to answer")]
    (event,) = [e for e in seen if e.kind == "epic.resplit_stopped"]
    assert event.detail["round"] == 3 and event.detail["stop"] == 3


def test_before_the_stop_the_product_owner_answers(monkeypatch):
    issues = Issues(f"{STORY_SPLIT_MARKER}", *rounds(2), EVIDENCE)
    reply = ProductAnswer(answer="Opt-in.", rows=rows("Opt-in"), based_on=["#100"])
    monkeypatch.setattr(board_flow, "answer_story_problem", lambda **kw: reply)
    go = product_step(
        issues,
        EventSink(),
        TickResult(),
        epic(),
        REPO,
        goal="g",
        project="p",
        delivered="d",
        stop=3,
    )
    assert go and not any(PRODUCT_QUESTION_MARKER in b for b in issues.posted)


def test_the_sponsors_reply_at_the_stop_lets_the_round_go_ahead(monkeypatch):
    issues = Issues(f"{STORY_SPLIT_MARKER}", *rounds(3), EVIDENCE)
    stopped_step(issues, monkeypatch, stop=3)
    issues.bodies.append("Drop the Prometheus line; keep the rest.")
    monkeypatch.setattr(board_flow, "from_sponsor", lambda issues, c: True)
    go, _, calls, _ = stopped_step(issues, monkeypatch, stop=3)
    assert go and calls == []


def test_the_rounds_are_counted_from_the_marked_splits():
    assert board_flow.resplit.rounds(rounds(5)) == 5
