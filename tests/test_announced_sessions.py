"""An announced major's contract reaches a project's sessions, with no network (crew#969).

On mqucifer/crew-presentation#211 the Business Analyst searched the 1.x package for
`top_failure_causes`, because the 2.0.0 shape was nowhere it could read. A session sees
what differs in its start, and the announced schemas and mock as files in its checkout.
"""

from __future__ import annotations

import json
import subprocess

import httpx
import pytest

from crew_org import usage
from crew_org.project import RECORD_PATH, read_record
from crew_org.session import start as session_start
from crew_org.session import steps
from crew_org.session.reading import ReadTools

RECORD = """version: 3
intent:
  scope:
    serves: [{who: the Sponsor, needs: [how the crew performs]}]
    purpose: Show how the crew works.
    uses:
      - repo: mqucifer/crew
        section: "Data packages"
        pin: delivery-history.tag
        contract:
          - contracts/delivery-history/schema.json
          - contracts/delivery-history/manifest.schema.json
  release:
    deploys: false
  done:
    bar: CI is green.
"""
TAG = "delivery-history-2026-10-30"
DIR = "contracts/delivery-history"
OLD = {
    "properties": {
        "kept": {"type": "string", "description": "Stays as it is."},
        "escalation_count": {"type": "integer", "description": "Leaves."},
    }
}
NEW = {
    "properties": {
        "kept": {"type": "string", "description": "Stays as it is."},
        "cost": {"type": "object", "description": "What a sprint cost."},
    }
}
MANIFEST = {"properties": {"schema_version": {"type": "string"}}}
MOCK = {"top_failure_causes": [{"class": "VERIFY", "role": "Developer", "count": 2}]}
ANNOUNCEMENT = {
    "2.0.0": {
        "replaces": "1.x",
        "on": "2026-11-06",
        "contract": f"{DIR}/2.0.0/",
        "changelog": {"file": "README.md", "section": "Announced"},
        "mock": ["mock/delivery-history.json", "mock/events/2026-09-25.json"],
    }
}
FILES = {
    ("README.md", "HEAD"): "## Data packages\n\nHow to use it.\n",
    (f"{DIR}/announced.json", "HEAD"): json.dumps(ANNOUNCEMENT),
    (f"{DIR}/versions.json", TAG): json.dumps({"1.11.0": "f"}),
    (f"{DIR}/schema.json", TAG): json.dumps(OLD),
    (f"{DIR}/manifest.schema.json", TAG): json.dumps(MANIFEST),
    (f"{DIR}/2.0.0/schema.json", "HEAD"): json.dumps(NEW),
    (f"{DIR}/2.0.0/manifest.schema.json", "HEAD"): json.dumps(MANIFEST),
    (f"{DIR}/2.0.0/mock/delivery-history.json", "HEAD"): json.dumps(MOCK),
    (f"{DIR}/2.0.0/mock/events/2026-09-25.json", "HEAD"): "[]",
}
FOLDER = ".announced/mqucifer-crew/delivery-history-2.0.0"


