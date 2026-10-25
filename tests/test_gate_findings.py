"""What the gates found, and what a call ran with, kept as fields (crew#449 part 2, PR C).

A review was "REQUEST_CHANGES — 2 findings", a VERIFY failure kept its first 600
characters and lost the failing tests' names, and a model call didn't say which
prompt or output form it ran with (discussion 552). An ask for a file had no
reason (discussion 553).
"""

from __future__ import annotations

from pydantic import BaseModel

from crew_org.flows.attempts import failing_tests

REPORT = """\
....F..F
=================================== FAILURES ===================================
_____________________________ test_trend_empty_db ______________________________
E       assert 0 is None
=========================== short test summary info ============================
FAILED tests/test_service.py::test_trend_empty_db_all_zeros - assert 0 is None
FAILED tests/test_docs.py::test_drift_check - KeyError: 'points_delivered'
FAILED tests/test_service.py::test_trend_empty_db_all_zeros - assert 0 is None
2 failed, 6 passed in 1.20s
"""


def test_the_failing_tests_are_named_with_their_assertion_once_each():
    assert failing_tests(REPORT) == [
        {
            "id": "tests/test_service.py::test_trend_empty_db_all_zeros",
            "assertion": "assert 0 is None",
        },
        {"id": "tests/test_docs.py::test_drift_check", "assertion": "KeyError: 'points_delivered'"},
    ]
    assert failing_tests("ruff: F821 Undefined name `x`") == []


# A real report, from pytest -q outside a terminal: the summary line is too long
# for 80 columns, so pytest left its message off (sprint-metrics#529, 2026-10-09).
LONG_ID = """F                                                                        [100%]
=================================== FAILURES ===================================
___ test_single_sprint_json_output_validates_against_schema_with_a_long_name ___

    def test_single_sprint_json_output_validates_against_schema_with_a_long_name():
        data = {"a": 1}
>       assert data == {"a": 2}
E       AssertionError: assert {'a': 1} == {'a': 2}
E         
E         Differing items:
E         {'a': 1} != {'a': 2}
E         Use -v to get more diff

tests/test_schema_long_name_example.py:3: AssertionError
=========================== short test summary info ============================
FAILED {ID}
1 failed in 0.01s
""".replace(
    "{ID}",
    "tests/test_schema_long_name_example.py::"
    "test_single_sprint_json_output_validates_against_schema_with_a_long_name",
)


def test_a_summary_line_with_no_message_takes_the_tests_first_error_line():
    [failed] = failing_tests(LONG_ID)
    assert failed["assertion"] == "AssertionError: assert {'a': 1} == {'a': 2}"


class Verdict(BaseModel):
    approve: bool


def test_a_call_says_which_prompt_and_form_it_ran_with_as_hashes():
    """Recorded by the crew's own call layer (ADR 0025), as CrewAI's bridge did."""
    from crew_org import calls, events  # noqa: PLC0415
    from crew_org.events import EventKind, EventSink  # noqa: PLC0415

    seen: list = []
    sink = EventSink(None)
    sink.subscribe(seen.append)
    events.reset_bridge()
    events.forward_calls_to(sink)
    answer = {"choices": [{"message": {"content": '{"approve": true}'}, "finish_reason": "stop"}]}
    try:
        for role in ("code_reviewer", "qa_engineer"):
            calls.ask(role, [calls.Part("task", "the diff")], Verdict, send=lambda body: answer)
    finally:
        events.reset_bridge()
    started = [e.detail for e in seen if e.kind is EventKind.LLM_CALL_STARTED]
    assert len(started[0]["prompt_hash"]) == 12 and "Code Reviewer" not in str(started[0])
    assert started[0]["output_schema"].startswith("Verdict:")
    assert started[0]["prompt_hash"] != started[1]["prompt_hash"], "another role's text"
