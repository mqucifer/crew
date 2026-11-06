"""A criterion states its rule, its example illustrates it, and QA judges the rule (crew#1037).

Criteria arrived as exact strings, and QA held them to the letter: about half of 12
recent QA verdicts and criteria checks were literal readings. The fixtures are two of
them. mqucifer/crew-presentation#296's split was refused because one criterion's line
reads "...after it was escalated" while another bans event-kind names; the same refusal
also held a real conflict, a criterion against the record's row R2. On
mqucifer/sprint-metrics#653 one criterion forbade the substring "no-op" while another
required "not a no-op".

The standard is Specification by Example: a rule, illustrated by examples, as Gherkin's
`Rule:` holds its `Example:`s.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from crew_org.crews import criteria_crew
from crew_org.crews.criteria_crew import CriteriaCheck, CriteriaConflict, check_criteria
from crew_org.crews.refinement_crew import AcceptanceCriterion, Story, StoryProposal
from crew_org.flows import criteria_check, delivered, review
from crew_org.flows.board_flow import render_story_body
from crew_org.session import steps
from tests.test_refine_sessions import main  # noqa: F401
from tests.test_session import said, seen  # noqa: F401
from tests.test_session_git import clone  # noqa: F401


def criterion() -> dict:
    return {
        "rule": "Every story of the sprint is listed",
        "given": "a sprint",
        "when": "its page is built",
        "then": "ten rows",
    }


# --- the form ----------------------------------------------------------------------------


def test_a_criterion_with_no_rule_is_refused():
    with pytest.raises(ValidationError, match="rule"):
        AcceptanceCriterion(given="a sprint", when="its page is built", then="ten rows")


def test_a_blank_rule_is_refused_by_its_rule():
    with pytest.raises(ValidationError) as caught:
        AcceptanceCriterion(rule="  ", given="a sprint", when="its page is built", then="ten rows")
    [error] = caught.value.errors()
    assert error["ctx"]["error"].rule == "form.AcceptanceCriterion.states_its_rule"


def test_the_story_form_refuses_a_criterion_with_no_rule():
    no_rule = {k: v for k, v in criterion().items() if k != "rule"}
    with pytest.raises(ValidationError, match="acceptance_criteria.1.rule"):
        Story(
            title="List the sprint's stories",
            as_a="a visitor",
            i_want="every story of the sprint",
            so_that="I see what got done",
            acceptance_criteria=[criterion(), no_rule],
            points=2,
        )


def test_a_rule_that_only_names_a_quality_is_refused():
    """The check against vague words stays: a rule can't be "clear" either."""
    with pytest.raises(ValidationError, match="'clear' names a quality"):
        AcceptanceCriterion(rule="the summary is clear", given="g", when="w", then="3 lines")


def test_the_then_is_what_the_user_sees_and_exact_only_when_the_words_are_the_requirement():
    then = AcceptanceCriterion.model_fields["then"].description or ""
    rule = AcceptanceCriterion.model_fields["rule"].description or ""
    assert "what the user sees or gets" in then.lower()
    assert "exact string" in then and "approved design" in then
    assert "example" in rule.lower()


# --- every reader gets the rule beside its example ---------------------------------------

RULED = [
    AcceptanceCriterion(
        rule="Every story of the sprint is listed under its epic, whatever its status",
        given="a sprint of ten stories, five of them done",
        when="the sprint page is built",
        then="ten rows appear, under their epics",
    ),
    AcceptanceCriterion(
        rule="A story outside the sprint isn't listed",
        given="a story with no sprint",
        when="the sprint page is built",
        then="it has no row",
    ),
]
STORY = Story(
    title="Group the sprint's stories under their epics",
    as_a="a visitor",
    i_want="the sprint's stories under their epics",
    so_that="I see what got done",
    acceptance_criteria=RULED,
    points=3,
)


def test_the_story_issue_states_each_rule_above_its_example():
    body = render_story_body(STORY, 296, "Show the sprint's stories")
    assert (
        "1. **Rule** Every story of the sprint is listed under its epic, whatever its status\n"
        "   **Given** a sprint of ten stories, five of them done\n"
        "   **When** the sprint page is built\n"
        "   **Then** ten rows appear, under their epics\n"
    ) in body


class Issues:
    def __init__(self, body: str) -> None:
        self.body = body

    def get(self, repo, number):
        return {"number": number, "title": "t", "body": self.body}


