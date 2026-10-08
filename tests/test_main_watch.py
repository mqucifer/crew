"""A workflow failing on a default branch becomes a technical epic (crew#335, phase 1).

Nothing watched `main` after merge: a release workflow failing on the push that
should cut 1.0.0 would have been seen only by someone looking.
"""

from __future__ import annotations

import httpx

from crew_org.events import EventSink
from crew_org.flows.main_watch import RED_MARKER, watch_default_branches
from crew_org.tools.github_issues import IssueClient

RELEASE = ".github/workflows/release.yml"


def run(conclusion: str, *, id: int = 9, path: str = RELEASE, name: str = "release") -> dict:
    return {
        "id": id,
        "path": path,
        "name": name,
        "conclusion": conclusion,
        "head_sha": "abc1234def",
        "html_url": f"https://github.com/mqucifer/sprint-metrics/actions/runs/{id}",
    }


class Issues:
    def __init__(self, latest: dict, *, open_epics: list[dict] | None = None, children=()):
        self.owner = "mqucifer"
        self._latest, self._open, self._children = latest, open_epics or [], list(children)
        self.created, self.comments, self.closed, self.labels = [], [], [], []

    def repository(self, repo):
        return {"default_branch": "main"}

    def latest_runs(self, repo, branch):
        assert branch == "main"
        return self._latest

    def labelled(self, repo, label):
        return self._open

    def failed_jobs(self, repo, run_id):
        return [{"name": "release", "conclusion": "failure", "id": 77}]

    def job_log(self, repo, job_id):
        return "##[group]Run docker/build-push-action\nerror: denied\n##[error]exit code 1\n"

    def ensure_label(self, repo, name, *, color, description):
        self.labels.append(name)

    def create(self, repo, title, body, labels=None):
        self.created.append({"title": title, "body": body, "labels": labels})
        return {"number": 300, "node_id": "N300"}

    def sub_issues(self, repo, number):
        return self._children

    def comment(self, repo, number, body):
        self.comments.append((number, body))

    def close(self, repo, number, *, reason="completed"):
        self.closed.append(number)


class Board:
    def __init__(self):
        self.moves, self.selects = [], []

    def add_issue(self, node_id):
        return "ITEM300"

    def repo_of(self, item_id):
        return None

    def set_status(self, item_id, column):
        self.moves.append((item_id, column))

    def set_select(self, item_id, field, option):
        self.selects.append((item_id, field, option))


def watch(issues, board=None):
    return watch_default_branches(
        issues, board or Board(), EventSink(None), repos={"sprint-metrics"}
    )


def red_epic(workflow: str = RELEASE) -> dict:
    return {"number": 300, "state": "open", "body": RED_MARKER.format(workflow=workflow)}


def test_a_red_default_branch_files_a_technical_epic_with_the_failing_step():
    issues, board = Issues({RELEASE: run("failure")}), Board()
    result = watch(issues, board)
    assert result.filed == [("sprint-metrics", 300)]
    [epic] = issues.created
    assert epic["title"] == "Fix: `release` fails on main"
    assert epic["labels"] == ["technical"]
    assert RED_MARKER.format(workflow=RELEASE) in epic["body"]
    assert "error: denied" in epic["body"] and "actions/runs/9" in epic["body"]
    assert ("ITEM300", "Needs Refinement") in board.moves
    assert ("ITEM300", "Work Type", "Epic") in board.selects


def test_one_epic_per_red_workflow_while_it_is_open():
    issues = Issues({RELEASE: run("failure")}, open_epics=[red_epic()])
    assert watch(issues).filed == [] and issues.created == []


def test_a_green_default_branch_files_nothing():
    issues = Issues({RELEASE: run("success")})
    assert watch(issues).filed == []


def test_green_again_before_any_work_closes_the_epic():
    issues = Issues({RELEASE: run("success")}, open_epics=[red_epic()])
    result = watch(issues)
    assert result.closed == [("sprint-metrics", 300)] and issues.closed == [300]
    assert "Passes again on `abc1234`" in issues.comments[0][1]


def test_an_epic_already_being_worked_is_left_to_its_stories():
    issues = Issues({RELEASE: run("success")}, open_epics=[red_epic()], children=[{"number": 301}])
    assert watch(issues).closed == [] and issues.closed == []


def test_a_closed_epic_does_not_stop_a_new_one():
    issues = Issues({RELEASE: run("failure")}, open_epics=[{**red_epic(), "state": "closed"}])
    assert watch(issues).filed == [("sprint-metrics", 300)]


def test_another_workflows_epic_does_not_count():
    issues = Issues({RELEASE: run("failure")}, open_epics=[red_epic(".github/workflows/tests.yml")])
    assert watch(issues).filed == [("sprint-metrics", 300)]


def test_a_repository_that_cannot_be_read_is_reported_not_raised():
    class Down(Issues):
        def repository(self, repo):
            raise httpx.ConnectError("down")

    result = watch(Down({}))
    assert result.failed and result.failed[0][0] == "sprint-metrics"


def test_the_latest_run_of_each_workflow_is_what_the_branch_is_now():
    runs = [
        run("success", id=3),  # newest release run: fixed
        run("failure", id=2, path=".github/workflows/tests.yml", name="tests"),
        run("failure", id=1),  # an older red release run
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["branch"] == "main"
        assert request.url.params["event"] == "push"
        return httpx.Response(200, json={"workflow_runs": runs})

    client = IssueClient(
        "t", "mqucifer", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    latest = client.latest_runs("sprint-metrics", "main")
    assert latest[RELEASE]["id"] == 3
    assert latest[".github/workflows/tests.yml"]["id"] == 2
