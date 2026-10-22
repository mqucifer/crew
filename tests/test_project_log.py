"""A project's own decision log reaches the Goal's look, its settle and the split (crew#468).

Decisions that apply to every epic in a project name no Goal, so the Goal-based rule
never found them, and the panel replay missed the findings they settle.
"""

from __future__ import annotations

import contextlib

import httpx

from crew_org.crews import goal_look_crew, settle_crew
from crew_org.crews.goal_look_crew import APPROVED, EpicText, GoalContext, LookResult
from crew_org.flows import project_log
from crew_org.flows.project_log import HEADER, accepted, read_log
from crew_org.tools.github_issues import IssueClient, Trust
from tests import asked


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
    return GoalContext(
        goal_ref="mqucifer/sprint-metrics#174",
        goal="GOAL",
        decisions="DECISIONS",
        epics=[EpicText("mqucifer/sprint-metrics#406", APPROVED, "EPIC")],
        project_log=log,
    )


def opened(parts: dict[str, str]) -> str:
    """A step's own parts of its session's start, as it reads them (crew#807)."""
    return "\n\n".join(p for p in parts.values() if p)


def test_both_roles_of_the_look_are_shown_the_projects_log():
    for role in goal_look_crew.ROLES:
        assert "### 1. Counting" in opened(goal_look_crew.opening(context(), role))


def test_the_goals_settle_is_shown_it_as_a_source():
    text = opened(settle_crew.goal_opening(context(), LookResult(None, None), []))
    assert "### 1. Counting" in text and "the project's log" in text


def test_a_project_without_a_log_reads_as_before():
    assert "decision log\n\n###" not in opened(goal_look_crew.opening(context(log=""), "architect"))


def test_the_split_is_shown_the_log(monkeypatch):

    from crew_org.crews import refinement_crew

    seen = asked.capture(monkeypatch)
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


def test_the_log_lives_where_the_crew_keeps_its_own():
    assert project_log.DECISIONS_DIR == "docs/decisions"