def test_the_review_and_qa_read_the_rule_with_its_example():
    """The Developer's start and QA's verify read the issue whole; the review its criteria
    section, which holds each rule with its Given/When/Then."""
    from crew_org.tools.github_project import Card

    body = render_story_body(STORY, 296, "Show the sprint's stories")
    card = Card(item_id="i", number=7, title="t", status="In Review", repo="r")
    shown = review.story_criteria(Issues(body), card, "r")
    assert "**Rule** A story outside the sprint isn't listed" in shown
    assert "**Given** a story with no sprint" in shown and "**Then** it has no row" in shown


def test_what_was_delivered_still_reads_each_examples_action_and_outcome():
    body = render_story_body(STORY, 296, "Show the sprint's stories")
    found = delivered._WHEN_THEN.findall(body)
    assert found[1] == ("the sprint page is built", "it has no row")


def test_the_criteria_check_is_shown_each_rule_with_its_example():
    shown = criteria_check.render_stories([STORY])
    assert (
        "2. Rule: A story outside the sprint isn't listed\n"
        "   Example: Given a story with no sprint / When the sprint page is built / "
        "Then it has no row"
    ) in shown


# --- QA judges the rule --------------------------------------------------------------------

# mqucifer/crew-presentation#296, as its split stood at the story problem of
# 2026-11-05T20:08Z, each criterion now with the rule its example illustrates.
R2 = (
    "R2: The story list is `roster.completed_stories`; the grouping under epics is the "
    "crew's record, `cards[].parent` and `repo`."
)
EVERY_STORY = "Every story of the sprint appears as a row under its epic, whatever its status"
PLAIN_WORDS = "Each story's line says in plain words what happened to it"
NO_CODES = "No badge or line shows an event kind's code, such as `attempt` or `pull_merged`"
GROUP = Story(
    title="Group the sprint's stories under their epics on the sprint page",
    as_a="a visitor",
    i_want="the sprint's stories under their epics",
    so_that="I see what got done",
    acceptance_criteria=[
        AcceptanceCriterion(
            rule=EVERY_STORY,
            given="the 3.0.0 mock fixture, its roster naming five of the sprint's ten stories",
            when="the sprint-6 page is built",
            then="ten rows appear, grouped under their epics (R12)",
        ),
        AcceptanceCriterion(
            rule="A story outside the sprint has no row",
            given="the sprint-5 stories #110 to #112 in the same package",
            when="the sprint-6 page is built",
            then="none of them has a row",
        ),
    ],
    points=3,
    follows=["R2", "R12"],
)
STATUS = Story(
    title="Show each story's status and what happened to it",
    as_a="a visitor",
    i_want="each story's status and what happened to it",
    so_that="I understand how each story went",
    acceptance_criteria=[
        AcceptanceCriterion(
            rule=PLAIN_WORDS,
            given="#133, missed twice and landed after an escalation",
            when="the sprint-6 page is built",
            then="its line reads 'Missed twice, then landed after it was escalated.'",
        ),
        AcceptanceCriterion(
            rule=NO_CODES,
            given="any story of the sprint",
            when="the sprint-6 page is built",
            then="no badge or line contains `attempt` or `pull_merged`",
        ),
    ],
    points=2,
)
SPLIT_296 = StoryProposal(epic_title="Show the sprint's stories", stories=[GROUP, STATUS])

# The two conflicts QA reported, the criterion each quoted as it quoted it then: the
# record one by the rule its criterion states, the "escalated" one by an example's words.
RECORD_CONFLICT = {
    "story": GROUP.title,
    "criterion": f"Criterion 1's rule: '{EVERY_STORY}'",
    "against": f"the epic's record, {R2}",
    "why": "R2's list is the roster's five completed stories; the rule lists all ten",
}
ESCALATED = {
    "story": STATUS.title,
    "criterion": "Criterion 3: '#133 \"Missed twice, then landed after it was escalated.\"'",
    "against": "Criterion 4 (same story): 'no event kind names (attempt, pull_merged)'",
    "why": "'escalated' is an event kind name in the package's events schema",
}

