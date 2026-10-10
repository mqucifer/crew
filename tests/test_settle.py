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
    GOAL_QUOTE_CHARS,
    INFRA_CHARS,
    Conclusion,
    Dismissal,
    ForInfra,
    OpenQuestion,
    Row,
    Settlement,
    Unsettled,
    check_covers,
    check_grounded,
    check_not_cut,
    check_product_calls,
    describe,
    note_owners,
    numbered,
)
from crew_org.events import EventSink
from crew_org.flows import panel as panel_flow
from crew_org.flows import record
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
            open=[
                OpenQuestion(
                    question="Which key?", impact="The store", settled_by="architect", settles=[2]
                )
            ],
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
        open=[
            OpenQuestion(
                question="Same version track?",
                impact="Every sender",
                settled_by="architect",
                settles=[3],
            )
        ],
        for_infra=[ForInfra(item="A Postgres schema and user for the service", settles=[5])],
        dismissed=[Dismissal(note=4, why="Wholly inside #186")],
    )


def rendered():
    return flow.render(conclusion(), owner="mqucifer", repo="sprint-metrics", known={"crew"})


def test_the_conclusion_opens_with_its_header_and_the_bottom_line():
    lines = rendered().split("\n")
    assert lines[0] == "## Refinement conclusion"
    assert lines[2] == (
        "Ready to split: 2 decided, 1 open for the Architect, 0 left to the implementer, "
        "1 for infra, 1 dismissed."
    )


def test_ids_and_status_are_the_codes_not_the_models():
    text = rendered()
    assert "| R1 | binding |" in text and "| R2 | binding |" in text
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
    assert "| Q1 | Same version track? | Every sender | Architect |" in rendered()
    left = Conclusion(
        open=[
            OpenQuestion(
                question="Log format?", impact="None", settled_by="implementer", settles=[1]
            )
        ]
    )
    out = flow.render(left, owner="mqucifer", repo="sprint-metrics")
    assert "| Q1 | Log format? | None | Implementer |" in out
    assert "0 open for the Architect, 1 left to the implementer" in out


def test_each_row_says_it_binds_who_set_it_and_when_and_that_it_replaces_nothing():
    text = flow.render(
        Conclusion(rows=[row()]), owner="mqucifer", repo="sprint-metrics", today="2026-10-10"
    )
    assert "| Set by | Date | Replaces | Source |" in text
    assert "| R1 | binding | History | Postgres, own schema |" in text
    assert "| Product Owner | 2026-10-10 | — | Goal decision D1 |" in text


def test_a_question_must_say_who_settles_it():
    with pytest.raises(ValidationError):
        OpenQuestion(question="Which key?", impact="The store", settles=[1])


def test_what_infra_has_to_provide_is_its_own_table_and_not_a_row():
    text = rendered()
    assert "| I1 | A Postgres schema and user for the service |" in text
    assert "For infra: what the deployed runtime has to provide" in text
    assert "| R3 |" not in text


def test_an_epic_with_nothing_for_infra_says_so_in_the_bottom_line():
    text = flow.render(Conclusion(rows=[row()]), owner="mqucifer", repo="sprint-metrics")
    assert "0 for infra" in text and "For infra:" not in text


def test_a_note_for_infra_is_answered_by_listing_it_and_a_conclusion_may_be_only_that():
    only = Settlement(
        conclusion=Conclusion(for_infra=[ForInfra(item="A shared Postgres", settles=[1, 2])])
    )
    check_covers(only, 2)
    with pytest.raises(Unsettled, match="N3"):
        check_covers(only, 3)


def test_an_infra_item_that_ran_into_its_limit_was_cut_off():
    cut = Settlement(
        conclusion=Conclusion(for_infra=[ForInfra(item="x" * INFRA_CHARS, settles=[1])])
    )
    with pytest.raises(Unsettled, match="for infra 1"):
        check_not_cut(cut)


