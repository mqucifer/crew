"""The settle step (crew#440): the panel's notes become the epic's conclusion.

The model call (`settle`) is replaced here; `crew settle` was run for real before the
pull request.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from crew_org.crews.panel_crew import PanelAnswer, PanelContext, PanelNote, PanelResult
from crew_org.crews.settle_crew import (
    CONSEQUENCES_CHARS,
    DECISION_CHARS,
    Conclusion,
    Dismissal,
    OpenQuestion,
    Row,
    Settlement,
    Unsettled,
    check_covers,
    check_not_cut,
    describe,
    numbered,
)
from crew_org.events import EventSink
from crew_org.flows import panel as panel_flow
from crew_org.flows import settle as flow
from crew_org.tools.github_issues import IssueClient, Trust

TRUST = Trust(
    sponsor="mquarters",
    crew=frozenset({"mqucifer-crew[bot]"}),
    tools=frozenset({"dependabot[bot]"}),
)
APPROVED = "# Run as a service\n\nThe approved text.\n\n---\n\nProposed by the Product Owner."


def note(problem="History is in memory.", settled_by="product_owner"):
    return PanelNote(
        problem=problem, source="the Goal", settle="Where it lives", settled_by=settled_by
    )


def panel(*per_role: int) -> PanelResult:
    roles = ["architect", "ux_designer", "qa_engineer", "devops_engineer"]
    answers = {}
    for role, count in zip(roles, per_role, strict=False):
        answers[role] = (
            PanelAnswer(nothing_to_add=True)
            if count == 0
            else PanelAnswer(
                nothing_to_add=False, notes=[note(f"{role} {i}") for i in range(count)]
            )
        )
    return PanelResult(answers, {})


def row(settles=(1,), **kw):
    params = dict(
        context="History",
        decision="Postgres, own schema",
        consequences="A client; CI needs a database",
        source="Goal decision D1",
        settles=list(settles),
    )
    params.update(kw)
    return Row(**params)


def context() -> PanelContext:
    return PanelContext(
        goal_ref="mqucifer/sprint-metrics#174",
        goal="GOAL",
        project="",
        decisions="DECISIONS",
        epic_ref="mqucifer/sprint-metrics#406",
        epic=APPROVED,
    )


# --- the schema holds the cells short -------------------------------------------------------


def test_a_cell_over_its_limit_is_refused_not_trimmed():
    row()
    with pytest.raises(ValidationError):
        row(decision="x" * (DECISION_CHARS + 1))


def test_a_row_names_the_notes_it_answers():
    with pytest.raises(ValidationError):
        row(settles=[])


def test_a_conclusion_with_nothing_in_it_is_refused():
    with pytest.raises(ValidationError):
        Conclusion()


def test_a_settlement_is_a_conclusion_or_one_question_never_both_or_neither():
    Settlement(conclusion=Conclusion(rows=[row()]))
    Settlement(sponsor_question="Is the history kept after a restart?")
    with pytest.raises(ValidationError):
        Settlement(conclusion=Conclusion(rows=[row()]), sponsor_question="Which?")
    with pytest.raises(ValidationError):
        Settlement()


def test_the_question_is_held_short_too():
    with pytest.raises(ValidationError):
        Settlement(sponsor_question="?" * 301)


def test_a_cell_that_ran_into_its_limit_was_cut_off_and_is_sent_back():
    # The server enforces the limit while decoding, so a long cell arrives cut at it.
    cut = Settlement(conclusion=Conclusion(rows=[row(consequences="x" * CONSEQUENCES_CHARS)]))
    with pytest.raises(Unsettled, match="row 1 consequences"):
        check_not_cut(cut)
    check_not_cut(
        Settlement(conclusion=Conclusion(rows=[row(consequences="x" * (CONSEQUENCES_CHARS - 1))]))
    )


def test_a_cut_question_for_the_sponsor_is_sent_back_too():
    with pytest.raises(Unsettled, match="the question for the Sponsor"):
        check_not_cut(Settlement(sponsor_question="?" * 300))


def test_the_model_is_told_to_stay_well_under_each_limit():
    schema = Row.model_json_schema()["properties"]
    assert "under 105 characters" in schema["decision"]["description"]
    assert "under 105 characters" in schema["consequences"]["description"]


# --- every note answered --------------------------------------------------------------------


def test_the_notes_are_numbered_in_a_fixed_order_across_the_members():
    found = numbered(panel(2, 0, 1, 0))
    assert [(n, role) for n, role, _ in found] == [
        (1, "architect"),
        (2, "architect"),
        (3, "qa_engineer"),
    ]
    assert "architect 0" in found[0][2] and "Source: the Goal" in found[0][2]


def test_a_conclusion_that_leaves_a_note_unanswered_is_refused_by_name():
    settlement = Settlement(conclusion=Conclusion(rows=[row(settles=[1])]))
    with pytest.raises(Unsettled, match="N2, N3"):
        check_covers(settlement, 3)


def test_a_row_for_a_note_that_does_not_exist_is_refused():
    settlement = Settlement(conclusion=Conclusion(rows=[row(settles=[1, 9])]))
    with pytest.raises(Unsettled, match="N9 are not notes"):
        check_covers(settlement, 1)


def test_a_row_a_question_and_a_dismissal_together_cover_the_notes():
    settlement = Settlement(
        conclusion=Conclusion(
            rows=[row(settles=[1])],
            open=[OpenQuestion(question="Which key?", impact="The store", settles=[2])],
            dismissed=[Dismissal(note=3, why="Inside the delivered epic")],
        )
    )
    check_covers(settlement, 3)


def test_a_question_for_the_sponsor_needs_no_coverage():
    check_covers(Settlement(sponsor_question="Which?"), 5)


def test_the_task_numbers_the_notes_and_carries_a_reply_and_the_last_refusal():
    text = describe(
        context(), panel(1, 0, 1, 0), reply="Keep it after a restart.", feedback="No row for N2."
    )
    assert "N1 (architect)" in text and "N2 (qa_engineer)" in text
    assert "Keep it after a restart." in text and "No row for N2." in text
    assert "GOAL" in text and "DECISIONS" in text and "The approved text." in text


# --- the table the epic carries -------------------------------------------------------------


def conclusion():
    return Conclusion(
        rows=[
            row(
                decision="Postgres | own schema\non the shared server",
                source="mqucifer/crew#280 and #186",
            ),
            row(settles=[2], context="Counting", source="crew#389, Goal decision D3"),
        ],
        open=[OpenQuestion(question="Same version track?", impact="Every sender", settles=[3])],
        dismissed=[Dismissal(note=4, why="Wholly inside #186")],
    )


def rendered():
    return flow.render(conclusion(), owner="mqucifer", repo="sprint-metrics", known={"crew"})


def test_the_conclusion_opens_with_its_header_and_the_bottom_line():
    lines = rendered().split("\n")
    assert lines[0] == "## Refinement conclusion"
    assert lines[2] == "Ready to split: 2 decided, 1 open for the design note, 1 dismissed."


def test_ids_and_status_are_the_codes_not_the_models():
    text = rendered()
    assert "| R1 | accepted |" in text and "| R2 | accepted |" in text
    assert "| Q1 |" in text and "| N4 |" in text


def test_a_cell_cannot_break_the_table():
    first = next(line for line in rendered().split("\n") if line.startswith("| R1"))
    assert "Postgres \\| own schema on the shared server" in first
    assert first.count("\n") == 0


def test_every_reference_is_explicit_and_linked():
    text = rendered()
    assert "[mqucifer/crew#280](https://github.com/mqucifer/crew/issues/280)" in text
    assert (
        "[mqucifer/sprint-metrics#186](https://github.com/mqucifer/sprint-metrics/issues/186)"
        in text
    )
    assert "[mqucifer/crew#389](https://github.com/mqucifer/crew/issues/389)" in text
    assert not any(
        part.startswith("#") and part[1:2].isdigit()
        for part in text.replace("|", " ").replace("(", " ").split()
    )
    assert "Goal decision D3" in text


def test_a_link_already_written_is_not_wrapped_again():
    link = "[mqucifer/crew#280](https://github.com/mqucifer/crew/issues/280)"
    out = flow.render(Conclusion(rows=[row(source=link)]), owner="mqucifer", repo="sprint-metrics")
    assert out.count("](https://") == 1


def test_open_questions_say_who_settles_them():
    assert "| Q1 | Same version track? | Every sender | Design note |" in rendered()


# --- settling an epic -----------------------------------------------------------------------


class Github:
    """One repository: issues' bodies, comments, labels and every write."""

    def __init__(self, body=APPROVED, comments=None):
        self.body = body
        self.comments = list(comments or [])
        self.labels: list[str] = []
        self.writes: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path.endswith("/comments") and method == "GET":
            return httpx.Response(200, json=self.comments)
        if path.endswith("/comments"):
            posted = json.loads(request.content)["body"]
            self.comments.append({"user": {"login": "mqucifer-crew[bot]"}, "body": posted})
            self.writes.append(("comment", posted))
            return httpx.Response(201, json={"id": 1})
        if path.endswith("/labels"):
            self.labels += json.loads(request.content)["labels"]
            return httpx.Response(200, json=[])
        if method == "PATCH":
            self.body = json.loads(request.content)["body"]
            self.writes.append(("body", self.body))
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"number": 406, "body": self.body, "state": "open"})

    def client(self) -> IssueClient:
        return IssueClient(
            "t", "mqucifer", client=httpx.Client(transport=httpx.MockTransport(self)), trust=TRUST
        )

    def said(self, login: str, text: str) -> None:
        self.comments.append({"user": {"login": login}, "body": text})


