"""The Business Analyst sees, in full, the tests that pin what an epic touches (#189).

sprint-metrics#97 changed the default table's rows, which 25 merged tests
pinned, and said neither "opt-in" nor "contract change". Its splitter hadn't
read them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from crew_org.crews.refinement_crew import AcceptanceCriterion, Story
from crew_org.flows.board_flow import render_story_body
from crew_org.tools import pinning
from crew_org.tools.pinning import named_tests, pinning_tests

TABLE_TESTS = """
def test_default_table_rows():
    out = run([])
    assert out.splitlines() == ["| Sprint | Cycle |", "|---|---|", "| Current | 4 days |"]


def test_thresholds_flag_the_table():
    out = run(["--thresholds", "t.json"])
    assert "7 days" in out


def test_json_matches_the_schema():
    validate(load(run(["--json"])), SCHEMA)


def test_drift():
    _check(Path("docs/formats.md"))


def _check(path):
    text = path.read_text()
    section = text.split("BEGIN")[1]
    assert section == generate()
"""

REPORT = "src/pkg/report.py"


class Map:
    """The coverage map, standing in: every test here runs the report."""

    tests = {
        f"tests/test_table.py::{name}": {f"{REPORT}::format_performance_table"}
        for name in (
            "test_default_table_rows",
            "test_thresholds_flag_the_table",
            "test_json_matches_the_schema",
            "test_drift",
        )
    }

    def covering(self, files=(), names=()):
        wanted = set(files)
        return sorted(
            t
            for t, units in self.tests.items()
            if any(u.partition("::")[0] in wanted for u in units)
        )


EPIC = f"Flag metrics that exceed thresholds in the table, in `{REPORT}`."


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_table.py").write_text(TABLE_TESTS)
    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / REPORT).write_text("def format_performance_table(rows):\n    return ''\n")
    return tmp_path


def test_a_story_problems_test_ids_are_read_from_its_text():
    assert named_tests("- `tests/test_table.py::test_default_table_rows`: rows") == {
        ("tests/test_table.py", "test_default_table_rows")
    }


def test_the_tests_that_run_the_code_and_check_a_whole_shape_are_shown_whole(clone: Path):
    """crew#584: the tests a shape change breaks assert a whole shape (crew#583, C2)."""
    shown = pinning_tests(clone, EPIC, coverage=Map())
    assert "def test_default_table_rows" in shown, "an exact list"
    assert "def test_json_matches_the_schema" in shown, "a schema validation"
    assert "def test_drift" in shown, "a file's content, through its module's helper"
    assert "def test_thresholds_flag_the_table" not in shown, "a membership, not a shape"
    assert "4 merged tests in all run the code" in shown
    assert "opt-in" in shown and "contract change" in shown


def test_a_story_problems_tests_are_shown_even_if_no_rule_picks_them(clone: Path):
    evidence = "- `tests/test_table.py::test_thresholds_flag_the_table`: AssertionError"
    shown = pinning_tests(clone, "Something unrelated", evidence, coverage=Map())
    assert "def test_thresholds_flag_the_table" in shown


def test_a_test_the_record_names_is_shown(clone: Path):
    record = "| C1 | `tests/test_table.py::test_thresholds_flag_the_table` | x | y | declared |"
    shown = pinning_tests(clone, f"An epic.\n\n{record}")
    assert "def test_thresholds_flag_the_table" in shown


def test_without_a_map_or_a_named_test_nothing_is_shown(clone: Path):
    assert pinning_tests(clone, EPIC) == ""


def test_what_doesnt_fit_is_named_not_dropped(clone: Path, monkeypatch):
    monkeypatch.setattr(pinning, "PINNING_CHAR_CEILING", 40)
    shown = pinning_tests(clone, EPIC, coverage=Map())
    assert "not shown because they did not fit" in shown
    assert "tests/test_table.py::test_default_table_rows" in shown


RESTATED = """
def test_the_default_table_rows():
    assert len(run([])) == 3


def test_the_metric_keys_spelled_out():
    assert keys() == ["throughput", "cycle_time_days", "lead_time_days"]
"""


def test_a_test_that_spells_out_a_named_files_table_comes_first(clone: Path, monkeypatch):
    """Under the ceiling, the tests an addition breaks are shown before the rest (crew#612)."""
    (clone / REPORT).write_text(
        'COLUMNS = ["throughput", "cycle_time_days", "lead_time_days"]\n\n'
        "def format_performance_table(rows):\n    return ''\n"
    )
    (clone / "tests/test_a.py").write_text(RESTATED)
    tests = {
        f"tests/test_a.py::{name}": {f"{REPORT}::format_performance_table"}
        for name in ("test_the_default_table_rows", "test_the_metric_keys_spelled_out")
    }
    monkeypatch.setattr(pinning, "PINNING_CHAR_CEILING", 120)
    shown = pinning_tests(clone, EPIC, coverage=type("M", (Map,), {"tests": tests})())
    assert "def test_the_metric_keys_spelled_out" in shown
    assert "def test_the_default_table_rows" not in shown


def story(**kw) -> Story:
    criteria = [
        AcceptanceCriterion(given="g", when="w", then="t"),
        AcceptanceCriterion(given="g2", when="w2", then="error"),
    ]
    return Story(
        title="Flag the table",
        as_a="lead",
        i_want="flags",
        so_that="see breaches",
        acceptance_criteria=criteria,
        points=3,
        **kw,
    )


def test_a_story_says_opt_in_or_contract_change_and_nothing_else():
    assert story(pinned_behaviour="opt-in: only with --thresholds").pinned_behaviour
    assert story(pinned_behaviour="contract change: updates test_default_table_rows")
    with pytest.raises(ValidationError, match="opt-in' or 'contract change"):
        story(pinned_behaviour="probably fine")


def test_the_developer_reads_it_in_the_story():
    body = render_story_body(story(pinned_behaviour="opt-in: only with --thresholds"), 54, "Flags")
    assert (
        "**Existing tests** — unchanged: the new behaviour is opt-in (only with --thresholds)"
        in body
    )
    assert "Existing tests" not in render_story_body(story(), 54, "Flags")
