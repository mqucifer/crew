"""The Goal's look (crew#858): its two forms, what both roles are shown, how they run, the comment.

Each role's session (`read`) is replaced here, and the session itself is tested in
`test_refine_sessions.py`; `crew look` was run for real before the pull request.
"""

from __future__ import annotations

import threading

import httpx
import pytest
from pydantic import ValidationError

from crew_org.crews import goal_look_crew
from crew_org.crews.goal_look_crew import (
    APPROVED,
    ROLES,
    Callout,
    Decided,
    EpicText,
    GoalContext,
    ReaderLook,
    ReaderView,
    SystemLook,
    check_epics,
    opening,
    run_look,
)
from crew_org.flows import goal_look
from crew_org.flows import record as record_flow
from crew_org.rulebook import Refusal
from crew_org.session import steps
from crew_org.tools.github_issues import IssueClient, Trust
from tests.test_mermaid import CONTAINERS, SEQUENCE

TRUST = Trust(sponsor="mquarters", crew=frozenset({"mqucifer-crew[bot]"}), tools=frozenset())
FIRST, SECOND = "mqucifer/sprint-metrics#684", "mqucifer/sprint-metrics#685"
# The crew's own account: only trusted accounts' comments are read (crew#399).
CREW = {"login": "mqucifer-crew[bot]"}


def context(**overrides) -> GoalContext:
    fields = dict(
        goal_ref="mqucifer/sprint-metrics#666",
        goal="Goal: the Sponsor reads a sprint at a glance",
        decisions="",
        epics=[EpicText(FIRST, APPROVED, "# First"), EpicText(SECOND, APPROVED, "# Second")],
    )
    return GoalContext(**{**fields, **overrides})


def system(**overrides) -> SystemLook:
    fields = dict(
        decided=[
            Decided(
                context="Where a sprint's figures come from",
                decision="The delivery-history package",
                weighed="The package, or sprint-metrics' own store: the package is published",
                consequences="Stories read the package",
                epics=["all"],
            )
        ],
        callouts=[Callout(risk="Both epics draw the same chart", epics=[FIRST, SECOND])],
        diagram=CONTAINERS,
        new=["events"],
    )
    return SystemLook(**{**fields, **overrides})


def reader(*epics: str) -> ReaderLook:
    return ReaderLook(
        reader="The Sponsor, at the sprint review",
        epics=[ReaderView(epic=e, meets="a chart", missing="nothing") for e in epics],
    )


# --- the forms ------------------------------------------------------------------------------


def test_the_architects_answer_carries_a_diagram_that_renders():
    assert system().diagram == CONTAINERS
    assert system(sequence=SEQUENCE).sequence == SEQUENCE


def test_a_diagram_that_wouldnt_render_is_refused_naming_why():
    with pytest.raises(ValidationError, match="doesn't render as written.*`tik`"):
        system(diagram=CONTAINERS.replace("Rel(tick, github", "Rel(tik, github"))
    with pytest.raises(ValidationError, match="the sequence: .*block left open"):
        system(sequence=SEQUENCE.replace("\nend\n", "\n"))


def test_a_new_part_has_to_be_one_the_diagram_draws():
    with pytest.raises(ValidationError, match="`?store`? aren't in the diagram|store aren't"):
        system(new=["store"])


def test_an_answer_names_only_the_goals_epics():
    check_epics(system(), context())
    with pytest.raises(Refusal, match="#999 aren't epics of this Goal"):
        check_epics(
            system(callouts=[Callout(risk="x", epics=["mqucifer/sprint-metrics#999"])]),
            context(),
        )


def test_the_reader_is_said_for_every_epic_the_look_reads():
    check_epics(reader(FIRST, SECOND), context())
    with pytest.raises(Refusal, match="#685 too"):
        check_epics(reader(FIRST), context())


# --- what both are shown --------------------------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_each_role_is_shown_the_goal_and_all_its_epics(role):
    start = opening(context(decisions="## The Sponsor's decisions\n\nD1"), role)
    assert "Goal: the Sponsor reads" in start["goal"]
    assert FIRST in start["epics"] and SECOND in start["epics"]
    assert start["decisions"].endswith("D1")
    assert "None of them is split yet" in start["step"]
    assert goal_look_crew.QUESTION[role] in start["step"]


def test_each_role_asks_its_own_question():
    architect, ux = (opening(context(), r)["step"] for r in ("architect", "ux_designer"))
    assert "C4 diagram" in architect and "C4 diagram" not in ux
    assert "the reader" in ux and "beyond_the_goal" in architect


def test_a_later_look_is_shown_the_record_and_the_diagram_it_reads_against():
    later = context(
        epics=[EpicText(FIRST, "looked at", "# First"), EpicText(SECOND, APPROVED, "# Second")],
        record="## The Goal's record\n\nR1",
        diagram=CONTAINERS,
    )
    start = opening(later, "architect")
    assert later.looking_at == [SECOND]
    assert "marked `looked at` are settled" in start["step"]
    assert start["record"].endswith("R1") and "```mermaid\nC4Container" in start["diagram"]


# --- the two at once ------------------------------------------------------------------------


def test_the_two_roles_run_at_the_same_time(monkeypatch):
    barrier = threading.Barrier(len(ROLES), timeout=10)

    def read(ctx, role, reading=None, on_end=None):
        barrier.wait()
        return system() if role == "architect" else reader(FIRST, SECOND)

    monkeypatch.setattr(goal_look_crew, "read", read)
    result = run_look(context())
    assert result.system == system() and result.reader == reader(FIRST, SECOND)
    assert result.failed == {}


