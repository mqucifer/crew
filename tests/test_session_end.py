"""Where a Developer session's ending sends its story (crew#778, ADR 0032, decision 9).

A missing file or fact, or a stop, goes back to the Business Analyst who wrote the
story by the path a story takes back to refinement, with the session's evidence. A
choice goes to the Sponsor as one reaches a person today: blocked, saying so. `done`
goes to the gates, and from there to review, QA, a new session or the ruling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from crew_org.events import EventKind, EventSink
from crew_org.flows import delivery, gates, session_end
from crew_org.flows.board_flow import NEEDS_REWORK, STORY_PROBLEM_MARKER
from crew_org.rules import RETURN_RULES, Rule
from crew_org.tools.github_project import Card

REPO = "sprint-metrics"
CAPPED = "tests/test_metrics.py::test_rate_is_capped"


@dataclass
class Ending:
    """Stage C's `Ending`, by its fields."""

    how: str
    said: dict[str, Any] = field(default_factory=dict)
    stop: str = ""
    why: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


EVIDENCE = {
    "last_failure": {
        "what": "tests",
        "ok": False,
        "first_error": "AssertionError: assert '0.5 days' in text",
        "failing": ["tests/test_browser.py::test_stats_row"],
    },
    "tried": {
        "runs": 2,
        "files_read": ["site/css/site.css", "tests/test_browser.py"],
        "files_edited": ["site/css/site.css"],
        "tools_used": {"view": 30, "run": 2},
    },
    "signals": {
        "since_action": 12,
        "most_views": 9,
        "most_viewed": "tests/test_browser.py",
        "reasoning_tokens": 12865,
        "edits_since_run": 0,
        "empty_lookups_total": 3,
    },
}


def story(number=124, parent: int | None = 120) -> Card:
    return Card(
        item_id=f"S{number}",
        number=number,
        title="Reflow the stats row at phone width",
        repo=REPO,
        status="In Progress",
        state="OPEN",
        work_type="Story",
        parent=parent,
        sprint="Sprint 30",
    )


class Board:
    def __init__(self, cards=()):
        self.status, self.cleared, self._cards = {}, [], list(cards)

    def cards(self):
        return self._cards

    def repo_of(self, item_id):
        return None

    def set_status(self, item, to):
        self.status[item] = to

    def set_owner_agent(self, item, by):
        pass

    def clear_field(self, item, field):
        self.cleared.append((item, field))


class Issues:
    def __init__(self):
        self.labels, self.added, self.posted = set(), [], []

    def sub_issues(self, repo, number):
        return [{"number": 124}]

    def branches(self, repo):
        return []

    def ensure_label(self, repo, name, *, color, description):
        self.labels.add(name)

    def add_labels(self, repo, number, labels):
        self.added += [(number, label) for label in labels]

    def comment(self, repo, number, body):
        self.posted.append((number, body))
        return {}


def route(ending, card=None, **kw):
    seen: list = []
    sink = EventSink()
    sink.subscribe(seen.append)
    board, issues = kw.pop("board", Board([card or story()])), kw.pop("issues", Issues())
    routed = session_end.route(
        ending,
        card=card or story(),
        repo=REPO,
        worktree=Path("."),
        story="the story",
        record=None,
        board=board,
        issues=issues,
        sink=sink,
        **kw,
    )
    return routed, board, issues, seen


def test_a_missing_fact_goes_back_to_the_business_analyst_with_the_evidence():
    ending = Ending(
        "blocked",
        {"missing": "criterion 4 asks for '0.5 days'; the data has hours", "kind": "fact"},
        evidence=EVIDENCE,
    )
    routed, board, issues, seen = route(ending)
    assert routed.to == session_end.REFINEMENT
    assert routed.rule == Rule.RETURN_SESSION_BLOCKED
    assert board.status == {"S124": "Ready"} and ("S124", "Sprint") in board.cleared
    assert (120, NEEDS_REWORK) in issues.added
    [(epic, comment)] = issues.posted
    assert epic == 120 and comment.startswith(STORY_PROBLEM_MARKER)
    assert "on a fact the story doesn't give" in comment
    assert "> criterion 4 asks for '0.5 days'; the data has hours" in comment
    assert "AssertionError: assert '0.5 days' in text" in comment
    assert "Edited `site/css/site.css`" in comment and "12865 reasoning tokens" in comment
    [returned] = [e for e in seen if e.kind is EventKind.STORY_RETURNED]
    assert returned.detail["reason"] == "session blocked" and returned.detail["kind"] == "fact"
    assert RETURN_RULES[returned.detail["reason"]] == Rule.RETURN_SESSION_BLOCKED


def test_a_stop_goes_back_the_same_way_with_its_signals():
    evidence = dict(EVIDENCE, stop={"ceiling": "reading", "calls": 60})
    ending = Ending(
        "blocked", {"missing": "60 calls", "kind": "stop"}, "ceiling", evidence=evidence
    )
    routed, _, issues, seen = route(ending)
    assert routed.to == session_end.REFINEMENT
    assert "on a ceiling" in issues.posted[0][1]
    assert '**What stopped it:** `{"calls": 60, "ceiling": "reading"}`' in issues.posted[0][1]
    [returned] = [e for e in seen if e.kind is EventKind.STORY_RETURNED]
    assert returned.detail["stop"] == "ceiling"