def _producer(files):
    def fetched(repo, path, ref="HEAD"):
        if (path, ref) in files:
            return files[(path, ref)]
        request = httpx.Request("GET", f"https://raw/{path}")
        raise httpx.HTTPStatusError(
            "404", request=request, response=httpx.Response(404, request=request)
        )

    return fetched


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A git checkout of the project, pinning a release at the replaced major."""
    monkeypatch.setattr(usage, "_file", _producer(FILES))
    (tmp_path / ".crew").mkdir()
    (tmp_path / RECORD_PATH).write_text(RECORD)
    (tmp_path / "delivery-history.tag").write_text(f"{TAG}\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def _uses(root):
    return read_record(root).intent.scope.uses


def test_a_session_is_shown_only_what_differs_from_the_pinned_fields(project):
    [use] = _uses(project)
    said = usage.announced(use, project)
    assert "`delivery-history` 2.0.0 replaces 1.x on 2026-11-06" in said
    assert "`cost`: What a sprint cost." in said
    assert "`escalation_count`" in said
    # A field that stays is in the pinned list above, and isn't said twice.
    assert "`kept`" not in said
    assert "`manifest.schema.json`: as above" in said


def test_nothing_is_shown_for_a_producer_with_no_announcement(project, monkeypatch):
    without = {k: v for k, v in FILES.items() if not k[0].endswith("announced.json")}
    monkeypatch.setattr(usage, "_file", _producer(without))
    assert usage.announced(_uses(project)[0], project) == ""
    assert usage.place_announced(_uses(project), project) == []
    assert not (project / ".announced").exists()


def test_nothing_is_shown_once_the_project_pins_the_announced_major(project, monkeypatch):
    moved = {**FILES, (f"{DIR}/versions.json", TAG): json.dumps({"2.0.0": "f"})}
    monkeypatch.setattr(usage, "_file", _producer(moved))
    assert usage.announced(_uses(project)[0], project) == ""


def test_an_announcement_that_can_t_be_read_says_so_and_stops_nothing(project, monkeypatch):
    broken = {**FILES, (f"{DIR}/announced.json", "HEAD"): "{not json"}
    monkeypatch.setattr(usage, "_file", _producer(broken))
    assert "couldn't be read" in usage.announced(_uses(project)[0], project)
    [gap] = usage.place_announced(_uses(project), project)
    assert "mqucifer/crew" in gap
    assert not (project / ".announced").exists()


def test_the_schemas_and_mock_are_placed_where_a_test_reads_them_never_committed(project):
    assert usage.place_announced(_uses(project), project) == []
    folder = project / FOLDER
    assert json.loads((folder / "mock" / "delivery-history.json").read_text()) == MOCK
    assert (folder / "mock" / "events" / "2026-09-25.json").read_text() == "[]"
    assert json.loads((folder / "schema.json").read_text()) == NEW
    assert (folder / "manifest.schema.json").is_file()
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=project, capture_output=True, text=True, check=True
    ).stdout
    assert ".announced" not in status
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=project,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert ".announced" not in staged
    # Again, as a returned story's session does: replaced, and excluded once.
    assert usage.place_announced(_uses(project), project) == []
    exclude = (project / ".git" / "info" / "exclude").read_text()
    assert exclude.count("/.announced/") == 1


def test_a_file_that_can_t_be_read_places_none_of_them(project, monkeypatch):
    missing = {k: v for k, v in FILES.items() if not k[0].endswith("2026-09-25.json")}
    monkeypatch.setattr(usage, "_file", _producer(missing))
    [gap] = usage.place_announced(_uses(project), project)
    assert "2026-09-25.json" in gap
    assert not (project / FOLDER).exists()


@pytest.mark.parametrize("path", ["../../../outside.json", "/etc/passwd", "mock/../../x"])
def test_a_mock_path_can_t_lead_out_of_the_folder(project, monkeypatch, path):
    bad = {"2.0.0": {**ANNOUNCEMENT["2.0.0"], "mock": [path]}}
    monkeypatch.setattr(
        usage, "_file", _producer({**FILES, (f"{DIR}/announced.json", "HEAD"): json.dumps(bad)})
    )
    assert usage.place_announced(_uses(project), project)
    assert not (project / ".announced").exists()


def test_a_session_reads_the_placed_files_with_its_own_tools(project):
    usage.place_announced(_uses(project), project)
    tools = ReadTools(project)
    assert "top_failure_causes" in tools.view(f"{FOLDER}/mock/delivery-history.json").text
    found = tools.search("top_failure_causes")
    assert f"{FOLDER}/mock/delivery-history.json" in found.text


def test_the_map_names_the_placed_files_with_their_sizes(project):
    usage.place_announced(_uses(project), project)
    assert f"{FOLDER}/mock/delivery-history.json (" in session_start.project_map(project)


def test_a_refinement_step_s_start_shows_the_changes_and_where_the_mock_is(project):
    usage.place_announced(_uses(project), project)
    section = steps.project_section(project)
    assert "`cost`: What a sprint cost." in section
    assert f"`{FOLDER}/`" in section
    assert "mock/delivery-history.json" in section


def test_without_the_folder_the_start_links_the_contract_instead(project):
    section = steps.project_section(project)
    assert FOLDER not in section
    assert "https://github.com/mqucifer/crew/tree/HEAD/contracts/delivery-history/2.0.0/" in section


def test_a_story_s_start_names_the_announcement_and_its_note_has_the_changes(project):
    usage.place_announced(_uses(project), project)
    text, notes = session_start.how_it_works(read_record(project), project)
    assert "`delivery-history` 2.0.0 replaces 1.x on 2026-11-06" in text
    assert f"`{FOLDER}/`" in text
    # The fields stay in the note, as what the release provides does.
    assert "`cost`" not in text
    assert "`cost`: What a sprint cost." in notes["@notes/mqucifer-crew.md"]


def test_the_split_may_name_what_the_announced_major_provides(project):
    provided = usage.provided_fields(_uses(project), project)
    assert {"kept", "escalation_count", "cost"} <= provided


def test_the_split_check_holds_with_no_announcement(project, monkeypatch):
    without = {k: v for k, v in FILES.items() if not k[0].endswith("announced.json")}
    monkeypatch.setattr(usage, "_file", _producer(without))
    provided = usage.provided_fields(_uses(project), project)
    assert "kept" in provided and "cost" not in provided


def test_refinement_s_checkout_gets_the_files_and_a_gap_is_noted(project, monkeypatch):
    from crew_org.events import EventSink
    from crew_org.flows import board_flow
    from crew_org.tools import coverage_map

    class Checkout:
        def at_main(self):
            return project

    class Ws:
        def for_repo(self, repo):
            return Checkout()

    def no_map(root, repo):
        raise coverage_map.CoverageUnavailable("no map here")

    monkeypatch.setattr(coverage_map, "build", no_map)
    said = []
    sink = EventSink()
    sink.subscribe(lambda e: said.append(e.summary))
    board_flow.RepoContext(Ws(), sink).reading_for("crew-presentation")
    assert (project / FOLDER / "mock" / "delivery-history.json").is_file()

    broken = {**FILES, (f"{DIR}/announced.json", "HEAD"): "{not json"}
    monkeypatch.setattr(usage, "_file", _producer(broken))
    board_flow.RepoContext(Ws(), sink).reading_for("crew-presentation")
    assert any("weren't placed" in line for line in said)
