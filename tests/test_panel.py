"""The refinement panel (crew#440): what each member is shown, how the four run, and the comment.

Each member's session (`review`) is replaced here, and the session itself is tested
in `test_refine_sessions.py`; `crew panel` was run for real before the pull request,
because a mock is what hid crew#294.
"""

from __future__ import annotations

import json
import threading

import httpx
import pytest
from pydantic import ValidationError

from crew_org.crews import panel_crew
from crew_org.crews.panel_crew import (
    ASKS,
    ROLES,
    Decision,
    Decisions,
    Example,
    Examples,
    PanelContext,
    PanelResult,
    Question,
    ReaderGap,
    ReaderView,
    RuntimeChange,
    RuntimeChanges,
    Sibling,
    opening,
    run_panel,
)
from crew_org.flows import panel
from crew_org.session import steps
from crew_org.tools.github_issues import IssueClient, Trust

TRUST = Trust(sponsor="mquarters", crew=frozenset({"mqucifer-crew[bot]"}), tools=frozenset())


SOURCE = "Goal: 'keeps the history it's given'"


def decision(**overrides):
    params = dict(
        decision="Where the history lives.",
        why="Every later story reads it from there.",
        options="A file, or Postgres; the record names Postgres.",
        source=SOURCE,
    )
    params.update(overrides)
    return Decision(**params)


def nothing(role: str):
    """A member's answer with nothing to raise."""
    if role == "ux_designer":
        return ReaderView(reader="the Sponsor", sees="a trend", matters="it shows a drift")
    return ASKS[role].form()


def context(**overrides) -> PanelContext:
    params = dict(
        goal_ref="mqucifer/sprint-metrics#174",
        goal="GOAL TEXT",
        decisions="# The Sponsor's recorded decisions\n\n## sm#174: DECISIONS TEXT",
        epic_ref="mqucifer/sprint-metrics#406",
        epic="# Run as a service\n\nEPIC TEXT",
        siblings=[
            Sibling("mqucifer/sprint-metrics#186", "delivered", "# The image\n\nSIBLING ONE"),
            Sibling("mqucifer/sprint-metrics#407", "open", "# Trends\n\nSIBLING TWO"),
        ],
    )
    params.update(overrides)
    return PanelContext(**params)


# --- an answer -----------------------------------------------------------------------------


def test_each_member_has_its_own_form_and_kind_of_answer():
    """Different kinds of answer can't repeat each other (ADR 0035, crew#828)."""
    assert {r: a.form for r, a in ASKS.items()} == {
        "ux_designer": ReaderView,
        "architect": Decisions,
        "qa_engineer": Examples,
        "devops_engineer": RuntimeChanges,
    }
    assert len({a.question for a in ASKS.values()}) == len({a.goal for a in ASKS.values()}) == 4


def test_each_form_gives_the_settle_its_items_by_kind():
    view = ReaderView(
        reader="the Sponsor",
        sees="a trend line",
        matters="it shows a drift",
        missing=[ReaderGap(gap="No unit on the axis.", source=SOURCE, settled_by="product_owner")],
    )
    examples = Examples(
        examples=[Example(rule="A rate", example="2 of 4 shows 50%", edge=True, source=SOURCE)],
        questions=[Question(question="What is a sprint?", source=SOURCE, settled_by="sponsor")],
    )
    runtime = RuntimeChanges(
        changes=[RuntimeChange(area="settings", change="DB_URL", source=SOURCE, settled_by="infra")]
    )
    assert [(i.kind, i.settled_by) for i in view.items()] == [("reader", "product_owner")]
    assert [(i.kind, i.settled_by) for i in Decisions(decisions=[decision()]).items()] == [
        ("decision", "architect")
    ]
    assert [(i.kind, i.settled_by) for i in examples.items()] == [
        ("example", "product_owner"),
        ("question", "sponsor"),
    ]
    assert "Edge case: 2 of 4 shows 50%" in examples.items()[0].text
    assert [(i.kind, i.settled_by) for i in runtime.items()] == [("runtime", "infra")]
    assert "Reader: the Sponsor" in view.view() and Decisions().view() == ""


def test_a_small_epic_gets_short_answers():
    """Nothing to raise is an empty list: no runtime change, nothing expensive to reverse."""
    for role in ROLES:
        assert nothing(role).items() == []


def test_an_item_says_who_settles_it_and_only_from_the_four():
    assert decision(settled_by="sponsor").settled_by == "sponsor"
    with pytest.raises(ValidationError):
        decision(settled_by="qa_engineer")


