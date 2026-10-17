"""How often each rule fired, and the model's time by role (crew#756).

Each count names its card with its repository (crew#752), and says how the event
was matched: by the rule's name, or only by its text.
"""

from __future__ import annotations

import json

from crew_org import rule_list, rule_report


def test_a_message_template_matches_in_order_and_from_its_start():
    p = rule_list.pattern("these named tests don't exist in the repository: {}. Name them.")
    assert p is not None
    assert p.search("these named tests don't exist in the repository: a, b. Name them.")[0]
    whole, fixed = p.search("these named tests don't exist in the repository: a, b, c, d")
    assert not whole and fixed >= rule_report.PARTIAL
    assert rule_list.pattern("{} is {}") is None


def event(kind, detail, *, card=7, repo="sprint-metrics", role="Developer"):
    ctx = {"repo": repo} if repo else {}
    return {
        "kind": kind,
        "role": role,
        "card": card,
        "detail": detail,
        "ctx": ctx,
        "at": "2026-10-16T00:00:00Z",
    }


def test_a_refusal_is_matched_by_its_form_and_field_before_its_text():
    report = rule_report.count(
        [
            event(
                "llm.refused",
                {
                    "output_schema": "FirstAttempt:abc",
                    "reason": "text_edits.0: Value error, the edit to 'x.md' changes nothing",
                    "duration_s": 3600,
                },
            )
        ]
    )
    fired = report.fired["form.TextEdit.changes_something"]
    assert fired.events == 1
    assert fired.matched == {rule_report.BY_NAME: 1}
    assert fired.seconds == 3600


def test_a_missing_field_is_the_schema_and_an_empty_answer_the_call_layers():
    report = rule_report.count(
        [
            event(
                "llm.refused",
                {"output_schema": "Settlement:x", "reason": "summary: Field required"},
            ),
            event("llm.refused", {"empty": True, "reason": "the answer was empty"}),
        ]
    )
    assert report.fired["call.schema"].events == 1
    assert report.fired["call.the-answer-was-empty-you"].events == 1


def test_crewais_validation_errors_are_read_by_form_and_field():
    error = (
        "1 validation error for Implementation\ntext_edits.1.path\n  Value error, "
        "'src/a.py' is Python: change it with `edits`, addressed by name, not by quoting "
        "its text [type=value_error, input_value='src/a.py', input_type=str]"
    )
    report = rule_report.count(
        [event("escalation.decided", {"failure_class": "SCHEMA", "error": error})]
    )
    assert report.fired["form.TextEdit.stays_in_the_repository"].matched == {rule_report.BY_NAME: 1}


def test_a_rule_and_a_guard_are_matched_by_the_names_the_event_records():
    report = rule_report.count(
        [
            event(
                "escalation.decided",
                {"rule": "guard.local_repair", "failure_class": "OVERWRITE", "paths": ["a.py"]},
            )
        ]
    )
    assert report.fired["guard.local_repair"].events == 1
    assert report.fired["guard.overwrite.overwrites_existing"].events == 1


def test_cards_are_counted_with_their_repository_and_an_unknown_one_stays_unknown():
    """crew#752: card 7 in two repositories is two cards, and no repo is no guess."""
    report = rule_report.count(
        [
            event("note", {"rule": "hold.sibling"}, card=7, repo="sprint-metrics"),
            event("note", {"rule": "hold.sibling"}, card=7, repo="crew-presentation"),
            event("note", {"rule": "hold.sibling"}, card=7, repo=""),
        ]
    )
    fired = report.fired["hold.sibling"]
    assert fired.cards == {"sprint-metrics#7", "crew-presentation#7", "repo unknown#7"}
    assert fired.repos[rule_report.UNKNOWN] == 1


def test_a_refusal_no_rule_matches_is_counted_as_unmatched():
    report = rule_report.count(
        [event("llm.refused", {"reason": "something no rule ever says, about 'x' and 42"})]
    )
    assert sum(report.unmatched.values()) == 1
    assert "'x'" not in next(iter(report.unmatched))


def test_model_time_is_counted_per_role_and_repository():
    report = rule_report.count(
        [
            event(
                "llm.finished", {"duration_s": 1800, "completion_tokens": 10, "reasoning_tokens": 4}
            ),
            event("llm.finished", {"completion_tokens": 5}, repo=""),
        ]
    )
    overall = report.time[("Developer", "")]
    assert (overall.calls, overall.timed, overall.seconds, overall.completion) == (2, 1, 1800, 15)
    assert report.time[("Developer", rule_report.UNKNOWN)].calls == 1


def test_a_second_report_shows_only_rows_that_changed():
    report = rule_report.count([])
    before = rule_report.snapshot(report)
    after = json.loads(json.dumps(before))
    gone = next(iter(after["rows"]))
    del after["rows"][gone]
    after["rows"]["told.new.rule"] = {**before["rows"][gone], "says": "A new rule."}
    changed = next(r for r in after["rows"] if r not in (gone, "told.new.rule"))
    after["rows"][changed]["roles"] = ["Someone else"]
    text = rule_report.changes(before, after)
    assert "1 added, 1 removed, 1 changed" in text
    assert f"`{gone}`" in text and "`told.new.rule`" in text and f"`{changed}` | roles" in text