def run(gh: Github, monkeypatch, *settlements, notes=None):
    """settle_epic with the model replaced by `settlements`, in turn.

    Returns (result, calls the model got, events).
    """
    notes = notes or panel(1, 0, 1, 0)
    calls: list[dict] = []
    queue = list(settlements)

    def fake(ctx, pnl, *, reply="", feedback=""):
        calls.append({"reply": reply, "feedback": feedback})
        return queue.pop(0)

    monkeypatch.setattr(flow, "settle", fake)
    events: list = []
    sink = EventSink(None)
    sink.subscribe(events.append)
    result = flow.settle_epic(
        gh.client(), sink, repo="sprint-metrics", epic=406, context=context(), panel=notes
    )
    return result, calls, events


def good(count=2):
    return Settlement(conclusion=Conclusion(rows=[row(settles=list(range(1, count + 1)))]))


def test_a_conclusion_is_added_below_the_approved_text_which_is_untouched(monkeypatch):
    gh = Github()
    result, calls, events = run(gh, monkeypatch, good())
    assert result.outcome is flow.Outcome.WRITTEN
    assert gh.body.startswith(APPROVED)
    assert gh.body[len(APPROVED) :].lstrip().startswith("## Refinement conclusion")
    assert gh.body.count("## Refinement conclusion") == 1
    assert [e.summary for e in events if "settled" in e.summary] == [
        "epic #406: panel settled into its conclusion"
    ]


