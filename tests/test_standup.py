"""A standup every tick, recorded where the retro reads it (#79)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import yaml

import crew_org.flows.close as close_mod
from crew_org.config import load_org
from crew_org.crews.retro_crew import Retro
from crew_org.escalation import EscalationLedger
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows import standup as standup_mod
from crew_org.flows.close import close_sprint
from crew_org.flows.loop import LoopResult, PhaseOutcome
from crew_org.flows.standup import (
    MAX_STANDUP_CHARS,
    QUIET_MARKER,
    STANDUP_LABEL,
    aging_blocked,
    marker,
    record_standup,
    standups_for_retro,
    waiting_on_a_person,
    write_standup,
)
from crew_org.process import ProcessRules
from crew_org.tools.github_project import Card

SPRINT = "S5"
CREW = "crew"
AT = datetime(2026, 9, 23, 14, 5, tzinfo=UTC)


def run(*outcomes: PhaseOutcome, passes=1, settled=True, over=None) -> LoopResult:
    return LoopResult(
        passes=passes, outcomes=list(outcomes), settled=settled, over_limit=over or {}
    )


def standup(result, waiting=(), aging=()):
    return write_standup(result, sprint=SPRINT, at=AT, waiting=list(waiting), aging=list(aging))


def card(number, status, labels=frozenset(), state="OPEN") -> Card:
    return Card(
        item_id=f"I{number}",
        number=number,
        status=status,
        state=state,
        work_type="Story",
        repo="sprint-metrics",
        labels=labels,
    )


# --- what it says (criteria 1–3) --------------------------------------------------


def test_it_reports_what_moved_what_blocked_and_what_is_waiting():
    """Criterion 1."""
    s = standup(
        run(
            PhaseOutcome("deliver", moved=True, counts={"merged": 1, "delivered": 2}),
            PhaseOutcome("qa", blocked=["#31 — two criteria unproven"]),
            PhaseOutcome("admit", held=["#40 — no epic and no estimate"]),
        ),
        waiting=["#12"],
    )
    assert not s.quiet
    assert "**deliver**: 1 merged, 2 delivered" in s.text
    assert "**Blocked this tick**" in s.text and "#31 — two criteria unproven" in s.text
    assert "**Waiting**" in s.text and "#40 — no epic and no estimate" in s.text
    assert "**Waiting on a person**" in s.text and "- #12" in s.text


def test_a_card_blocked_past_the_threshold_is_named_with_how_long():
    """Criterion 2."""
    s = standup(run(), aging=[("#12", 4)])
    assert "**Blocked past the threshold**" in s.text and "#12: blocked 4 days" in s.text


def test_a_tick_that_moved_nothing_says_so():
    """Criterion 3: not silence."""
    s = standup(run(PhaseOutcome("deliver")))
    assert s.quiet and "Nothing moved." in s.text


def test_a_tick_that_only_blocked_is_not_quiet():
    assert not standup(run(PhaseOutcome("qa", blocked=["#9 — failed"]))).quiet


def test_a_failed_phase_and_a_wip_breach_are_reported():
    s = standup(run(PhaseOutcome("review", error="rate limited"), over={"In Progress": (4, 3)}))
    assert "**review**: rate limited" in s.text
    assert "In Progress: 4 against a limit of 3" in s.text


def test_it_says_when_the_tick_stopped_rather_than_settled():
    s = standup(run(passes=6, settled=False))
    assert "**Tick at 14:05 UTC** — 6 passes, stopped at the pass cap." in s.text


def test_write_standup_exists_behind_the_capability():
    """Criterion 4: the Scrum Master's `write_standup` has a function behind it."""
    with open("src/crew_org/config/agents.yaml") as fh:
        agents = yaml.safe_load(fh)
    scrum_master = next(a for a in agents.values() if "write_retro" in (a.get("can") or []))
    assert "write_standup" in scrum_master["can"]
    assert callable(standup_mod.write_standup)


# --- who is waiting on a person, and for how long --------------------------------


