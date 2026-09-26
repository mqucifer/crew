"""Code review: judging a diff.

The Reviewer judges the *diff* — correctness, reuse, scope — and never asks
"does it work". That is QA's evidence to produce, from the running system. The
two gates answer different questions and collapsing them loses one of them.

A rejection must carry findings. The schema enforces it, because "looks wrong"
is not a review and an author cannot act on it.
"""

from __future__ import annotations

from crewai import Crew, Process, Task
from pydantic import BaseModel, Field, field_validator, model_validator

from crew_org.agents import build_agents

# The whole diff, or no approval. A head slice of 30,000 characters meant the
# Reviewer approved files it had never seen — and its approval now merges, so
# what it cannot see it must not wave through. Past the ceiling the honest
# review is the one a person would give: this is too large to review in one
# pass, split it.
MAX_DIFF_CHARS = 200_000
MIN_ACTION_CHARS = 15


class Finding(BaseModel):
    file: str = Field(description="The file the finding is in")
    concern: str = Field(description="What is wrong, stated as a fact about the diff")
    action: str = Field(description="What to do about it, specifically")
    blocking: bool = Field(
        default=True,
        description=(
            "True when the change can't merge until this is done. False for a note worth "
            "having that doesn't hold it up"
        ),
    )

    @field_validator("action")
    @classmethod
    def _is_actionable(cls, value: str) -> str:
        if len(value.strip()) < MIN_ACTION_CHARS:
            raise ValueError(
                "say specifically what to change. A finding an author cannot act on "
                "is not a finding."
            )
        return value


class ReviewVerdict(BaseModel):
    summary: str = Field(description="One paragraph: what this change does and whether it holds")
    approve: bool = Field(description="True to approve, False to request changes")
    findings: list[Finding] = Field(
        default_factory=list, description="Specific, actionable findings"
    )

    @model_validator(mode="after")
    def _a_rejection_must_say_why(self) -> ReviewVerdict:
        blocking = [f for f in self.findings if f.blocking]
        if not self.approve and not blocking:
            raise ValueError(
                "requesting changes with no finding that blocks is not a review. Name "
                "what must change and what to do, or approve."
            )
        # sprint-metrics PR #139 was approved with five findings saying "remove
        # these imports", and nothing acted on them: an approval's findings were
        # dropped. Here they were wrong; next time they'd be right (#214).
        if self.approve and blocking:
            raise ValueError(
                "an approval can't carry a finding that blocks. Request changes, or mark "
                "it `blocking: false` if it's a note that doesn't hold the merge up."
            )
        return self

    @property
    def notes(self) -> list[Finding]:
        """Findings that don't hold the merge up."""
        return [f for f in self.findings if not f.blocking]

    @property
    def event(self) -> str:
        return "APPROVE" if self.approve else "REQUEST_CHANGES"


def review_diff(
    title: str,
    diff: str,
    *,
    acceptance_criteria: str = "",
    prior_verdicts: str = "",
    checks: str = "",
    imported: str = "",
    design_note: str = "",
) -> ReviewVerdict:
    """Review one pull request's diff.

    `prior_verdicts` is what the Reviewer said about earlier heads of this same
    pull request. Judging every push cold is how a diff returned for one
    finding comes back rejected for another the first review never raised, and
    the author cannot converge on a target that moves.

    Deliberately the Reviewer's own reviews and not QA's verdicts. QA judges
    the behaviour and this gate judges the diff; handing this one QA's unproven
    criteria invites it to start asking whether the code works, which is the
    one question §1 says it must never ask.

    `checks` and `imported` are the evidence a diff can't carry (#160): what CI
    and delivery's own run reported, and the code the diff imports as it is on
    the base branch. Without them a diff of tests alone looks like tests that
    must fail, even when the code they exercise has already landed.
    """
    if len(diff) > MAX_DIFF_CHARS:
        return ReviewVerdict(
            summary=(
                f"This diff is {len(diff):,} characters, beyond the "
                f"{MAX_DIFF_CHARS:,} a single review pass can hold. Reviewing part of "
                "it and approving the whole is not a review."
            ),
            approve=False,
            findings=[
                Finding(
                    file="(whole change)",
                    concern="The change is too large to be reviewed in one pass.",
                    action=(
                        "Split it into pull requests that can each be read end to end, "
                        "one story's worth of change per pull request."
                    ),
                )
            ],
        )

    criteria = (
        f"\n\n## Acceptance criteria this must satisfy\n\n{acceptance_criteria}"
        if acceptance_criteria
        else ""
    )
    previously = (
        "\n\n## What you asked for on an earlier push to this pull request\n\n"
        f"{prior_verdicts}\n\n"
        "These are your own earlier reviews. Check first whether this diff answers "
        "them. Do not re-litigate a point you settled, and do not raise a new one "
        "late unless this diff introduced it — a finding that could have been made "
        "the first time costs another delivery cycle.\n"
        if prior_verdicts
        else ""
    )
    agents = build_agents("code_reviewer")
    task = Task(
        description=(
            f"Review this pull request.\n\n## Title\n\n{title}{criteria}{previously}\n\n"
            f"## Diff\n\n```diff\n{diff}\n```\n\n"
            + (f"{checks}\n\n" if checks else "")
            + (f"{imported}\n\n" if imported else "")
            + (
                "## The Architect's design note for this story's epic\n\n"
                f"{design_note}\n\nJudge the diff against this approach as well (#155).\n\n"
                if design_note
                else ""
            )
            + "Review for correctness first, then reuse and simplification. Name the file "
            "for every finding and say what to do about it. Reject scope creep: a diff "
            "doing more than its change is not ready, however good the extra is.\n"
            "Do not ask whether it works at runtime — that is QA's evidence to produce. "
            "Judge the diff.\n"
            "If it is sound, approve it. Approving good work quickly matters as much as "
            "catching bad work."
        ),
        expected_output="A verdict with a summary and any findings.",
        agent=agents["code_reviewer"],
        output_pydantic=ReviewVerdict,
    )
    crew = Crew(
        agents=list(agents.values()), tasks=[task], process=Process.sequential, verbose=False
    )
    return crew.kickoff().pydantic