def test_an_epic_that_already_has_a_conclusion_is_left_alone(monkeypatch):
    gh = Github(body=APPROVED + "\n\n## Refinement conclusion\n\nReady to split: 1 decided.")
    result, calls, _ = run(gh, monkeypatch)
    assert result.outcome is flow.Outcome.ALREADY
    assert calls == [] and gh.writes == []


def test_a_refused_conclusion_is_asked_for_again_with_the_reason(monkeypatch):
    gh = Github()
    short = Settlement(conclusion=Conclusion(rows=[row(settles=[1])]))
    result, calls, _ = run(gh, monkeypatch, short, good())
    assert result.outcome is flow.Outcome.WRITTEN
    assert calls[0]["feedback"] == ""
    assert "No row, question or dismissal for N2" in calls[1]["feedback"]


def test_a_cut_cell_is_asked_for_again_with_the_reason(monkeypatch):
    gh = Github()
    cut = Settlement(
        conclusion=Conclusion(rows=[row(settles=[1, 2], consequences="x" * CONSEQUENCES_CHARS)])
    )
    result, calls, _ = run(gh, monkeypatch, cut, good())
    assert result.outcome is flow.Outcome.WRITTEN
    assert "cut off: row 1 consequences" in calls[1]["feedback"]


def test_a_conclusion_refused_every_time_fails_and_writes_nothing(monkeypatch):
    gh = Github()
    short = Settlement(conclusion=Conclusion(rows=[row(settles=[1])]))
    with pytest.raises(flow.SettleFailed, match="refused 3 times"):
        run(gh, monkeypatch, short, short, short)
    assert gh.writes == []