def test_the_task_tells_the_product_owner_what_to_do_with_a_note_a_member_marked_infra():
    text = describe(context(), panel(1, 0, 0, 0))
    assert "**For infra** when a member marked it infra" in text
    assert "no story waits on it" in text and "one of the four" in text


# --- the Product Owner's own call --------------------------------------------------------------


GOAL = "The tool reports on what it's handed and forgets it. It should keep the history it's given."


def own(quote="keep the history it's given", **kw):
    return row(
        own_call=True, goal_wording=quote, source="Restart survival is what keeping means", **kw
    )


def test_a_call_the_product_owner_makes_itself_quotes_the_goal_it_stays_within():
    assert own().own_call is True
    with pytest.raises(ValidationError, match="quotes the Goal's wording"):
        row(own_call=True)
    with pytest.raises(ValidationError):
        own(quote="x" * (GOAL_QUOTE_CHARS + 1))


def test_a_sourced_row_needs_no_quotation():
    assert row().own_call is False and row().goal_wording == ""


def test_a_call_that_quotes_the_goal_is_grounded_whatever_the_case_spacing_or_quote_marks():
    check_grounded(Settlement(conclusion=Conclusion(rows=[own()])), GOAL)
    check_grounded(
        Settlement(conclusion=Conclusion(rows=[own("KEEP  the history it\u2019s given")])),
        GOAL,
    )


def test_a_call_that_quotes_wording_the_goal_does_not_have_is_refused():
    made_up = Settlement(conclusion=Conclusion(rows=[row(), own("survive every upgrade")]))
    with pytest.raises(Unsettled, match=r"row 2: your own call quotes wording that isn't"):
        check_grounded(made_up, GOAL)


def test_a_sourced_row_is_not_held_to_the_goals_words_and_a_question_needs_nothing():
    check_grounded(Settlement(conclusion=Conclusion(rows=[row(source="D1")])), "something else")
    check_grounded(Settlement(sponsor_question="Which?"), GOAL)


def test_a_quotation_that_ran_into_its_limit_was_cut_off():
    cut = Settlement(conclusion=Conclusion(rows=[own("x" * GOAL_QUOTE_CHARS)]))
    with pytest.raises(Unsettled, match="row 1 goal wording"):
        check_not_cut(cut)


def test_an_own_call_says_so_and_carries_the_goals_words_in_the_table():
    out = flow.render(
        Conclusion(rows=[row(), own(settles=[2])]), owner="mqucifer", repo="sprint-metrics"
    )
    lines = out.split("\n")
    assert "2 decided (1 the Product Owner's call)," in out
    first, second = [line for line in lines if line.startswith("| R")]
    assert "Product Owner's call" not in first
    assert "Product Owner's call, within the Goal: \"keep the history it's given\"." in second


def test_a_conclusion_with_no_own_calls_has_no_count_of_them():
    out = flow.render(Conclusion(rows=[row()]), owner="mqucifer", repo="sprint-metrics")
    assert "the Product Owner's call" not in out


def test_the_task_lets_the_product_owner_decide_and_asks_it_to_record_the_call():
    text = describe(context(), panel(1, 0, 0, 0))
    assert "decide it yourself, as the product owner does for the team" in text
    assert "set `own_call`" in text and "quote the Goal's own words" in text
    assert "must not contradict the Goal or a Sponsor decision" in text
    assert "only when you can't tell which way the Goal points" in text
    assert "Never decide something" not in text


def tagged() -> PanelResult:
    """N1 for the Product Owner, N2 for the Architect, N3 for the Sponsor, N4 for the Architect."""
    both = [
        note("a product choice", "product_owner"),
        note("a response shape", "architect"),
        note("a priority", "sponsor"),
    ]
    return PanelResult(
        {
            "qa_engineer": PanelAnswer(nothing_to_add=False, notes=both),
            "architect": PanelAnswer(nothing_to_add=False, notes=[note("a path", "architect")]),
        },
        {},
    )


