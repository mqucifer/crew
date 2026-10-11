"""A repair is shown each cause once, every failing test named (crew#591).

mqucifer/sprint-metrics#567's first attempt broke 50 merged tests on 2026-10-11: a
352,767-character report, cut to 200,000, fed a repair of 142,865 prompt tokens. Grouped,
it was 15,597 characters, and its first cause, 46 tests unpacking three values from a
function that now returned four, was its first line.
"""

from __future__ import annotations

from crew_org.flows.attempts import failing_tests, repair_report


def section(name: str, error: str, filler: int = 0) -> str:
    return (
        f"_____ {name} _____\n\n"
        "    def test_it():\n"
        "        cards = make_cards()\n"
        ">       a, b, c = summary_counts(cards)\n"
        f"E       {error}\n"
        + "".join(f"E           'line {i}': {'x' * 80}\n" for i in range(filler))
        + "\ntests/test_report.py:12: ValueError\n"
    )


def report(n_unpack: int, n_schema: int, filler: int = 40) -> str:
    sections = [
        section(f"test_unpack_{i}", "ValueError: too many values to unpack (expected 3)", filler)
        for i in range(n_unpack)
    ] + [
        section(
            f"test_schema_{i}", "ValidationError: 'superseded_count' is a required property", filler
        )
        for i in range(n_schema)
    ]
    failed = [f"FAILED tests/test_report.py::test_unpack_{i}" for i in range(n_unpack)]
    failed += [f"FAILED tests/test_schema.py::test_schema_{i}" for i in range(n_schema)]
    return (
        "$ uv run pytest -q\n(exit 1)\n"
        + "=" * 20
        + " FAILURES "
        + "=" * 20
        + "\n"
        + "\n".join(sections)
        + "=" * 10
        + " short test summary info "
        + "=" * 10
        + "\n"
        + "\n".join(failed)
        + f"\n{n_unpack + n_schema} failed, 200 passed in 3.1s\n"
    )


def test_each_cause_is_shown_once_with_every_failing_test_named():
    long = report(46, 4)
    short = repair_report(long)
    assert len(short) < len(long) // 5
    assert "50 tests failed, from 2 causes" in short
    assert "### Cause 1: ValueError: too many values to unpack (expected 3) (46 tests)" in short
    assert (
        "### Cause 2: ValidationError: 'superseded_count' is a required property (4 tests)" in short
    )
    assert {t["id"] for t in failing_tests(short)} == {t["id"] for t in failing_tests(long)}
    assert short.count("a, b, c = summary_counts(cards)") == 2, "one example per cause"
    assert "50 failed, 200 passed" in short


def test_a_short_report_is_shown_as_it_is():
    small = report(2, 1, filler=0)
    assert repair_report(small) == small


def test_what_isnt_a_test_run_is_kept_head_and_tail():
    lint = (
        "$ uv run ruff check .\n(exit 1)\n" + "E501 Line too long\n" * 2000 + "Found 2000 errors.\n"
    )
    short = repair_report(lint + "\n\n" + report(46, 4))
    assert short.startswith("$ uv run ruff check .") and "Found 2000 errors." in short
    assert "characters left out" in short and "### Cause 1:" in short


def test_past_the_budget_later_causes_keep_their_tests_not_their_output():
    short = repair_report(report(30, 30), budget=4_500)
    assert "### Cause 2:" in short and "its output left out" in short
    assert short.count("\nFAILED ") == 60, "every failing test named, past the budget too"
