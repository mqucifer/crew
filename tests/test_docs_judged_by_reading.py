"""Documentation is proven by reading it, not by a test (§7.1, crew#324).

sprint-metrics's first docs stories were written with criteria like "README.md
contains the strings 'cycle time', 'lead time'", and tests/test_readme.py
pinned the prose word for word. The docs redesign (#250) moved them into
docs/ files, where a generated section is checked against the code and the
examples run. What is left is prose, and the Sponsor's rule is that it's judged
by the Code Reviewer and QA reading it, not by a test. QA reads them in its session
(crew#895), and quotes what proves a criterion.
"""

from __future__ import annotations

from crew_org.crews.qa_crew import CriterionVerdict, QAVerdict, Quote, check_cited
from crew_org.flows.acceptance import is_doc


def test_what_counts_as_a_doc():
    assert is_doc("README.md")
    assert is_doc("docs/metrics.md")
    assert is_doc("CHANGELOG.md")
    assert not is_doc("tests/fixtures/expected.md"), "a fixture a test reads is the test's"
    assert not is_doc("src/sprint_metrics/_docs_gen.py")
    assert not is_doc(".crew/project.yaml")


def test_qa_may_prove_a_doc_criterion_by_quoting_the_doc(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/metrics.md").write_text(
        "# Metrics\n\nCycle time counts completed cards only.\n"
    )
    verdict = QAVerdict(
        summary="The doc explains cycle time",
        accepted=True,
        criteria=[
            CriterionVerdict(
                criterion="docs/metrics.md explains which cards cycle time counts",
                proven=True,
                by="read",
                quoted=[
                    Quote(path="docs/metrics.md", text="Cycle time counts completed cards only.")
                ],
                evidence="docs/metrics.md says which cards count, in its first section",
            )
        ],
    )
    check_cited(verdict, ran=[], root=tmp_path, base="", checks={})
    assert verdict.accepted
