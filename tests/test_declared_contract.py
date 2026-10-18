"""A story declares the merged tests it changes, and the gates read it (#316, #311).

The Business Analyst writes `contract change: <the tests it updates>` when a
story changes what merged tests assert. Nothing in the regression guard read
it: sprint-metrics's docs redesign ends by removing `tests/test_readme.py`,
declared, and would have been refused three times and blocked, as #200 was. The
gates at `done` read the line against what `done` declares (crew#778,
`test_gates.py`).
"""

from __future__ import annotations

from crew_org.flows.board_flow import existing_tests_line, render_story_body
from crew_org.flows.delivery import declared_contract
from tests.test_pinning import story as split_story


def test_stories_written_before_311_are_still_read():
    body = "**Behaviour merged tests pin** — contract change: tests/test_readme.py\n"
    assert declared_contract(body) == {"tests/test_readme.py"}


def test_what_is_declared_is_read_from_the_line_only():
    body = "Criterion: tests/test_docs.py regenerates each section.\n\n" + existing_tests_line(
        "contract change: tests/test_readme.py::test_readme_installation"
    )
    assert declared_contract(body) == {"tests/test_readme.py::test_readme_installation"}


# --- the story reads right (#311) ------------------------------------------------------------


def test_opt_in_no_longer_reads_as_optional_tests():
    body = render_story_body(
        split_story(pinned_behaviour="opt-in: only with --thresholds"), 54, "F"
    )
    assert (
        "**Existing tests** — unchanged: the new behaviour is opt-in (only with --thresholds)"
        in (body)
    )
    assert "Behaviour merged tests pin" not in body


def test_a_contract_change_names_what_it_changes():
    line = existing_tests_line("contract change: tests/test_readme.py")
    assert line == "**Existing tests** — this story changes what they assert: tests/test_readme.py"


def test_a_story_that_touches_no_merged_test_has_no_line():
    assert "Existing tests" not in render_story_body(split_story(), 54, "F")
