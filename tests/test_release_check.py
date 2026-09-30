"""What a release actually published, checked after its run (crew#335).

sprint-metrics#262's approved image push set `provenance: true` (BuildKit's
provenance, not a GitHub artifact attestation) and added no source label. Its
run would be green, so only reading what was published shows it.
"""

from __future__ import annotations

import json

import httpx
import pytest

from crew_org.events import EventSink
from crew_org.flows.release_check import (
    RELEASE_MARKER,
    RELEASE_WORKFLOW,
    changelog_section,
    check_releases,
    gaps,
)
from crew_org.tools.registry import Image, ImageUnreadable, Registry
from tests.test_main_watch import Board

VERSION = "1.0.0"
DIGEST = "sha256:index"
SOURCE = "https://github.com/mqucifer/sprint-metrics"
CHANGELOG = f"""# Changelog

## [Unreleased]

## [{VERSION}] - 2026-10-05

### Added
- The first release.

## [0.9.0]
- older
"""


class FakeRegistry:
    def __init__(self, image=None, tags=None, unreadable=False):
        self._image = image
        self._tags = tags if tags is not None else {"1.0": DIGEST, "latest": DIGEST}
        self._unreadable = unreadable

    def image(self, name, tag):
        if self._unreadable:
            raise ImageUnreadable(
                f"ghcr.io/{name} can't be read anonymously: "
                "the package doesn't exist yet, or isn't public"
            )
        return self._image

    def digest(self, name, tag):
        return self._tags.get(tag)


GOOD_IMAGE = Image(
    digest=DIGEST,
    platforms=["linux/amd64", "linux/arm64"],
    labels={"org.opencontainers.image.source": SOURCE},
)


class Issues:
    owner = "mqucifer"

    def __init__(
        self,
        *,
        head="abc",
        run_sha="abc",
        version=VERSION,
        tag=True,
        release=True,
        notes="- The first release.",
        attested=True,
        epics=(),
    ):
        self._head, self._run_sha, self._version = head, run_sha, version
        self._tag, self._release, self._notes, self._attested = tag, release, notes, attested
        self._epics = list(epics)
        self.created = []

    def repository(self, repo):
        return {"default_branch": "main"}

    def latest_runs(self, repo, branch):
        return {RELEASE_WORKFLOW: {"head_sha": self._run_sha, "conclusion": "success"}}

    def head_sha(self, repo, branch):
        return self._head

    def file_at(self, repo, path, ref):
        if path == "pyproject.toml":
            return f'[project]\nname = "sprint-metrics"\nversion = "{self._version}"\n'
        if path == "CHANGELOG.md":
            return CHANGELOG
        return None

    def labelled(self, repo, label):
        return self._epics

    def tag_exists(self, repo, tag):
        return self._tag

    def release_for_tag(self, repo, tag):
        return {"body": self._notes} if self._release else None

    def attestations(self, repo, digest):
        return [{"bundle": {}}] if self._attested else []

    def ensure_label(self, repo, name, *, color, description):
        pass

    def create(self, repo, title, body, labels=None):
        self.created.append({"title": title, "body": body})
        return {"number": 400, "node_id": "N400"}


def check(issues, registry):
    return check_releases(
        issues, Board(), EventSink(None), repos={"sprint-metrics"}, registry=registry
    )


# --- the checks ------------------------------------------------------------------------------


def test_a_complete_release_is_verified():
    result = check(Issues(), FakeRegistry(GOOD_IMAGE))
    assert result.verified == [("sprint-metrics", VERSION)] and result.filed == []


def test_the_262_release_files_an_epic_for_attestation_and_label():
    """What PR #278 would publish: two platforms, tagged, no attestation, no label."""
    image = Image(digest=DIGEST, platforms=["linux/amd64", "linux/arm64"], labels={})
    issues = Issues(attested=False)
    result = check(issues, FakeRegistry(image))
    assert result.filed == [("sprint-metrics", 400)]
    body = issues.created[0]["body"]
    assert RELEASE_MARKER.format(version=VERSION) in body
    assert "no GitHub artifact attestation" in body and "provenance: true" in body
    assert "org.opencontainers.image.source" in body
    assert "can't be changed once published" in body


def test_each_gap_is_named():
    image = Image(digest=DIGEST, platforms=["linux/amd64"], labels={})
    registry = FakeRegistry(image, tags={"1.0": "sha256:other", "latest": DIGEST})
    found = gaps(Issues(tag=False, release=False), registry, "sprint-metrics", VERSION)
    text = "\n".join(found)
    assert "The tag `v1.0.0` doesn't exist, so there's no GitHub Release on it" in text
    assert "no linux/arm64 image" in text
    assert "`ghcr.io/mqucifer/sprint-metrics:1.0` doesn't point at" in text


