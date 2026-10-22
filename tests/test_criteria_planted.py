"""A planted contradiction proves the criteria check still fires (crew#860).

Since the check became a reading session (crew#807) it flagged 1 conflict in 33
checks, and nothing showed whether it still could. Here everything from the split's
proposal to the stories it flags is real: `check_each`, the session's start, the
`done` form and its parsing, the de-duplication and `flagged`. Only the model is a
test double, a fake rather than a scripted answer: it reports the conflict only when
both planted criteria reach it. So a check that stops showing a story its siblings,
or drops a conflict on the way back, finds nothing here and fails the test.

The pair is sprint-metrics#406's: one story's service exits when the database is
down, another's answers 503.
"""

from __future__ import annotations

from pathlib import Path

from crew_org.crews.criteria_crew import check_criteria
from crew_org.crews.refinement_crew import AcceptanceCriterion, Story, StoryProposal
from crew_org.events import EventKind
from crew_org.flows import criteria_check
from crew_org.session import steps
from tests.test_refine_sessions import main  # noqa: F401
from tests.test_session import said, seen  # noqa: F401
from tests.test_session_git import clone  # noqa: F401

EXITS = "the service exits with a non-zero code"
ANSWERS_503 = "GET /health answers 503"
DOWN = "the database is down"


def story(title: str, then: str) -> Story:
    return Story(
        title=title,
        as_a="an operator",
        i_want="the service to say when its database is down",
        so_that="I can tell a fault from a quiet sprint",
        acceptance_criteria=[
            AcceptanceCriterion(given=DOWN, when="the service is running", then=then),
            AcceptanceCriterion(given="the database is up", when="it starts", then="it serves"),
        ],
        points=2,
    )


PLANTED = StoryProposal(
    epic_title="Say when the database is down",
    stories=[
        story("Exit without a database", EXITS),
        story("Report a missing database", ANSWERS_503),
    ],
)


class Fake:
    """The model, as a fake: it reads what the check was shown, and names the conflict
    only when the story under check holds one planted criterion and what it's checked
    against holds the other."""

    def __init__(self) -> None:
        self.shown: list[str] = []

    def __call__(self, body):
        opening = body["messages"][1]["content"]
        self.shown.append(opening)
        mine = opening.partition("## The story under check")[2]
        conflicts = [
            {
                "story": title,
                "criterion": f"Given {DOWN} / When the service is running / Then {own}",
                "against": f"the other story's criterion: Then {other}",
                "why": "with the database down it can't both exit and answer",
            }
            for title, own, other in (
                ("Exit without a database", EXITS, ANSWERS_503),
                ("Report a missing database", ANSWERS_503, EXITS),
            )
            if own in mine and other in opening.replace(mine, "")
        ]
        return said(("done", {"conflicts": conflicts}))


def checked(events):
    """The criteria check's records, as a tick writes them."""
    return [
        e for e in events if e.kind == EventKind.NOTE and e.detail.get("step") == "criteria check"
    ]


def check_with(model: Fake, reading: steps.Reading):
    def check(**shown):
        return check_criteria(**shown, send=model)

    return criteria_check.check_each(check, PLANTED, reading=reading)


def test_the_planted_contradiction_is_flagged_on_both_stories(main: Path, seen):  # noqa: F811
    found = check_with(Fake(), steps.Reading.at(main, "r"))
    assert {c.story for c in found.conflicts} == {s.title for s in PLANTED.stories}
    assert criteria_check.flagged(PLANTED, found) == PLANTED.stories
    noted = checked(seen)
    assert noted[-1].detail["conflicts"] == {s.title: 1 for s in PLANTED.stories}


def test_a_check_not_shown_the_other_story_goes_silent_and_is_counted(main: Path, seen):  # noqa: F811
    """What the planted test catches: with the siblings dropped, nothing is found, and
    the record counts no conflicts, as it would in a tick."""
    model = Fake()

    def blind(**shown):
        return check_criteria(**{**shown, "siblings": ""}, send=model)

    found = criteria_check.check_each(blind, PLANTED, reading=steps.Reading.at(main, "r"))
    assert found.conflicts == [] and len(model.shown) == 2
    noted = checked(seen)
    assert noted[-1].detail["conflicts"] == {s.title: 0 for s in PLANTED.stories}