def test_the_results_items_are_in_the_order_the_settle_numbers_them():
    result = PanelResult(
        {
            "architect": Decisions(decisions=[decision(), decision(decision="Two.")]),
            "qa_engineer": Examples(),
            "devops_engineer": RuntimeChanges(
                changes=[RuntimeChange(area="ci", change="CI", source=SOURCE, settled_by="infra")]
            ),
        },
        {},
    )
    assert [(r, i.kind) for r, i in result.items()] == [
        ("architect", "decision"),
        ("architect", "decision"),
        ("devops_engineer", "runtime"),
    ]


# --- what a member is shown ----------------------------------------------------------------


def describe(ctx: PanelContext, role: str) -> str:
    """A member's own parts of its session's start, as it reads them."""
    return "\n\n".join(p for p in opening(ctx, role).values() if p)


@pytest.mark.parametrize("role", ROLES)
def test_every_member_is_shown_the_same_context(role):
    text = describe(context(), role)
    for shown in (
        "GOAL TEXT",
        "DECISIONS TEXT",
        "EPIC TEXT",
        "SIBLING ONE",
        "SIBLING TWO",
    ):
        assert shown in text
    assert "mqucifer/sprint-metrics#186 (delivered)" in text
    assert "mqucifer/sprint-metrics#407 (open)" in text


@pytest.mark.parametrize("role", ROLES)
def test_every_member_reviews_the_epic_in_front_of_it(role):
    text = describe(context(), role)
    assert "Something wholly inside another epic belongs to that epic's review" in text
    assert "a delivered epic is background only" in text


@pytest.mark.parametrize("role", ROLES)
def test_each_member_is_asked_its_own_question_and_not_the_others(role):
    text = describe(context(), role)
    ask = ASKS[role]
    assert f"Your goal is {ask.goal}, and your question is: {ask.question}" in text
    assert ask.how in text
    for other, theirs in ASKS.items():
        if other != role:
            assert theirs.question not in text and theirs.how not in text


def test_devops_lists_the_runtime_contract_and_leaves_the_deployed_runtime_to_infra():
    how = ASKS["devops_engineer"].how
    assert "The project builds to its spec; where it runs is infra's" in how
    for contract in ("settings it reads", "started, health-checked and", "throwaway services"):
        assert contract in how
    assert "mark that change infra" in how


def test_the_architect_is_asked_for_the_versioning_rule_and_sibling_dependencies():
    assert "MAJOR, MINOR or" in ASKS["architect"].how
    assert "name the sibling" in ASKS["architect"].how


def test_qa_writes_examples_with_the_edge_cases_and_asks_what_stops_one():
    how = ASKS["qa_engineer"].how
    assert "concrete situations with real values" in how and "edge cases" in how
    assert "ask for it as a question" in how


def test_an_item_can_be_for_infra_and_the_member_is_told_when():
    described = RuntimeChange.model_json_schema()["properties"]["settled_by"]["description"]
    assert "infra only for the deployed runtime" in described


def test_references_are_asked_for_in_full():
    assert "owner/repo#number" in describe(context(), "qa_engineer")


def test_a_context_without_siblings_or_decisions_still_reads():
    text = describe(context(siblings=[], decisions=""), "qa_engineer")
    assert "other epics under this Goal" not in text
    assert "EPIC TEXT" in text


# --- the four at once ----------------------------------------------------------------------


def test_the_four_members_run_at_the_same_time(monkeypatch):
    """They read independently, and the settle waits for all four (the Sponsor, crew#807)."""
    # Each waits for the other three: this only passes if all four are in flight together.
    barrier = threading.Barrier(len(ROLES), timeout=10)
    seen = []

    def review(ctx, role, reading=None, on_end=None):
        barrier.wait()
        seen.append(role)
        return nothing(role)

    monkeypatch.setattr(panel_crew, "review", review)
    result = run_panel(context())
    assert sorted(seen) == sorted(ROLES)
    assert list(result.answers) == list(ROLES)
    assert result.failed == {}


def test_each_members_model_time_under_load_and_the_throughput_are_recorded(monkeypatch):
    """A first measure of several sessions at once (mqucifer/crew#790)."""
    from types import SimpleNamespace

    from crew_org import events
    from crew_org.events import EventKind, EventSink

    found = []
    sink = EventSink(None)
    sink.subscribe(found.append)
    events.reset_bridge()
    events._TARGETS.append((sink, None))

    def review(ctx, role, reading=None, on_end=None):
        session = SimpleNamespace(calls=3, model_s=60.0, usage={"completion_tokens": 600})
        on_end(session, SimpleNamespace(how="done"))
        return nothing(role)

    monkeypatch.setattr(panel_crew, "review", review)
    try:
        run_panel(context())
    finally:
        events.reset_bridge()
    [note] = [e for e in found if e.kind == EventKind.NOTE and e.detail.get("step") == "panel"]
    assert set(note.detail["sessions"]) == set(ROLES)
    assert note.detail["sessions"]["architect"] == {
        "how": "done",
        "model_calls": 3,
        "model_s": 60.0,
        "completion_tokens": 600,
    }
    assert note.detail["completion_tokens"] == 2400 and note.detail["wall_s"] >= 0


