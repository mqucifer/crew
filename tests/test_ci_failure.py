"""A red CI check reaches the Developer with its log (#325).

The crew's own checks run lint and tests in a sandbox with no network, so what
only CI can run (an image build, a release workflow) was proven nowhere the
crew could see: the merge queue removing a pull request for a failed check
was rebuilt from `main` like a conflict, and a red check on the head just sat.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import httpx

from crew_org.flows.ci import CI_MARKER, LOG_TAIL_CHARS, SERVICE_LOG_LINES, job_id, log_tail
from crew_org.flows.delivery import awaiting_rework, prior_verdicts
from crew_org.tools.github_issues import IssueClient, QueueState
from tests import asked
from tests.test_merge import FakeIssues, QueueIssues, card, run

HEAD = "c0ffee"
IMAGE_JOB = {
    "name": "image",
    "status": "completed",
    "conclusion": "failure",
    "details_url": "https://github.com/mqucifer/sprint-metrics/actions/runs/9/job/4242",
}


class Headed(FakeIssues):
    """A pull request whose head is known, as the board's linked pull request gives it."""

    def pull_for_branch(self, repo, branch, *, known=None):
        return {"number": 100, "head": {"ref": branch, "sha": HEAD}}


def red_on_head(**kw) -> Headed:
    issues = Headed(**kw)
    issues.failed_on_head = [IMAGE_JOB]
    return issues


# --- the merge pass sends it back with the log ----------------------------------------------


def test_a_check_red_on_the_head_goes_back_to_the_developer_with_its_log():
    issues = red_on_head()
    result, board, _ = run([card(6)], issues)
    assert result.ci_failed == [(6, 100)]
    assert ("C6", "In Progress") in board.moves
    assert issues.merged == [] and issues.enqueued == []
    [(pull, body)] = issues.posted
    assert pull == 100
    assert CI_MARKER.format(head=HEAD) in body
    assert "**CI failed on its head" in body
    assert "### `image`: failure" in body
    assert "ERROR job 4242" in body, "the job behind the check run, read by its id"
    assert "2026-09-28T03" not in body, "timestamps are noise"


def test_the_same_head_is_reported_once():
    issues = red_on_head()
    issues.earlier = [f"{CI_MARKER.format(head=HEAD)}\nCI failed"]
    run([card(6)], issues)
    assert issues.posted == []


def test_a_green_head_lands_as_before():
    issues = Headed()
    result, _, _ = run([card(6)], issues)
    assert result.ci_failed == [] and issues.merged == [100]


def test_the_queue_removing_it_for_a_failed_check_sends_the_log_not_a_rebuild():
    issues = QueueIssues(QueueState(has_queue=True, removed="CI failed"))
    issues.failed_in_queue = [{"name": "tests", "conclusion": "failure", "id": 77}]
    result, board, _ = run([card(6)], issues)
    assert result.ci_failed == [(6, 100)] and result.rebuilding == []
    assert ("C6", "In Progress") in board.moves
    body = issues.posted[-1][1]
    assert "**CI failed in the merge queue" in body and "ERROR job 77" in body
    assert "crew:rebuild" not in body


def test_the_queue_removing_it_with_no_failed_run_is_still_a_rebuild():
    """A conflict with what landed ahead of it: no run failed, so it's rebuilt on main."""
    issues = QueueIssues(QueueState(has_queue=True, removed="MERGE_CONFLICT"))
    result, _, _ = run([card(6)], issues)
    assert result.rebuilding == [(6, 100)] and result.ci_failed == []


def test_an_unreadable_log_says_why():
    issues = red_on_head()
    issues.job_log = lambda repo, job: ""
    run([card(6)], issues)
    assert "lacks Actions read access" in issues.posted[0][1]


def test_a_read_that_fails_never_fails_the_pass():
    issues = Headed()

    def down(repo, sha):
        raise httpx.ConnectError("down")

    issues.failed_checks = down
    result, _, _ = run([card(6)], issues)
    assert issues.merged == [100] and result.ci_failed == []


# --- the log, readable ------------------------------------------------------------------------


def test_a_log_loses_its_timestamps_and_colour():
    raw = "2026-09-28T03:00:00.1234567Z \x1b[31mERROR\x1b[0m: no such file\n"
    assert log_tail(raw) == "ERROR: no such file"


def test_a_long_log_keeps_its_end_from_a_line_boundary():
    raw = "\n".join(f"line {i}" for i in range(5000)) + "\nthe failure"
    tail = log_tail(raw)
    assert tail.startswith("…\nline ") and tail.endswith("the failure")
    assert len(tail) <= 6_010


