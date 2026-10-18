"""What the crew writes on pull requests is signed by the role that wrote it.

Issue comments were signed; pull requests weren't. And what the Sponsor asked
for, where no role wrote the content, is signed Sponsor. A comment nobody asked
for in particular still claims no author (#22).
"""

from __future__ import annotations

from crew_org.crews.review_crew import Finding, ReviewVerdict
from crew_org.flows.design import DESIGN
from crew_org.flows.onboard import ONBOARDING
from crew_org.flows.record_pr import propose
from crew_org.flows.review import render_review
from crew_org.project import parse
from tests.test_onboard import FakeIssues, FakeWorkspace
from tests.test_revert import (
    FakeIssues as RevertIssues,
)
from tests.test_revert import (
    FakeWorkspace as RevertWorkspace,
)
from tests.test_revert import (
    ask,
    card,
    land,
    merged_pull,
    revert_pull,
)

RECORD = parse(
    "version: 2\nintent:\n  scope:\n    purpose: p\n  release:\n    deploys: false\n"
    "  done:\n    bar: tests pass\n"
)


def signed_by(text: str, role: str) -> bool:
    return text.rstrip().endswith(f"— *{role}*")


# --- the roles --------------------------------------------------------------------------


def test_a_review_is_signed_by_the_code_reviewer():
    approve = ReviewVerdict(summary="Sound.", approve=True)
    reject = ReviewVerdict(
        summary="Not yet.",
        approve=False,
        findings=[
            Finding(
                file="a.py",
                concern="The helper duplicates format_table.",
                action="Call format_table instead of the local copy in render_rows.",
            )
        ],
    )
    assert signed_by(render_review(approve), "Code Reviewer")
    assert signed_by(render_review(reject), "Code Reviewer")


def test_an_onboarding_record_pull_request_is_signed_by_the_product_owner(tmp_path):
    issues = FakeIssues()
    propose(FakeWorkspace(tmp_path), issues, "r", RECORD, ONBOARDING, base="main", body=str)
    assert signed_by(issues.pulls[0]["body"], "Product Owner")


def test_a_design_pull_request_and_its_update_are_signed_by_the_architect(tmp_path):
    issues = FakeIssues()
    propose(FakeWorkspace(tmp_path), issues, "r", RECORD, DESIGN, base="main", body=str)
    assert signed_by(issues.pulls[0]["body"], "Architect")

    existing = FakeIssues(
        open_issues=[{"number": 7, "title": DESIGN.issue_title}],
        open_pulls=[
            {"number": 8, "head": {"ref": "chore/7-project-design"}, "html_url": "u/pull/8"}
        ],
    )
    propose(
        FakeWorkspace(tmp_path),
        existing,
        "r",
        RECORD,
        DESIGN,
        base="main",
        body=str,
        update_note="\n\nChanged.",
    )
    ((_, comment),) = existing.comments
    assert signed_by(comment, "Architect")


# --- the Sponsor ------------------------------------------------------------------------


def test_what_the_sponsor_asked_for_with_crew_revert_is_signed_sponsor():
    issues = RevertIssues(pulls=[merged_pull()])
    ask(issues, RevertWorkspace(), [card()])
    assert signed_by(issues.created[0]["body"], "Sponsor")
    ((_, told),) = issues.comments
    assert signed_by(told, "Sponsor")


def test_a_revert_that_conflicts_is_signed_sponsor():
    issues = RevertIssues(pulls=[merged_pull()])
    ask(issues, RevertWorkspace(conflict=["src/table.py"]), [card()])
    assert signed_by(issues.comments[0][1], "Sponsor")


def test_the_card_returning_when_a_revert_lands_claims_no_author():
    """Nobody asked for that comment in particular: #22's rule holds."""
    pull = revert_pull()
    issues = RevertIssues(pulls=[pull], open_=[pull], reviews={50: [{"state": "APPROVED"}]})
    land(issues, [card()])
    assert not any(signed_by(issues.comments[0][1], r) for r in ("Sponsor", "Developer"))
