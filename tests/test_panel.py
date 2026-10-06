"""The refinement panel (crew#440): what each member is shown, how the four run, and the comment.

The model call itself (`review`) is replaced here; `crew panel` was run for real
before the pull request, because a mock is what hid crew#294.
"""

from __future__ import annotations

import threading

import httpx
import pytest
from pydantic import ValidationError

from crew_org.crews import panel_crew
from crew_org.crews.panel_crew import (
    FOCUS,
    ROLES,
    PanelAnswer,
    PanelContext,
    PanelNote,
    PanelResult,
    Sibling,
    describe,
    run_panel,
)
from crew_org.flows import panel
from crew_org.tools.github_issues import IssueClient, Trust

TRUST = Trust(sponsor="mquarters", crew=frozenset({"mqucifer-crew[bot]"}), tools=frozenset())


def note(**overrides):
    params = dict(
        problem="History is kept in memory.",
        source="Goal: 'keeps the history it's given'",
        settle="Where the history lives.",
        settled_by="product_owner",
    )
    params.update(overrides)
    return PanelNote(**params)


def context(**overrides) -> PanelContext:
    params = dict(
        goal_ref="mqucifer/sprint-metrics#174",
        goal="GOAL TEXT",
        project="## What this project is for\n\nPROJECT TEXT",
        decisions="# The Sponsor's recorded decisions\n\nDECISIONS TEXT",
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


def test_an_answer_is_notes_or_nothing_to_add_never_both_or_neither():
    PanelAnswer(nothing_to_add=True)
    PanelAnswer(nothing_to_add=False, notes=[note()])
    with pytest.raises(ValidationError):
        PanelAnswer(nothing_to_add=True, notes=[note()])
    with pytest.raises(ValidationError):
        PanelAnswer(nothing_to_add=False)


def test_a_note_says_who_settles_it_and_only_from_the_three():
    assert note(settled_by="sponsor").settled_by == "sponsor"
    with pytest.raises(ValidationError):
        note(settled_by="qa_engineer")


# --- what a member is shown ----------------------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_every_member_is_shown_the_same_context(role):
    text = describe(context(), role)
    for shown in (
        "GOAL TEXT",
        "PROJECT TEXT",
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
    assert "A problem wholly inside another epic belongs to that epic's review" in text
    assert "a delivered epic is background only" in text
    assert FOCUS[role] in text


def test_each_member_gets_its_own_area_and_not_the_others():
    texts = {role: describe(context(), role) for role in ROLES}
    for role, text in texts.items():
        for other in ROLES:
            if other != role:
                assert FOCUS[other] not in text


def test_devops_checks_the_runtime_contract_and_leaves_the_deployed_runtime_to_infra():
    focus = FOCUS["devops_engineer"]
    assert "The project builds to its spec; where it runs is infra's" in focus
    # What the product owes: its settings, startup, health, and the CI proof.
    for contract in ("settings it reads", "started, health-checked and", "throwaway services"):
        assert contract in focus
    # What it doesn't: production, real secrets, provisioning.
    assert "The deployed runtime is infra's, not the epic's" in focus
    assert "mark it infra" in focus
    assert "local checks, CI, a test bed, production" not in focus


def test_the_architect_is_asked_for_the_versioning_rule_and_sibling_dependencies():
    assert "MAJOR, MINOR or" in FOCUS["architect"]
    assert "name the sibling" in FOCUS["architect"]


def test_a_note_can_be_for_infra_and_the_member_is_told_when():
    assert note(settled_by="infra").settled_by == "infra"
    described = PanelNote.model_json_schema()["properties"]["settled_by"]["description"]
    assert "infra only for the deployed runtime" in described


def test_references_are_asked_for_in_full():
    assert "owner/repo#number" in describe(context(), "qa_engineer")


def test_a_context_without_siblings_or_decisions_still_reads():
    text = describe(context(siblings=[], decisions="", project=""), "qa_engineer")
    assert "other epics under this Goal" not in text
    assert "EPIC TEXT" in text


# --- the four at once ----------------------------------------------------------------------


def test_the_four_members_run_at_the_same_time(monkeypatch):
    # Each waits for the other three: this only passes if all four are in flight together.
    barrier = threading.Barrier(len(ROLES), timeout=10)
    seen = []

    def review(ctx, role):
        barrier.wait()
        seen.append(role)
        return PanelAnswer(nothing_to_add=True)

    monkeypatch.setattr(panel_crew, "review", review)
    result = run_panel(context())
    assert sorted(seen) == sorted(ROLES)
    assert list(result.answers) == list(ROLES)
    assert result.failed == {}


def test_a_member_that_fails_does_not_lose_the_others_notes(monkeypatch):
    def review(ctx, role):
        if role == "qa_engineer":
            raise RuntimeError("the model returned nothing")
        return PanelAnswer(nothing_to_add=False, notes=[note(problem=f"from {role}")])

    monkeypatch.setattr(panel_crew, "review", review)
    result = run_panel(context())
    assert set(result.answers) == set(ROLES) - {"qa_engineer"}
    assert result.failed == {"qa_engineer": "RuntimeError: the model returned nothing"}


def test_a_member_whose_model_gave_no_structured_answer_is_a_failure_not_a_none(monkeypatch):
    from types import SimpleNamespace

    class Crew:
        def __init__(self, **_):
            pass

        def kickoff(self):
            return SimpleNamespace(pydantic=None)

    monkeypatch.setattr(panel_crew, "build_agent", lambda role: object())
    monkeypatch.setattr(panel_crew, "Task", lambda **_: object())
    monkeypatch.setattr(panel_crew, "Crew", Crew)
    with pytest.raises(ValueError, match="gave no answer"):
        panel_crew.review(context(), "qa_engineer")


# --- the comment ---------------------------------------------------------------------------


def test_the_comment_is_marked_and_counts_notes_by_who_settles_them():
    result = PanelResult(
        answers={
            "architect": PanelAnswer(
                nothing_to_add=False,
                notes=[note(settled_by="architect"), note(problem="Two.", settled_by="sponsor")],
            ),
            "ux_designer": PanelAnswer(nothing_to_add=True),
        },
        failed={"devops_engineer": "TimeoutError: slow"},
    )
    text = panel.render(result)
    assert text.startswith(panel.PANEL_MARKER)
    assert "2 notes from 2 members" in text
    assert (
        "0 for the Product Owner, 1 for the Architect, 1 for the Sponsor, 0 for the Infra" in text
    )
    assert "Nothing to add." in text
    assert "  - Settled by: Architect" in text
    assert "Did not answer: TimeoutError: slow" in text
    assert "### Architect" in text and "### DevOps Engineer" in text


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
    ctx = panel.gather(github(by, children), "sprint-metrics", 406, project="THE RECORD", search=[])
    assert ctx.goal_ref == "mqucifer/sprint-metrics#174"
    assert "THE GOAL BODY" in ctx.goal and "Goal: a service" in ctx.goal
    assert ctx.epic_ref == "mqucifer/sprint-metrics#406"
    assert ctx.epic == "# Run as a service\n\nTHE EPIC"
    assert ctx.project == "THE RECORD"
    assert "The Sponsor's recorded decisions" in ctx.decisions
    assert [(s.ref, s.state) for s in ctx.siblings] == [
        ("mqucifer/sprint-metrics#186", "delivered"),
        ("mqucifer/sprint-metrics#407", "open"),
    ]


def test_a_superseded_epic_and_the_epic_itself_are_not_among_its_siblings():
    by, children = goal_with_epics()
    ctx = panel.gather(github(by, children), "sprint-metrics", 406, project="", search=[])
    refs = [s.ref for s in ctx.siblings]
    assert "mqucifer/sprint-metrics#184" not in refs
    assert "mqucifer/sprint-metrics#406" not in refs
    assert not any("SUPERSEDED" in s.text for s in ctx.siblings)


def test_the_approval_footer_is_not_part_of_what_a_member_reads():
    by, children = goal_with_epics()
    ctx = panel.gather(github(by, children), "sprint-metrics", 406, project="", search=[])
    assert "Proposed by the Product Owner" not in ctx.epic
    assert all("Proposed by the Product Owner" not in s.text for s in ctx.siblings)


def test_an_epic_with_no_parent_has_no_goal_to_be_read_against():
    by, children = goal_with_epics()
    by[406] = issue(406, body="THE EPIC")
    with pytest.raises(panel.NotUnderAGoal):
        panel.gather(github(by, children), "sprint-metrics", 406, project="", search=[])


# --- posting -------------------------------------------------------------------------------


def test_the_comment_is_posted_once_per_epic():
    by, children = goal_with_epics()
    result = PanelResult({"architect": PanelAnswer(nothing_to_add=True)}, {})

    fresh = github(by, children)
    assert panel.post(fresh, "sprint-metrics", 406, result) is True
    [(number, body)] = fresh.posted  # type: ignore[attr-defined]
    assert number == 406 and panel.PANEL_MARKER in body

    already = github(
        by,
        children,
        comments={406: [{"user": {"login": "mqucifer-crew[bot]"}, "body": panel.PANEL_MARKER}]},
    )
    assert panel.post(already, "sprint-metrics", 406, result) is False
    assert already.posted == []  # type: ignore[attr-defined]
