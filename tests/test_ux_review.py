"""The UX Designer's review of each release as built (crew#830)."""

from __future__ import annotations

from typing import Any

import pytest

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


# --- the review session ------------------------------------------------------------------


def _task(issues: Released, tmp_path) -> Any:
    from crew_org.tools.github_project import Card  # noqa: PLC0415

    ux_review.release_review_tasks(issues, Board(), sink(tmp_path), repos={"site"}, cards=[])
    return Card(
        item_id="I901",
        number=901,
        title="UX review of v0.1.2",
        status="In Progress",
        state="OPEN",
        work_type="Task",
        repo="site",
        labels=("role:ux",),
    )


class Reviewed(Released):
    def __init__(self) -> None:
        super().__init__()
        self.released = {
            "published_at": "2026-10-17T15:57:22Z",
            "upload_url": "https://uploads.github.com/releases/1/assets{?name,label}",
            "assets": [],
        }
        self.uploaded: list[tuple[str, str]] = []

    def upload_release_asset(self, release, path, *, name="", content_type=""):
        self.uploaded.append((name, content_type))
        return {"name": name, "browser_download_url": f"https://github.com/dl/{name}"}


class Workspace:
    def __init__(self, root) -> None:
        self.root = root

    def at_main(self):
        return self.root


def _correction(page: str, width: int, owner: str = "bug", saw: str = "the nav overflows"):
    from crew_org.crews.ux_review_crew import Correction  # noqa: PLC0415

    return Correction(
        page=page,
        width=width,
        saw=saw,
        instead="the nav wraps within 375px",
        standard="WCAG 2.2 1.4.10 Reflow",
        owner=owner,
    )


def _session(monkeypatch, tmp_path, corrections, looked_at):
    """The session, as its `done` answers: what it looked at, and its corrections."""
    from crew_org.crews.ux_review_crew import ReleaseReview  # noqa: PLC0415
    from crew_org.session import steps  # noqa: PLC0415

    ran: dict[str, Any] = {}

    def run(step, parts, reading, *, about, check, context):
        ran.update(step=step, parts=parts, context=context)
        for page, width in looked_at:
            shot = context.keep / f"{width}.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            shot.write_bytes(b"png")
            context.shots[(page, width)] = shot
        answer = ReleaseReview(readers_meet="The overview reads well.", corrections=corrections)
        check(answer)
        return answer

    monkeypatch.setattr(steps, "run", run)
    monkeypatch.setattr(
        steps.Reading, "at", classmethod(lambda cls, root, repo, cov, gh: cls(root, repo, "abc"))
    )
    return ran


class Cards(Board):
    def cards(self):
        return []


def test_every_ux_task_is_its_review_sessions_not_the_developers(tmp_path):
    issues = Released()
    card = _task(issues, tmp_path)
    assert ux_review.is_review(card)
    devops = type(card)(item_id="x", number=1, work_type="Task", labels=("role:devops",))
    assert not ux_review.is_review(devops)


def test_a_review_files_each_correction_with_its_screenshot_and_closes(tmp_path, monkeypatch):
    issues, board, s = Reviewed(), Cards(), sink(tmp_path)
    card = _task(issues, tmp_path)
    corrections = [
        _correction("index.html", 375),
        _correction("replay.html", 768, saw="the player has no labels"),
        _correction("index.html", 1280, owner="devops", saw="the root page only redirects"),
    ]
    ran = _session(
        monkeypatch,
        tmp_path,
        corrections,
        [("index.html", 375), ("replay.html", 768), ("index.html", 1280)],
    )
    assert (
        ux_review.review(
            card, board=board, issues=issues, sink=s, ws=Workspace(tmp_path), repo="site"
        )
        == "done"
    )
    assert ran["step"].tools == ("look",) and ran["step"].step == "release_review"
    assert "review `v0.1.2` as its readers meet it" in ran["parts"]["task"]
    assert "WCAG 2.2 1.4.10 Reflow" in ran["parts"]["task"]
    # Two bugs and a DevOps Task: more than one per owner, each its own issue.
    bugs = [i for i in issues.issues.values() if {"name": "bug"} in i["labels"]]
    assert len(bugs) == 2
    first = bugs[0]["body"]
    assert "reviewing `v0.1.2` as its readers meet it" in first
    assert f"{SITE}index.html at 375px" in first
    assert "**Standard:** WCAG 2.2 1.4.10 Reflow" in first
    assert "![`index.html` at 375px](https://github.com/dl/ux-v0.1.2-index-html-375.png)" in first
    assert issues.uploaded[0] == ("ux-v0.1.2-index-html-375.png", "image/png")
    assert len(issues.uploaded) == 3
    devops = [i for i in issues.issues.values() if {"name": "role:devops"} in i["labels"]]
    assert len(devops) == 1
    # Closed and Done, with what the readers meet and what was filed, on the Task.
    assert ("close", 901) in issues.done
    assert ("status", "I901", "Done") in board.done
    [said] = [d[2] for d in issues.done if d[0] == "comment" and d[1] == 901]
    assert "The overview reads well." in said and "**Corrections filed:**" in said


