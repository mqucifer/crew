"""Where a Developer session's ending sends its story (crew#778, ADR 0032, decision 9).

A missing file or fact, or a stop, goes back to the Business Analyst who wrote the
story by the path a story takes back to refinement, with the session's evidence. A
choice goes to the Sponsor as one reaches a person today: blocked, saying so. `done`
goes to the gates, and from there to review, QA, a new session or the ruling.

Work leaving for a rewrite is kept first, and its note links it, saying what happened
in plain words (sprint-metrics#662's stop gave the backstop as what's missing).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from crew_org.events import EventKind, EventSink
from crew_org.flows import delivery, gates, session_end
from crew_org.flows.board_flow import NEEDS_REWORK, STORY_PROBLEM_MARKER
from crew_org.rules import RETURN_RULES, Rule
from crew_org.session.conversation import Ending
from crew_org.tools.github_project import Card
from crew_org.tools.workspace import CheckResult, CommandResult

REPO = "sprint-metrics"
CAPPED = "tests/test_metrics.py::test_rate_is_capped"


EVIDENCE = {
    "last_run": {
        "what": "tests",
        "ok": False,
        "first_error": "AssertionError: assert '0.5 days' in text",
        "failing": ["tests/test_browser.py::test_stats_row"],
    },
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
    "last_words": "Now let me run the full suite to confirm it's green:",
}
GREEN = {"what": "suite", "ok": True, "first_error": "", "failing": []}
STORY = (
    "**Existing tests** — this story changes what they assert: "
    "tests/test_docs.py::test_drift_check: the docs say uncapped."
)


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
    assert (
        "**Its last run:** `run tests` failed: AssertionError: assert '0.5 days' in text "
        "(failing: `tests/test_browser.py::test_stats_row`)." in comment
    )
    assert "the story has to give what's missing" in comment
    assert "Its last words" not in comment, "a `blocked` says it in its own words"
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
    comment = issues.posted[0][1]
    assert (
        "its Developer session was stopped by a ceiling: 60 model calls in a row with no "
        "edit and no run, at a limit of 60.**" in comment
    )
    assert '"ceiling"' not in comment and "{" not in comment, "never raw JSON"
    assert "> Now let me run the full suite to confirm it's green:" in comment
    assert "what's missing" not in comment, "a stop isn't the story's to supply"
    [returned] = [e for e in seen if e.kind is EventKind.STORY_RETURNED]
    assert returned.detail["stop"] == "ceiling"


def test_the_gates_same_finding_again_goes_back_as_a_stop_named_for_the_measures():
    """crew#790: a stop between sessions, by the path a stopped session takes."""
    finding = gates.Finding(
        "check.delivery.lint_or_tests_failed",
        "Lint or tests failed:\n\n$ uv run ruff check .\n(exit 2)\n"
        "error: Request failed after 3 retries",
    )
    ending = Ending(
        "blocked",
        {"missing": "the gates' same findings, sent back 3 times in a row", "kind": "stop"},
        "gate_repeat",
        evidence=dict(EVIDENCE, stop={"repeats": 3}),
    )
    routed, _, issues, seen = route(
        ending, leaving=leaving(None, findings=[[finding]] * 3, sessions=3)
    )
    assert routed.to == session_end.REFINEMENT
    comment = issues.posted[0][1]
    assert (
        "stopped by the gates at `done`, which sent back the same finding 3 times in a row.**"
        in comment
    )
    assert "> (exit 2)\n> error: Request failed after 3 retries" in comment
    assert "For the rewrite" not in comment
    [returned] = [e for e in seen if e.kind is EventKind.STORY_RETURNED]
    assert (returned.detail["kind"], returned.detail["stop"]) == ("stop", "gate_repeat")


def test_replies_that_call_no_tool_end_on_the_last_one_as_its_last_words():
    evidence = dict(
        EVIDENCE,
        stop={"replies": ["I think the criterion is wrong.", "Still."]},
        last_words="Still.",
    )
    ending = Ending(
        "blocked", {"missing": "3 replies", "kind": "stop"}, "no_tool_call", evidence=evidence
    )
    _, _, issues, _ = route(ending)
    assert "stopped by 3 replies in a row that called no tool.**" in issues.posted[0][1]
    assert "**Its last words:**\n\n> Still." in issues.posted[0][1]
    assert "For the rewrite" not in issues.posted[0][1]