def test_the_failing_step_is_shown_not_the_cleanup_after_it():
    """The shape of sprint-metrics PR #31's real log: ruff failed, then post-job cleanup."""
    raw = "\n".join(
        [
            "2026-09-28T03:00:00.0Z ##[group]Run uv sync --extra dev",
            "2026-09-28T03:00:01.0Z  + pytest==9.1.1",
            "2026-09-28T03:00:02.0Z ##[group]Run uv run ruff check .",
            "2026-09-28T03:00:03.0Z ##[endgroup]",
            "2026-09-28T03:00:04.0Z invalid-syntax: Positional argument cannot follow keyword",
            "2026-09-28T03:00:05.0Z Found 1 error.",
            "2026-09-28T03:00:06.0Z ##[error]Process completed with exit code 1.",
            "2026-09-28T03:00:07.0Z Post job cleanup.",
            "2026-09-28T03:00:08.0Z Cleaning up orphan processes",
        ]
    )
    shown = log_tail(raw)
    assert shown.startswith("##[group]Run uv run ruff check .")
    assert "invalid-syntax" in shown and shown.endswith("exit code 1.")
    assert "pytest==" not in shown and "Post job cleanup" not in shown


# --- what the job's services did (crew#839) --------------------------------------------------

REAL_LOG = Path(__file__).parent / "fixtures/ci_logs/sprint-metrics-748-tests.log.gz"


def test_a_real_log_keeps_the_services_start_up_and_own_log_before_the_failure():
    """mqucifer/sprint-metrics#748's third run, as the crew reads it: Postgres started
    healthy, then every test failed to resolve `postgres` from the runner. The failing
    step alone read as a container that hadn't started."""
    shown = log_tail(gzip.decompress(REAL_LOG.read_bytes()).decode())
    assert len(shown) <= LOG_TAIL_CHARS
    services, _, step = shown.partition("\n\n")
    assert services.splitlines()[:2] == [
        "Services, as the runner started them:",
        "- `postgres`: healthy",
    ]
    own = services.split("The end of `postgres`'s own log:\n", 1)[1].splitlines()
    assert len(own) == SERVICE_LOG_LINES
    assert 'FATAL:  role "root" does not exist' in own[-1]
    assert "failed to resolve host 'postgres'" in step
    assert step.endswith("##[error]Process completed with exit code 1.")
    assert "Post job cleanup" not in shown


def test_a_job_with_no_services_shows_the_failing_step_alone():
    raw = "##[group]Run uv run pytest -q\nE   assert 1 == 2\n##[error]Process completed."
    assert log_tail(raw) == raw


def started(label: str, ident: str) -> list[str]:
    return [
        f"##[group]Starting {label} service container",
        f"##[command]/usr/bin/docker start {ident}",
        ident,
        "##[endgroup]",
    ]


def test_a_service_that_failed_its_health_check_says_so_with_the_runners_error():
    raw = "\n".join(
        [
            *started("db", "aa11"),
            *started("cache", "bb22"),
            "##[group]Waiting for all services to be ready",
            "db service is starting, waiting 2 seconds before checking again.",
            "##[error]Failed to initialize, db service is unhealthy.",
            "Print service container logs: x_db",
            "##[command]/usr/bin/docker logs --details aa11",
            "  FATAL: could not open PGDATA",
            "Stop and remove container: x_db",
        ]
    )
    shown = log_tail(raw)
    assert shown.startswith(
        "Services, as the runner started them:\n- `db`: unhealthy\n- `cache`: started\n"
        "##[error]Failed to initialize, db service is unhealthy.\n"
        "The end of `db`'s own log:\n FATAL: could not open PGDATA\n\n"
    )


def test_a_long_service_log_leaves_the_failure_most_of_the_room():
    noisy = [f" line {i} " + "x" * 200 for i in range(SERVICE_LOG_LINES * 3)]
    raw = "\n".join(
        [
            *started("db", "aa11"),
            "db service is healthy.",
            "##[group]Run uv run pytest -q",
            *(f"test {i} failed " + "y" * 80 for i in range(200)),
            "##[error]Process completed with exit code 1.",
            "##[command]/usr/bin/docker logs --details aa11",
            *noisy,
        ]
    )
    shown = log_tail(raw)
    assert len(shown) <= LOG_TAIL_CHARS
    services, _, step = shown.partition("\n\n")
    assert len(services) <= LOG_TAIL_CHARS // 2
    assert f"line {SERVICE_LOG_LINES * 3 - 1} " in services and "line 0 " not in services
    assert step.endswith("exit code 1.")


def test_a_check_run_names_its_job():
    assert job_id(IMAGE_JOB) == 4242
    assert job_id({"id": 77}) == 77


# --- delivery reads it -----------------------------------------------------------------------


def test_a_ci_failure_on_the_current_head_is_rework_due():
    issues = Headed()
    issues.earlier = [f"{CI_MARKER.format(head=HEAD)}\n**CI failed**"]
    assert awaiting_rework(issues, "sprint-metrics", "feat/6-show-metric-6") is not None