def test_a_member_that_fails_does_not_lose_the_others_notes(monkeypatch):
    def review(ctx, role, reading=None, on_end=None):
        if role == "qa_engineer":
            raise RuntimeError("the model returned nothing")
        return Decisions(decisions=[decision(decision=f"from {role}")])

    monkeypatch.setattr(panel_crew, "review", review)
    result = run_panel(context())
    assert set(result.answers) == set(ROLES) - {"qa_engineer"}
    assert result.failed == {"qa_engineer": "RuntimeError: the model returned nothing"}


def test_a_member_with_no_checkout_to_read_fails_rather_than_answering_blind():
    result = run_panel(context(), roles=("qa_engineer",))
    assert result.answers == {}
    assert result.failed["qa_engineer"].startswith("NoCheckout")


def test_each_member_is_its_own_session_with_its_role_text(monkeypatch):
    """The Architect and QA read with their refinement role text (crew#807)."""
    ran = []
    monkeypatch.setattr(
        steps,
        "run",
        lambda step, parts, reading, **kw: ran.append(step) or nothing(step.role),
    )
    run_panel(context(), reading="the checkout")
    assert sorted((s.name, s.role, s.step or "") for s in ran) == [
        ("panel architect", "architect", "refinement"),
        ("panel devops_engineer", "devops_engineer", "panel"),
        ("panel qa_engineer", "qa_engineer", "panel"),
        ("panel ux_designer", "ux_designer", "panel"),
    ]
    assert all(s.form is ASKS[s.role].form for s in ran)


# --- the comment ---------------------------------------------------------------------------


def test_the_comment_is_marked_and_counts_items_by_who_settles_them():
    result = PanelResult(
        answers={
            "architect": Decisions(
                decisions=[decision(), decision(decision="Two.", settled_by="sponsor")]
            ),
            "ux_designer": ReaderView(reader="the Sponsor", sees="a trend", matters="a drift"),
            "devops_engineer": RuntimeChanges(),
        },
        failed={"qa_engineer": "TimeoutError: slow"},
    )
    text = panel.render(result)
    assert text.startswith(panel.PANEL_MARKER)
    assert "2 items from 3 members" in text
    assert (
        "0 for the Product Owner, 1 for the Architect, 1 for the Sponsor, 0 for the Infra" in text
    )
    assert "### Architect: What does this touch that's expensive to reverse?" in text
    assert "  - Expensive to reverse because: Every later story reads it from there." in text
    assert "  - Settled by: Architect" in text
    assert "- Reader: the Sponsor" in text and "Nothing missing for the reader." in text
    assert "No runtime change." in text
    assert "Did not answer: TimeoutError: slow" in text


def test_the_comment_reads_back_as_the_same_answers():
    result = PanelResult(
        {"architect": Decisions(decisions=[decision()]), "qa_engineer": Examples()},
        {"devops_engineer": "TimeoutError: slow -- twice"},
    )
    assert panel.read_panel(panel.render(result)) == result


def test_a_panel_from_before_the_four_forms_reads_as_none_so_it_runs_again():
    """ADR 0035: an epic not yet settled gets the new panel; there's no old reader."""
    old = (
        f"{panel.PANEL_MARKER}\n<!-- crew:panel-data "
        '{"answers": {"architect": {"nothing_to_add": true, "notes": []}}, "failed": {}} -->'
    )
    assert panel.read_panel(old) is None


# --- the context from GitHub ---------------------------------------------------------------


def issue(number, *, title="", body="", state="open", reason=None, parent=None, by="mquarters"):
    return {
        "number": number,
        "title": title or f"Issue {number}",
        "body": body,
        "state": state,
        "state_reason": reason,
        "user": {"login": by},
        "html_url": f"https://github.com/mqucifer/sprint-metrics/issues/{number}",
        "parent_issue_url": (
            f"https://api.github.com/repos/mqucifer/sprint-metrics/issues/{parent}"
            if parent
            else None
        ),
    }


FOOTER = "\n\n---\n\nProposed by the Product Owner from #174.\n\n**Awaiting Sponsor approval.**"