def test_replies_that_call_no_tool_are_quoted_as_the_evidence():
    evidence = dict(EVIDENCE, stop={"replies": ["I think the criterion is wrong.", "Still."]})
    ending = Ending(
        "blocked", {"missing": "3 replies", "kind": "stop"}, "no_tool_call", evidence=evidence
    )
    _, _, issues, _ = route(ending)
    assert "3 replies in a row that called no tool" in issues.posted[0][1]
    assert "> I think the criterion is wrong." in issues.posted[0][1]


def test_a_choice_goes_to_the_sponsor_blocked_saying_so():
    ending = Ending(
        "blocked",
        {"missing": "the declared test contradicts all seven criteria", "kind": "choice"},
        evidence=EVIDENCE,
    )
    routed, board, issues, seen = route(ending)
    assert routed.to == session_end.BLOCKED_FOR_A_PERSON and routed.rule == Rule.SESSION_CHOICE
    assert board.status == {"S124": "Blocked"}
    assert (124, "blocked") in issues.added
    [(number, comment)] = issues.posted
    assert number == 124 and comment.startswith("**Blocked: a choice for the Sponsor.**")
    [blocked] = [e for e in seen if e.kind is EventKind.CARD_BLOCKED]
    assert blocked.detail["rule"] == Rule.SESSION_CHOICE


def test_a_story_with_no_epic_is_blocked_for_a_person_with_the_same_evidence():
    ending = Ending("blocked", {"missing": "a file", "kind": "file"}, evidence=EVIDENCE)
    routed, board, issues, _ = route(ending, card=story(parent=None))
    assert routed.rule == Rule.SESSION_NO_EPIC and board.status == {"S124": "Blocked"}
    assert STORY_PROBLEM_MARKER not in issues.posted[0][1]


def test_an_interrupted_session_isnt_routed():
    with pytest.raises(ValueError, match="resumes"):
        route(Ending("interrupted", why="workspace_gone"))


SAID = {
    "summary": "Removed the cap.",
    "criteria": [{"criterion": "200", "test": "tests/test_metrics.py::test_rate_exceeds_100"}],
    "changed_tests": [{"test": "tests/test_metrics.py::test_rate_is_capped", "why": "uncapped"}],
    "docs": "docs/metrics.md",
}


@pytest.fixture
def verdict(monkeypatch):
    """The gates' verdict, set by each test: the route is what's under test here."""
    holder = SimpleNamespace(verdict=gates.Verdict())
    monkeypatch.setattr(gates, "at_done", lambda *a, **k: holder.verdict)
    return holder


def test_done_through_every_gate_goes_on_to_review_with_its_sections(verdict):
    verdict.verdict = gates.Verdict(declared={CAPPED: "uncapped"})
    routed, board, issues, _ = route(Ending("done", SAID))
    assert routed.to == session_end.REVIEW and not board.status and not issues.posted
    assert f"- `{CAPPED}`: uncapped" in routed.sections, "for the pull request"


def test_done_already_proven_goes_to_qa(verdict):
    verdict.verdict = gates.Verdict(already_done=True)
    assert route(Ending("done", SAID))[0].to == session_end.QA


def test_findings_start_a_new_session_with_every_one_and_the_last_done(verdict):
    verdict.verdict = gates.Verdict(
        findings=[gates.Finding("a.rule", "First finding."), gates.Finding("b.rule", "Second.")]
    )
    routed, board, _, _ = route(Ending("done", SAID))
    assert routed.to == session_end.SESSION and routed.rule == "a.rule" and not board.status
    assert "### 1.\n\nFirst finding." in routed.start and "### 2.\n\nSecond." in routed.start
    assert "## The last `done`" in routed.start
    assert "- `tests/test_metrics.py::test_rate_is_capped`: uncapped" in routed.start


@pytest.mark.parametrize("decision", ["amend", "repair", "return"])
def test_the_ruling_decides_where_a_disagreement_goes(verdict, monkeypatch, decision):
    asked: list = []

    def rule_on_contract(issues, sink, **kw):
        asked.append(kw["failures"])
        return SimpleNamespace(decision=decision, why="Criterion 2 removes the cap.", tests=[])

    monkeypatch.setattr(delivery, "rule_on_contract", rule_on_contract)
    monkeypatch.setattr(session_end, "story_rows", lambda issues, card, repo: "")
    verdict.verdict = gates.Verdict(
        disagree={"tests/test_metrics.py::test_rate_is_capped": "uncapped"}
    )
    routed, board, issues, _ = route(Ending("done", SAID))
    assert asked == [{CAPPED: "changed by the Developer, who says: uncapped"}]
    if decision == "amend":
        assert routed.to == session_end.REVIEW
        assert routed.verdict.declared == {"tests/test_metrics.py::test_rate_is_capped": "uncapped"}
    elif decision == "repair":
        assert routed.to == session_end.SESSION
        assert "The Business Analyst ruled that this story doesn't change" in routed.start
    else:
        assert routed.to == session_end.REFINEMENT and board.status == {"S124": "Ready"}
        assert "- `tests/test_metrics.py::test_rate_is_capped`: uncapped" in issues.posted[0][1]
