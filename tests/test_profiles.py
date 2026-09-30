"""The Python profile holds the crew's rules exactly as they were (#404, phase 2a).

Five copies of "is this a test?" lived in five modules, and they didn't quite
agree. The profile keeps both rules they used: named as a test file, and among
the tests (by location or name). These pin each to the copies it replaced.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from crew_org.profiles import PYTHON, profile_for

PATHS = [
    "tests/test_report.py",
    "src/pkg/test_helpers.py",
    "pkg/report_test.py",
    "tests/conftest.py",
    "tests/fixtures/static-site/index.html",
    "src/pkg/report.py",
    "test_page.js",
    "docs/testing.md",
]


def old_developer_rule(path):
    """delivery_crew.py, four copies."""
    name = PurePosixPath(path).name
    return name.startswith("test_") or name.endswith("_test.py")


def old_map_rule(path):
    """repo_context._is_test."""
    rel = PurePosixPath(path)
    return rel.parts[0] == "tests" or rel.name.startswith("test_") or rel.name.endswith("_test.py")


@pytest.mark.parametrize("path", PATHS)
def test_named_as_a_test_file_is_the_developer_s_rule(path):
    assert PYTHON.is_test_file(path) == old_developer_rule(path)


@pytest.mark.parametrize("path", PATHS)
def test_among_the_tests_is_the_map_s_rule(path):
    assert PYTHON.is_test_path(path) == old_map_rule(path)


def test_the_guard_and_review_now_also_see_suffix_named_tests():
    """They used `tests/` or a `test_` name, and missed `*_test.py`: now one rule."""
    assert PYTHON.is_test_path("pkg/report_test.py")


def test_a_test_is_found_by_its_definition():
    source = "import x\n\nasync def test_reports_health(tmp_path):\n    assert 1\n"
    assert PYTHON.defines_test(source, "test_reports_health")
    assert PYTHON.test_exists(source, "test_reports_health")
    assert not PYTHON.test_exists(source, "test_reports")
    assert PYTHON.tests_defined(source) == ["test_reports_health"]


def test_a_test_id_splits_to_its_file_and_name():
    assert PYTHON.split_test_id("tests/test_cli.py::TestFlags::test_schema[json]") == (
        "tests/test_cli.py",
        "test_schema",
    )


def test_tests_named_in_prose_but_not_files():
    assert PYTHON.test_names_in("`test_api_version` in tests/test_report.py") == [
        "test_api_version"
    ]


def test_a_module_is_paired_with_its_test_module():
    assert PYTHON.paired_test("src/sprint_metrics/report.py") == "tests/test_report.py"
    assert PYTHON.paired_test("tests/test_report.py") is None
    assert PYTHON.paired_test("docs/usage.md") is None


def test_only_python_tests_get_fixtures_declared():
    js = "test('x', async ({ page }) => {})"
    assert PYTHON.prepare_test("tests/page.spec.js", js) == js


def test_every_file_is_python_s_until_parts_are_declared():
    assert profile_for("site/index.html") is PYTHON
