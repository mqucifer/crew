"""A project's parts, each read by its own language's profile (#404, phase 2b)."""

from __future__ import annotations

import pytest

from crew_org import profiles
from crew_org.flows.delivery import names_a_test
from crew_org.project import Part
from crew_org.tools.review_evidence import unguarded_section

SITE = Part(path="site/", language="javascript", tests=["site/tests/**/*.spec.js"])
SPEC = """const { test, expect } = require("@playwright/test");

test("the health summary is the first section", async ({ page }) => {
  await expect(page.locator("main > section").first()).toHaveAttribute("id", "health");
});
"""


class Design:
    parts = [SITE, Part(path="", language="python")]


class Record:
    design = Design()


@pytest.fixture(autouse=True)
def mixed_project():
    profiles.set_project(Record())
    yield
    profiles.clear()


def test_each_file_gets_its_part_s_profile():
    assert profiles.profile_for("site/index.html").name == "javascript"
    assert profiles.profile_for("src/sprint_metrics/report.py") is profiles.PYTHON
    assert profiles.profile_for("tests/test_report.py").is_test_file("tests/test_report.py")


def test_with_no_parts_every_file_is_python_s():
    profiles.clear()
    assert profiles.profile_for("site/index.html") is profiles.PYTHON


def test_a_generic_part_s_tests_are_its_declared_files_found_by_title():
    js = profiles.profile_for("site/tests/page.spec.js")
    assert js.is_test_file("site/tests/page.spec.js")
    assert not js.is_test_file("site/app.js")
    assert js.tests_defined(SPEC) == ["the health summary is the first section"]
    assert not js.guarded


def test_python_files_in_a_mixed_project_keep_their_tools():
    python = profiles.profile_for("src/sprint_metrics/report.py")
    assert python.guarded


def test_delivery_finds_a_generic_test_by_its_title(tmp_path):
    (tmp_path / "site/tests").mkdir(parents=True)
    (tmp_path / "site/tests/page.spec.js").write_text(SPEC)
    assert names_a_test(
        tmp_path, "site/tests/page.spec.js::the health summary is the first section"
    )
    assert not names_a_test(tmp_path, "site/tests/page.spec.js::a test nobody wrote")


def test_the_reviewer_is_told_which_changed_files_had_no_guard():
    diff = (
        "--- a/site/index.html\n+++ b/site/index.html\n+<main></main>\n"
        "--- a/src/sprint_metrics/report.py\n+++ b/src/sprint_metrics/report.py\n+x = 1\n"
    )
    notice = unguarded_section(diff)
    assert "`site/index.html`" in notice and "report.py" not in notice


def test_a_python_only_change_needs_no_notice():
    diff = "--- a/src/sprint_metrics/report.py\n+++ b/src/sprint_metrics/report.py\n+x = 1\n"
    assert unguarded_section(diff) == ""
