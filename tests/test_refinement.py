"""The constitution's story rules are validators, so a malformed story is a
SCHEMA failure the repair loop handles rather than something a human notices
three columns later."""

from __future__ import annotations

import re

import pytest
from pydantic import ValidationError

from crew_org.crews.refinement_crew import (
    MAX_EPICS,
    MIN_EPICS,
    POINT_SCALE,
    AcceptanceCriterion,
    Epic,
    EpicProposal,
    SkeletonEpic,
    Story,
    StoryProposal,
    overreach,
    propose_epics,
)
from tests.asked import Stop, capture


def criteria(n: int) -> list[AcceptanceCriterion]:
    return [AcceptanceCriterion(given=f"g{i}", when=f"w{i}", then=f"t{i}") for i in range(n)]


def story(**overrides) -> Story:
    params = dict(
        title="Report cycle time",
        as_a="Sponsor",
        i_want="cycle time per sprint",
        so_that="I can see how quickly work moves",
        acceptance_criteria=criteria(2),
        points=3,
    )
    params.update(overrides)
    return Story(**params)


@pytest.mark.parametrize("points", POINT_SCALE)
def test_every_point_on_the_scale_is_accepted(points):
    assert story(points=points).points == points


@pytest.mark.parametrize("points", [0, 4, 6, 7, 13, 21])
def test_estimates_off_the_scale_are_rejected(points):
    with pytest.raises(ValidationError, match="estimation scale"):
        story(points=points)


def test_thirteen_points_is_rejected_because_it_must_be_split():
    """§6: nothing larger than 8 enters a sprint."""
    with pytest.raises(ValidationError, match="must be split"):
        story(points=13)


def test_a_single_acceptance_criterion_is_not_enough():
    with pytest.raises(ValidationError, match="at least 2 acceptance criteria"):
        story(acceptance_criteria=criteria(1))


def test_no_acceptance_criteria_is_rejected():
    with pytest.raises(ValidationError):
        story(acceptance_criteria=[])


def test_the_edge_case_requirement_is_stated_in_the_error():
    """The message has to teach, because the model reads it on retry."""
    with pytest.raises(ValidationError, match="failure or edge case"):
        story(acceptance_criteria=criteria(1))


def test_an_empty_proposal_is_rejected():
    with pytest.raises(ValidationError, match="at least 2 epics"):
        EpicProposal(epics=[], ordering_rationale="none")


def test_an_epic_must_decompose_into_at_least_one_story():
    with pytest.raises(ValidationError, match="at least one story"):
        StoryProposal(epic_title="E", stories=[])


def epic(title: str = "Report performance", **overrides) -> Epic:
    params = dict(
        title=title,
        reader="the Sponsor",
        outcome="Sponsor sees metrics",
        rationale="why",
        separately_deliverable="The Sponsor can read the metrics with nothing else built.",
        changes_what_readers_see=False,
    )
    params.update(overrides)
    return Epic(**params)


def test_a_well_formed_proposal_validates():
    proposal = EpicProposal(
        epics=[epic(), epic("Feed other tools")], ordering_rationale="value first"
    )
    assert proposal.epics[0].title == "Report performance"


# --- decomposition, encoded ---------------------------------------------


def test_one_epic_is_not_a_decomposition():
    """Observed in practice: the same goal produced two epics on one run and one
    on the next. The judgement is encoded rather than left to the Sponsor."""
    with pytest.raises(ValidationError, match="not a decomposition"):
        EpicProposal(epics=[epic()], ordering_rationale="only one")


def test_a_walking_skeleton_is_exactly_one_epic():
    """crew#690: the skeleton has one outcome; a second epic is product work."""
    SkeletonEpic(epics=[epic()], ordering_rationale="one outcome")
    with pytest.raises(ValidationError, match="walking skeleton is one epic"):
        SkeletonEpic(epics=[epic(), epic("Render the pages")], ordering_rationale="two")


def test_only_a_skeleton_goal_is_asked_for_one_epic(monkeypatch):
    seen = capture(monkeypatch)
    with pytest.raises(Stop):
        propose_epics("Goal: a walking skeleton", skeleton=True)
    assert seen["form"] is SkeletonEpic
    assert "exactly one epic" in seen["description"]
    assert "has not been decomposed" not in seen["description"]

    with pytest.raises(Stop):
        propose_epics("Goal: a site")
    assert seen["form"] is EpicProposal
    assert f"between {MIN_EPICS} and {MAX_EPICS} epics" in seen["description"]


def test_the_product_owner_chooses_for_the_goals_reader(monkeypatch):
    """crew#846: the Goal is a lead, not a list to copy (ADR 0018, ADR 0035)."""
    seen = capture(monkeypatch)
    with pytest.raises(Stop):
        propose_epics("Goal: a site")
    assert "You choose what serves that reader" in seen["description"]
    assert "not a list to copy" in seen["description"]
    assert "propose the epics that deliver it" not in seen["description"]


def test_an_epic_names_its_reader():
    with pytest.raises(ValidationError, match="reader"):
        Epic(
            title="Report",
            outcome="sees metrics",
            rationale="why",
            separately_deliverable="The Sponsor can read the metrics with nothing else built.",
            changes_what_readers_see=False,
        )


