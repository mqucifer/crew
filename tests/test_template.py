"""Every crew project's own files come from mqucifer/project-template (crew#690, ADR 0030).

crew-presentation, the first project taken from an empty repository, had no CI on
pull requests and committed `.DS_Store` and `.pyc` files: sprint-metrics had both
by hand, so no plan had named them.
"""

from __future__ import annotations

from pathlib import Path

from crew_org.flows import template
from crew_org.flows.onboard import open_record_pr, validate
from crew_org.project import is_protected, protected
from tests.test_onboard import COMPLETE, TEMPLATE, FakeIssues, FakeWorkspace


def test_the_template_s_files_come_without_its_own_readme():
    found = template.files(FakeIssues())
    assert "README.md" not in found
    assert found[".github/workflows/checks.yml"] == "name: checks\n"


def test_a_project_without_them_is_proposed_all_of_them(tmp_path: Path):
    ws, issues = FakeWorkspace(tmp_path), FakeIssues()
    open_record_pr(ws, issues, "site", validate(COMPLETE), base="main")
    for path in TEMPLATE:
        assert (tmp_path / path).exists() == (path != "README.md"), path
    body = issues.pulls[0]["body"]
    assert "mqucifer/project-template" in body and "`.github/workflows/checks.yml`" in body


def test_the_crew_s_files_catch_up_and_the_project_s_own_are_kept(tmp_path: Path):
    """A `.gitignore` the project added to is its own; CI is the crew's."""
    merged = tmp_path / "merged"
    for path, text in {
        ".gitignore": ".DS_Store\nsite/build/\n",
        ".github/workflows/checks.yml": "name: checks (old)\n",
    }.items():
        (merged / path).parent.mkdir(parents=True, exist_ok=True)
        (merged / path).write_text(text)

    proposed = template.wanted(template.files(FakeIssues()), merged)

    assert ".gitignore" not in proposed
    assert proposed[".github/workflows/checks.yml"] == "name: checks\n"
    assert ".github/crew/run_checks.py" in proposed


def test_no_story_changes_the_crew_s_files():
    rules = protected(None)
    for path in (
        ".github/workflows/checks.yml",
        ".github/workflows/board.yml",
        ".github/crew/run_checks.py",
    ):
        assert is_protected(path, rules), path
    assert not is_protected(".github/workflows/deploy.yml", rules)