def test_release_notes_missing_changelog_lines_are_named():
    found = gaps(
        Issues(notes="Something else"), FakeRegistry(GOOD_IMAGE), "sprint-metrics", VERSION
    )
    assert any("leave out lines of the CHANGELOG section" in f for f in found)


def test_a_private_package_is_named_and_the_image_checks_stop():
    found = gaps(Issues(), FakeRegistry(unreadable=True), "sprint-metrics", VERSION)
    assert any("isn't public" in f for f in found)


def test_no_image_at_all_is_a_gap():
    found = gaps(Issues(), FakeRegistry(None), "sprint-metrics", VERSION)
    assert "There's no image `ghcr.io/mqucifer/sprint-metrics:1.0.0`." in found


# --- when it runs ------------------------------------------------------------------------------


def test_nothing_is_checked_before_a_first_release():
    issues = Issues(version="0.0.0")
    result = check(issues, FakeRegistry(None))
    assert result.verified == result.filed == result.waiting == []


def test_a_release_still_being_built_is_waited_for():
    result = check(Issues(head="new", run_sha="old"), FakeRegistry(None))
    assert result.waiting == [("sprint-metrics", VERSION)] and result.filed == []


def test_a_version_already_reported_is_not_reported_again():
    epic = {"body": RELEASE_MARKER.format(version=VERSION), "state": "closed"}
    issues = Issues(attested=False, epics=[epic])
    assert check(issues, FakeRegistry(GOOD_IMAGE)).filed == []


def test_a_repo_without_a_release_workflow_is_left_alone():
    class NoRelease(Issues):
        def latest_runs(self, repo, branch):
            return {}

    result = check(NoRelease(), FakeRegistry(None))
    assert result.verified == result.filed == result.failed == []


def test_changelog_section_stops_at_the_next_version():
    assert changelog_section(CHANGELOG, VERSION) == ["### Added", "- The first release."]
    assert changelog_section(CHANGELOG, "2.0.0") == []


# --- the registry, over HTTP -------------------------------------------------------------------


def registry(routes: dict) -> Registry:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/token":
            return httpx.Response(200, json={"token": "anon"})
        for suffix, (status, body, headers) in routes.items():
            if path.endswith(suffix):
                return httpx.Response(status, content=json.dumps(body), headers=headers)
        return httpx.Response(404)

    return Registry(httpx.Client(transport=httpx.MockTransport(handler)))


INDEX = {
    "mediaType": "application/vnd.oci.image.index.v1+json",
    "manifests": [
        {"digest": "sha256:amd", "platform": {"os": "linux", "architecture": "amd64"}},
        {"digest": "sha256:arm", "platform": {"os": "linux", "architecture": "arm64"}},
        {"digest": "sha256:att", "platform": {"os": "unknown", "architecture": "unknown"}},
    ],
}


def test_the_registry_reads_platforms_and_labels_from_an_index():
    reg = registry(
        {
            "manifests/1.0.0": (200, INDEX, {"Docker-Content-Digest": DIGEST}),
            "manifests/sha256:amd": (200, {"config": {"digest": "sha256:cfg"}}, {}),
            "blobs/sha256:cfg": (200, {"config": {"Labels": {"a": "b"}}}, {}),
        }
    )
    image = reg.image("mqucifer/sprint-metrics", "1.0.0")
    assert image.digest == DIGEST
    assert image.platforms == ["linux/amd64", "linux/arm64"], "attestation entries left out"
    assert image.labels == {"a": "b"}


def test_an_unknown_tag_is_none():
    assert registry({}).image("mqucifer/sprint-metrics", "9.9.9") is None


def test_a_refused_read_is_unreadable():
    reg = registry({"manifests/1.0.0": (401, {}, {})})
    with pytest.raises(ImageUnreadable):
        reg.image("mqucifer/sprint-metrics", "1.0.0")


# --- crew#384: nothing published is not an incomplete release -----------------


def test_a_version_never_published_is_released_as_itself():
    """sprint-metrics#350: the workflow skipped 1.0.0, so no tag, Release or image."""
    issues = Issues(tag=False, release=False)
    result = check(issues, FakeRegistry(None))
    assert result.filed == [("sprint-metrics", 400)]
    title, body = issues.created[0]["title"], issues.created[0]["body"]
    assert title == "Release v1.0.0 was never published"
    assert "can still be released as itself" in body
    assert "can't be changed once published" not in body
    # The missing tag is one gap, not three.
    assert "no `## [1.0.0]` section" not in body
    assert "There's no GitHub Release on" not in body