def test_an_epic_that_names_fields_formats_endpoints_or_files_is_sent_back():
    """crew#815: an epic stays at the level of intent (ADR 0035). Each kind, from a
    real epic's words: sm#684 cited files and functions, sm#467 an endpoint."""
    for words, named in [
        ("as count_escalated_events does today", "a field (`count_escalated_events`)"),
        ("from src/sprint_metrics/store.py", "a file (`src/sprint_metrics/store.py`)"),
        ("by asking GET /sprint?label=X", "an endpoint (`GET /sprint?label=X`)"),
        ("as JSON", "a format (`JSON`)"),
    ]:
        with pytest.raises(ValidationError, match=re.escape(named)) as refused:
            EpicProposal(epics=[epic(), epic("Two", outcome=words)], ordering_rationale="x")
        assert '"Two" names' in str(refused.value)
        assert "level of intent" in str(refused.value)


def test_a_plain_language_epic_is_left_alone():
    """crew-presentation#14's words, which the check passed over the past epics."""
    plain = epic(
        "A reader sees how the crew's sprints have been changing",
        outcome="A reader compares the latest sprint with the ones before it, on a phone.",
    )
    EpicProposal(epics=[epic(), plain], ordering_rationale="value first")
    assert overreach("cost in which role, its middle and total") == []


def test_a_walking_skeleton_may_name_its_files():
    """ADR 0030: the skeleton's outcome is the project's files and checks running."""
    SkeletonEpic(
        epics=[epic(outcome="build.py writes site/index.html and tests.yml runs pytest")],
        ordering_rationale="one outcome",
    )


def test_too_many_epics_is_also_rejected():
    many = [epic(f"Epic {i}") for i in range(MAX_EPICS + 1)]
    with pytest.raises(ValidationError, match="too many"):
        EpicProposal(epics=many, ordering_rationale="lots")


def test_the_allowed_range_is_accepted():
    for n in range(MIN_EPICS, MAX_EPICS + 1):
        proposal = EpicProposal(
            epics=[epic(f"Epic {i}") for i in range(n)], ordering_rationale="ok"
        )
        assert len(proposal.epics) == n


def test_duplicate_titles_are_rejected():
    with pytest.raises(ValidationError, match="share a title"):
        EpicProposal(epics=[epic("Same"), epic("same")], ordering_rationale="x")


# --- standing alone ------------------------------------------------------


def test_a_hand_wave_is_not_a_standalone_argument():
    with pytest.raises(ValidationError, match="what a user could do"):
        epic(separately_deliverable="it is useful")


def test_restating_the_outcome_is_not_an_argument():
    """Why a slice stands alone is a different question from what it delivers."""
    with pytest.raises(ValidationError, match="repeats the outcome"):
        epic(
            outcome="The Sponsor can read every metric in a table",
            separately_deliverable="The Sponsor can read every metric in a table",
        )


def test_a_real_standalone_argument_is_accepted():
    assert epic(
        separately_deliverable="The Sponsor can read every metric in the terminal alone."
    ).separately_deliverable


def test_a_story_shows_only_fields_its_package_provides():
    """crew-presentation#68 promised "crew issues fixed", which its package doesn't
    carry: shown, it would have been computed or guessed (crew#690)."""
    from crew_org.crews.refinement_crew import NotInPackage, check_provided

    shows = story().model_copy(
        update={"package_fields": ["sprint_metrics.answers.<key>.throughput"]}
    )
    proposal = StoryProposal(epic_title="E", stories=[shows])
    check_provided(proposal, frozenset({"sprint_metrics.answers.<key>.throughput", "throughput"}))
    check_provided(
        StoryProposal(
            epic_title="E", stories=[story().model_copy(update={"package_fields": ["throughput"]})]
        ),
        frozenset({"throughput"}),
    )

    guessed = story().model_copy(update={"package_fields": ["crew_issues_fixed"]})
    with pytest.raises(ValueError, match="`crew_issues_fixed`.*`not_in_package`"):
        check_provided(StoryProposal(epic_title="E", stories=[guessed]), frozenset({"throughput"}))
    check_provided(
        StoryProposal(epic_title="E", stories=[guessed]), frozenset()
    )  # no package: no check
    asked = StoryProposal(
        epic_title="E",
        stories=[story()],
        not_in_package=[NotInPackage(value="crew issues fixed", why="a big number on the mock")],
    )
    assert asked.not_in_package[0].value == "crew issues fixed"


def test_the_sponsor_is_told_what_the_package_does_not_carry():
    from crew_org.crews.refinement_crew import NotInPackage
    from crew_org.flows.board_flow import not_in_package_note

    note = not_in_package_note([NotInPackage(value="crew issues fixed", why="a big number")])
    assert "For the Sponsor" in note and "- **crew issues fixed**: a big number" in note


def test_the_story_check_reads_the_scale_and_its_ceiling_from_org_yaml():
    """crew#756: one source; a constant beside org.yaml's could change without it."""
    from crew_org.config import load_org
    from crew_org.crews import refinement_crew

    estimation = load_org()["estimation"]
    assert tuple(estimation["scale"]) == refinement_crew.POINT_SCALE
    assert estimation["max_story_points"] == refinement_crew.MAX_POINTS
