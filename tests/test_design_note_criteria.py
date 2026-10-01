"""A design note can't overrule a story's acceptance criterion (#258, #425).

Epic sprint-metrics#59's note told #145's test to leave out the stdout-empty
assertion that #145's own criterion required, so #146's change would be "a
pure addition". Design review couldn't see it: the Code Reviewer was shown the
note and the project's guidelines, never the stories. #145 then went round
the gates six times.
"""

from __future__ import annotations

import contextlib

from crew_org.crews import design_crew, design_note_crew
from crew_org.crews.design_crew import Conflict, DesignReview
from crew_org.flows.board_flow import NEEDS_REWORK, STORY_PROBLEM_MARKER, story_problem_evidence
from tests.test_design_notes import Architect, Issues, design_epic, note, run

CRITERION = "the exit code is 2, stderr contains the label, and stdout is empty"
STORIES = {
    50: [
        {"number": 145, "title": "Prior sprint in JSON", "body": f"**Then** {CRITERION}"},
        {"number": 146, "title": "Errors as JSON", "body": "**Then** stdout has an error object"},
    ]
}
OVERRULED = DesignReview(
    conflicts=[
        Conflict(
            guideline=f"#145's criterion: {CRITERION}",
            choice="#145's test omits the stdout assertion",
            why="the criterion requires stdout to be empty",
        )
    ]
)


class Reviewer:
    def __init__(self, *reviews) -> None:
        self.reviews, self.calls = list(reviews), []

    def __call__(self, **context):
        self.calls.append(context)
        return self.reviews.pop(0) if self.reviews else DesignReview()


def test_the_design_review_is_shown_the_stories_and_their_criteria(tmp_path):
    reviewer = Reviewer()
    run(tmp_path, Issues(subs=STORIES), Architect(note()), reviewer)
    (call,) = reviewer.calls
    assert "#145 Prior sprint in JSON" in call["stories"] and CRITERION in call["stories"]


def test_a_note_that_overrules_a_criterion_is_sent_back_naming_it(tmp_path):
    architect = Architect(note(), note())
    result = run(tmp_path, Issues(subs=STORIES), architect, Reviewer(OVERRULED))
    assert CRITERION in architect.calls[1]["feedback"]
    assert result.written == [50]


def test_a_note_that_still_overrules_a_criterion_is_not_written(tmp_path):
    issues = Issues(subs=STORIES)
    result = run(tmp_path, issues, Architect(note(), note()), Reviewer(OVERRULED, OVERRULED))
    ((number, why),) = result.blocked
    assert number == 50 and "a story's criterion" in why and CRITERION in why
    assert result.written == []


def captured_description(monkeypatch, module, call):
    seen: dict = {}

    class Stop(Exception):
        pass

    def crew(**kwargs):
        raise Stop

    monkeypatch.setattr(module, "Task", lambda **k: seen.update(k))
    monkeypatch.setattr(module, "Crew", crew)
    with contextlib.suppress(Stop):
        call()
    return seen["description"]


def test_the_reviewer_is_asked_about_criteria_only_when_shown_stories(monkeypatch):
    monkeypatch.setattr(design_crew, "_role", lambda *a: None)
    with_stories = captured_description(
        monkeypatch,
        design_crew,
        lambda: design_crew.review_design(project="p", design="d", stories=f"#145 {CRITERION}"),
    )
    assert "drop, weaken or defer what that story's own criterion requires" in with_stories
    assert CRITERION in with_stories

    project_design = captured_description(
        monkeypatch, design_crew, lambda: design_crew.review_design(project="p", design="d")
    )
    assert "criterion" not in project_design, "a project's design has no stories to overrule"


def test_the_architect_is_told_criteria_are_the_products_decisions(monkeypatch):
    monkeypatch.setattr(design_note_crew, "load_agents", lambda: {"architect": {}})
    monkeypatch.setattr(design_note_crew, "build_agent", lambda *a: None)
    text = captured_description(
        monkeypatch,
        design_note_crew,
        lambda: design_note_crew.write_note(epic="e", stories="s", project="", repository=""),
    )
    assert "never drop, weaken or defer one" in text


# --- #425: a note that can't meet the stories' criteria sends the stories back ---------------

CONTRADICTORY = DesignReview(
    conflicts=[
        Conflict(
            guideline="#145 criterion 5: the response has HTTP status 400",
            choice="every unknown card returns 404",
            why="criterion 4 asks 404 of the same request",
            story=145,
        )
    ]
)


class Labelled(Issues):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.ensured: list[str] = []

    def ensure_label(self, repo, name, **kwargs):
        self.ensured.append(name)


def test_a_note_that_still_contradicts_only_criteria_goes_to_the_product_owner(tmp_path):
    issues = Labelled(subs=STORIES)
    result = run(
        tmp_path, issues, Architect(note(), note()), Reviewer(CONTRADICTORY, CONTRADICTORY)
    )
    ((number, why),) = result.returned
    assert number == 50 and "HTTP status 400" in why
    assert result.blocked == [] and result.written == []
    assert issues.labels == [(50, NEEDS_REWORK)], "not blocked, not for a person"
    ((on, body),) = issues.posted
    assert on == 50 and STORY_PROBLEM_MARKER in body and "#145" in body
    assert "HTTP status 400" in story_problem_evidence(issues, "sprint-metrics", 50)


def test_a_guideline_among_the_conflicts_still_needs_a_person(tmp_path):
    mixed = DesignReview(conflicts=CONTRADICTORY.conflicts + OVERRULED.conflicts)
    issues = Labelled(subs=STORIES)
    result = run(tmp_path, issues, Architect(note(), note()), Reviewer(mixed, mixed))
    assert [n for n, _ in result.blocked] == [50] and result.returned == []
    assert (50, "needs:human") in issues.labels


def test_an_epic_waiting_to_be_split_again_gets_no_note(tmp_path):
    waiting = design_epic(labels=frozenset({"needs:design", NEEDS_REWORK}))
    architect = Architect(note())
    result = run(tmp_path, Labelled(subs=STORIES), architect, cards=[waiting])
    assert architect.calls == [] and result.written == []