# mqucifer/sprint-metrics#653's pair: its criterion 2 required "not a no-op", and its
# criterion 6 forbade the substring "no-op".
FILLS_IN = "A re-send fills in the stored row's empty fields, and is safe to repeat"
NOT_THE_OLD = "The fill-in passage doesn't describe the old skip-on-conflict behaviour"
DOCS = Story(
    title="Document the re-send fill-in and protection contract",
    as_a="a client integrating with the service",
    i_want="the re-send contract stated in docs/service.md",
    so_that="I can replay events safely",
    acceptance_criteria=[
        AcceptanceCriterion(
            rule=FILLS_IN,
            given="docs/service.md's hand-written section",
            when="a client reads how POST /events treats a re-send",
            then="it reads that the result is a partial fill, not a full replacement "
            "and not a no-op",
        ),
        AcceptanceCriterion(
            rule=NOT_THE_OLD,
            given="the fill-in paragraph",
            when="a test searches it",
            then="none of 'discarded', 'ignored', 'skipped', 'DO NOTHING' or 'no-op' appear",
        ),
    ],
    points=2,
)
NO_OP = {
    "story": DOCS.title,
    "criterion": "Criterion 6: none of 'discarded', 'ignored', 'skipped', 'DO NOTHING' "
    "or 'no-op' appear",
    "against": "Criterion 2: 'not a full replacement and not a no-op'",
    "why": "'not a no-op' contains the substring 'no-op'",
}


def test_the_check_is_told_two_criteria_conflict_when_their_rules_cant_both_hold():
    step = criteria_crew.opening(stories="### A story")["step"]
    assert "their rules can't both hold" in step
    assert "an example's wording" in step


@pytest.mark.parametrize(
    ("story", "conflict"),
    [(STATUS, ESCALATED), (DOCS, NO_OP)],
    ids=["crew-presentation#296 escalated", "sprint-metrics#653 no-op"],
)
def test_a_conflict_that_quotes_an_example_not_a_rule_is_refused(story, conflict):
    rules = [ac.rule for ac in story.acceptance_criteria]
    with pytest.raises(ValueError, match="quotes no rule") as caught:
        criteria_crew.between_rules(CriteriaCheck(conflicts=[conflict]), rules)
    assert caught.value.rule == "check.criteria_crew.between_rules"
    assert rules[0] in str(caught.value)


def test_the_record_conflict_quotes_its_rule_and_stands():
    rules = [ac.rule for ac in GROUP.acceptance_criteria]
    criteria_crew.between_rules(CriteriaCheck(conflicts=[RECORD_CONFLICT]), rules)


@pytest.mark.parametrize(
    "quoted",
    [
        "'every story of the sprint appears as a row'",
        "Criterion 1's rule, in part: 'Every story of the sprint appears'",
        "“Every story of the sprint appears as a row under its epic, whatever its status.”",
    ],
)
def test_a_rule_quoted_in_part_or_in_other_quotes_is_a_rule_quoted(quoted):
    conflict = {**RECORD_CONFLICT, "criterion": quoted}
    criteria_crew.between_rules(CriteriaCheck(conflicts=[conflict]), [EVERY_STORY])


def test_a_story_without_rules_is_never_refused():
    criteria_crew.between_rules(CriteriaCheck(conflicts=[ESCALATED]), [])


class QA:
    """The model, as a fake: its first `done` is the conflicts QA reported on #296, and
    after a refusal it answers again with only those the refusal didn't name."""

    def __init__(self, first: list[dict]) -> None:
        self.first = first
        self.shown: list[list[dict]] = []

    def __call__(self, body):
        messages = body["messages"]
        self.shown.append(messages)
        opening = messages[1]["content"]
        mine = opening.partition("## The story under check")[2]
        own = [c for c in self.first if c["story"] in mine]
        refused = [m["content"] for m in messages if m.get("role") == "tool"]
        if refused:
            own = [
                c for i, c in enumerate(own, 1) if not re.search(rf"\bconflict {i}\b", refused[-1])
            ]
        return said(("done", {"conflicts": own}))


def test_the_296_check_keeps_the_record_conflict_and_drops_the_escalated_one(main: Path, seen):  # noqa: F811
    model = QA([RECORD_CONFLICT, ESCALATED])

    def check(**shown):
        return check_criteria(**shown, send=model)

    found = criteria_check.check_each(
        check, SPLIT_296, conclusion=R2, reading=steps.Reading.at(main, "r")
    )
    assert [c.model_dump() for c in found.conflicts] == [
        CriteriaConflict(**RECORD_CONFLICT).model_dump()
    ]
    refusals = [m["content"] for shown in model.shown for m in shown if m.get("role") == "tool"]
    assert len(refusals) == 1 and "quotes no rule" in refusals[0]
    assert PLAIN_WORDS in refusals[0] and NO_CODES in refusals[0]


def test_the_653_no_op_pair_is_not_a_conflict(main: Path, seen):  # noqa: F811
    model = QA([NO_OP])

    def check(**shown):
        return check_criteria(**shown, send=model)

    proposal = StoryProposal(epic_title="Document the re-send", stories=[DOCS])
    found = criteria_check.check_each(check, proposal, reading=steps.Reading.at(main, "r"))
    assert found.conflicts == []
