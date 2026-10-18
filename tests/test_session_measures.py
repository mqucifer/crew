"""delivery-history carries the Developer's session, and decision 8's numbers come from it.

crew#778, Stage F. The session's events reach the package by their names and
counts only (ADR 0020, ADR 0010). Each story's deliveries in sessions are derived
from them, and the three measures the Sponsor set (ADR 0032, decisions 8 and 9)
are computed here from a built package: the first-attempt rate, where an attempt
is the branch handed to review and QA (the Sponsor, 2026-10-18); the model time
and tokens per story, across roles; and the rate of stories returned to the
Business Analyst. sprint-metrics computes them once the Sponsor's Goal lands.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import jsonschema

from crew_org.packages import contract
from crew_org.packages import delivery_history as dh
from crew_org.packages.sprint_metrics_feed import feed
from crew_org.tools.github_project import Card

T = datetime(2026, 10, 18, 14, 0, tzinfo=UTC)
SM = "sprint-metrics"
SECRET = "src/sprint_metrics/secret_plan.py"


def at(minutes: float) -> str:
    return (T + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def raw(minutes: float, kind: str, card: int, role: str = "Developer", /, **detail: Any):
    return (
        "tick",
        {
            "at": at(minutes),
            "kind": kind,
            "role": role,
            "card": card,
            "summary": f"viewed {SECRET}",
            "detail": {"repo": SM, **detail},
            "ctx": {"repo": SM},
        },
    )


def started(m, card, rework=False):
    sizes = {"story": 3537, "map": 4367, "rules": 1386, "total": 16182}
    sizes["instead_of_hints" if rework else "hints"] = 804
    return raw(m, "session.started", card, start_chars=sizes, base="aa78bf2")


def turn(m, card, n, **signals):
    return raw(
        m,
        "session.turn",
        card,
        turn=n,
        tool_calls=1,
        model_s=8.0 * n,
        most_viewed=SECRET,
        **{"since_action": 1, "most_views": 1, "edits_since_run": 0, "empty_lookups": 0} | signals,
    )


def tool(m, card, name, result="ok", **more):
    return raw(
        m,
        "tool.finished",
        card,
        tool=name,
        turn=1,
        target=SECRET,
        result=result,
        args_chars=40,
        reply_chars=900,
        duration_s=0.2,
        **more,
    )


def run(m, card, what, ok):
    return tool(
        m,
        card,
        "run",
        "passed" if ok else "failed",
        run=what,
        commands=[{"command": "uv run pytest -q", "code": 0 if ok else 1, "seconds": 5.0}],
        first_error="" if ok else "E   assert 200 == 100",
        failing=[] if ok else ["tests/test_metrics.py::test_cap"],
    )


def ended(m, card, how="done", *, stop="", why="", kind=None, calls=10, ceiling=None):
    said: dict[str, Any] = {"summary": "f returns 2, no cap", "docs": "docs/metrics.md"}
    if how == "done":
        said |= {"criteria": [{"criterion": "c", "test": "t"}] * 3, "changed_tests": []}
    elif how == "blocked":
        said = {"missing": "the rate's cap isn't decided", "kind": kind or "stop"}
    evidence: dict[str, Any] = {
        "last_failure": None,
        "tried": {"files_read": [SECRET]},
        "signals": {"since_action": 2, "most_views": 3, "edits_since_run": 1, "most_viewed": SECRET}
        | {"empty_lookups_total": 4},
    }
    if ceiling:
        evidence["stop"] = {"ceiling": ceiling, "calls": 60}
    return raw(
        m,
        "session.ended",
        card,
        how=how,
        stop=stop,
        why=why,
        said=said,
        evidence=evidence,
        model_calls=calls,
        model_s=60.0,
        edits_applied=2,
        test_runs=2,
        prompt_tokens=100_000,
        cached_tokens=90_000,
        completion_tokens=4_000,
        reasoning_tokens=2_500,
    )


def gate(m, card, passed, rules=()):
    return raw(m, "gate.checked", card, passed=passed, rules=list(rules), already_done=False)


def moved(m, card, frm, to, role="Developer"):
    return raw(m, "card.moved", card, role, **{"from": frm, "to": to})


def verdict(m, card, role, **flag):
    return raw(m, "agent.finished", card, role, **flag)


def call(m, card, role="Developer", **tokens):
    usage = {"prompt_tokens": 1000, "completion_tokens": 100, "reasoning_tokens": 60}
    usage |= {"cached_tokens": 900, "duration_s": 6.0, "call_id": f"c{m}-{card}-{role}"}
    return raw(m, "llm.finished", card, role, model="crew-code-think", **(usage | tokens))


def card(number, status="Done") -> Card:
    return Card(
        item_id=f"{SM}-{number}",
        number=number,
        repo=SM,
        title=f"story {number}",
        work_type="Story",
        status=status,
        created=T - timedelta(days=1),
    )


def build(events, cards):
    return dh.build(
        dh.Inputs(
            events=events,
            cards=cards,
            sprints=dh.sprints_from(
                [{"title": "Sprint 40", "startDate": "2026-10-18", "duration": 1}]
            ),
            # The pull request review reads: a reviewer's work is placed by it.
            pulls={
                SM: [
                    {
                        "number": 700,
                        "title": "fix: no cap",
                        "created_at": at(8),
                        "merged_at": at(19),
                        "closed_at": at(19),
                        "closes": [(SM, 662)],
                    }
                ]
            },
            crew_repository="crew",
            repositories=[SM],
            timezone="America/Chicago",
            date=date(2026, 10, 18),
            generated=T,
        )
    )


def kinds(files, kind):
    return [e for period in files.values() for e in period.events if e.kind == kind]


# Story 662: a gate return, then review asks for changes, then accepted. Story 663:
# first time through review and QA. Story 664: blocked on a fact, returned. Story 665:
# a choice for the Sponsor. Story 666: interrupted, resumed, then interrupted again.
STORIES = [
    started(0, 662),
    turn(1, 662, 1),
    tool(1, 662, "search", found_nothing=True, looked_for="zebra_crossing"),
    tool(1.5, 662, "view", "refused", rule="session.view"),
    run(2, 662, "tests", ok=False),
    run(3, 662, "suite", ok=True),
    call(3, 662),
    ended(4, 662),
    gate(5, 662, False, ["contract.declared_tests", "scope.protected_path"]),
    started(6, 662, rework=True),
    ended(7, 662),
    gate(8, 662, True),
    moved(9, 662, "In Progress", "Reviewing"),
    verdict(10, 662, "Code Reviewer", approved=False, pr=700, findings=["the cap's test is gone"]),
    moved(11, 662, "Reviewing", "In Progress"),
    started(12, 662, rework=True),
    ended(13, 662),
    gate(14, 662, True),
    moved(15, 662, "In Progress", "Reviewing"),
    verdict(16, 662, "Code Reviewer", approved=True, pr=700),
    moved(17, 662, "Reviewing", "QAing"),
    call(17, 662, "QA Engineer"),
    verdict(18, 662, "QA Engineer", accepted=True),
    moved(19, 662, "QAing", "Done"),
    started(20, 663),
    ended(21, 663),
    gate(22, 663, True),
    moved(23, 663, "In Progress", "Reviewing"),
    moved(24, 663, "Reviewing", "QAing"),
    moved(25, 663, "QAing", "Done"),
    started(26, 664),
    ended(27, 664, "blocked", stop="ceiling", ceiling="reading"),
    raw(28, "story.returned", 664, reason="session blocked", kind="stop", stop="ceiling"),
    started(30, 665),
    ended(31, 665, "blocked", kind="choice"),
    moved(32, 665, "In Progress", "Blocked"),
    started(40, 666),
    ended(41, 666, "interrupted", why="call_failed"),
    started(42, 666, rework=True),
    ended(43, 666, "interrupted", why="workspace_gone"),
]
CARDS = [card(662), card(663), card(664, "Ready"), card(665, "Blocked"), card(666, "In Progress")]


def deliveries(history):
    return {(d.card.number, d.number): d for d in history.analysis.deliveries}


# --- what reaches the package ------------------------------------------------------------


def test_the_session_reaches_the_package_by_names_and_counts_and_matches_its_schema():
    history, files, manifest = build(STORIES, CARDS)
    schema = json.loads((contract.CONTRACTS / "events.schema.json").read_text())
    for period in files.values():
        jsonschema.validate(json.loads(period.model_dump_json()), schema)
    index = json.loads((contract.CONTRACTS / "schema.json").read_text())
    jsonschema.validate(json.loads(history.model_dump_json()), index)
    written = "".join(p.model_dump_json() for p in files.values()) + history.model_dump_json()
    for never in (
        SECRET,
        "zebra_crossing",
        "assert 200",
        "the rate's cap",
        "f returns 2",
        "cap's test",
    ):
        assert never not in written, never
    assert {e.kind for p in files.values() for e in p.events} >= {
        "session_started",
        "session_turn",
        "tool_call",
        "session_ended",
        "gate_checked",
        "returned",
        "work_finished",
        "model_call",
    }
    assert not manifest.left_out


def test_each_event_keeps_what_the_measures_need():
    _, files, _ = build(STORIES, CARDS)
    search, view, failing, passing = kinds(files, "tool_call")[:4]
    assert (search.tool, search.found_nothing, search.argument_chars) == ("search", True, 40)
    assert (view.result, view.rule) == ("refused", "session.view")
    assert (failing.run, failing.exit_codes, passing.run) == ("tests", [1], "suite")
    first = kinds(files, "session_ended")[0]
    assert (first.how, first.criteria, first.changed_tests, first.cached_tokens) == (
        "done",
        3,
        0,
        90_000,
    )
    assert (first.since_action, first.most_views, first.empty_lookups) == (2, 3, 4)
    stopped = next(e for e in kinds(files, "session_ended") if e.card.number == 664)
    assert (stopped.blocked_on, stopped.stop, stopped.ceiling) == ("stop", "ceiling", "reading")
    gates = kinds(files, "gate_checked")
    assert gates[0].rules == ["contract.declared_tests", "scope.protected_path"]
    (returned,) = kinds(files, "returned")
    assert (returned.reason, returned.blocked_on, returned.stop) == (
        "session blocked",
        "stop",
        "ceiling",
    )
    assert [w.verdict for w in kinds(files, "work_finished")] == [
        "changes_requested",
        "approved",
        "accepted",
    ]
    assert kinds(files, "model_call")[0].cached_tokens == 900
    assert kinds(files, "session_started")[1].rework is True


def test_a_return_for_a_reason_the_package_doesnt_know_is_left_out_and_counted():
    _, files, manifest = build([raw(1, "story.returned", 662, reason="a new reason")], CARDS)
    assert not kinds(files, "returned")
    assert manifest.left_out == {"returned: reason unknown": 1}


def test_a_step_s_read_rounds_arent_session_tool_calls():
    """`calls._use_tools` records `tool.finished` too, with no `result`."""
    read = raw(1, "tool.finished", 662, tool="read_file", round=1, chars=900)
    _, files, _ = build([read], CARDS)
    assert not kinds(files, "tool_call")


# --- the deliveries ------------------------------------------------------------------------


def test_a_delivery_counts_its_submissions_apart_from_the_gates_returns():
    history, _, _ = build(STORIES, CARDS)
    d = deliveries(history)[(662, 1)]
    assert (d.outcome, d.sessions, d.submissions, d.gate_rounds, d.review_returns) == (
        "done",
        3,
        2,
        1,
        1,
    )
    assert d.gate_rules == ["contract.declared_tests", "scope.protected_path"]
    assert (d.rework_rounds, d.resumes, d.runs, d.first_green_run) == (2, 0, 2, 2)
    assert (d.model_calls, d.prompt_tokens, d.cached_tokens) == (30, 300_000, 270_000)
    assert d.ended == T + timedelta(minutes=19)


def test_a_delivery_ends_returned_blocked_or_interrupted():
    found = deliveries(build(STORIES, CARDS)[0])
    returned = found[(664, 1)]
    assert (returned.outcome, returned.returned_because, returned.stop) == (
        "returned",
        "session blocked",
        "ceiling",
    )
    assert (returned.ceiling, returned.since_action) == ("reading", 2)
    assert (found[(665, 1)].outcome, found[(665, 1)].blocked_on) == ("blocked", "choice")
    interrupted = found[(666, 1)]
    assert (interrupted.outcome, interrupted.sessions, interrupted.resumes) == ("interrupted", 2, 1)


def test_a_story_already_done_is_handed_on_when_it_goes_to_qa():
    events = [started(0, 662), ended(1, 662), moved(2, 662, "In Progress", "QAing")]
    events += [moved(3, 662, "QAing", "Done")]
    d = deliveries(build(events, CARDS)[0])[(662, 1)]
    assert (d.outcome, d.submissions) == ("done", 1)


def test_a_story_returned_and_worked_again_is_a_second_delivery():
    events = [
        *STORIES,
        started(50, 664),
        ended(51, 664),
        moved(52, 664, "In Progress", "Reviewing"),
    ]
    found = deliveries(build(events, CARDS)[0])
    assert (found[(664, 2)].outcome, found[(664, 2)].submissions) == ("open", 1)


def test_the_gates_returns_dont_spoil_a_first_try_and_reviews_do():
    stories = {s.card.number: s for s in build(STORIES, CARDS)[0].analysis.stories}
    assert (stories[663].pattern, stories[663].misses) == ("first_try", 0)
    assert (stories[662].pattern, stories[662].misses) == ("retried", 1)
    gate_only = [started(0, 663), ended(1, 663), gate(2, 663, False, ["scope.protected_path"])]
    gate_only += [
        started(3, 663, rework=True),
        ended(4, 663),
        moved(5, 663, "In Progress", "Reviewing"),
    ]
    gate_only += [moved(6, 663, "Reviewing", "QAing"), moved(7, 663, "QAing", "Done")]
    (story,) = [s for s in build(gate_only, CARDS)[0].analysis.stories if s.card.number == 663]
    assert (story.pattern, story.misses) == ("first_try", 0)


# --- decision 8's three numbers, from the package ------------------------------------------


def measures(history: dh.DeliveryHistory, files: dict[str, dh.PeriodEvents]) -> dict[str, Any]:
    """The three numbers, as sprint-metrics will compute them from what the package holds.

    The return rate's denominator is the stories worked in sessions: once delivery
    runs sessions, the stories started in the sprint, as the Goal's draft asks.
    """
    by_story: dict[tuple[str, int], list[dh.Delivery]] = {}
    for d in history.analysis.deliveries:
        by_story.setdefault((d.card.repo, d.card.number), []).append(d)
    delivered = [
        key
        for key, ds in by_story.items()
        if ds[-1].outcome == "done"
        and next(c for c in history.cards if (c.repo, c.number) == key).closed_as != "not_planned"
    ]
    first = [key for key in delivered if sum(d.submissions for d in by_story[key]) == 1]
    events = [e for p in files.values() for e in p.events]
    worked = {(e.card.repo, e.card.number) for e in events if e.kind == "session_started"}
    returned = {(e.card.repo, e.card.number) for e in events if e.kind == "returned"}
    costs = {(s.card.repo, s.card.number): s.cost for s in history.analysis.stories if s.cost}
    return {
        "first_attempt_rate_percent": round(100 * len(first) / len(delivered)),
        "per_story": {
            key[1]: (
                c.model_seconds,
                c.prompt_tokens,
                round(100 * c.cached_tokens / c.prompt_tokens),
            )
            for key, c in costs.items()
        },
        "returned_rate_percent": round(100 * len(returned & worked) / len(worked)),
    }


def test_decision_8s_numbers_are_computed_from_the_package():
    history, files, _ = build(STORIES, CARDS)
    found = measures(history, files)
    # 662 went to review twice; 663 once: one of the two delivered was a first attempt.
    assert found["first_attempt_rate_percent"] == 50
    # Every role's calls on the story: the Developer's and QA's.
    assert found["per_story"][662] == (12.0, 2000, 90)
    # 664 of the five stories with a session went back to the Business Analyst.
    assert found["returned_rate_percent"] == 20


# --- sprint-metrics' feed --------------------------------------------------------------------


def test_the_feed_counts_a_sessions_attempts_as_times_handed_to_review():
    history, files, _ = build(STORIES, CARDS)
    sent = feed(history, files.values()).events
    failed = [e for e in sent if e["type"] == "attempt_failed"]
    assert [
        (e["card_id"], e["attempt_number"], e["failure_class"], e["failure_role"]) for e in failed
    ] == [("sprint-metrics#662", 1, "REVIEW", "Code Reviewer")]
    finished = {e["card_id"]: e["card"] for e in sent if e["type"] == "finished"}
    assert finished["sprint-metrics#662"]["attempts"] == 2
    assert finished["sprint-metrics#662"]["failure_class"] == "REVIEW"
    assert finished["sprint-metrics#663"]["attempts"] == 1


def test_a_qa_return_is_an_attempt_and_escalations_attempts_arent_counted_twice():
    escalation = raw(16.5, "escalation.decided", 662, summary="REVIEW — retry_local")
    escalation[1]["summary"] = "REVIEW — retry_local"
    escalation[1]["detail"]["failure_class"] = "REVIEW"
    qa = [verdict(18, 662, "QA Engineer", accepted=False), moved(18.5, 662, "QAing", "In Progress")]
    events = [e for e in STORIES if not (e[1]["card"] == 662 and e[1]["at"] >= at(18))]
    events += [escalation, *qa, started(20, 662, rework=True), ended(21, 662)]
    events += [moved(22, 662, "In Progress", "Reviewing"), moved(23, 662, "Reviewing", "QAing")]
    events += [moved(24, 662, "QAing", "Done")]
    history, files, _ = build(events, CARDS)
    sent = [e for e in feed(history, files.values()).events if e["card_id"] == "sprint-metrics#662"]
    failed = [
        (e["failure_class"], e["failure_role"]) for e in sent if e["type"] == "attempt_failed"
    ]
    assert failed == [("REVIEW", "Code Reviewer"), ("QA", "QA Engineer")]
    (finished,) = [e for e in sent if e["type"] == "finished"]
    assert finished["card"]["attempts"] == 3


# --- records that are missing, partial or malformed (the Sponsor, 2026-10-18) -----------


def test_a_session_that_crashed_is_built_around_and_named():
    """A start with no end, then the card moved on: interrupted, and said so."""
    events = [started(0, 662), turn(1, 662, 1), run(2, 662, "suite", ok=True)]
    events += [moved(30, 662, "In Progress", "Ready")]
    history, files, manifest = build(events, CARDS)
    d = deliveries(history)[(662, 1)]
    assert (d.outcome, d.gaps, d.runs, d.first_green_run) == (
        "interrupted",
        ["session_not_ended"],
        1,
        1,
    )
    assert manifest.gaps == {"session_not_ended": 1}


def test_a_session_with_nothing_after_its_start_may_still_be_running():
    history, _, manifest = build([started(0, 662), turn(1, 662, 1)], CARDS)
    d = deliveries(history)[(662, 1)]
    assert (d.outcome, d.ended, d.gaps) == ("open", None, ["session_not_ended"])


def test_a_session_started_again_after_one_that_never_ended_is_a_resume():
    events = [started(0, 662), started(10, 662, rework=True), ended(11, 662)]
    d = deliveries(build(events, CARDS)[0])[(662, 1)]
    assert (d.sessions, d.resumes, d.gaps, d.model_calls) == (2, 1, ["session_not_ended"], 10)


def test_an_end_with_no_start_still_counts_what_it_cost():
    history, _, manifest = build([ended(5, 662), moved(6, 662, "In Progress", "Reviewing")], CARDS)
    d = deliveries(history)[(662, 1)]
    assert (d.sessions, d.model_calls, d.submissions, d.gaps) == (1, 10, 1, ["session_not_started"])
    assert manifest.gaps == {"session_not_started": 1}


def test_a_malformed_session_event_is_left_out_and_counted_and_the_rest_is_built():
    no_how = ended(4, 662)
    del no_how[1]["detail"]["how"]
    bad_result = tool(2, 662, "view", result="exploded")
    bad_turn = turn(1, 662, 1)
    bad_turn[1]["detail"]["turn"] = "one"
    odd = started(0, 662)
    odd[1]["detail"]["start_chars"] = "16182"
    odd_run = run(3, 662, "suite", ok=True)
    odd_run[1]["detail"]["commands"] = "uv run pytest"
    no_passed = raw(5, "gate.checked", 662, rules=["scope.protected_path"])
    events = [odd, bad_turn, bad_result, odd_run, no_how, no_passed, *STORIES[24:30]]
    history, files, manifest = build(events, CARDS)
    assert manifest.left_out == {
        "gate.checked: malformed": 1,
        "session.ended: malformed": 1,
        "session.turn: malformed": 1,
        "tool.finished: malformed": 1,
    }
    started_, *_ = kinds(files, "session_started")
    assert started_.start_chars == {}
    assert next(e for e in kinds(files, "tool_call") if e.tool == "run").exit_codes == []
    # The story whose records were whole is built as ever.
    assert deliveries(history)[(663, 1)].outcome == "done"
    assert deliveries(history)[(662, 1)].gaps == ["session_not_ended"]
    schema = json.loads((contract.CONTRACTS / "schema.json").read_text())
    jsonschema.validate(json.loads(history.model_dump_json()), schema)
    feed(history, files.values())  # the feed builds from it too


def test_a_session_end_missing_its_counts_says_none_not_zero():
    bare = raw(4, "session.ended", 662, how="done", said={}, evidence={})
    (end,) = kinds(build([started(0, 662), bare], CARDS)[1], "session_ended")
    assert (end.model_calls, end.model_seconds, end.edits, end.since_action) == (
        None,
        None,
        None,
        None,
    )
    assert end.criteria == 0
