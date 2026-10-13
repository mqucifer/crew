"""Releasing delivery-history: its tag, its archive, retention, and its consumers' pull requests.

ADR 0021: a release is dated, its archive is the Release's asset, and it reaches
each consumer as a pull request that changes the one line naming its tag (crew#521).
"""

from __future__ import annotations

import json
import tarfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from crew_org.packages import delivery_history as dh
from crew_org.packages import release
from crew_org.tools.github_issues import IssueClient, Trust

DAY = date(2026, 10, 17)
T = datetime(2026, 10, 17, 20, 0, tzinfo=UTC)


def package() -> tuple[dh.DeliveryHistory, dict[str, dh.PeriodEvents], dh.Manifest]:
    return dh.build(
        dh.Inputs(
            events=[],
            cards=[],
            sprints=[],
            pulls={},
            crew_repository="crew",
            repositories=["sprint-metrics"],
            timezone="America/Chicago",
            date=DAY,
            generated=T,
        )
    )


def asset(n: int) -> dict:
    return {"id": n, "name": "archive.tar.gz"}


# --- the tag -----------------------------------------------------------------------------


def test_a_release_is_tagged_by_its_date_and_a_second_that_day_counts_on():
    assert release.next_tag(DAY, ["v1.2.0"]) == "delivery-history-2026-10-17"
    taken = ["delivery-history-2026-10-17", "delivery-history-2026-10-17-2"]
    assert release.next_tag(DAY, taken) == "delivery-history-2026-10-17-3"


def test_only_the_packages_tags_are_read_as_its_releases():
    assert release.tag_day("delivery-history-2026-10-17-2") == (DAY, 2)
    assert release.tag_day("delivery-history-2026-10-17") == (DAY, 1)
    assert release.tag_day("v1.2.0") is None
    assert release.tag_day("model-performance-2026-10-17") is None


# --- what may be released -----------------------------------------------------------------


def test_nothing_is_released_until_sprint_metrics_answers_are_in_it():
    history, _, _ = package()
    assert "sprint-metrics' answers aren't in the package yet" in (release.refusal(history) or "")


def test_the_archive_holds_the_package_its_manifest_and_its_schemas(tmp_path: Path):
    history, files, manifest = package()
    archive = release.write(history, files, manifest, tmp_path / "delivery-history-2026-10-17")
    with tarfile.open(archive) as tar:
        names = {Path(n).name for n in tar.getnames()}
    assert {
        "manifest.json",
        "delivery-history.json",
        "schema.json",
        "events.schema.json",
        "manifest.schema.json",
    } <= names


def test_the_release_notes_say_what_it_holds_and_what_was_left_out():
    _, _, manifest = package()
    manifest = manifest.model_copy(update={"left_out": {"work: pull request unknown": 3}})
    text = release.notes(manifest)
    assert "up to 2026-10-17: 0 events, schema" in text
    assert "- 3 × work: pull request unknown" in text


# --- retention ------------------------------------------------------------------------------


def test_old_archives_go_but_the_last_week_and_each_months_first_stay():
    days = [date(2026, 9, 20) + timedelta(days=i) for i in range(28)]  # Sep 20 to Oct 17
    releases = [
        {"tag_name": f"delivery-history-{d.isoformat()}", "assets": [asset(i)]}
        for i, d in enumerate(days)
    ]
    releases += [
        # A second on one day is its own release.
        {"tag_name": "delivery-history-2026-10-01-2", "assets": [asset(100)]},
        # Already pruned: nothing to delete.
        {"tag_name": "delivery-history-2026-09-25-2", "assets": []},
        # The crew's own software release is never touched.
        {"tag_name": "v1.2.0", "assets": [asset(200)]},
    ]
    pruned = {r["tag_name"].removeprefix("delivery-history-") for r in release.to_prune(releases)}
    kept = {d.isoformat() for d in days} - pruned
    assert kept == {"2026-09-20", "2026-10-01"} | {
        (date(2026, 10, 11) + timedelta(days=i)).isoformat() for i in range(7)
    }
    assert "2026-10-01-2" in pruned
    assert "v1.2.0" not in {r["tag_name"] for r in release.to_prune(releases)}


# --- publishing ---------------------------------------------------------------------------


class Issues:
    """The GitHub calls a release makes, recorded."""

    def __init__(self, *, open_pulls=(), releases=()):
        self.calls: list[tuple] = []
        self._open = list(open_pulls)
        self._releases = list(releases)

    def create_release(self, repo, **kw):
        self.calls.append(("create_release", repo, kw))
        return {"html_url": f"https://github.com/mqucifer/{repo}/releases/tag/{kw['tag']}"}

    def upload_release_asset(self, rel, path):
        self.calls.append(("upload", path.name))

    def releases(self, repo):
        return self._releases

    def delete_release_asset(self, repo, asset_id):
        self.calls.append(("delete_asset", repo, asset_id))

    def open_pulls(self, repo):
        return self._open

    def repository(self, repo):
        return {"default_branch": "main"}

    def create_pull(self, repo, **kw):
        self.calls.append(("create_pull", repo, kw))
        return {"html_url": f"https://github.com/mqucifer/{repo}/pull/40"}

    def comment(self, repo, number, body):
        self.calls.append(("comment", repo, number, body))

    def close_pull(self, repo, number):
        self.calls.append(("close_pull", repo, number))