def test_blocked_and_flagged_cards_are_waiting_on_a_person():
    cards = [
        card(1, "Blocked"),
        card(2, "QAing", labels=frozenset({"needs:human"})),
        card(3, "Ready"),
        card(4, "Blocked", state="CLOSED"),
    ]
    assert waiting_on_a_person(cards) == ["#1", "#2"]


def test_aging_is_read_from_the_move_log(tmp_path):
    events = tmp_path / "events"
    events.mkdir()
    blocked = CrewEvent(
        at=AT - timedelta(days=5),
        kind=EventKind.CARD_MOVED,
        card=12,
        detail={"from": "In Progress", "to": "Blocked"},
    )
    (events / "deliver.jsonl").write_text(blocked.model_dump_json() + "\n")
    rules = ProcessRules.from_config(load_org())
    assert aging_blocked([card(12, "Blocked")], rules, events, AT) == [("#12", 5)]


# --- where it goes ---------------------------------------------------------------------


class FakeIssues:
    def __init__(self, existing=None, comments=None):
        self.owner = "mqucifer"
        self.existing = existing or []
        self._comments = comments or {}
        self.created: list[dict] = []
        self.posted: list[tuple[int, str]] = []
        self.closed: list[int] = []
        self._next = 300

    def labelled(self, repo, label):
        return [i for i in self.existing if label in i.get("labels", [])]

    def open_issues(self, repo):
        return getattr(self, "open_", [])

    def ensure_label(self, repo, name, *, color, description):
        pass

    def create(self, repo, title, body, labels=None):
        self._next += 1
        issue = {"number": self._next, "title": title, "body": body, "labels": labels}
        self.created.append(issue)
        self.existing.append(issue)
        return issue

    def comments(self, repo, number):
        return [{"body": b} for n, b in self.posted if n == number] + self._comments.get(number, [])

    def comment(self, repo, number, body):
        self.posted.append((number, body))
        return {}

    def close(self, repo, number, *, reason="completed"):
        self.closed.append(number)


def record(issues, s):
    sink, seen = EventSink(None), []
    sink.subscribe(seen.append)
    return record_standup(issues, sink, s, sprint=SPRINT, crew_repo=CREW), seen


def test_the_first_tick_of_a_sprint_opens_its_standup_issue():
    issues = FakeIssues()
    (number, commented), seen = record(issues, standup(run(PhaseOutcome("deliver", moved=True))))
    [created] = issues.created
    assert created["title"] == f"Standup: {SPRINT}" and created["labels"] == [STANDUP_LABEL]
    assert marker(SPRINT) in created["body"]
    assert commented and issues.posted[0][0] == number
    assert "Scrum Master" in issues.posted[0][1]
    assert [e.kind for e in seen] == [EventKind.STANDUP_WRITTEN]


def test_later_ticks_comment_on_the_same_issue():
    issues = FakeIssues()
    record(issues, standup(run(PhaseOutcome("deliver", moved=True))))
    record(issues, standup(run(PhaseOutcome("qa", moved=True))))
    assert len(issues.created) == 1 and len(issues.posted) == 2


def test_a_run_of_quiet_ticks_is_one_comment():
    issues = FakeIssues()
    quiet = standup(run())
    record(issues, standup(run(PhaseOutcome("deliver", moved=True))))
    (_, first), _ = record(issues, quiet)
    (_, second), seen = record(issues, quiet)
    assert first and not second
    assert seen == []
    assert QUIET_MARKER in issues.posted[-1][1]


def test_a_quiet_tick_after_movement_is_recorded():
    issues = FakeIssues()
    record(issues, standup(run()))
    record(issues, standup(run(PhaseOutcome("deliver", moved=True))))
    (_, commented), _ = record(issues, standup(run()))
    assert commented and len(issues.posted) == 3


# --- the retro reads them ------------------------------------------------------------


def standup_issue(bodies):
    existing = [{"number": 40, "labels": [STANDUP_LABEL], "body": marker(SPRINT)}]
    return FakeIssues(existing=existing, comments={40: [{"body": b} for b in bodies]})