def test_the_notes_are_marked_in_the_order_they_are_numbered():
    owners = note_owners(tagged())
    assert owners == {1: "product_owner", 2: "architect", 3: "sponsor", 4: "architect"}
    assert [n for n, *_ in numbered(tagged())] == [1, 2, 3, 4]


def test_an_own_call_on_a_design_question_is_refused_and_the_note_named():
    design = Settlement(conclusion=Conclusion(rows=[own(settles=[2]), row(settles=[1, 3, 4])]))
    with pytest.raises(Unsettled, match=r"row 1 \(N2\).*marked for the Architect"):
        check_product_calls(design, tagged())


def test_an_own_call_that_also_settles_a_design_note_is_refused():
    mixed = Settlement(conclusion=Conclusion(rows=[own(settles=[1, 4])]))
    with pytest.raises(Unsettled, match=r"row 1 \(N4\)"):
        check_product_calls(mixed, tagged())


def test_an_own_call_on_a_product_or_sponsor_note_is_allowed():
    check_product_calls(
        Settlement(conclusion=Conclusion(rows=[own(settles=[1, 3]), row(settles=[2, 4])])),
        tagged(),
    )


def test_a_row_with_a_source_may_settle_a_design_note_and_a_question_is_unchecked():
    check_product_calls(
        Settlement(
            conclusion=Conclusion(rows=[row(settles=[2, 4], source="D6"), row(settles=[1, 3])])
        ),
        tagged(),
    )
    check_product_calls(Settlement(sponsor_question="Which?"), tagged())


def test_the_task_keeps_design_questions_for_the_design_note():
    text = describe(context(), tagged())
    assert "Your own calls are for what the product does" in text
    assert "marked for the Architect is a design question" in text


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
        if path.endswith("/labels") and "/issues/" not in path:
            return httpx.Response(201, json={})  # a repository's label, created
        if path.endswith("/labels"):
            self.labels += json.loads(request.content)["labels"]
            return httpx.Response(200, json=[])
        if method == "PATCH":
            self.body = json.loads(request.content)["body"]
            self.writes.append(("body", self.body))
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={
                "number": 406,
                "body": self.body,
                "state": "open",
                "labels": [{"name": name} for name in self.labels],
            },
        )

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


def test_an_own_call_that_quotes_wording_the_goal_lacks_is_asked_for_again(monkeypatch):
    gh = Github()
    made_up = Settlement(conclusion=Conclusion(rows=[own("made up wording", settles=[1, 2])]))
    grounded = Settlement(conclusion=Conclusion(rows=[own("GOAL", settles=[1, 2])]))
    result, calls, _ = run(gh, monkeypatch, made_up, grounded)
    assert result.outcome is flow.Outcome.WRITTEN
    assert "isn't in the Goal" in calls[1]["feedback"]
    assert 'Product Owner\'s call, within the Goal: "GOAL"' in gh.body


def test_each_refusal_is_in_the_event_log_with_its_reason(monkeypatch):
    gh = Github()
    short = Settlement(conclusion=Conclusion(rows=[row(settles=[1])]))
    _, _, events = run(gh, monkeypatch, short, good())
    refused = [e for e in events if "conclusion refused" in e.summary]
    assert len(refused) == 1
    assert "attempt 1 of 3" in refused[0].summary and "N2" in refused[0].summary
    assert "No row, question or dismissal for N2" in refused[0].detail["reason"]


def test_an_own_call_on_a_design_question_is_asked_for_again_as_an_open_question(monkeypatch):
    gh = Github()
    design = Settlement(conclusion=Conclusion(rows=[own("GOAL", settles=[1, 2])]))
    fixed = Settlement(
        conclusion=Conclusion(
            rows=[own("GOAL", settles=[1])],
            open=[
                OpenQuestion(
                    question="Which response shape?",
                    impact="The API",
                    settled_by="architect",
                    settles=[2],
                )
            ],
        )
    )
    two = PanelResult(
        {
            "qa_engineer": PanelAnswer(
                nothing_to_add=False,
                notes=[note("a product choice", "product_owner"), note("a shape", "architect")],
            )
        },
        {},
    )
    result, calls, _ = run(gh, monkeypatch, design, fixed, notes=two)
    assert result.outcome is flow.Outcome.WRITTEN
    assert "marked for the Architect" in calls[1]["feedback"]
    assert "| Q1 | Which response shape?" in gh.body


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