def kept(tests: dict[str, str] | None = None) -> session_end.Kept:
    return session_end.Kept(
        "crew/kept/124-20261018-1745",
        "https://github.com/mqucifer/sprint-metrics/compare/abc...crew/kept/124-20261018-1745",
        files=["`src/rate.py` (changed)", "`tests/test_rate.py` (new)"],
        tests=tests or {},
        unproven=True,
    )


DRIFT = "tests/test_docs.py::test_drift_check"


def leaving(held: session_end.Kept | None, **kw) -> session_end.Leaving:
    return session_end.Leaving(lambda: held, **kw)


def note(stop: str, seen: dict, *, held=None, run=GREEN, **kw) -> str:
    ending = Ending(
        "blocked",
        {"missing": "...", "kind": "stop"},
        stop,
        evidence=dict(EVIDENCE, last_run=run, stop=seen),
    )
    return session_end.returned_note(
        ending, card=story(), story=STORY, leaving=leaving(held, **kw), kept=held
    )


def test_a_backstop_with_a_green_run_says_so_with_the_findings_and_links_the_kept_work():
    findings = [gates.Finding("a.rule", "These merged tests changed: `x`.\n\nAs merged:\n\nbig")]
    held = kept({DRIFT: "changed"})
    said = note(
        "backstop",
        {"model_minutes": 114},
        held=held,
        model_s=7340.0,
        sessions=2,
        findings=[findings],
    )
    assert (
        "stopped by the backstop: 122 minutes of model time over 2 sessions, the delivery's "
        "3 hours.**" in said
    )
    assert "**Its last run:** `run suite`, green." in said
    assert "- At `done` 1: These merged tests changed: `x`." in said and "big" not in said
    assert "**What it changed:** 2 files: `src/rate.py` (changed), `tests/test_rate.py` (new)." in (
        said
    )
    assert (
        "**Merged tests it changed:** 1. The story's Existing tests line names 1: "
        "`tests/test_docs.py`: `test_drift_check`." in said
    )
    assert "> Now let me run the full suite to confirm it's green:" in said
    assert f"on [`{held.branch}`]({held.link}), with no pull request." in said
    assert "which no run proved" in said
    assert "For the rewrite" not in said, "a backstop on a green run doesn't point at the story"


def test_merged_tests_the_story_doesn_t_name_are_called_out_for_the_rewrite():
    held = kept(
        {
            DRIFT: "changed",
            "tests/test_report.py::test_table": "changed",
            "tests/test_report.py::test_old": "removed",
        }
    )
    said = note("backstop", {"model_minutes": 180}, held=held, model_s=10800.0)
    assert "**2 merged tests the story doesn't name**, beyond its scope:" in said
    assert "- `tests/test_report.py`: `test_old` (removed), `test_table`" in said
    assert said.endswith(
        "**For the rewrite:** It changed 2 merged tests the Existing tests line doesn't name: "
        "say whether this behaviour is opt-in, or a contract change that names them."
    )
    from crew_org.flows.story_problem import _LISTED

    assert not _LISTED.findall(said), "a stop's tests aren't pinned on the epic's record"


def test_no_progress_says_the_test_and_its_error_and_points_at_the_story():
    failed = {
        "what": "tests",
        "ok": False,
        "first_error": "E   assert 100 == 200",
        "failing": ["tests/test_rate.py::test_cap"],
    }
    said = note(
        "no_progress",
        {"first_error": "E   assert 100 == 200", "test": "tests/test_rate.py::test_cap"},
        run=failed,
    )
    assert (
        "stopped by no progress: the same first error in `tests/test_rate.py::test_cap` "
        "after 3 runs, with edits between.**" in said
    )
    assert "**Its last run:** `run tests` failed: E   assert 100 == 200" in said
    assert "**Nothing was kept:** its branch was no different from main." in said
    assert "**For the rewrite:** `tests/test_rate.py::test_cap` failed the same way" in said


def test_a_ceiling_on_one_file_says_which_and_points_at_the_story():
    said = note("ceiling", {"ceiling": "re_viewing", "file": "src/rate.py", "views": 50})
    assert (
        "stopped by a ceiling: 50 views of `src/rate.py` with no edit to it between, at a "
        "limit of 50.**" in said
    )
    assert "**For the rewrite:** It read without acting until a ceiling stopped it" in said


