"""The same CI failure, three returns running, blocks the card for a person (crew#944).

QA's sessions 2 to 4 on mqucifer/qa-suite#8 were each sent back on CI for a failure the
session had already explained: about 18 minutes of model time that changed nothing.
"""

from __future__ import annotations

import pytest

from crew_org import config
from crew_org.columns import BLOCKED, IN_PROGRESS, REVIEWING
from crew_org.events import EventSink
from crew_org.flows import ci
from crew_org.flows.ci import CI_MARKER, failing_tests
from crew_org.tools.github_project import Card
from crew_org.work_types import SUITE, TASK
from tests.test_merge import FakeBoard, FakeIssues

TEST = "crew-presentation/test_reads_crew.py::test_a_sprints_page_shows_its_throughput"


def _log(test: str) -> str:
    return (
        "2026-10-29T03:00:00.0000000Z ##[group]Run uv run --locked pytest crew-presentation\n"
        f"2026-10-29T03:00:01.0000000Z FAILED {test} - AssertionError: 'None' != '—'\n"
        "2026-10-29T03:00:02.0000000Z ##[error]Process completed with exit code 1.\n"
    )


class Issues(FakeIssues):
    def __init__(self, test: str = TEST) -> None:
        super().__init__()
        self.test = test

    def job_log(self, repo, job_id):
        return _log(self.test) if self.test else "nothing named\n##[error]exit 1\n"


def _earlier(*tests: str) -> list[str]:
    return [
        f"{CI_MARKER.format(head=f'old{n}')}\n**CI failed**\n\n```\nFAILED {t} - no\n```"
        for n, t in enumerate(tests)
    ]


def _task() -> Card:
    return Card(
        item_id="I8",
        number=8,
        title="Disputed",
        status=REVIEWING,
        state="OPEN",
        work_type=TASK,
        labels=frozenset({"role:qa", "project:crew-presentation"}),
        repo=SUITE,
    )


def _return(issues, stop=3):
    board = FakeBoard()
    ci.return_for_ci(
        board,
        issues,
        EventSink(None),
        _task(),
        repo=SUITE,
        pull={"number": 10, "head": {"sha": "new"}},
        failed=[{"name": "suite / qa", "conclusion": "failure", "id": 7}],
        where="before review",
        frm=REVIEWING,
        stop=stop,
    )
    return board


def test_the_tests_a_report_failed_on_are_read_from_its_log():
    assert failing_tests(f"x\nFAILED {TEST} - no\nERROR a/b.py::c\n") == {TEST, "a/b.py::c"}
    assert failing_tests("##[error]Process completed with exit code 1.") == frozenset()


def test_the_third_return_running_for_the_same_test_blocks_it_with_the_log():
    issues = Issues()
    issues.earlier = _earlier(TEST, TEST)
    board = _return(issues)
    assert board.moves == [("I8", BLOCKED)]
    assert (8, "blocked") in issues.labels and (8, "needs:human") in issues.labels
    [(number, said)] = issues.posted
    assert number == 8 and "Blocked: CI failed the same tests 3 returns running" in said
    assert f"FAILED {TEST}" in said


@pytest.mark.parametrize(
    "earlier",
    [
        [TEST],
        ["other/test_x.py::test_y", TEST],
        [TEST, "other/test_x.py::test_y"],
    ],
)
def test_fewer_returns_or_another_test_returns_it_as_before(earlier):
    issues = Issues()
    issues.earlier = _earlier(*earlier)
    board = _return(issues)
    assert board.moves == [("I8", IN_PROGRESS)]
    assert "returned to the QA Engineer" in issues.posted[-1][1]


def test_a_failure_naming_no_test_is_never_counted_the_same():
    issues = Issues(test="")
    issues.earlier = [f"{CI_MARKER.format(head=f'old{n}')}\n**CI failed**" for n in range(5)]
    assert _return(issues).moves == [("I8", IN_PROGRESS)]


def test_the_stop_is_org_yaml_s_and_starts_at_three():
    assert config.ci_same_failure_stop_of(config.load_org()) == 3
    assert config.ci_same_failure_stop_of({}) == config.CI_SAME_FAILURE_STOP == 3


def test_a_stop_below_two_is_refused_when_org_yaml_loads(tmp_path):
    text = config.ORG_CONFIG.read_text(encoding="utf-8")
    assert "ci_same_failure_stop: 3" in text
    bad = tmp_path / "org.yaml"
    bad.write_text(text.replace("ci_same_failure_stop: 3", "ci_same_failure_stop: 1"))
    with pytest.raises(ValueError, match="ci_same_failure_stop"):
        config.load_org(bad)
