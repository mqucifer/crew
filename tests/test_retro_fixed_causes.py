"""The retro doesn't re-file a failure a mid-sprint fix already solved (#199).

The Sprint 6 retro filed #195 ("… already exists") and #197 ("no test") as
recurring. #183 fixed both during the sprint, and every occurrence it counted
came before the fix.
"""

from __future__ import annotations

from crew_org.events import EventSink
from crew_org.flows.attempts import Cause
from crew_org.flows.retro import CAUSE_MARKER, FINDING_LABEL, RetroRecord, _file_recurring
from tests.test_retro import CREW, FakeIssues


def cause(*occurrences: tuple[str, int]) -> Cause:
    cards = sorted({card for _at, card in occurrences})
    return Cause("EDIT", "… already exists", len(occurrences), cards, "exists", list(occurrences))


BEFORE = [("2026-09-25T20:00:00Z", 93), ("2026-09-25T20:30:00Z", 95)]


class Issues(FakeIssues):
    def __init__(self, *, findings=(), pulls=()):
        super().__init__(existing=list(findings))
        self._pulls = list(pulls)

    def closed_pulls(self, repo):
        return self._pulls


def run(issues, the_cause):
    record = RetroRecord()
    lines = _file_recurring(
        issues, EventSink(None), [the_cause], sprint="Sprint 6", crew_repo=CREW, record=record
    )
    return lines, record


def closed_finding(key: str, closed_at: str) -> dict:
    return {
        "number": 195,
        "labels": [FINDING_LABEL],
        "state": "closed",
        "state_reason": "completed",
        "closed_at": closed_at,
        "body": CAUSE_MARKER.format(key=key),
    }


def test_a_cause_fixed_after_every_occurrence_is_not_filed_and_cites_the_fix():
    c = cause(*BEFORE)
    issues = Issues(findings=[closed_finding(c.key, "2026-09-25T22:00:00Z")])
    lines, record = run(issues, c)
    assert issues.created == [] and record.filed == []
    assert lines == [f"- recurred, and was fixed during the sprint by {CREW}#195: … already exists"]


def test_a_merged_pull_request_naming_the_cause_is_a_fix_too():
    """#183 fixed it before any finding existed."""
    c = cause(*BEFORE)
    pull = {
        "number": 183,
        "merged_at": "2026-09-25T22:00:00Z",
        "body": CAUSE_MARKER.format(key=c.key),
    }
    lines, _ = run(Issues(pulls=[pull]), c)
    assert lines == [
        f"- recurred, and was fixed during the sprint by {CREW} PR #183: … already exists"
    ]


def test_recurring_after_the_fix_is_filed_counting_only_what_came_after():
    after = [("2026-09-26T09:00:00Z", 125), ("2026-09-26T10:00:00Z", 126)]
    c = cause(*BEFORE, *after)
    issues = Issues(findings=[closed_finding(c.key, "2026-09-25T22:00:00Z")])
    _, record = run(issues, c)
    (filed,) = issues.created
    assert "Seen 2 times, on 2 of the sprint's cards: #125, #126." in filed["body"]
    assert "#93" not in filed["body"], "evidence from before the fix isn't counted"


def test_once_after_the_fix_is_noted_not_filed():
    c = cause(*BEFORE, ("2026-09-26T09:00:00Z", 125))
    issues = Issues(findings=[closed_finding(c.key, "2026-09-25T22:00:00Z")])
    lines, _ = run(issues, c)
    assert issues.created == []
    assert lines[0].endswith("(once since, on #125)")


def test_a_finding_closed_as_not_planned_is_no_fix():
    c = cause(*BEFORE)
    declined = closed_finding(c.key, "2026-09-25T22:00:00Z") | {"state_reason": "not_planned"}
    run_issues = Issues(findings=[declined])
    run(run_issues, c)
    assert len(run_issues.created) == 1, "declined isn't fixed: it recurs and is filed"
