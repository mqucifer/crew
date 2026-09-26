"""User docs change in the same pull request as the story (#191).

sprint-metrics shipped `--sprint-range`, `--thresholds` and four output
formats in a day and its README stayed at 14 lines. Sprint 7 added
`--schema`, `/json` and prior-sprint deltas the same way. The Definition of
Done asked for docs in the same pull request; nothing asked the Business
Analyst for a criterion, and nothing checked the diff.
"""

from __future__ import annotations

from crew_org.events import EventSink
from crew_org.flows import review as review_flow
from crew_org.flows.review import review_open_pulls, user_docs, with_docs_finding
from crew_org.project import brief, parse
from crew_org.tools.review_evidence import added_options, undocumented_options
from tests.test_project import SPRINT_METRICS
from tests.test_review import APPROVAL, BOT, FakeIssues, pull

DIFF = """diff --git a/src/sprint_metrics/cli.py b/src/sprint_metrics/cli.py
+++ b/src/sprint_metrics/cli.py
+    parser.add_argument("--schema", action="store_true")
+    parser.add_argument("-t", "--thresholds", type=Path)
+    parser.add_argument("--debug-dump", help=argparse.SUPPRESS)
 parser.add_argument("--json")
diff --git a/tests/test_cli.py b/tests/test_cli.py
+++ b/tests/test_cli.py
+    p.add_argument("--only-in-a-test")
"""

WITH_DOCS = SPRINT_METRICS + "design:\n  docs: [docs/usage.md, README.md]\n"


def test_the_options_a_diff_adds_are_found_outside_its_tests():
    assert added_options(DIFF) == ["--schema", "--thresholds"], (
        "a hidden option, an unchanged line and a test's parser are not user-facing"
    )


def test_an_option_no_user_doc_mentions_is_undocumented():
    docs = {"README.md": "Use `--thresholds` to flag metrics."}
    assert undocumented_options(DIFF, ["README.md"], docs.get) == ["--schema"]


def test_a_doc_that_does_not_exist_documents_nothing():
    assert undocumented_options(DIFF, ["README.md"], lambda p: None) == ["--schema", "--thresholds"]


def test_a_diff_adding_no_option_asks_nothing():
    """#191, criterion 5."""
    assert undocumented_options("+++ b/src/a.py\n+x = 1\n", ["README.md"], lambda p: "") == []


def test_an_approval_with_an_undocumented_option_becomes_a_request_for_changes():
    verdict = with_docs_finding(APPROVAL, DIFF, ["README.md"], {"README.md": "--thresholds"}.get)
    assert not verdict.approve
    (finding,) = verdict.findings
    assert finding.blocking and finding.file == "README.md"
    assert "`--schema`" in finding.concern and "README.md" in finding.action


def test_a_documented_change_keeps_its_verdict():
    docs = {"README.md": "--schema and --thresholds"}.get
    assert with_docs_finding(APPROVAL, DIFF, ["README.md"], docs) is APPROVAL


def test_where_user_docs_live_comes_from_the_design_else_the_readme():
    """#191, criterion 4."""
    assert parse(WITH_DOCS).user_docs == ["docs/usage.md", "README.md"]
    assert parse(SPRINT_METRICS).user_docs == ["README.md"]

    class Record(FakeIssues):
        def __init__(self, text):
            super().__init__([])
            self.files = {".crew/project.yaml": text} if text else {}

    assert user_docs(Record(WITH_DOCS), "r", "head") == ["docs/usage.md", "README.md"]
    assert user_docs(Record(None), "r", "head") == ["README.md"], "no record: the README"
    assert user_docs(Record("not: [a record"), "r", "head") == ["README.md"]


def test_every_agent_shown_the_record_is_told_where_user_docs_live():
    assert "**A user reads how to use it in:** `docs/usage.md`, `README.md`" in brief(
        parse(WITH_DOCS)
    )


def test_the_review_itself_requests_the_docs(monkeypatch):
    """#191, criterion 3: the reviewer approved, the check did not."""

    class Diffing(FakeIssues):
        def pull_diff(self, repo, number):
            return DIFF

    issues = Diffing([pull(title="Add --schema")])
    issues.files = {"README.md": "Use --thresholds to flag metrics."}
    monkeypatch.setattr(review_flow, "review_diff", lambda *a, **k: APPROVAL)
    review_open_pulls(issues, EventSink(None), repo="sprint-metrics", bot_login=BOT)

    ((_, event, body),) = issues.submitted
    assert event == "REQUEST_CHANGES"
    assert "`--schema` is added by this change" in body


def test_the_business_analyst_is_asked_for_a_docs_criterion(monkeypatch):
    """#191, criteria 1 and 5: a story a user sees names its doc change; others don't."""
    import pytest

    from crew_org.crews import refinement_crew

    seen: dict = {}

    class Stop(Exception):
        pass

    def crew(**kwargs):
        raise Stop

    monkeypatch.setattr(refinement_crew, "build_agents", lambda *a: {"business_analyst": None})
    monkeypatch.setattr(refinement_crew, "Task", lambda **k: seen.update(k))
    monkeypatch.setattr(refinement_crew, "Crew", crew)
    with pytest.raises(Stop):
        refinement_crew.split_epic("Expose the API")
    assert "carries a criterion naming its documentation change" in seen["description"]
    assert "changes nothing a user sees needs no such criterion" in seen["description"]