def test_the_work_is_kept_before_the_note_is_written_and_a_failed_keep_writes_nothing():
    posted_when_kept: list[int] = []
    issues = Issues()

    def keep():
        posted_when_kept.append(len(issues.posted))
        return kept()

    ending = Ending("blocked", {"missing": "a fact", "kind": "fact"}, evidence=EVIDENCE)
    route(ending, issues=issues, leaving=session_end.Leaving(keep))
    assert posted_when_kept == [0] and "crew/kept/124-20261018-1745" in issues.posted[0][1]

    def refused():
        raise session_end.NotKept("crew/kept/124-x", "hook declined")

    board, issues = Board([story()]), Issues()
    with pytest.raises(session_end.NotKept):
        route(ending, board=board, issues=issues, leaving=session_end.Leaving(refused))
    assert not board.status and not issues.posted, "nothing moved or said"


def test_a_choice_for_the_sponsor_links_the_kept_work_too():
    ending = Ending("blocked", {"missing": "which cap", "kind": "choice"}, evidence=EVIDENCE)
    _, _, issues, _ = route(ending, leaving=leaving(kept()))
    assert "crew/kept/124-20261018-1745" in issues.posted[0][1]


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
    routed, board, issues, seen = route(Ending("done", SAID))
    assert routed.to == session_end.REVIEW and not board.status and not issues.posted
    assert f"- `{CAPPED}`: uncapped" in routed.sections, "for the pull request"
    [checked] = [e for e in seen if e.kind is EventKind.GATE_CHECKED]
    assert checked.detail == {
        "repo": REPO,
        "passed": True,
        "rules": [],
        "already_done": False,
        "resetups": 0,
        "resetups_failed": 0,
        "turned_on": 0,
    }


def test_the_gates_count_setup_run_again_and_whether_it_passed(verdict):
    """Counts, no output (crew#814): the output is in the finding the session gets."""
    failed = CommandResult("uv sync --extra dev", 2, "error: Failed to fetch: dns error")
    verdict.verdict = gates.Verdict(check=CheckResult([failed], resetup=False))
    gates._checked(verdict.verdict)
    routed, _, _, seen = route(Ending("done", SAID))
    [checked] = [e for e in seen if e.kind is EventKind.GATE_CHECKED]
    assert (checked.detail["resetups"], checked.detail["resetups_failed"]) == (1, 1)
    assert routed.to == session_end.SESSION and "dns error" in routed.start


def test_done_already_proven_goes_to_qa(verdict):
    verdict.verdict = gates.Verdict(already_done=True)
    assert route(Ending("done", SAID))[0].to == session_end.QA


def test_findings_start_a_new_session_with_every_one_and_the_last_done(verdict):
    verdict.verdict = gates.Verdict(
        findings=[gates.Finding("a.rule", "First finding."), gates.Finding("b.rule", "Second.")]
    )
    routed, board, _, seen = route(Ending("done", SAID))
    assert routed.to == session_end.SESSION and routed.rule == "a.rule" and not board.status
    [checked] = [e for e in seen if e.kind is EventKind.GATE_CHECKED]
    assert checked.detail["rules"] == ["a.rule", "b.rule"] and not checked.detail["passed"]
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
    taken: list = []
    held = kept({CAPPED: "changed"})
    routed, board, issues, _ = route(
        Ending("done", SAID, evidence={"last_run": GREEN}),
        leaving=session_end.Leaving(lambda: taken.append(1) or held),
    )
    assert asked == [{CAPPED: "changed by the Developer, who says: uncapped"}]
    if decision == "amend":
        assert routed.to == session_end.REVIEW
        assert routed.verdict.declared == {"tests/test_metrics.py::test_rate_is_capped": "uncapped"}
    elif decision == "repair":
        assert routed.to == session_end.SESSION
        assert "The Business Analyst ruled that this story doesn't change" in routed.start
    else:
        assert routed.to == session_end.REFINEMENT and board.status == {"S124": "Ready"}
        comment = issues.posted[0][1]
        assert "- `tests/test_metrics.py::test_rate_is_capped`: uncapped" in comment
        assert taken == [1] and f"[`{held.branch}`]({held.link})" in comment
        assert "**Its last run:** `run suite`, green." in comment
    if decision != "return":
        assert taken == [], "work that stays in delivery isn't pushed aside"


def test_a_full_context_names_what_compacting_left_and_the_limit():
    evidence = dict(
        EVIDENCE, stop={"estimated_tokens": 140_000, "estimated_after": 131_000, "limit": 125_000}
    )
    ending = Ending(
        "blocked", {"missing": "full", "kind": "stop"}, "context_full", evidence=evidence
    )
    _, _, issues, _ = route(ending)
    assert (
        "stopped by a full context: compacting left about 131000 tokens, at or over the "
        "limit of 125000.**" in issues.posted[0][1]
    )
