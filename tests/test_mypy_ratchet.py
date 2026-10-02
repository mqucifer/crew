"""The mypy count can only go down (crew#459)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mypy_ratchet.py"
spec = importlib.util.spec_from_file_location("mypy_ratchet", SCRIPT)
ratchet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ratchet)


def test_the_count_comes_from_the_summary_line():
    out = "src/a.py:1: error: x  [misc]\nFound 282 errors in 66 files (checked 95 source files)\n"
    assert ratchet.count_errors(out) == 282


def test_a_clean_run_counts_zero():
    assert ratchet.count_errors("Success: no issues found in 95 source files\n") == 0


def test_output_with_no_summary_is_an_error_not_a_pass():
    with pytest.raises(ValueError):
        ratchet.count_errors("mypy: can't read file 'src'\n")


def test_one_more_error_fails():
    ok, message = ratchet.judge(283, 282)
    assert not ok
    assert "above the baseline" in message


def test_fewer_errors_pass_and_ask_for_the_baseline_to_drop():
    ok, message = ratchet.judge(270, 282)
    assert ok
    assert "Lower mypy-baseline to 270" in message


def test_the_recorded_baseline_is_a_number():
    assert int((SCRIPT.parents[1] / "mypy-baseline").read_text().strip()) >= 0
