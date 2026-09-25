"""Why work doesn't land first time, counted (#157).

The event log already records every retry with its failure class and what went
wrong. These tests pin how that becomes a cause that can be counted, what the
export says per story, and what the retro files.
"""

from __future__ import annotations

import json
from pathlib import Path

from crew_org.crews.retro_crew import Retro
from crew_org.events import EventSink
from crew_org.flows.attempts import (
    Attempt,
    Cause,
    cause_of,
    causes_of,
    first_error,
    read_attempts,
    retries_text,
    sprint_report,
)
from crew_org.flows.retro import CAUSE_MARKER, FINDING_LABEL, record_retro
from crew_org.tools.github_project import Card
from tests.test_retro import CREW, SPRINT, FakeIssues

NO_TEST = (
    "1 validation error for FirstAttempt\n  Value error, no test. Every acceptance criterion "
    "needs an automated test that proves it — add one. [type=value_error, input_value={}]"
)


def story(number: int, sprint: str = "Sprint 6") -> Card:
    return Card(
        item_id=f"S{number}",
        number=number,
        title=f"Story {number}",
        status="Done",
        state="CLOSED",
        work_type="Story",
        repo="sprint-metrics",
        sprint=sprint,
    )


# --- a failure becomes a cause that can be counted ----------------------------------


def test_a_parse_failure_is_counted_by_the_rule_it_broke():
    cause, first = cause_of("SCHEMA", {"error": NO_TEST})
    assert cause == "no test"
    assert first == "no test"


def test_the_same_mistake_reads_the_same_whatever_it_hit():
    a, _ = cause_of("SCHEMA", {"error": "Value error, 'tests/a.py' is Python: change it [type=x"})
    b, _ = cause_of("SCHEMA", {"error": "Value error, 'src/b.py' is Python: change it [type=x"})
    assert a == b == "… is Python: change it"


def test_a_lint_failure_keeps_its_rule_and_masks_the_name():
    output = "$ uv run ruff check .\n(exit 1)\nF821 Undefined name `tmp_path`\n  --> tests/x.py:260"
    cause, first = cause_of(
        "VERIFY", {"output": output, "failing_commands": ["uv run ruff check ."]}
    )
    assert cause == "lint F821: Undefined name …"
    assert first == "F821 Undefined name `tmp_path`"


def test_a_test_failure_is_counted_by_its_error():
    output = "$ uv run pytest -q\n(exit 1)\nE   NameError: name 'x' is not defined\n"
    assert cause_of("VERIFY", {"output": output})[0] == "a test failed: NameError"
    asserted = "$ uv run pytest -q\n(exit 1)\nE   AssertionError: assert 1 == 2\n"
    assert cause_of("VERIFY", {"output": asserted})[0] == "a test's assertion failed"


def test_an_edit_failure_masks_its_line_numbers():
    error = (
        "the file does not parse: unterminated triple-quoted string literal (detected at line 402)"
    )
    assert cause_of("EDIT", {"error": error})[0].endswith("(detected at line N)")


def test_a_contract_break_is_one_cause():
    assert cause_of("REGRESSION", {"contracts": {"main": ["a", "b"]}})[0] == (
        "changed a contract merged code depends on"
    )


# --- 1: each story's attempts, from the event log -------------------------------------