def test_a_body_edited_while_the_model_was_thinking_is_not_overwritten(monkeypatch):
    gh = Github()
    queue = [good()]

    def fake(ctx, pnl, *, reply="", feedback=""):
        gh.body = gh.body + "\n\nThe Sponsor added a line."
        return queue.pop(0)

    monkeypatch.setattr(flow, "settle", fake)
    with pytest.raises(flow.SettleFailed, match="changed while"):
        flow.settle_epic(
            gh.client(),
            EventSink(None),
            repo="sprint-metrics",
            epic=406,
            context=context(),
            panel=panel(1, 0, 1, 0),
        )
    assert gh.writes == [] and "The Sponsor added a line." in gh.body


# --- the Sponsor's question -----------------------------------------------------------------


ASK = Settlement(sponsor_question="Is the history kept after a restart?")


def test_a_note_nothing_answers_becomes_one_question_and_the_epic_waits(monkeypatch):
    gh = Github()
    result, _, events = run(gh, monkeypatch, ASK)
    assert result.outcome is flow.Outcome.WAITING
    [(kind, text)] = gh.writes
    assert kind == "comment" and flow.SETTLE_QUESTION_MARKER in text
    assert "Is the history kept after a restart?" in text
    assert gh.labels == ["needs:human"]
    assert gh.body == APPROVED
    assert any(e.kind.value == "product.asked" for e in events)


def test_it_waits_without_calling_the_model_until_the_sponsor_has_replied(monkeypatch):
    gh = Github(
        comments=[{"user": {"login": "mqucifer-crew[bot]"}, "body": flow.SETTLE_QUESTION_MARKER}]
    )
    result, calls, _ = run(gh, monkeypatch)
    assert result.outcome is flow.Outcome.WAITING and calls == []


def test_a_strangers_reply_or_a_crews_comment_is_not_the_sponsors_answer(monkeypatch):
    gh = Github(
        comments=[{"user": {"login": "mqucifer-crew[bot]"}, "body": flow.SETTLE_QUESTION_MARKER}]
    )
    gh.said("stranger", "Keep it forever.")
    gh.said("mqucifer-crew[bot]", "<!-- crew:qa --> something")
    result, calls, _ = run(gh, monkeypatch)
    assert result.outcome is flow.Outcome.WAITING and calls == []


def test_the_sponsors_reply_is_what_the_next_pass_settles_with(monkeypatch):
    gh = Github(
        comments=[{"user": {"login": "mqucifer-crew[bot]"}, "body": flow.SETTLE_QUESTION_MARKER}]
    )
    gh.said("mquarters", "Yes, it survives a restart.")
    result, calls, events = run(gh, monkeypatch, good())
    assert result.outcome is flow.Outcome.WRITTEN
    assert calls[0]["reply"] == "Yes, it survives a restart."
    assert [e.detail["after_reply"] for e in events if "settled" in e.summary] == [True]


def test_a_reply_before_the_question_does_not_count(monkeypatch):
    gh = Github()
    gh.said("mquarters", "An earlier comment.")
    gh.said("mqucifer-crew[bot]", flow.SETTLE_QUESTION_MARKER)
    result, calls, _ = run(gh, monkeypatch)
    assert result.outcome is flow.Outcome.WAITING and calls == []


# --- the panel's notes, kept as data ---------------------------------------------------------


def test_the_panels_notes_come_back_from_its_comment():
    result = PanelResult(
        {
            "architect": PanelAnswer(nothing_to_add=False, notes=[note("a --> b")]),
            "qa_engineer": PanelAnswer(nothing_to_add=True),
        },
        {"devops_engineer": "TimeoutError: slow"},
    )
    assert panel_flow.read_panel(panel_flow.render(result)) == result


def test_a_comment_with_no_data_has_no_notes():
    assert panel_flow.read_panel("<!-- crew:panel -->\n## Refinement panel") is None


def test_the_latest_panel_comment_with_notes_is_the_one_settled():
    old = PanelResult({"architect": PanelAnswer(nothing_to_add=True)}, {})
    new = panel(1, 0, 0, 0)
    gh = Github(
        comments=[
            {"user": {"login": "mqucifer-crew[bot]"}, "body": panel_flow.render(old)},
            {"user": {"login": "mqucifer-crew[bot]"}, "body": "<!-- crew:panel --> no data here"},
            {"user": {"login": "mqucifer-crew[bot]"}, "body": panel_flow.render(new)},
        ]
    )
    assert panel_flow.panel_on(gh.client(), "sprint-metrics", 406) == new


def test_no_panel_comment_means_no_panel_yet():
    assert panel_flow.panel_on(Github().client(), "sprint-metrics", 406) is None
