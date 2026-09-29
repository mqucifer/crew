"""Nothing merges without its checks, even where GitHub doesn't enforce them (#335).

`infra` is private, and a private repository on a free organization gets no
branch protection and no merge queue. Where nothing on GitHub holds a pull
request to its checks, the crew merged an approved, accepted one directly: a
check still running, or none at all, didn't stop it. Constitution §19.7 says
nothing merges without them, so the crew keeps it itself.
"""

from __future__ import annotations

from crew_org.flows.merge import Landing, land
from crew_org.tools.github_issues import QueueState
from tests.test_merge import FakeIssues, QueueIssues, card, run

PASSED = {"name": "tests", "status": "completed", "conclusion": "success"}
RUNNING = {"name": "lint", "status": "in_progress", "conclusion": None}
SKIPPED = {"name": "image", "status": "completed", "conclusion": "skipped"}
FAILED = {"name": "tests", "status": "completed", "conclusion": "failure"}


def checks(*runs) -> FakeIssues:
    issues = FakeIssues()
    issues.head_checks = list(runs)
    return issues


def test_all_checks_passed_merges_as_before():
    issues = checks(PASSED, SKIPPED)
    result, board, _ = run([card(6)], issues)
    assert issues.merged == [100] and result.merged == [(6, 100)]


def test_a_check_still_running_waits_for_a_later_pass():
    issues = checks(PASSED, RUNNING)
    result, board, _ = run([card(6)], issues)
    assert issues.merged == []
    assert result.checking == [(6, "checks still running: lint")]
    assert result.failed == [] and board.moves == [], "it waits in Merging; nothing went wrong"


def test_no_checks_at_all_is_not_merged_and_says_why():
    issues = checks()
    result, _, _ = run([card(6)], issues)
    assert issues.merged == []
    [(number, why)] = result.failed
    assert number == 6 and "§19.7" in why


def test_skipped_checks_alone_prove_nothing():
    issues = checks(SKIPPED)
    result, _, _ = run([card(6)], issues)
    assert issues.merged == [] and len(result.failed) == 1


def test_checks_that_cannot_be_read_are_not_taken_as_passing():
    class Down(FakeIssues):
        def check_runs(self, repo, sha):
            raise RuntimeError("503 from GitHub")

    issues = Down()
    result, _, _ = run([card(6)], issues)
    assert issues.merged == []
    [(_, why)] = result.failed
    assert "couldn't be read" in why


def test_a_failed_check_is_refused_where_land_is_called_directly():
    """Sprint close and reverts call `land` without the merge pass's red-check return."""
    landed = land(checks(PASSED, FAILED), "infra", 100)
    assert landed.how == Landing.UNPROVEN and "failed: tests" in landed.reason


def test_a_merge_queue_still_decides_for_itself():
    issues = QueueIssues(QueueState(has_queue=True))
    issues.head_checks = [RUNNING]
    run([card(6)], issues)
    assert issues.enqueued == [("PR_x", "c0ffee")], "the queue runs and waits on the checks"