class Workspace:
    """A consumer's clone: the files a release changes, and what it commits."""

    def __init__(self, root: Path, *, changes: bool = True):
        self.root, self.changes = root, changes
        self.repo: str | None = None
        self.calls: list[tuple] = []

    def for_repo(self, repo):
        self.repo = repo
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def open(self, branch):
        self.calls.append(("open", self.repo, branch))
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def commit(self, message):
        self.calls.append(("commit", message))
        return self.changes

    def push(self, *, force=False):
        self.calls.append(("push", force))


def test_a_release_is_the_tag_with_the_archive_as_its_asset(tmp_path: Path):
    _, _, manifest = package()
    archive = tmp_path / "delivery-history-2026-10-17.tar.gz"
    archive.write_bytes(b"gz")
    issues = Issues()
    release.publish(
        issues, repo="crew", tag="delivery-history-2026-10-17", archive=archive, manifest=manifest
    )
    (_, repo, kw), upload = issues.calls
    assert (repo, kw["tag"], kw["target"], kw["name"]) == (
        "crew",
        "delivery-history-2026-10-17",
        "main",
        "delivery-history-2026-10-17",
    )
    assert upload == ("upload", "delivery-history-2026-10-17.tar.gz")


def test_pruning_deletes_old_archives_and_keeps_every_release():
    old = [
        {"tag_name": f"delivery-history-2026-10-0{d}", "assets": [asset(d)]} for d in range(2, 10)
    ]
    first = {"tag_name": "delivery-history-2026-10-01", "assets": [asset(1)]}
    issues = Issues(releases=[first, *old])
    assert release.prune(issues, "crew") == ["delivery-history-2026-10-02"]
    assert issues.calls == [("delete_asset", "crew", 2)]


def test_a_consumer_gets_a_pull_request_changing_only_the_line_naming_the_tag(tmp_path: Path):
    older = {"number": 31, "head": {"ref": "chore/delivery-history-2026-10-16"}}
    other = {"number": 32, "head": {"ref": "feat/13-latest-sprint"}}
    issues = Issues(open_pulls=[older, other])
    ws = Workspace(tmp_path / "clone")
    url = release.propose(
        ws,
        issues,
        consumer="crew-presentation",
        tag="delivery-history-2026-10-17",
        url="https://github.com/mqucifer/crew/releases/tag/delivery-history-2026-10-17",
    )
    assert url == "https://github.com/mqucifer/crew-presentation/pull/40"
    assert [p.name for p in (tmp_path / "clone").iterdir()] == ["delivery-history.tag"]
    assert (tmp_path / "clone" / "delivery-history.tag").read_text() == (
        "delivery-history-2026-10-17\n"
    )
    assert ws.calls[0] == ("open", "crew-presentation", "chore/delivery-history-2026-10-17")
    pull = next(c for c in issues.calls if c[0] == "create_pull")
    assert pull[2]["base"] == "main"
    assert pull[2]["head"] == "chore/delivery-history-2026-10-17"
    # The older release's pull request is superseded; anyone else's is left alone.
    assert ("close_pull", "crew-presentation", 31) in issues.calls
    assert not any(c[0] == "close_pull" and c[2] == 32 for c in issues.calls)


def test_a_release_already_proposed_or_already_named_opens_nothing(tmp_path: Path):
    same = {"number": 40, "head": {"ref": "chore/delivery-history-2026-10-17"}}
    issues = Issues(open_pulls=[same])
    tag = "delivery-history-2026-10-17"
    outcome = release.propose(
        Workspace(tmp_path / "a"), issues, consumer="crew-presentation", tag=tag, url="u"
    )
    assert outcome.startswith("already proposed")
    issues = Issues()
    outcome = release.propose(
        Workspace(tmp_path / "b", changes=False),
        issues,
        consumer="crew-presentation",
        tag=tag,
        url="u",
    )
    assert outcome == f"already names {tag}"
    assert not any(c[0] == "create_pull" for c in issues.calls)


# --- the GitHub calls -------------------------------------------------------------------------


def client(answer) -> IssueClient:
    return IssueClient(
        "t",
        "mqucifer",
        client=httpx.Client(transport=httpx.MockTransport(answer)),
        trust=Trust(sponsor="s", crew=frozenset(), tools=frozenset()),
    )


def test_a_packages_release_is_never_marked_the_repositorys_latest():
    sent: list[dict] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(201, json={"id": 1, "upload_url": "u{?name,label}"})

    client(answer).create_release(
        "crew", tag="delivery-history-2026-10-17", target="main", name="n", body="b"
    )
    assert sent[0]["make_latest"] == "false"
    assert sent[0]["tag_name"] == "delivery-history-2026-10-17"


def test_the_archive_is_uploaded_under_its_own_name(tmp_path: Path):
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"id": 9})

    archive = tmp_path / "delivery-history-2026-10-17.tar.gz"
    archive.write_bytes(b"gz")
    rel = {"upload_url": "https://uploads.github.com/repos/mqucifer/crew/releases/1/assets{?name}"}
    client(answer).upload_release_asset(rel, archive)
    [request] = seen
    assert str(request.url) == (
        "https://uploads.github.com/repos/mqucifer/crew/releases/1/assets"
        "?name=delivery-history-2026-10-17.tar.gz"
    )
    assert request.headers["Content-Type"] == "application/gzip"
    assert request.content == b"gz"


@pytest.mark.parametrize("count", [3, 100])
def test_every_release_is_paged_through(count: int):
    def answer(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        found = count if page == 1 else 2
        return httpx.Response(200, json=[{"tag_name": f"t{page}-{i}"} for i in range(found)])

    found = client(answer).releases("crew")
    assert len(found) == (3 if count == 3 else 102)