def test_a_correction_on_a_page_it_never_looked_at_is_refused(tmp_path, monkeypatch):
    """`done` refuses it in the session; here, the check the session is given."""
    from crew_org.rulebook import Refusal  # noqa: PLC0415

    issues, board, s = Reviewed(), Cards(), sink(tmp_path)
    card = _task(issues, tmp_path)
    _session(monkeypatch, tmp_path, [_correction("replay.html", 375)], [("index.html", 375)])
    with pytest.raises(Refusal, match="You didn't look at `replay.html` at 375px"):
        from crew_org.crews import ux_review_crew  # noqa: PLC0415
        from crew_org.crews.ux_review_crew import ReleaseReview  # noqa: PLC0415

        ux_review_crew.check_seen(
            ReleaseReview(readers_meet="x", corrections=[_correction("replay.html", 375)]),
            {("index.html", 375): tmp_path},
        )
    # Raised from the session, the review blocks for a person with why.
    ended = ux_review.review(
        card, board=board, issues=issues, sink=s, ws=Workspace(tmp_path), repo="site"
    )
    assert ended == "blocked"
    assert ("status", "I901", "Blocked") in board.done
    assert ("label", 901, "blocked") in issues.done


def test_a_review_with_no_release_blocks_for_a_person(tmp_path, monkeypatch):
    issues, board, s = Reviewed(), Cards(), sink(tmp_path)
    card = _task(issues, tmp_path)
    issues.released = None
    _session(monkeypatch, tmp_path, [], [])
    assert (
        ux_review.review(
            card, board=board, issues=issues, sink=s, ws=Workspace(tmp_path), repo="site"
        )
        == "blocked"
    )
    [said] = [d[2] for d in issues.done if d[0] == "comment" and d[1] == 901]
    assert "no live release" in said


def test_a_screenshot_that_wont_upload_leaves_its_correction_filed(tmp_path, monkeypatch):
    issues, board, s = Reviewed(), Cards(), sink(tmp_path)
    card = _task(issues, tmp_path)

    def refused(*a, **kw):
        raise RuntimeError("422 already_exists")

    issues.upload_release_asset = refused
    _session(monkeypatch, tmp_path, [_correction("index.html", 375)], [("index.html", 375)])
    ux_review.review(card, board=board, issues=issues, sink=s, ws=Workspace(tmp_path), repo="site")
    [bug] = [i for i in issues.issues.values() if {"name": "bug"} in i["labels"]]
    assert "The screenshot wasn't uploaded: 422 already_exists" in bug["body"]


def test_a_screenshot_already_on_the_release_is_used_again():
    issues = Reviewed()
    name = ux_review.shot_name("v0.1.2", "index.html", 375)
    issues.released["assets"] = [{"name": name, "browser_download_url": "https://x/old.png"}]
    said = ux_review.screenshot(
        issues, issues.released, "v0.1.2", _correction("index.html", 375), None
    )
    assert said.endswith("(https://x/old.png)") and issues.uploaded == []


def test_more_than_one_finding_per_owner_only_where_the_caller_says(tmp_path):
    """A story's reviewers still file one per owner each round; a release review, more."""
    from crew_org.tools.github_project import Card  # noqa: PLC0415

    found = [ux_review.Found("a", "w", "e", "bug"), ux_review.Found("b", "w", "e", "bug")]
    story = Card(item_id="I5", number=5, repo="site")
    issues = Issues()
    one = role_tasks.file_findings(
        issues,
        Board(),
        sink(tmp_path),
        repo="site",
        story=story,
        found=found,
        by="QA",
        round_key="r",
        cards=[],
    )
    assert len(one) == 1
    both = role_tasks.file_findings(
        issues,
        Board(),
        sink(tmp_path),
        repo="site",
        story=story,
        found=found,
        by="QA",
        round_key="r2",
        cards=[],
        cap=10,
    )
    assert len(both) == 2 and len(set(both)) == 2
