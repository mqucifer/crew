"""QA: does the running code satisfy the acceptance criteria?

The Reviewer judges the diff. QA judges behaviour, and judges it criterion by
criterion — because "the suite passes" is not the same claim as "every
criterion is proven". A test that exists but exercises something adjacent is
the failure this role is here to catch.

Acceptance is all-or-nothing by construction: a verdict cannot accept a story
while any criterion is unproven.
"""

from __future__ import annotations

from crewai import Crew, Process, Task
from pydantic import BaseModel, Field, field_validator, model_validator

from crew_org.agents import build_agents

MIN_EVIDENCE_CHARS = 20


class CriterionVerdict(BaseModel):
    criterion: str = Field(description="The acceptance criterion, quoted")
    proven: bool = Field(
        description=(
            "Is this criterion proven by a test that actually exercises it? A criterion "
            "about what a user doc says is proven by the doc itself, when it says it "
            "and says it correctly"
        )
    )
    evidence: str = Field(
        description=(
            "Which test proves it, or what is missing. Name the test function; for a "
            "doc criterion, quote the passage and name the file"
        )
    )

    @field_validator("evidence")
    @classmethod
    def _is_specific(cls, value: str) -> str:
        if len(value.strip()) < MIN_EVIDENCE_CHARS:
            raise ValueError(
                "name the test that proves this criterion, or say specifically what is "
                "missing. 'Tested' is not evidence."
            )
        return value


class QAVerdict(BaseModel):
    summary: str = Field(description="What was verified and what the result was")
    accepted: bool = Field(description="True only if every criterion is proven")
    criteria: list[CriterionVerdict] = Field(
        default_factory=list, description="One verdict per criterion"
    )
    # Asking to see more, as the Developer can (#231): a test file listed by
    # name only, needed in full to judge a criterion. An answer that asks is
    # not a verdict; QA is asked again with the files shown.
    need_files: list[str] = Field(
        default_factory=list,
        description=(
            "Test files listed by name only that you need in full to judge a criterion. "
            "Only when judging needs them; then leave `criteria` empty"
        ),
    )

    @model_validator(mode="after")
    def _a_verdict_or_an_ask(self) -> QAVerdict:
        if not self.criteria and not self.need_files:
            raise ValueError("a story is accepted against its criteria; list them")
        return self

    @model_validator(mode="after")
    def _acceptance_requires_every_criterion(self) -> QAVerdict:
        unproven = [c.criterion for c in self.criteria if not c.proven]
        if self.accepted and unproven:
            raise ValueError(
                f"cannot accept with {len(unproven)} unproven criteria. "
                "Definition of Done requires every criterion proven: by a test, or a "
                "doc criterion by the doc."
            )
        return self

    @property
    def unproven(self) -> list[CriterionVerdict]:
        return [c for c in self.criteria if not c.proven]


def verify_story(
    story: str,
    *,
    test_output: str,
    test_code: str,
    prior_verdicts: str = "",
    project: str = "",
    docs: str = "",
    checks: str = "",
    can_ask: bool = False,
) -> QAVerdict:
    """Judge an implementation against its acceptance criteria.

    `prior_verdicts` is what QA itself said about this card on earlier
    attempts. Without it every attempt was judged cold, so a story returned for
    one unproven criterion could come back for a different one that was never
    mentioned — a moving target, and a delivery cycle spent each time it moved.

    `project` is the project's record, as `project.brief` writes it (#131): what
    done means in this project, and what agents must not touch, beside the story.
    """
    agents = build_agents("qa_engineer")
    # Stable-first for the prefix cache: the story is fixed for this card, the
    # trail is fixed for this attempt, the tests and their output change every
    # time.
    previously = (
        "\n\n## What you said about this card before\n\n"
        f"{prior_verdicts}\n\n"
        "Your own earlier verdicts. Judge this attempt consistently with them: a "
        "criterion you proved before stays proven unless the code that proved it "
        "changed. If you return this card again, return it for a reason already "
        "named above, or say plainly why a new one has appeared.\n"
        if prior_verdicts
        else ""
    )
    task = Task(
        description=(
            (f"{project}\n\n" if project else "")
            + f"Verify this story against its acceptance criteria.\n\n{story}{previously}\n\n"
            f"## The tests that were written\n\n```python\n{test_code}\n```\n\n"
            f"## What running the suite produced\n\n```\n{test_output}\n```\n\n"
            + (
                f"{checks}\n\nA criterion only CI can run (an image build, a workflow) "
                "is proven by naming the passing check above **that runs it**. A check "
                "proves only what its steps execute: a `tests` check that runs lint and "
                "pytest proves nothing about a workflow it never runs, and a test the run "
                "skipped proves nothing at all. A check that failed or is still running "
                "proves nothing yet. If no check runs it, the criterion is unproven.\n\n"
                if checks
                else ""
            )
            + (
                f"## The docs this change edits, as they now read\n\n{docs}\n\n"
                "A criterion about what a doc says is proven by the doc, not a test "
                "(Definition of Done §7.1). Read it: quote the passage that meets the "
                "criterion as your evidence, and hold it unproven if the doc doesn't say "
                "it or says something the code doesn't do.\n\n"
                if docs
                else ""
            )
            + (
                "Test files listed by name only weren't shown in full. If judging a "
                "criterion needs one, name it in `need_files` (and nothing else) and you'll "
                "be asked again with it shown. Don't cite a test you haven't read.\n"
                if can_ask
                else ""
            )
            + "For each acceptance criterion, decide whether a test actually exercises it "
            "and name that test. A passing suite is not the same claim as a proven "
            "criterion — a test that exists but checks something adjacent proves nothing. "
            "Report what you found, including what went badly. Do not soften a failure."
        ),
        expected_output="A verdict per acceptance criterion, with evidence.",
        agent=agents["qa_engineer"],
        output_pydantic=QAVerdict,
    )
    crew = Crew(
        agents=list(agents.values()), tasks=[task], process=Process.sequential, verbose=False
    )
    return crew.kickoff().pydantic
