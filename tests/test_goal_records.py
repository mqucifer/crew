"""A Goal's epics share what they decide, and the one others build on goes first (crew#611).

In Sprint 21's dry run, sprint-metrics#552's panel and Architect couldn't see #551's
record and settled the same schema differently: the `because` values, the
`replaced_by` type, and which epic adds the columns.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

from crew_org.crews.settle_crew import describe_design
from crew_org.events import EventSink
from crew_org.flows import goal_index, panel, refine_panel
from crew_org.flows import record as record_flow
from crew_org.flows.board_flow import STORY_SPLIT_MARKER
from crew_org.tools.github_issues import IssueClient
from tests.test_panel import FOOTER, TRUST, context, github, issue


def record_of(*rows: tuple[str, str, str]) -> str:
    """A record whose binding rows are (id, context, decision), set by the Architect."""
    return record_flow.render(
        record_flow.Record(
            decisions=tuple(
                record_flow.Decision(
                    id=i,
                    context=c,
                    decision=d,
                    consequences="-",
                    source="the code",
                    set_by="Architect",
                    date="2026-10-10",
                )
                for i, c, d in rows
            )
        )
    )


STORE = record_of(("R3", "Reason values", "because is sponsor or story"))


def goal_with_records():
    by = {
        528: issue(528, title="Goal: superseded stories", body="THE GOAL"),
        551: issue(
            551, title="Report superseded", body=f"THE STORE{FOOTER}\n\n{STORE}", parent=528
        ),
        552: issue(552, title="Report the reason", body=f"BUILDS ON #551{FOOTER}", parent=528),
        540: issue(
            540,
            title="An earlier plan",
            state="closed",
            reason="not_planned",
            parent=528,
            body=f"OLD PLAN{FOOTER}\n\n" + record_of(("R1", "Table", "board_events holds them")),
        ),
    }
    return by, {528: [540, 551, 552]}


def test_a_siblings_record_is_shown_with_its_text():
    by, children = goal_with_records()
    ctx = panel.gather(github(by, children), "sprint-metrics", 552, project="", search=[])
    [store] = [s for s in ctx.siblings if s.ref.endswith("#551")]
    assert "THE STORE" in store.text and "because is sponsor or story" in store.text
    assert "Proposed by the Product Owner" not in store.text


def test_a_superseded_epic_carries_only_its_binding_rows():
    by, children = goal_with_records()
    ctx = panel.gather(github(by, children), "sprint-metrics", 552, project="", search=[])
    [old] = [s for s in ctx.siblings if s.ref.endswith("#540")]
    assert old.state == "superseded"
    assert "board_events holds them" in old.text and "OLD PLAN" not in old.text


def test_the_architect_is_shown_the_other_epics_records():
    shown = describe_design(context(), record="R", questions="Q1", code="")
    assert "## The other epics under this Goal, with their records" in shown
    assert "answer with its decision and name its row" in shown


# --- the Goal's index ------------------------------------------------------------------------


class Indexed:
    """A Goal and its epics, with the comments written and edited on it."""

    def __init__(self):
        self.by, self.children = goal_with_records()
        self.comments: list[dict] = []
        self.edits: list[tuple[int, str]] = []

    def answer(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if "/issues/comments/" in path and method == "PATCH":
            cid = int(path.rsplit("/", 1)[1])
            body = json.loads(request.content)["body"]
            self.edits.append((cid, body))
            self.comments[cid - 1]["body"] = body
            return httpx.Response(200, json={"id": cid})
        _, _, _, _repo, _, n, *rest = path.split("/")
        if rest == ["sub_issues"]:
            return httpx.Response(200, json=[self.by[c] for c in self.children.get(int(n), [])])
        if rest == ["comments"] and method == "POST":
            body = json.loads(request.content)["body"]
            self.comments.append(
                {
                    "id": len(self.comments) + 1,
                    "body": body,
                    "user": {"login": "mqucifer-crew[bot]"},
                }
            )
            return httpx.Response(201, json={"id": len(self.comments)})
        if rest == ["comments"]:
            return httpx.Response(200, json=self.comments)
        return httpx.Response(200, json=self.by[int(n)])

    def client(self) -> IssueClient:
        return IssueClient(
            "t",
            "mqucifer",
            client=httpx.Client(transport=httpx.MockTransport(self.answer)),
            trust=TRUST,
        )


def test_the_goal_shows_every_epics_rows_in_one_index():
    gh = Indexed()
    goal_index.refresh_for_epic(gh.client(), EventSink(None), repo="sprint-metrics", epic=551)
    [posted] = gh.comments
    assert goal_index.INDEX_MARKER in posted["body"]
    assert "| mqucifer/sprint-metrics#551 (open) | R3 |" in posted["body"]
    assert "| mqucifer/sprint-metrics#540 (superseded) | R1 |" in posted["body"]


def test_the_index_is_rewritten_in_place_not_posted_again():
    gh = Indexed()
    client = gh.client()
    goal_index.refresh(client, EventSink(None), repo="sprint-metrics", goal=528)
    goal_index.refresh(client, EventSink(None), repo="sprint-metrics", goal=528)
    assert len(gh.comments) == 1 and gh.edits == [], "unchanged: nothing written"
    gh.by[551]["body"] += "\n" + record_of(("R4", "Columns", "three nullable columns"))
    goal_index.refresh(client, EventSink(None), repo="sprint-metrics", goal=528)
    assert len(gh.comments) == 1 and len(gh.edits) == 1
    assert "three nullable columns" in gh.comments[0]["body"]


def test_an_index_that_cant_be_written_never_fails_the_record_edit():
    class Broken:
        def get(self, repo, number):
            raise RuntimeError("down")

    goal_index.refresh_for_epic(Broken(), EventSink(None), repo="sprint-metrics", epic=551)


# --- dependent epics in order ----------------------------------------------------------------


class Board:
    owner = "mqucifer"

    def __init__(self, bodies: dict[int, str], split: set[int] = frozenset()):
        self.bodies, self.split = bodies, set(split)

    def sub_issues(self, repo, goal):
        return [{"number": n, "state": "open", "body": b} for n, b in self.bodies.items()]

    def has_comment_marked(self, repo, number, marker):
        assert marker == STORY_SPLIT_MARKER
        return number in self.split


def test_an_epic_that_builds_on_an_unsplit_sibling_waits_for_it():
    board = Board({551: "THE STORE", 552: "builds on mqucifer/sprint-metrics#551's store"})
    assert (
        refine_panel.builds_on_unsplit(board, "sprint-metrics", 528, 552, board.bodies[552]) == 551
    )


def test_once_the_sibling_is_split_it_goes_ahead():
    board = Board({551: "THE STORE", 552: "builds on #551"}, split={551})
    assert (
        refine_panel.builds_on_unsplit(board, "sprint-metrics", 528, 552, board.bodies[552]) is None
    )


def test_two_epics_that_name_each_other_go_in_issue_order():
    board = Board({551: "beside #552", 552: "beside #551"})
    assert (
        refine_panel.builds_on_unsplit(board, "sprint-metrics", 528, 551, board.bodies[551]) is None
    )
    assert (
        refine_panel.builds_on_unsplit(board, "sprint-metrics", 528, 552, board.bodies[552]) == 551
    )


def test_another_repositorys_issue_is_not_a_sibling():
    board = Board({551: "THE STORE", 552: "see crew#551 and mqucifer/crew#551"})
    assert (
        refine_panel.builds_on_unsplit(board, "sprint-metrics", 528, 552, board.bodies[552]) is None
    )


def test_the_waiting_epic_is_not_designed_and_says_why(monkeypatch):
    body = "builds on #551\n\n" + record_flow.render(
        record_flow.Record(
            open=(record_flow.Question(id="Q1", question="Which column?", impact="schema"),)
        )
    )

    class Gh(Board):
        def get(self, repo, number):
            return {"body": body}

    asked = []
    monkeypatch.setattr(
        refine_panel.settle_flow, "settle_design_questions", lambda *a, **k: asked.append(k)
    )
    result = SimpleNamespace(failed=[], skipped=[])
    epic = SimpleNamespace(number=552, parent=528, title="Report the reason")
    ok = refine_panel.panel_step(
        Gh({551: "THE STORE", 552: body}),
        EventSink(None),
        result,
        epic,
        "sprint-metrics",
        project="",
        search=[],
    )
    assert not ok and asked == []
    assert result.skipped == [(552, "waits for #551 to be split: it builds on it")]


def test_the_split_is_shown_the_other_epics_records(monkeypatch):
    import contextlib

    from crew_org.crews import refinement_crew

    seen: dict = {}

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
    with contextlib.suppress(Exception):
        refinement_crew.split_epic("E", "body", siblings="### #551 (open)\n\nR3 because")
    assert "## The other epics under this Goal, with their records" in seen["description"]
    assert "R3 because" in seen["description"]
