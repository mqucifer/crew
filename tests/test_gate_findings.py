"""What the gates found, and what a call ran with, kept as fields (crew#449 part 2, PR C).

A review was "REQUEST_CHANGES — 2 findings", a VERIFY failure kept its first 600
characters and lost the failing tests' names, and a model call didn't say which
prompt or output form it ran with (discussion 552). An ask for a file had no
reason (discussion 553).
"""

from __future__ import annotations

from types import SimpleNamespace

from pydantic import BaseModel

from crew_org.crews.asks import FileAsk, paths, reasons
from crew_org.crews.delivery_crew import Implementation
from crew_org.events import _versions
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


def test_a_file_asked_for_says_why_and_a_bare_path_still_reads():
    answer = Implementation.model_validate(
        {
            "summary": "asks",
            "need_files": [
                "./src/sprint_metrics/service.py",
                {"path": "src/sprint_metrics/store.py", "why": "the query it changes"},
            ],
        }
    )
    assert all(isinstance(a, FileAsk) for a in answer.need_files)
    assert paths(answer.need_files) == [
        "src/sprint_metrics/service.py",
        "src/sprint_metrics/store.py",
    ]
    assert reasons(answer.need_files) == {"src/sprint_metrics/store.py": "the query it changes"}
    assert answer.asks


class Verdict(BaseModel):
    approve: bool


def test_a_call_says_which_prompt_and_form_it_ran_with_as_hashes():
    call = SimpleNamespace(
        messages=[
            {"role": "system", "content": "You are the Code Reviewer. Secret instructions."},
            {"role": "user", "content": "the diff"},
        ],
        from_task=SimpleNamespace(output_pydantic=Verdict),
    )
    found = _versions(call)
    assert len(found["prompt_hash"]) == 12 and "Secret" not in str(found)
    assert found["output_schema"].startswith("Verdict:")
    reworded = SimpleNamespace(
        messages=[{"role": "system", "content": "You are the Code Reviewer. New instructions."}],
        from_task=None,
    )
    assert _versions(reworded)["prompt_hash"] != found["prompt_hash"]
    assert "output_schema" not in _versions(reworded)
