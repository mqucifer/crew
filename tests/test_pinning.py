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
from crew_org.tools.pinning import named_tests, pinning_tests, terms

TABLE_TESTS = """
def test_default_table_rows():
    out = run([])
    assert "| Current | 4 days | 6 days |" in out


def test_thresholds_flag_the_table():
    out = run(["--thresholds", "t.json"])
    assert "7 days ⚠️" in out


def test_json_shape():
    assert load(run(["--json"]))["api_version"] == "1"


def helper_not_a_test():
    return "--thresholds"
"""

EPIC = (
    "Flag metrics that exceed thresholds in the table. Reuses `--thresholds` and "
    "keeps `format_performance_table`'s rows."
)


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_table.py").write_text(TABLE_TESTS)
    return tmp_path


def test_what_an_epic_names_is_read_from_its_text():
    assert terms(EPIC) == {"--thresholds", "format_performance_table"}
    assert named_tests("- `tests/test_table.py::test_default_table_rows`: rows") == {
        ("tests/test_table.py", "test_default_table_rows")
    }


def test_tests_mentioning_what_the_epic_names_are_shown_whole(clone: Path):
    shown = pinning_tests(clone, EPIC)
    assert "def test_thresholds_flag_the_table" in shown and '"7 days ⚠️" in out' in shown
    assert "def test_json_shape" not in shown, "it mentions nothing the epic names"
    assert "helper_not_a_test" not in shown, "only tests"
    assert "opt-in" in shown and "contract change" in shown


def test_a_story_problems_tests_are_shown_even_if_the_epic_doesnt_name_them(clone: Path):
    evidence = "- `tests/test_table.py::test_default_table_rows`: AssertionError"
    shown = pinning_tests(clone, "Something unrelated", evidence)
    assert "def test_default_table_rows" in shown


def test_nothing_named_shows_nothing(clone: Path):
    assert pinning_tests(clone, "A plain sentence with no names in it") == ""


def test_what_doesnt_fit_is_named_not_dropped(clone: Path, monkeypatch):
    monkeypatch.setattr(pinning, "PINNING_CHAR_CEILING", 40)
    shown = pinning_tests(clone, EPIC)
    assert "not shown because they did not fit" in shown
    assert "tests/test_table.py::test_thresholds_flag_the_table" in shown


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
    assert "**Behaviour merged tests pin** — opt-in: only with --thresholds" in body
    assert "Behaviour merged tests pin" not in render_story_body(story(), 54, "Flags")
