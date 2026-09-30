"""Phase 2c of #404: QA is told about unguarded changes; the Architect proposes parts."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from crew_org import profiles
from crew_org.crews.design_crew import Choice, DesignProposal, PartChoice
from crew_org.flows import acceptance
from crew_org.flows.design import to_design
from crew_org.project import Part
from crew_org.tools.repo_context import repository_context

FIXTURE = Path(__file__).parent / "fixtures" / "static-site"


class Record:
    class design:  # noqa: N801
        parts = [Part(path="site/", language="javascript", tests=["site/tests/**/*.spec.js"])]


@pytest.fixture(autouse=True)
def mixed_project():
    profiles.set_project(Record())
    yield
    profiles.clear()


def test_qa_is_told_which_changed_files_had_no_guard(monkeypatch, tmp_path):
    monkeypatch.setattr(
        acceptance, "changed_paths", lambda wt: ["site/index.html", "src/pkg/report.py"]
    )
    checks = acceptance._with_unguarded("- tests: passed", tmp_path)
    assert checks.startswith("- tests: passed")
    assert "`site/index.html`" in checks and "report.py" not in checks


def test_qa_s_checks_are_unchanged_when_everything_was_guarded(monkeypatch, tmp_path):
    monkeypatch.setattr(acceptance, "changed_paths", lambda wt: ["src/pkg/report.py"])
    assert acceptance._with_unguarded("- tests: passed", tmp_path) == "- tests: passed"


def test_the_architect_s_parts_reach_the_record():
    design = to_design(
        DesignProposal(
            checks=[Choice(value="npx playwright test", basis="package.json")],
            summary="A static site.",
            parts=[
                PartChoice(
                    path="site/",
                    language="javascript",
                    tests=["site/tests/**/*.spec.js"],
                    basis="package.json and playwright.config.js",
                )
            ],
        )
    )
    assert design.parts == [
        Part(path="site/", language="javascript", tests=["site/tests/**/*.spec.js"])
    ]


def test_a_python_design_declares_no_parts():
    design = to_design(
        DesignProposal(checks=[Choice(value="uv run pytest -q", basis="ci")], summary="s")
    )
    assert design.parts == []


def test_a_generic_part_s_source_and_tests_are_shown_whole(tmp_path):
    site = tmp_path / "site"
    shutil.copytree(FIXTURE, site)
    (site / "node_modules/pkg").mkdir(parents=True)
    (site / "node_modules/pkg/index.js").write_text("module.exports = 'installed';")
    shown = repository_context(site)
    page = (site / "index.html").read_text().strip()
    spec = (site / "tests/page.spec.js").read_text().strip()
    assert page in shown and spec in shown
    assert "node_modules/pkg" not in shown and "'installed'" not in shown
