"""What decided, and what kind of failure it was, as names (crew#449, crew#550).

On 2026-10-08 `llm.failed` on sprint-metrics#475 held answers cut off at the
output limit and answers the crew's form refused, and "failed" read as the model
call failing. Neither was.
"""

from __future__ import annotations

from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.rules import Kind, Rule, call_kind, kind_of


def test_a_cut_off_answer_and_a_refused_one_are_told_apart_from_a_failed_call():
    cut = "OpenAI API call failed: Could not parse response content as the length limit was reached"
    assert call_kind(cut) is Kind.CUT_OFF
    assert call_kind("1 validation error for Implementation") is Kind.FORM_REFUSED
    assert call_kind("Connection refused by upstream") is Kind.CALL_FAILED
    assert call_kind("ReadTimeout") is Kind.CALL_FAILED


def test_an_attempts_class_says_what_happened_to_the_work():
    assert kind_of("escalation.decided", "EDIT — retry_local", {}) is Kind.EDIT_NOT_APPLIED
    assert kind_of("escalation.decided", "VERIFY — retry_local", {}) is Kind.TESTS_FAILED
    assert kind_of("escalation.decided", "x", {"failure_class": "SCHEMA"}) is Kind.FORM_REFUSED
    assert kind_of("escalation.decided", "OVERWRITE — block", {}) is Kind.GUARD_REFUSED
    assert kind_of("llm.empty", "", {}) is Kind.EMPTY
    # sprint-metrics#529, 2026-10-09: an empty answer read as a refused form.
    empty = {"failure_class": "SCHEMA", "error": "Invalid response from LLM call - None or empty."}
    assert kind_of("escalation.decided", "SCHEMA — retry_local", empty) is Kind.EMPTY
    assert kind_of("card.moved", "diff approved", {}) is None


def test_the_sink_names_a_failures_kind_and_a_returns_rule():
    seen = []
    sink = EventSink(None)
    sink.subscribe(seen.append)
    sink.emit(
        CrewEvent(
            kind=EventKind.LLM_CALL_FAILED,
            detail={"error": "Could not parse response content as the length limit was reached"},
        )
    )
    sink.emit(CrewEvent(kind=EventKind.STORY_RETURNED, detail={"reason": "pinned tests"}))
    assert seen[0].detail["failure_kind"] == "cut_off"
    assert seen[1].detail["failure_kind"] == "gate_returned"
    assert seen[1].detail["rule"] == Rule.RETURN_PINNED_TESTS


def test_a_kind_its_site_named_is_kept():
    seen = []
    sink = EventSink(None)
    sink.subscribe(seen.append)
    sink.emit(
        CrewEvent(kind=EventKind.TASK_FAILED, detail={"error": "x", "failure_kind": "undecided"})
    )
    assert seen[0].detail["failure_kind"] == "undecided"


def test_every_rule_and_kind_says_what_it_means():
    """The reference a reader has: a name with no meaning is a guess."""
    from crew_org.rules import meanings

    assert set(meanings(Rule)) == {m.name for m in Rule}
    assert set(meanings(Kind)) == {m.name for m in Kind}
    assert all(meanings(Rule).values()) and all(meanings(Kind).values())


def test_the_committed_reference_is_the_codes():
    """Run `crew reference` after changing a rule, a kind, the context or an event name."""
    from crew_org import reference

    assert reference.PAGE.read_text(encoding="utf-8") == reference.render(), (
        "docs/reference/records.md is stale: run `crew reference`"
    )


def test_the_reference_explains_every_key_the_context_sends_out():
    from crew_org import log, reference

    assert set(log._CONTEXT_OUT) <= set(reference.CONTEXT)


def test_an_edit_or_guard_retry_names_its_own_rule():
    """An edit reaches the policy as SCHEMA, a guard as VERIFY or REGRESSION: their
    retries named the schema or verify rule (sprint-metrics#529, 2026-10-09)."""
    from crew_org.escalation import Disposition, EscalationDecision
    from crew_org.flows.delivery import _local_rule

    retry = EscalationDecision(
        disposition=Disposition.RETRY_LOCAL, reason="", rule=Rule.SCHEMA_LOCAL_REPAIR
    )
    filed = EscalationDecision(
        disposition=Disposition.FILE_PROMPT_DEFECT, reason="", rule=Rule.SCHEMA_PERSISTED
    )
    assert (
        _local_rule(retry, Rule.EDIT_LOCAL_REPAIR, Rule.EDIT_NOT_APPLIED) is Rule.EDIT_LOCAL_REPAIR
    )
    assert (
        _local_rule(filed, Rule.EDIT_LOCAL_REPAIR, Rule.EDIT_NOT_APPLIED) is Rule.EDIT_NOT_APPLIED
    )
    escalate = EscalationDecision(
        disposition=Disposition.ESCALATE, reason="", rule=Rule.ESCALATION_ALLOWED
    )
    assert _local_rule(escalate, Rule.GUARD_LOCAL_REPAIR) is Rule.ESCALATION_ALLOWED


def test_a_scope_return_for_pinned_tests_says_the_tests_failed(tmp_path):
    """sprint-metrics#529's return (2026-10-09) carried no kind: SCOPE names none."""
    sink = EventSink(tmp_path / "e.jsonl")
    sink.emit(
        CrewEvent(
            kind=EventKind.ESCALATION_DECIDED,
            card=529,
            summary="SCOPE — return_to_refinement",
            detail={"failure_class": "SCOPE", "failure_kind": Kind.TESTS_FAILED},
        )
    )
    import json

    line = json.loads((tmp_path / "e.jsonl").read_text())
    assert line["detail"]["failure_kind"] == "tests_failed"