def test_a_role_that_fails_does_not_lose_the_others_answer(monkeypatch):
    def read(ctx, role, reading=None, on_end=None):
        if role == "ux_designer":
            raise RuntimeError("the model returned nothing")
        return system()

    monkeypatch.setattr(goal_look_crew, "read", read)
    result = run_look(context())
    assert result.system == system() and result.reader is None
    assert result.failed == {"ux_designer": "RuntimeError: the model returned nothing"}


def test_each_role_is_its_own_session_with_its_form_and_tools(monkeypatch):
    ran = []

    def run(step, parts, reading, **kw):
        ran.append(step)
        return system() if step.role == "architect" else reader(FIRST, SECOND)

    monkeypatch.setattr(steps, "run", run)
    run_look(context(), reading="the checkout")
    assert sorted((s.role, s.step, s.form.__name__, s.tools) for s in ran) == [
        ("architect", "goal_look", "SystemLook", ("contracts",)),
        ("ux_designer", "goal_look", "ReaderLook", ("look",)),
    ]


def test_with_no_checkout_to_read_both_fail_rather_than_answering_blind():
    result = run_look(context())
    assert result.system is None and result.reader is None
    assert set(result.failed) == set(ROLES)


def test_each_role_carries_its_fields_practice():
    for role in ROLES:
        assert "The practice of your field:" in steps.system_text(role, "goal_look")


# --- on GitHub ------------------------------------------------------------------------------


def issue(number, *, title="", body="", state="open", reason=None, parent=None):
    return {
        "number": number,
        "title": title or f"Issue {number}",
        "body": body,
        "state": state,
        "state_reason": reason,
        "user": {"login": "mquarters"},
        "html_url": f"https://github.com/mqucifer/sprint-metrics/issues/{number}",
        "parent_issue_url": (
            f"https://api.github.com/repos/mqucifer/sprint-metrics/issues/{parent}"
            if parent
            else None
        ),
    }


FOOTER = "\n\n---\n\nProposed by the Product Owner from #666.\n\n**Awaiting Sponsor approval.**"


def github(by, children, comments=None):
    said = comments or {}

    def answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/search/issues":
            return httpx.Response(200, json={"items": []})
        _, _, _, _repo, _, n, *rest = path.split("/")
        n = int(n)
        if rest == ["sub_issues"]:
            return httpx.Response(200, json=[by[c] for c in children.get(n, [])])
        if rest == ["comments"]:
            return httpx.Response(200, json=said.get(n, []))
        return httpx.Response(200, json=by[n])

    return IssueClient(
        "t", "mqucifer", client=httpx.Client(transport=httpx.MockTransport(answer)), trust=TRUST
    )


def goal_with_epics():
    by = {
        666: issue(666, title="Goal: a sprint at a glance", body="THE GOAL"),
        683: issue(683, state="closed", reason="not_planned", parent=666, body="SET ASIDE"),
        682: issue(682, state="closed", reason="completed", parent=666, body="DELIVERED"),
        684: issue(684, title="First", body="FIRST EPIC" + FOOTER, parent=666),
        685: issue(685, title="Second", body="SECOND EPIC", parent=666),
        686: issue(686, title="Third", body="NOT APPROVED", parent=666),
    }
    return by, {666: [682, 683, 684, 685, 686]}


def test_the_look_reads_the_goals_approved_epics_with_the_delivered_ones():
    by, children = goal_with_epics()
    ctx = goal_look.gather(
        github(by, children), "sprint-metrics", 666, approved=[684, 685], search=[]
    )
    assert ctx.goal_ref == "mqucifer/sprint-metrics#666" and "THE GOAL" in ctx.goal
    assert [(e.ref, e.state) for e in ctx.epics] == [
        ("mqucifer/sprint-metrics#682", "delivered"),
        (FIRST, APPROVED),
        (SECOND, APPROVED),
    ]
    # The approval footer is the crew's, not the epic's.
    assert ctx.epics[1].text == "# First\n\nFIRST EPIC"
    assert ctx.record == "" and ctx.diagram == ""


def test_a_later_look_reads_the_record_and_diagram_and_only_the_new_epic():
    by, children = goal_with_epics()
    held = record_flow.render(record_flow.Record(on_goal=True, looked_at=(FIRST,)))
    comments = {
        666: [
            {
                "id": 1,
                "user": CREW,
                "body": f"{goal_look.DIAGRAM_MARKER}\n```mermaid\n{CONTAINERS}\n```",
            },
            {"id": 2, "user": CREW, "body": f"{record_flow.GOAL_RECORD_MARKER}\n{held}\n"},
        ]
    }
    ctx = goal_look.gather(
        github(by, children, comments), "sprint-metrics", 666, approved=[684, 685], search=[]
    )
    assert ctx.looking_at == [SECOND]
    assert ("mqucifer/sprint-metrics#684", "looked at") in [(e.ref, e.state) for e in ctx.epics]
    assert ctx.record.startswith(record_flow.GOAL_HEADER)
    assert ctx.diagram == CONTAINERS.rstrip("\n")


def test_the_comment_says_what_each_found():
    result = goal_look_crew.LookResult(system(), reader(FIRST, SECOND), {})
    text = goal_look.render(result, context())
    assert text.startswith(goal_look.LOOK_MARKER)
    assert "**Where a sprint's figures come from:** The delivery-history package" in text
    assert "Both epics draw the same chart" in text and "**The reader:** The Sponsor" in text
    assert "<!-- crew:goal-look-data " in text