def github(issues_by_number, children, comments=None):
    """A GitHub with one repository. `children` are the Goal's sub-issues, by Goal number."""
    posted: list[tuple[int, str]] = []
    said = comments or {}

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/search/issues":
            return httpx.Response(200, json={"items": []})
        _, _, _, _repo, _, n, *rest = path.split("/")
        n = int(n)
        if rest == ["sub_issues"]:
            return httpx.Response(200, json=[issues_by_number[c] for c in children.get(n, [])])
        if rest == ["comments"] and request.method == "POST":
            posted.append((n, request.content.decode()))
            return httpx.Response(201, json={"id": 1})
        if rest == ["comments"]:
            return httpx.Response(200, json=said.get(n, []))
        return httpx.Response(200, json=issues_by_number[n])

    client = IssueClient(
        "t", "mqucifer", client=httpx.Client(transport=httpx.MockTransport(answer)), trust=TRUST
    )
    client.posted = posted  # type: ignore[attr-defined]
    return client


def goal_with_epics():
    by = {
        174: issue(174, title="Goal: a service", body="THE GOAL BODY"),
        184: issue(184, state="closed", reason="not_planned", parent=174, body="SUPERSEDED"),
        186: issue(186, state="closed", reason="completed", parent=174, body="DELIVERED" + FOOTER),
        406: issue(406, title="Run as a service", body="THE EPIC" + FOOTER, parent=174),
        407: issue(407, title="Trends", body="OPEN SIBLING", parent=174),
    }
    return by, {174: [184, 186, 406, 407]}


def test_the_context_is_the_goal_the_epic_and_its_live_siblings():
    by, children = goal_with_epics()
    ctx = panel.gather(github(by, children), "sprint-metrics", 406, search=[])
    assert ctx.goal_ref == "mqucifer/sprint-metrics#174"
    assert "THE GOAL BODY" in ctx.goal and "Goal: a service" in ctx.goal
    assert ctx.epic_ref == "mqucifer/sprint-metrics#406"
    assert ctx.epic == "# Run as a service\n\nTHE EPIC"
    assert "The Sponsor's recorded decisions" in ctx.decisions
    assert [(s.ref, s.state) for s in ctx.siblings] == [
        ("mqucifer/sprint-metrics#186", "delivered"),
        ("mqucifer/sprint-metrics#407", "open"),
    ]


def test_a_superseded_epic_and_the_epic_itself_are_not_among_its_siblings():
    by, children = goal_with_epics()
    ctx = panel.gather(github(by, children), "sprint-metrics", 406, search=[])
    refs = [s.ref for s in ctx.siblings]
    assert "mqucifer/sprint-metrics#184" not in refs
    assert "mqucifer/sprint-metrics#406" not in refs
    assert not any("SUPERSEDED" in s.text for s in ctx.siblings)


def test_the_approval_footer_is_not_part_of_what_a_member_reads():
    by, children = goal_with_epics()
    ctx = panel.gather(github(by, children), "sprint-metrics", 406, search=[])
    assert "Proposed by the Product Owner" not in ctx.epic
    assert all("Proposed by the Product Owner" not in s.text for s in ctx.siblings)


def test_an_epic_with_no_parent_has_no_goal_to_be_read_against():
    by, children = goal_with_epics()
    by[406] = issue(406, body="THE EPIC")
    with pytest.raises(panel.NotUnderAGoal):
        panel.gather(github(by, children), "sprint-metrics", 406, search=[])


# --- posting -------------------------------------------------------------------------------


def test_the_comment_is_posted_once_per_epic():
    by, children = goal_with_epics()
    result = PanelResult({"architect": Decisions()}, {})

    fresh = github(by, children)
    assert panel.post(fresh, "sprint-metrics", 406, result) is True
    [(number, body)] = fresh.posted  # type: ignore[attr-defined]
    assert number == 406 and panel.PANEL_MARKER in body

    already = github(
        by,
        children,
        comments={406: [{"user": {"login": "mqucifer-crew[bot]"}, "body": panel.render(result)}]},
    )
    assert panel.post(already, "sprint-metrics", 406, result) is False
    assert already.posted == []  # type: ignore[attr-defined]


def test_a_panel_from_before_the_four_forms_gets_a_new_one_that_reads_back():
    """Refused on the old comment's marker, the panel ran again every tick it waited."""
    by, children = goal_with_epics()
    result = PanelResult({"architect": Decisions(decisions=[decision()])}, {})
    old = (
        f"{panel.PANEL_MARKER}\n<!-- crew:panel-data "
        '{"answers": {"architect": {"nothing_to_add": true, "notes": []}}, "failed": {}} -->'
    )
    comments = [{"user": {"login": "mqucifer-crew[bot]"}, "body": old}]
    client = github(by, children, comments={406: comments})
    assert panel.panel_on(client, "sprint-metrics", 406) is None
    assert panel.post(client, "sprint-metrics", 406, result) is True
    [(_, posted)] = client.posted  # type: ignore[attr-defined]
    comments.append({"user": {"login": "mqucifer-crew[bot]"}, "body": json.loads(posted)["body"]})
    assert panel.panel_on(client, "sprint-metrics", 406) == result
    assert panel.post(client, "sprint-metrics", 406, result) is False
