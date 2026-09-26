"""An approval can't also ask for changes (#214).

sprint-metrics PR #139 was approved with five findings saying "remove these
imports". Nothing acts on an approval's findings, so they were dropped. Here
they were wrong; the next time they're right, they'd be dropped just the same.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.crews.review_crew import Finding, ReviewVerdict
from crew_org.flows.review import render_review

ACTION = "Import it back into crew_performance.py with `as`."


def finding(blocking=True) -> Finding:
    return Finding(
        file="src/a.py", concern="The import is unused here.", action=ACTION, blocking=blocking
    )


def test_an_approval_carrying_a_blocking_finding_is_refused():
    with pytest.raises(ValidationError, match="an approval can't carry a finding that blocks"):
        ReviewVerdict(summary="s", approve=True, findings=[finding()])


def test_an_approval_may_carry_notes_and_they_are_shown_as_notes():
    verdict = ReviewVerdict(summary="Sound.", approve=True, findings=[finding(blocking=False)])
    text = render_review(verdict)
    assert "## Notes, not blocking" in text and "## Findings" not in text
    assert ACTION in text


def test_a_request_for_changes_needs_a_finding_that_blocks():
    with pytest.raises(ValidationError, match="no finding that blocks"):
        ReviewVerdict(summary="s", approve=False, findings=[finding(blocking=False)])
    verdict = ReviewVerdict(
        summary="s", approve=False, findings=[finding(), finding(blocking=False)]
    )
    text = render_review(verdict)
    assert text.index("## Findings") < text.index("## Notes, not blocking")


def test_a_finding_blocks_unless_it_says_otherwise():
    assert Finding(file="a.py", concern="c", action=ACTION).blocking
