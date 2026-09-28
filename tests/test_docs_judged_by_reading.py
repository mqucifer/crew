"""Documentation is proven by reading it, not by a test (§7.1, crew#324).

sprint-metrics's first docs stories were written with criteria like "README.md
contains the strings 'cycle time', 'lead time'", and tests/test_readme.py
pinned the prose word for word. The docs redesign (#250) moved them into
docs/ files, where a generated section is checked against the code and the
examples run. What is left is prose, and the Sponsor's rule is that it's judged
by the Code Reviewer and QA reading it, not by a test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from crew_org.crews.delivery_crew import (
    STANDING_INSTRUCTIONS,
    FileEdit,
    FileWrite,
    FirstAttempt,
    FirstOrDone,
    TextEdit,
    is_doc,
)
from crew_org.crews.qa_crew import CriterionVerdict, QAVerdict
from crew_org.events import EventSink
from crew_org.flows.acceptance import collect_docs
from crew_org.tools.regression import merged_base
from tests.test_regression_merged import git

PROSE = TextEdit(
    path="docs/metrics.md",
    find="<!-- END generated -->\n",
    replace="<!-- END generated -->\n\nCycle time counts completed cards only.\n",
)


@pytest.mark.parametrize("answer", [FirstAttempt, FirstOrDone])
def test_a_change_to_docs_alone_needs_no_test(answer):
    assert answer(criteria_tests=[], summary="Explain cycle time", text_edits=[PROSE]).docs_only


@pytest.mark.parametrize("answer", [FirstAttempt, FirstOrDone])
def test_a_new_doc_needs_no_test(answer):
    answer(
        criteria_tests=[],
        summary="Add a changelog",
        new_files=[FileWrite(path="CHANGELOG.md", content="# x\n")],
    )


@pytest.mark.parametrize("answer", [FirstAttempt, FirstOrDone])
def test_docs_and_code_together_still_need_a_test(answer):
    code = FileEdit(
        path="src/sprint_metrics/metrics.py",
        operation="replace",
        target="cycle_time",
        source="def cycle_time(cards):\n    return 0\n",
    )
    with pytest.raises(ValidationError, match="no test"):
        answer(
            criteria_tests=[],
            summary="Change cycle time and explain it",
            text_edits=[PROSE],
            edits=[code],
        )


@pytest.mark.parametrize("answer", [FirstAttempt, FirstOrDone])
def test_a_config_file_is_not_a_doc(answer):
    config = TextEdit(path="pyproject.toml", find="[tool.ruff]\n", replace="[tool.ruff]\n# x\n")
    with pytest.raises(ValidationError, match="no test"):
        answer(criteria_tests=[], summary="Tweak config", text_edits=[config])


def test_what_counts_as_a_doc():
    assert is_doc("README.md")
    assert is_doc("docs/metrics.md")
    assert is_doc("CHANGELOG.md")
    assert not is_doc("tests/fixtures/expected.md"), "a fixture a test reads is the test's"
    assert not is_doc("src/sprint_metrics/_docs_gen.py")
    assert not is_doc(".crew/project.yaml")


def test_the_developer_is_told_not_to_pin_prose():
    assert "Don't write a test that matches a doc's wording" in STANDING_INSTRUCTIONS


def test_qa_may_prove_a_doc_criterion_by_quoting_the_doc():
    verdict = QAVerdict(
        summary="The doc explains cycle time",
        accepted=True,
        criteria=[
            CriterionVerdict(
                criterion="docs/metrics.md explains which cards cycle time counts",
                proven=True,
                evidence="docs/metrics.md: 'Cycle time counts completed cards only.'",
            )
        ],
    )
    assert verdict.accepted


# --- QA reads the docs the branch changed ----------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/metrics.md").write_text("# Metrics\n")
    (tmp_path / "docs/usage.md").write_text("# Usage\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/m.py").write_text("x = 1\n")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "merged")
    git(tmp_path, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    git(tmp_path, "checkout", "-qb", "docs/178")
    return tmp_path


def test_qa_is_shown_the_docs_the_branch_changed_and_only_those(repo: Path):
    (repo / "docs/metrics.md").write_text("# Metrics\n\nCycle time counts completed cards.\n")
    (repo / "src/m.py").write_text("x = 2\n")
    shown = collect_docs(repo)
    assert "# docs/metrics.md" in shown
    assert "Cycle time counts completed cards." in shown
    assert "docs/usage.md" not in shown
    assert "src/m.py" not in shown


def test_a_new_doc_is_shown_once_committed(repo: Path):
    (repo / "CHANGELOG.md").write_text("# Changelog\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "changelog")
    assert "# Changelog" in collect_docs(repo)


def test_nothing_is_shown_when_no_doc_changed(repo: Path):
    assert merged_base(repo) is not None
    assert collect_docs(repo) == ""


def test_qa_is_handed_the_docs(monkeypatch, tmp_path):
    from crew_org.flows import acceptance as flow
    from crew_org.tools.github_project import Card
    from tests.test_acceptance import _green, _QABoard, _QAIssues, _QAWorkspace, criterion

    seen = {}

    def fake_verify(
        story, *, test_output, test_code, prior_verdicts="", project="", docs="", checks=""
    ):
        seen["docs"] = docs
        return QAVerdict(summary="ok", accepted=True, criteria=[criterion()])

    monkeypatch.setattr(flow, "verify_story", fake_verify)
    monkeypatch.setattr(flow.workspace, "check", lambda w, sandbox=None: _green())
    monkeypatch.setattr(flow, "collect_tests", lambda w: "def test_x(): pass")
    monkeypatch.setattr(flow, "collect_docs", lambda w: "# docs/metrics.md\nCycle time…")
    card = Card(
        item_id="C1",
        number=1,
        title="Explain cycle time",
        status="QAing",
        state="OPEN",
        work_type="Story",
        repo="sprint-metrics",
    )
    flow.run_qa(
        _QABoard(),
        _QAIssues(comments=[]),
        EventSink(None),
        _QAWorkspace(tmp_path),
        None,
        cards=[card],
        repo="sprint-metrics",
    )
    assert seen["docs"].startswith("# docs/metrics.md")
