"""The UX Designer's review of each release as built (crew#830)."""

from __future__ import annotations

from typing import Any

from crew_org.flows import role_tasks, ux_review
from crew_org.project import RECORD_PATH
from tests.role_task_fakes import Board, Issues, events, sink

RECORD = """
intent:
  scope:
    serves:
    - {who: the Sponsor, needs: [a sprint overview]}
    - {who: a public visitor, needs: [what the crew is, in plain words]}
    purpose: Show how the crew works.
    out_of_scope: [a backend]
  release:
    deploys: true
    where: GitHub Pages
  done:
    bar: Its tests pass.
"""
SITE = "https://mqucifer.github.io/site/"


class Released(Issues):
    """A project with a record, a version, its Release and a Pages site."""

    def __init__(self, *, live_since: str | None = "2026-10-21T12:58:58Z") -> None:
        super().__init__()
        self.files = {
            RECORD_PATH: RECORD,
            "pyproject.toml": 'name = "site"\nversion = "0.1.2"\n',
            "CHANGELOG.md": "## [0.1.2] - 2026-10-17\n### Fixed\n- The nav fits a phone.\n",
        }
        self.site: dict[str, Any] | None = {"html_url": SITE}
        self.released: dict[str, Any] | None = {"published_at": "2026-10-17T15:57:22Z"}
        self.live_since = live_since
        self.asked: list[str] = []

    def pages(self, repo: str) -> dict[str, Any] | None:
        return self.site

    def repository(self, repo: str) -> dict[str, Any]:
        return {"default_branch": "main"}

    def release_for_tag(self, repo: str, tag: str) -> dict[str, Any] | None:
        self.asked.append(tag)
        return self.released

    def pages_live_since(self, repo: str) -> str | None:
        return self.live_since


def test_a_release_live_on_pages_opens_one_review_task_per_version(tmp_path):
    issues, board, s = Released(), Board(), sink(tmp_path)
    opened = ux_review.release_review_tasks(issues, board, s, repos={"site"}, cards=[])
    assert opened.opened == [("site", 901)]
    assert issues.asked == ["v0.1.2"]
    task = issues.issues[901]
    found = role_tasks.marked(task["body"])
    assert (found.role, found.kind, found.key) == (role_tasks.UX, role_tasks.REVIEW, "v0.1.2")
    assert [lab["name"] for lab in task["labels"]] == ["role:ux"]
    # Who reads it, from the record, and what the version changed.
    assert "**the Sponsor**, who needs: a sprint overview" in task["body"]
    assert "**a public visitor**" in task["body"]
    assert "- The nav fits a phone." in task["body"]
    assert SITE in task["body"]
    assert ("number", "item-N901", "Points", ux_review.REVIEW_POINTS) in board.done
    [event] = [e for e in events(tmp_path) if e["kind"] == "role_task.opened"]
    assert event["detail"]["event"] == "release.live"
    assert event["detail"]["rule"] == "decision.ux_review.release_review_tasks"

    again = ux_review.release_review_tasks(issues, board, s, repos={"site"}, cards=[])
    assert again.opened == []
    assert len(issues.issues) == 1


def test_a_version_reviewed_and_closed_is_never_reviewed_again(tmp_path):
    issues, board, s = Released(), Board(), sink(tmp_path)
    ux_review.release_review_tasks(issues, board, s, repos={"site"}, cards=[])
    issues.issues[901]["state"] = "closed"
    assert ux_review.release_review_tasks(issues, board, s, repos={"site"}, cards=[]).opened == []


def test_a_pages_deployment_before_the_release_is_the_version_before(tmp_path):
    issues = Released(live_since="2026-10-16T10:00:00Z")
    found = ux_review.release_review_tasks(issues, Board(), sink(tmp_path), repos={"s"}, cards=[])
    assert found.opened == [] and issues.issues == {}


def test_no_successful_deployment_no_review(tmp_path):
    issues = Released(live_since=None)
    assert ux_review.live(issues, "site") is None


def test_no_pages_site_or_no_reader_or_no_release_means_no_review():
    no_site = Released()
    no_site.site = None
    assert ux_review.live(no_site, "site") is None

    no_reader = Released()
    no_reader.files[RECORD_PATH] = ""
    assert ux_review.live(no_reader, "site") is None

    unreadable = Released()
    unreadable.files[RECORD_PATH] = "intent: {}"
    assert ux_review.live(unreadable, "site") is None

    unreleased = Released()
    unreleased.released = None
    assert ux_review.live(unreleased, "site") is None

    unversioned = Released()
    unversioned.files["pyproject.toml"] = 'version = "0.0.0"\n'
    assert ux_review.live(unversioned, "site") is None


def test_a_javascript_project_says_its_version_in_package_json():
    issues = Released()
    issues.files = {"package.json": '{"name": "site", "version": "2.0.1"}'}
    assert ux_review.version_of(issues, "site", "main") == "2.0.1"
    issues.files = {}
    assert ux_review.version_of(issues, "site", "main") == ""


def test_a_review_github_refused_is_held_and_tried_next_pass(tmp_path):
    issues, board, s = Released(), Board(), sink(tmp_path)
    issues.fail = "create"
    found = ux_review.release_review_tasks(issues, board, s, repos={"site"}, cards=[])
    assert found.opened == []
    [(repo, why)] = found.failed
    assert repo == "site" and "UX Designer's review task not opened" in why
    issues.fail = None
    assert ux_review.release_review_tasks(issues, board, s, repos={"site"}, cards=[]).opened


def test_live_since_is_the_newest_deployment_that_succeeded():
    """A deployment that failed leaves the one before it served."""
    import httpx  # noqa: PLC0415

    from crew_org.tools.github_issues import IssueClient, Trust  # noqa: PLC0415

    statuses = {
        "7": [{"state": "failure", "created_at": "2026-10-22T00:00:00Z"}],
        "6": [{"state": "success", "created_at": "2026-10-21T12:58:58Z"}],
    }
    asked: list[dict[str, str]] = []

    def answer(request):
        path = request.url.path
        if path.endswith("/deployments"):
            asked.append(dict(request.url.params))
            return httpx.Response(
                200,
                json=[
                    {"id": n, "statuses_url": f"https://api.github.com/d/{n}/statuses"}
                    for n in (7, 6)
                ],
            )
        return httpx.Response(200, json=statuses[path.split("/")[2]])

    trust = Trust(sponsor="mquarters", crew=frozenset(), tools=frozenset())
    client = httpx.Client(transport=httpx.MockTransport(answer))
    github = IssueClient("t", "mqucifer", client=client, trust=trust)
    assert github.pages_live_since("site") == "2026-10-21T12:58:58Z"
    assert asked[0]["environment"] == "github-pages"
    statuses["6"] = [{"state": "in_progress", "created_at": "2026-10-21T12:57:22Z"}]
    assert github.pages_live_since("site") is None
