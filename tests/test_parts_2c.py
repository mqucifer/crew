"""Phase 2c of #404: QA is told about unguarded changes; the Architect proposes parts."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from crew_org import profiles
from crew_org.crews import qa_crew
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


def test_qa_is_told_which_changed_files_had_no_guard():
    changed = ["site/index.html", "src/pkg/report.py"]
    assert acceptance.unguarded(changed) == ["site/index.html"]
    shown = qa_crew.opening("s", changed=changed, unguarded=acceptance.unguarded(changed))
    assert "**No regression guard ran on:** `site/index.html`." in shown["changed"]


def test_qa_is_told_nothing_more_when_everything_was_guarded():
    assert acceptance.unguarded(["src/pkg/report.py"]) == []
    shown = qa_crew.opening("s", changed=["src/pkg/report.py"])
    assert "No regression guard" not in shown["changed"]


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
