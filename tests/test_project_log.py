"""A project's own decision log reaches the panel, the settle step and the split (crew#468).

Decisions that apply to every epic in a project name no Goal, so the Goal-based rule
never found them, and the panel replay missed the findings they settle.
"""

from __future__ import annotations

import contextlib

import httpx

from crew_org.crews import panel_crew, settle_crew
from crew_org.crews.panel_crew import PanelAnswer, PanelContext, PanelNote, PanelResult
from crew_org.flows import project_log
from crew_org.flows.project_log import HEADER, accepted, read_log
from crew_org.tools.github_issues import IssueClient, Trust


def entry(title, status="Accepted", decision="A card counts in the sprint it merges.", more=""):
    return (
        f"# {title}\n\n- **Date:** 2026-09-30\n- **Status:** {status}\n\n"
        f"## Context\n\nWhy it came up.\n\n## Decision\n\n{decision}\n\n"
        f"## Consequences\n\nWhat follows.{more}\n"
    )


LOG = {
    "0001-sprint-counting.md": entry("1. A card counts where it merges"),
    "0002-telemetry.md": entry(
        "2. Logs and traces as OpenTelemetry", decision="OTLP, no collector."
    ),
    "0003-old-store.md": entry("3. Its own Postgres", status="Superseded by 0004"),
    "0004-shared-store.md": entry("4. The shared Postgres", decision="Own schema and user."),
    "0005-maybe.md": entry("5. Maybe a cache", status="Proposed"),
    "README.md": "# Decisions\n",
}


class Repo:
    def __init__(self, files=None, fail=False):
        self.files = LOG if files is None else files
        self.fail = fail

    def list_dir(self, repo, path, ref):
        if self.fail:
            raise RuntimeError("GitHub is down")
        assert path == "docs/decisions" and ref == "main"
        return list(self.files)

    def file_at(self, repo, path, ref):
        return self.files.get(path.rsplit("/", 1)[1])


# --- reading the log -------------------------------------------------------------------------


def test_only_accepted_entries_are_read_and_only_their_decision():
    title, decision = accepted(LOG["0001-sprint-counting.md"])
    assert title == "1. A card counts where it merges"
    assert decision == "A card counts in the sprint it merges."
    assert accepted(LOG["0003-old-store.md"]) is None
    assert accepted(LOG["0005-maybe.md"]) is None


def test_an_entry_with_no_decision_section_is_left_out():
    assert accepted("# 9. Nothing\n\n- **Status:** Accepted\n\n## Context\n\nWhy.\n") is None


def test_the_log_is_the_accepted_decisions_in_order_under_its_header():
    text = read_log(Repo(), "sprint-metrics")
    assert text.startswith(HEADER)
    titles = [line for line in text.split("\n") if line.startswith("### ")]
    assert titles == [
        "### 1. A card counts where it merges",
        "### 2. Logs and traces as OpenTelemetry",
        "### 4. The shared Postgres",
    ]
    assert "Why it came up" not in text and "What follows" not in text
    assert "Its own Postgres" not in text and "Maybe a cache" not in text


def test_a_project_with_no_log_or_an_unreadable_one_gives_nothing():
    assert read_log(Repo(files={}), "r") == ""
    assert read_log(Repo(files={"README.md": "x"}), "r") == ""
    assert read_log(Repo(fail=True), "r") == ""


def test_a_directory_is_listed_by_its_file_names():
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/docs/decisions"):
            return httpx.Response(
                200,
                json=[
                    {"name": "0001-a.md", "type": "file"},
                    {"name": "images", "type": "dir"},
                ],
            )
        return httpx.Response(404, json={"message": "Not Found"})

    issues = IssueClient(
        "t",
        "mqucifer",
        client=httpx.Client(transport=httpx.MockTransport(answer)),
        trust=Trust(sponsor="s", crew=frozenset(), tools=frozenset()),
    )
    assert issues.list_dir("sprint-metrics", "docs/decisions", "main") == ["0001-a.md"]
    assert issues.list_dir("sprint-metrics", "docs/nothing", "main") == []


# --- who is shown it ------------------------------------------------------------------------------