def write_events(tmp_path: Path, events: list[dict]) -> Path:
    folder = tmp_path / "events"
    folder.mkdir()
    (folder / "tick.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return folder


def retry(card: int, failure_class: str, at: str, **detail) -> dict:
    return {
        "at": at,
        "kind": "escalation.decided",
        "role": "Developer",
        "card": card,
        "summary": f"{failure_class} — retry_local",
        "detail": {"failure_class": failure_class, **detail},
    }


def test_a_sprints_stories_carry_their_attempts_and_first_try(tmp_path):
    events = write_events(
        tmp_path,
        [
            retry(73, "SCHEMA", "2026-09-25T12:57", error=NO_TEST),
            retry(75, "SCHEMA", "2026-09-25T14:43", error=NO_TEST),
            retry(9, "SCHEMA", "2026-09-19T10:00", error=NO_TEST),  # another sprint's card
            {"at": "2026-09-25T13:00", "kind": "card.moved", "card": 73, "summary": "x"},
        ],
    )
    report = sprint_report(
        "Sprint 6", [story(73), story(75), story(76)], read_attempts(events), escalations=0
    )

    by_number = {s["number"]: s for s in report["stories"]}
    assert by_number[76]["first_try"] and by_number[76]["attempts"] == []
    assert not by_number[73]["first_try"]
    assert by_number[73]["attempts"] == [
        {
            "class": "SCHEMA",
            "role": "Developer",
            "cause": "no test",
            "error": "no test",
            "at": "2026-09-25T12:57",
        }
    ]
    ((cause,),) = [[c for c in report["causes"]]]
    assert cause["cards"] == [73, 75] and cause["count"] == 2 and cause["recurring"]


def test_the_retro_is_told_the_first_try_rate_and_the_causes():
    attempts = [
        Attempt(73, "Developer", "SCHEMA", "no test", "no test"),
        Attempt(75, "Developer", "SCHEMA", "no test", "no test"),
        Attempt(74, "Developer", "VERIFY", "lint F821: Undefined name …", "F821"),
    ]
    text = retries_text(sprint_report("S", [story(n) for n in (73, 74, 75, 76)], attempts, 0))
    assert text.startswith("1 of 4 stories landed on their first attempt.")
    assert "- SCHEMA: no test (2 times, on #73, #75)" in text
    assert "(once, on #74)" in text


# --- 2: escalations come from the ledger ----------------------------------------------------


def test_the_escalation_count_is_carried_in_the_export():
    assert sprint_report("S", [], [], escalations=2)["escalations"] == 2


# --- 3 and 4: the retro files what recurs -----------------------------------------------------


def recurring(failure_class="SCHEMA", cause="no test", cards=(73, 75)) -> Cause:
    return Cause(failure_class, cause, count=len(cards), cards=list(cards), example="no test")


def record(causes, issues=None):
    issues = issues or FakeIssues()
    out = record_retro(
        issues,
        EventSink(None),
        Retro(summary="s"),
        sprint=SPRINT,
        crew_repo=CREW,
        delivery_repos=["sprint-metrics"],
        recurring=causes,
    )
    return out, issues


def test_a_cause_on_two_cards_is_filed_with_its_count_and_cards():
    out, issues = record([recurring()])

    finding = issues.created[0]
    assert finding["title"] == "Recurring SCHEMA: no test"
    assert CAUSE_MARKER.format(key=recurring().key) in finding["body"]
    assert "Seen 2 times, on 2 of the sprint's cards: #73, #75." in finding["body"]
    assert FINDING_LABEL in finding["labels"]
    assert (CREW, finding["number"]) in out.filed
    retro = issues.created[-1]["body"]
    # On the crew repository its own issues read as #N; cards link to their repo.
    assert f"- #{finding['number']} — recurring: no test" in retro


def test_a_recurring_parse_failure_is_filed_as_a_prompt_or_schema_defect():
    _, issues = record([recurring()])
    assert "A prompt or schema defect" in issues.created[0]["body"]
    _, issues = record([recurring("VERIFY", "a test failed: AssertionError")])
    assert "A recurring failure" in issues.created[0]["body"]


def test_a_cause_on_one_card_is_not_filed():
    _, issues = record([recurring(cards=(74,))])
    assert [i["title"] for i in issues.created] == [f"Retro: {SPRINT}"]


def test_a_cause_already_open_is_cited_not_filed_again():
    mark = CAUSE_MARKER.format(key=recurring().key)
    issues = FakeIssues(
        existing=[{"number": 170, "state": "open", "labels": [FINDING_LABEL], "body": mark}]
    )
    _, issues = record([recurring()], issues)
    assert [i["title"] for i in issues.created] == [f"Retro: {SPRINT}"]
    retro = issues.created[-1]["body"]
    assert "- #170 — recurring again: no test" in retro
    assert "mqucifer/sprint-metrics#73, mqucifer/sprint-metrics#75" in retro


def test_the_causes_round_trip_from_the_report():
    report = sprint_report(
        "S",
        [story(73), story(75)],
        [
            Attempt(73, "Developer", "SCHEMA", "no test", "e"),
            Attempt(75, "Developer", "SCHEMA", "no test", "e"),
        ],
        0,
    )
    (cause,) = causes_of(report)
    assert cause.recurring and cause.prompt_defect and cause.cards == [73, 75]


def test_a_plain_assertion_failure_is_the_cards_own_and_never_filed():
    output = "FAILED tests/test_x.py::test_range - assert 1 == 2\n"
    cause, first = cause_of("VERIFY", {"first_error": output.strip()})
    assert cause == "a test's assertion failed"
    assert first.startswith("FAILED tests/test_x.py::test_range")
    assert not Cause("VERIFY", cause, count=3, cards=[70, 75]).recurring


def test_an_import_error_on_several_cards_is_systemic():
    cause, _ = cause_of("VERIFY", {"output": "E   ModuleNotFoundError: No module named 'x'"})
    assert cause == "a test failed: ModuleNotFoundError"
    assert Cause("VERIFY", cause, count=2, cards=[70, 75]).recurring


def test_a_failure_whose_cause_was_lost_is_counted_and_never_filed():
    cause, _ = cause_of(
        "VERIFY",
        {"output": "$ uv run pytest -q\n(exit 1)\n....F", "failing_commands": ["uv run pytest -q"]},
    )
    assert cause == "uv run pytest -q failed; its cause wasn't recorded"
    assert not Cause("VERIFY", cause, count=2, cards=[70, 75]).recurring


def test_the_first_real_error_is_taken_from_the_whole_report():
    report = (
        "$ uv run pytest -q\n(exit 1)\n"
        + "." * 900
        + "\nFAILED tests/t.py::test_a - NameError: name 'x' is not defined\n"
    )
    assert first_error(report) == "FAILED tests/t.py::test_a - NameError: name 'x' is not defined"