def test_the_retro_is_given_every_standup_in_order():
    issues = standup_issue(["first", f"{QUIET_MARKER}\nNothing moved.", "third"])
    number, text = standups_for_retro(issues, CREW, SPRINT)
    assert number == 40
    assert text.index("first") < text.index("Nothing moved.") < text.index("third")
    assert QUIET_MARKER not in text


def test_too_many_standups_keep_the_newest_whole_and_say_what_was_dropped():
    # Two fit; the third would take it past the budget.
    big = "x" * (MAX_STANDUP_CHARS // 3)
    issues = standup_issue(["oldest " + big, "middle " + big, "newest " + big])
    _, text = standups_for_retro(issues, CREW, SPRINT)
    assert "newest" in text and "oldest" not in text
    assert text.startswith("_1 earlier standup(s) omitted")


def test_no_standup_issue_is_nothing_to_read():
    assert standups_for_retro(FakeIssues(), CREW, SPRINT) == (None, "")


class CloseBoard:
    def cards(self):
        return []


@pytest.fixture
def ledger(tmp_path):
    return EscalationLedger(tmp_path / "escalations.jsonl")


def test_the_retro_reads_the_standups_and_closes_them(monkeypatch, ledger):
    """The model analyses the sprint's standups at the retro, as the Sponsor asked."""
    given = {}

    def retro(*a, standups="", **k):
        given["standups"] = standups
        return Retro(summary="s")

    monkeypatch.setattr(close_mod, "write_retro", retro)
    issues = standup_issue(["deliver moved #31"])
    issues.ensure_label = lambda *a, **k: None
    result = close_sprint(
        CloseBoard(),
        issues,
        EventSink(None),
        ledger,
        sprint=SPRINT,
        repo="sprint-metrics",
        crew_repo=CREW,
        delivery_repos=["sprint-metrics"],
    )
    assert "deliver moved #31" in given["standups"]
    retro_issue = issues.created[-1]
    assert "#40" in retro_issue["body"]
    assert issues.closed == [40]
    assert f"#{result.retro_record.issue}" in issues.posted[-1][1]


# --- links and the approval queue (#118, #122) -------------------------------------

from crew_org.flows.standup import awaiting_approval  # noqa: E402


def epic_at_gate(number) -> Card:
    return Card(
        item_id=f"E{number}",
        number=number,
        status="Inbox (Goals)",
        state="OPEN",
        work_type="Epic",
        repo="sprint-metrics",
        labels=frozenset({"needs:human"}),
    )


def test_epics_at_the_gate_are_not_waiting_on_a_person():
    """Sprint 4's standups listed 17 gate epics as waiting on a person, and the
    retro filed them as stuck (#122)."""
    cards = [
        epic_at_gate(49),
        card(12, "Blocked"),
        card(2, "QAing", labels=frozenset({"needs:human"})),
    ]
    assert waiting_on_a_person(cards) == ["#12", "#2"]
    assert awaiting_approval(cards) == ["#49"]


def test_the_standup_gives_the_approval_queue_its_own_heading():
    s = write_standup(run(), sprint=SPRINT, at=AT, waiting=["#12"], aging=[], awaiting=["#49"])
    assert "**Waiting on a person**\n- #12" in s.text
    assert "**Awaiting your approval" in s.text and "- #49" in s.text
    assert s.text.index("Waiting on a person") < s.text.index("Awaiting your approval")


def test_a_recorded_standup_links_to_the_delivery_cards():
    issues = FakeIssues()
    standup_ = write_standup(run(), sprint=SPRINT, at=AT, waiting=["#12"], aging=[])
    record_standup(
        issues,
        EventSink(None),
        standup_,
        sprint=SPRINT,
        crew_repo=CREW,
        delivery_repos=["sprint-metrics"],
    )
    assert "- mqucifer/sprint-metrics#12" in issues.posted[-1][1]