def test_a_panel_that_raised_nothing_is_settled_without_a_model_call(monkeypatch):
    gh = Github()
    nothing = PanelResult({"architect": PanelAnswer(nothing_to_add=True)}, {})
    result, calls, _ = run(gh, monkeypatch, notes=nothing)
    assert result.outcome is flow.Outcome.WRITTEN and calls == []
    assert gh.body.startswith(APPROVED)
    assert gh.body.rstrip().endswith("## Refinement conclusion\n\n" + record.NOTHING_RAISED)
    # And it is a conclusion, so the next tick leaves the epic alone.
    again, calls, _ = run(gh, monkeypatch, notes=nothing)
    assert again.outcome is flow.Outcome.ALREADY and calls == []


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


# --- a decision for every epic reaches the project's log (crew#468) ---------------------------


def test_a_row_that_decides_for_every_epic_is_marked_in_the_conclusion():
    marked = Conclusion(rows=[row(), row(settles=[2], context="Counting", project_wide=True)])
    text = flow.render(marked, owner="mqucifer", repo="sprint-metrics")
    assert record.TO_LOG.format("R2") in text
    assert "crew:to-log" not in rendered(), "a conclusion with none says nothing"


def test_settling_a_project_wide_row_labels_the_epic_for_the_log(monkeypatch):
    gh = Github()
    wide = Settlement(conclusion=Conclusion(rows=[row(settles=[1, 2], project_wide=True)]))
    run(gh, monkeypatch, wide)
    assert flow.TO_LOG_LABEL in gh.labels


def test_an_epic_whose_rows_are_its_own_gets_no_label(monkeypatch):
    gh = Github()
    run(gh, monkeypatch, good())
    assert flow.TO_LOG_LABEL not in gh.labels


def test_writing_the_conclusion_posts_one_comment_naming_its_rows(monkeypatch):
    gh = Github()
    run(gh, monkeypatch, good())
    [note] = [body for kind, body in gh.writes if kind == "comment"]
    assert note.startswith(record.CHANGE.format("R1"))
    assert "by the Product Owner" in note and "Settled from the panel's notes." in note


# --- the Architect settles the design questions before the split (ADR 0018) ----------------

from crew_org.crews.settle_crew import DesignAnswer, DesignSettlement  # noqa: E402

DESIGNED = (
    f"{APPROVED}\n\n## Refinement conclusion\n\n"
    "| ID | Status | Context | Decision | Consequences | Source |\n|---|---|---|---|---|---|\n"
    "| R1 | accepted | History | Postgres | A client | D1 |\n\n"
    "| ID | Open question | Impact | Settled by |\n|---|---|---|---|\n"
    "| Q1 | JSON field names? | Every reader | Design note |\n"
    "| Q2 | Log format? | Nobody | Implementer |\n"
)


def answered(*ids):
    return DesignSettlement(
        settled=[
            DesignAnswer(
                question=i,
                decision="first_attempt_count, first_attempt_total",
                consequences="Every story names these keys",
                source="metrics.py ALL_METRICS naming",
            )
            for i in ids
        ]
    )


def design(gh, monkeypatch, *answers):
    asked: list[dict] = []
    queue = list(answers)

    def fake(ctx, **kw):
        asked.append(kw)
        return queue.pop(0)

    monkeypatch.setattr(flow, "settle_design", fake)
    result = flow.settle_design_questions(
        gh.client(), EventSink(None), repo="sprint-metrics", epic=406, context=context(), code="MAP"
    )
    return result, asked


