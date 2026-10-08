"""A merged design's work goes where it belongs, not all into technical epics (crew#439).

sprint-metrics' design of 2026-10-01 declared six changes, and all six became
technical epics, which skip the Sponsor's gate and hold every other epic. Three were
what approved epics already deliver, one could only fail until the service existed,
and one corrected a typo in the design's own text. Only a prerequisite no epic
delivers is a technical epic now.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from crew_org.crews.design_crew import Change, Placement, Placements
from crew_org.events import EventSink
from crew_org.flows.revisit import (
    DESIGN_WORK_HEADER,
    Revisits,
    _give_to_epic,
    file_technical_epics,
    file_unchanged_design_work,
)
from tests.test_revisit import REPO, SPLIT, Board, Issues, changes_block, epic, proposal

STORE = Change(
    what="Name store.py as the module that keeps the history",
    was="no store",
    why="the service keeps what it's given",
    needs_work=True,
    work="Build store.py, which keeps board events in Postgres",
)
PROBE = Change(
    what="Add a service-mode check to CI",
    was="CI runs the tool once",
    why="the image must start as a service",
    needs_work=True,
    work="Prove in CI that the image starts in service mode",
)
TYPO = Change(
    what="Fix 'Prometeus' in the design's text",
    was="misspelled",
    why="review note",
    needs_work=True,
    work="Correct the spelling of Prometheus in the design",
)


class Issues439(Issues):
    """The revisit fake, with epics whose bodies can be edited."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.edits: list[int] = []

    def edit_issue(self, repo, number, *, body):
        self.bodies[number] = body
        self.edits.append(number)
        return {}


class Placer:
    """The Architect's placement, scripted, with what it was shown."""

    def __init__(self, *placements, fails=False):
        self.placements, self.fails, self.calls = list(placements), fails, []

    def __call__(self, **context):
        self.calls.append(context)
        if self.fails:
            raise RuntimeError("the model answered nothing")
        return Placements(placements=self.placements)


def file(issues, changes, place, cards):
    pull = {"number": 82, "body": changes_block(proposal(changes=changes))}
    return file_technical_epics(
        issues,
        Board(),
        EventSink(),
        repo=REPO,
        pull=pull,
        result=Revisits(),
        cards=cards,
        place=place,
    )


OUTCOME = "**Outcome** — A user runs the released container as a service."


def test_work_an_epic_delivers_goes_into_that_epic_not_a_technical_epic():
    issues = Issues439(bodies={406: OUTCOME})
    place = Placer(
        Placement(change=1, kind="prerequisite", why="no epic splits the module"),
        Placement(change=2, kind="epic", epic=406, why="406 builds the stateful service"),
    )

    filed = file(issues, [SPLIT, STORE], place, [epic(406)])

    assert [c["title"] for c in issues.created] == [SPLIT.work], "only the prerequisite is filed"
    assert filed == [200]
    assert DESIGN_WORK_HEADER in issues.bodies[406]
    assert STORE.work in issues.bodies[406] and "the design merged in #82" in issues.bodies[406]
    assert "Given to the epics that deliver it: #406" in issues.comments_on[82][0]
    assert "#406: Epic 406" in place.calls[0]["epics"] and OUTCOME in place.calls[0]["epics"]


def test_work_that_can_only_pass_once_an_epic_exists_builds_on_it_and_holds_nothing():
    issues = Issues439(bodies={406: OUTCOME})
    place = Placer(Placement(change=1, kind="epic", epic=406, why="the service mode is 406's"))

    assert file(issues, [PROBE], place, [epic(406)]) == []
    assert issues.created == [], "no technical epic, so nothing holds 406's split"
    assert PROBE.work in issues.bodies[406]


def test_a_correction_to_the_designs_own_text_files_nothing():
    issues = Issues439(bodies={406: OUTCOME})
    place = Placer(Placement(change=1, kind="design_text", why="a typo in the PR's text"))

    assert file(issues, [TYPO], place, [epic(406)]) == []
    assert issues.created == [] and issues.edits == []
    assert "Corrections to the design's own text, needing no work: 1" in issues.comments_on[82][0]


def test_an_epic_not_shown_or_a_change_left_unplaced_is_still_filed():
    issues = Issues439(bodies={406: OUTCOME})
    place = Placer(Placement(change=1, kind="epic", epic=999, why="guessed"))

    filed = file(issues, [SPLIT, STORE], place, [epic(406)])

    assert len(filed) == 2, "work is never dropped on a bad placement"
    assert issues.edits == []


def test_a_failed_placement_files_every_change_as_before():
    issues = Issues439(bodies={406: OUTCOME})
    assert len(file(issues, [SPLIT, STORE], Placer(fails=True), [epic(406)])) == 2


def test_with_no_open_product_epic_nothing_is_asked():
    issues = Issues439()
    place = Placer()
    cards = [epic(300, labels={"technical"}), epic(301, state="CLOSED")]

    assert len(file(issues, [SPLIT], place, cards)) == 1
    assert place.calls == [], "technical and closed epics aren't somewhere work can go"


def test_a_design_whose_record_already_says_it_places_its_work_too():
    issues = Issues439(bodies={406: OUTCOME})
    place = Placer(Placement(change=1, kind="epic", epic=406, why="406 builds it"))

    filed = file_unchanged_design_work(
        issues,
        Board(),
        EventSink(),
        repo=REPO,
        proposal=proposal(changes=[STORE]),
        result=Revisits(),
        cards=[epic(406)],
        place=place,
    )

    assert filed == [] and STORE.work in issues.bodies[406]


def test_given_work_sits_above_the_conclusion_once():
    conclusion = "## Refinement conclusion\n\nReady to split: 1 decided."
    issues = Issues439(bodies={406: f"{OUTCOME}\n\n{conclusion}\n"})

    for _ in range(2):
        _give_to_epic(issues, EventSink(), repo=REPO, epic=406, work=STORE.work, source="x")
    _give_to_epic(issues, EventSink(), repo=REPO, epic=406, work=PROBE.work, source="x")

    body = issues.bodies[406]
    assert body.count(STORE.work) == 1
    assert body.index(DESIGN_WORK_HEADER) < body.index(STORE.work) < body.index(PROBE.work)
    assert body.index(PROBE.work) < body.index("## Refinement conclusion")
    assert body.count(DESIGN_WORK_HEADER) == 1


def test_a_placement_on_an_epic_names_it():
    with pytest.raises(ValidationError, match="name it in `epic`"):
        Placement(change=1, kind="epic", why="it's an epic's")