def context(log="## The project's decision log\n\n### 1. Counting\n\nWhere it merges."):
    return PanelContext(
        goal_ref="mqucifer/sprint-metrics#174",
        goal="GOAL",
        project="",
        decisions="DECISIONS",
        epic_ref="mqucifer/sprint-metrics#406",
        epic="EPIC",
        project_log=log,
    )


def test_every_panel_member_is_shown_the_projects_log():
    for role in panel_crew.ROLES:
        text = panel_crew.describe(context(), role)
        assert "### 1. Counting" in text and "the project's decision log" in text


def test_the_settle_step_is_shown_it_as_a_source():
    notes = PanelResult(
        {
            "qa_engineer": PanelAnswer(
                nothing_to_add=False,
                notes=[PanelNote(problem="p", source="s", settle="t", settled_by="product_owner")],
            )
        },
        {},
    )
    text = settle_crew.describe(context(), notes)
    assert "### 1. Counting" in text and "the project's decision log or a sibling's" in text


def test_a_project_without_a_log_reads_as_before():
    assert "decision log\n\n###" not in panel_crew.describe(context(log=""), "architect")


def test_the_split_is_shown_the_log(monkeypatch):
    from types import SimpleNamespace

    from crew_org.crews import refinement_crew

    seen = {}

    class Crew:
        def __init__(self, **_):
            pass

        def kickoff(self):
            return SimpleNamespace(pydantic=None)

    class Agents(dict):
        def __missing__(self, key):
            return object()

    monkeypatch.setattr(refinement_crew, "build_agents", lambda *a, **k: Agents())
    monkeypatch.setattr(refinement_crew, "Task", lambda **k: seen.update(k) or object())
    monkeypatch.setattr(refinement_crew, "Crew", Crew)
    with contextlib.suppress(Exception):  # only the prompt is under test
        refinement_crew.split_epic("E", "body", project_log="## The project's decision log\n\nX")
    assert "## The project's decision log\n\nX" in seen["description"]


def test_the_tick_reads_the_log_and_gives_it_to_the_split(monkeypatch):
    from tests.test_split_conclusion import Concluded, proposal, run_tick, story

    class WithLog(Concluded):
        def list_dir(self, repo, path, ref):
            return list(LOG)

        def file_at(self, repo, path, ref):
            return LOG.get(path.rsplit("/", 1)[1])

    _, asked, _ = run_tick(
        monkeypatch, proposal(story("A", "R1", "R2"), story("B", "R3")), issues=WithLog()
    )
    assert "### 1. A card counts where it merges" in asked[0][1]["project_log"]


def test_the_panel_step_records_how_big_its_context_is(monkeypatch):
    from types import SimpleNamespace

    from crew_org.events import EventSink
    from crew_org.flows import panel as panel_flow
    from crew_org.flows import refine_panel
    from crew_org.flows import settle as settle_flow

    ctx = SimpleNamespace(epic_ref="x", decisions="D" * 120, project_log="L" * 45, siblings=[1, 2])
    monkeypatch.setattr(panel_flow, "gather", lambda *a, **k: ctx)
    monkeypatch.setattr(
        panel_flow, "panel_on", lambda *a: SimpleNamespace(answers={"x": 1}, failed={})
    )
    monkeypatch.setattr(
        settle_flow, "settle_epic", lambda *a, **k: settle_flow.Settled(settle_flow.Outcome.WRITTEN)
    )
    events = []
    sink = EventSink(None)
    sink.subscribe(events.append)

    class Gh:
        def get(self, repo, number):
            return {"body": "no conclusion"}

    epic = SimpleNamespace(number=406, title="t", parent=174)
    refine_panel.panel_step(
        Gh(),
        sink,
        SimpleNamespace(failed=[], skipped=[]),
        epic,
        "sprint-metrics",
        project="",
        search=[],
    )
    [note] = [e for e in events if "panel context" in e.summary]
    assert note.detail["decisions_chars"] == 120 and note.detail["project_log_chars"] == 45
    assert note.detail["siblings"] == 2


def test_the_log_lives_where_the_crew_keeps_its_own():
    assert project_log.DECISIONS_DIR == "docs/decisions"