def test_the_architect_is_shown_only_its_questions_with_the_record_and_the_code(monkeypatch):
    _, asked = design(Github(DESIGNED), monkeypatch, answered("Q1"))
    assert "| Q1 | JSON field names? |" in asked[0]["questions"]
    assert "Q2" not in asked[0]["questions"], "the implementer's question isn't the Architect's"
    assert "| R1 |" in asked[0]["record"] and asked[0]["code"] == "MAP"


def test_each_answer_becomes_a_binding_row_by_the_architect_that_replaces_its_question(
    monkeypatch,
):
    gh = Github(DESIGNED)
    result, _ = design(gh, monkeypatch, answered("Q1"))
    assert result.outcome is flow.Outcome.WRITTEN
    found = record.parse(record.split(gh.body)[1])
    row = found.row("R2")
    assert row.set_by == "Architect" and row.replaces == "Q1" and row.binding
    assert "first_attempt_count" in row.decision
    assert [q.id for q in found.open] == ["Q2"] and found.for_architect() == []
    assert gh.body.startswith(APPROVED)


def test_the_change_is_posted_once_naming_the_question_it_settles(monkeypatch):
    gh = Github(DESIGNED)
    design(gh, monkeypatch, answered("Q1"))
    [note] = [body for kind, body in gh.writes if kind == "comment"]
    assert "**R2** settles Q1" in note and "by the Architect" in note


def test_a_question_left_out_is_asked_for_again_by_name(monkeypatch):
    _, asked = design(Github(DESIGNED), monkeypatch, DesignSettlement(settled=[]), answered("Q1"))
    assert "You didn't settle Q1" in asked[1]["feedback"]


def test_a_question_never_settled_is_for_a_person_and_the_epic_waits(monkeypatch):
    gh = Github(DESIGNED)
    result, _ = design(gh, monkeypatch, *[DesignSettlement(settled=[])] * flow.ATTEMPTS)
    assert result.outcome is flow.Outcome.WAITING
    assert "needs:human" in gh.labels
    assert any(flow.DESIGN_QUESTION_MARKER in body for kind, body in gh.writes if kind == "comment")
    assert record.parse(record.split(gh.body)[1]).for_architect(), "nothing was written"


def test_the_approval_gates_label_doesnt_hold_an_approved_epic(monkeypatch):
    # Every proposed epic carries needs:human until the split (crew#608).
    gh = Github(DESIGNED)
    gh.labels = ["needs:human"]
    result, asked = design(gh, monkeypatch, answered("Q1"))
    assert result.outcome is flow.Outcome.WRITTEN and len(asked) == 1


def test_an_epic_waiting_on_the_architects_question_makes_no_call(monkeypatch):
    gh = Github(DESIGNED)
    design(gh, monkeypatch, *[DesignSettlement(settled=[])] * flow.ATTEMPTS)
    result, asked = design(gh, monkeypatch)
    assert result.outcome is flow.Outcome.WAITING and asked == []


def test_taking_the_label_off_has_the_architect_try_again(monkeypatch):
    gh = Github(DESIGNED)
    design(gh, monkeypatch, *[DesignSettlement(settled=[])] * flow.ATTEMPTS)
    gh.labels = []
    result, asked = design(gh, monkeypatch, answered("Q1"))
    assert result.outcome is flow.Outcome.WRITTEN and len(asked) == 1


def test_an_epic_with_nothing_for_the_architect_makes_no_call(monkeypatch):
    gh = Github(DESIGNED.replace("| Design note |", "| Implementer |"))
    result, asked = design(gh, monkeypatch)
    assert result.outcome is flow.Outcome.ALREADY and asked == []


def test_an_answer_that_ran_into_its_limit_is_sent_back():
    from crew_org.crews.settle_crew import DECISION_CHARS, check_design_cut

    cut = DesignSettlement(
        settled=[
            DesignAnswer(
                question="Q1", decision="x" * DECISION_CHARS, consequences="cc", source="ss"
            )
        ]
    )
    with pytest.raises(Unsettled, match="Q1 decision"):
        check_design_cut(cut)