def test_a_ci_failure_on_an_older_head_is_not():
    issues = Headed()
    issues.earlier = [f"{CI_MARKER.format(head='0ld')}\n**CI failed**"]
    assert awaiting_rework(issues, "sprint-metrics", "feat/6-show-metric-6") is None


def test_the_developer_is_shown_what_ci_said():
    issues = Headed(reviews=[])
    issues.earlier = [f"{CI_MARKER.format(head=HEAD)}\n### `image`: failure\nERROR job 4242"]
    assert "ERROR job 4242" in prior_verdicts(issues, "sprint-metrics", 6, "feat/6-show-metric-6")


# --- the GitHub reads ------------------------------------------------------------------------


def client(handler) -> IssueClient:
    return IssueClient(
        "tok", "mqucifer", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_a_job_log_follows_githubs_redirect_to_storage():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"Location": "https://logs.example/4242.txt"})
        return httpx.Response(200, text="the log")

    assert client(handler).job_log("sprint-metrics", 4242) == "the log"


def test_a_job_log_without_actions_access_is_empty_not_an_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Resource not accessible by integration"})

    assert client(handler).job_log("sprint-metrics", 4242) == ""


def test_only_finished_failed_checks_count():
    runs = [
        {"name": "tests", "status": "completed", "conclusion": "success"},
        {"name": "image", "status": "completed", "conclusion": "failure"},
        {"name": "lint", "status": "in_progress", "conclusion": None},
        {"name": "docs", "status": "completed", "conclusion": "skipped"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"check_runs": runs})

    assert [r["name"] for r in client(handler).failed_checks("sprint-metrics", HEAD)] == ["image"]


def test_merge_group_failures_are_this_pull_requests_only():
    runs = [
        {"id": 1, "head_branch": "gh-readonly-queue/main/pr-101-aaa"},
        {"id": 2, "head_branch": "gh-readonly-queue/main/pr-100-bbb"},
        {"id": 3, "head_branch": "gh-readonly-queue/main/pr-100-ccc"},
    ]
    jobs = [
        {"name": "tests", "conclusion": "failure", "id": 9},
        {"name": "x", "conclusion": "success"},
    ]
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path.endswith("/actions/runs"):
            assert request.url.params["event"] == "merge_group"
            return httpx.Response(200, json={"workflow_runs": runs})
        return httpx.Response(200, json={"jobs": jobs})

    found = client(handler).merge_group_failures("sprint-metrics", 100)
    assert [j["name"] for j in found] == ["tests"]
    assert asked[-1].endswith("/actions/runs/2/jobs"), "the newest run that carried it"


# --- QA can cite CI --------------------------------------------------------------------------


def test_qa_is_shown_the_checks_on_the_commit_it_judges():
    from crew_org.flows.acceptance import ci_checks

    class Checks:
        def check_runs(self, repo, sha):
            assert sha == HEAD
            return [{"name": "image", "status": "completed", "conclusion": "success"}]

    assert "- `image`: success" in ci_checks(Checks(), "sprint-metrics", HEAD)


def test_checks_that_cannot_be_read_are_left_out():
    from crew_org.flows.acceptance import ci_checks

    class Down:
        def check_runs(self, repo, sha):
            raise httpx.ConnectError("down")

    assert ci_checks(Down(), "sprint-metrics", HEAD) == ""


def test_qa_is_handed_the_checks(monkeypatch, tmp_path):
    from crew_org.crews.qa_crew import QAVerdict
    from crew_org.events import EventSink
    from crew_org.flows import acceptance as flow
    from crew_org.tools.github_project import Card
    from tests.test_acceptance import _green, _QABoard, _QAIssues, _QAWorkspace, criterion

    seen = {}

    def fake_verify(story, *, test_output, test_code, prior_verdicts="", **kw):
        seen.update(kw)
        return QAVerdict(summary="ok", accepted=True, criteria=[criterion()])

    monkeypatch.setattr(flow, "verify_story", fake_verify)
    monkeypatch.setattr(flow.workspace, "check", lambda w, sandbox=None: _green())
    monkeypatch.setattr(flow, "collect_tests", lambda w: "def test_x(): pass")
    monkeypatch.setattr(flow, "ci_checks", lambda issues, repo, sha: "- `image`: success")
    card_ = Card(
        item_id="C1",
        number=1,
        title="Ship the image",
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
        cards=[card_],
        repo="sprint-metrics",
    )
    assert seen["checks"] == "- `image`: success"


def test_qa_is_told_a_check_proves_only_what_it_runs(monkeypatch):
    """sprint-metrics#260: QA cited `tests` as proof of release.yml, which it never runs."""
    import contextlib

    from crew_org.crews import qa_crew

    seen = asked.capture(monkeypatch)
    with contextlib.suppress(asked.Stop):
        qa_crew.verify_story("S", test_output="", test_code="", checks="- `tests`: success")
    assert "**that runs it**" in seen["description"]
    assert "a test the run skipped proves nothing" in seen["description"]
