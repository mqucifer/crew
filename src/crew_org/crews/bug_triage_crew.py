"""A filed bug, triaged: accepted with criteria and an estimate, or not, with why (crew#407).

A collaborator files a bug (ADR 0027): what happens, what was expected, the
evidence. The Business Analyst reads it against the code and decides one of
IEEE 1044's dispositions, in plain words:
- accepted: it contradicts what was accepted, so it's written as criteria that
  reproduce it, Given/When/Then with the expected behaviour as the Then, and
  sized as a bug is, 1 to 5 points;
- not a bug: it works as documented, or it asks for new behaviour, which is a Goal;
- a duplicate of another issue;
- more information needed: one question for whoever filed it.

The Business Analyst, not the Product Owner: accepting a bug is writing its
criteria and sizing it, which is the analyst's craft (agents.yaml).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from crew_org import calls
from crew_org.crews.refinement_crew import AcceptanceCriterion
from crew_org.rulebook import refuse
from crew_org.tools.repo_context import READ_MIN_CHARS, READING_CHAR_CEILING
from crew_org.tools.repo_read import Reader

# The taxonomy sizes a bug 1 to 5 points (ways-of-working, "Work item taxonomy").
BUG_POINTS = (1, 2, 3, 5)

Outcome = Literal["accepted", "not_a_bug", "duplicate", "more_information"]


class Triage(BaseModel):
    outcome: Outcome = Field(
        description=(
            "accepted: the behaviour contradicts what the project accepted (its criteria, "
            "its docs, its release notes); not_a_bug: it works as documented, or the report "
            "asks for new behaviour, which is a Goal; duplicate: another issue already "
            "reports it; more_information: the report doesn't say enough to tell"
        )
    )
    reason: str = Field(
        description="Why, from what the code or docs show: the file, and what it says or does"
    )
    acceptance_criteria: list[AcceptanceCriterion] = Field(
        description=(
            "accepted only: criteria that reproduce the bug, the expected behaviour as the "
            "Then, so a test written from one fails today and passes once it's fixed. At "
            "least one; empty for any other outcome"
        )
    )
    points: int | None = Field(description=f"accepted only: one of {BUG_POINTS}; else null")
    duplicate_of: int | None = Field(
        description="duplicate only: the number of the issue that already reports it; else null"
    )
    question: str = Field(
        description="more_information only: the one question whoever filed it should answer; "
        "else empty"
    )

    @model_validator(mode="after")
    def _fits_its_outcome(self) -> Triage:
        if self.outcome == "accepted":
            if not self.acceptance_criteria:
                refuse("form.Triage.fits_its_outcome", "no_criteria")
            if self.points not in BUG_POINTS:
                refuse("form.Triage.fits_its_outcome", "size", sizes=BUG_POINTS)
        elif self.acceptance_criteria:
            refuse("form.Triage.fits_its_outcome", "criteria", outcome=self.outcome)
        if self.outcome == "duplicate" and not self.duplicate_of:
            refuse("form.Triage.fits_its_outcome", "duplicate")
        if self.outcome == "more_information" and not self.question.strip():
            refuse("form.Triage.fits_its_outcome", "question")
        return self


def triage_bug(
    title: str,
    report: str,
    *,
    number: int,
    repository: str,
    reader: Reader | None = None,
) -> Triage:
    """The Business Analyst's triage of one filed bug, read against the repository."""
    shown = calls.Part(
        "task",
        (f"## The repository as it stands\n\n{repository}\n\n" if repository else "")
        + f"## The bug filed, #{number}: {title}\n\n{report}\n\n"
        "Triage it. Read the code and docs it names before deciding: a report can be "
        "wrong about the cause and right about the behaviour. A bug contradicts what "
        "the project already accepted. A request for behaviour the project never "
        "promised is not a bug, however reasonable: say it's a Goal. When accepted, "
        "write criteria a test can be written from directly, that fail against the code "
        "as it is now.",
    )
    room = max(READING_CHAR_CEILING - len(shown.text), 0)
    if reader is not None:
        reader.reset(room)
    if room < READ_MIN_CHARS:
        reader = None
    return calls.ask(
        "business_analyst",
        [shown],
        Triage,
        step="bug_triage",
        expected="The bug's triage: its outcome, why, and what the outcome needs.",
        tools=[reader.tool()] if reader is not None else None,
    )


TaskOutcome = Literal["accepted", "not_a_task", "duplicate"]


class TaskTriage(BaseModel):
    """A role's Task, filed from its finding outside its story (ADR 0034), triaged as a
    bug is: accepted with what done means and a size, or closed with why."""

    outcome: TaskOutcome = Field(
        description=(
            "accepted: the work is real and the named role's to do; not_a_task: the code "
            "or docs show it's already done or wrong, or it asks for new behaviour for the "
            "project's users, which is a Goal; duplicate: another issue already has it"
        )
    )
    reason: str = Field(
        description="Why, from what the code or docs show: the file, and what it says or does"
    )
    done_when: list[str] = Field(
        description=(
            "accepted only: what is true once it's done, each a check its role can show "
            "passing (a CI job runs the tests, a file says so). At least one; empty for "
            "any other outcome"
        )
    )
    points: int | None = Field(description=f"accepted only: one of {BUG_POINTS}; else null")
    duplicate_of: int | None = Field(
        description="duplicate only: the number of the issue that already has it; else null"
    )

    @model_validator(mode="after")
    def _fits_its_outcome(self) -> TaskTriage:
        if self.outcome == "accepted":
            if not [d for d in self.done_when if d.strip()]:
                refuse("form.TaskTriage.fits_its_outcome", "no_done_when")
            if self.points not in BUG_POINTS:
                refuse("form.TaskTriage.fits_its_outcome", "size", sizes=BUG_POINTS)
        elif self.done_when:
            refuse("form.TaskTriage.fits_its_outcome", "done_when", outcome=self.outcome)
        if self.outcome == "duplicate" and not self.duplicate_of:
            refuse("form.TaskTriage.fits_its_outcome", "duplicate")
        return self


def triage_task(
    title: str,
    filed: str,
    *,
    number: int,
    role: str,
    repository: str,
    reader: Reader | None = None,
) -> TaskTriage:
    """The Business Analyst's triage of a Task a role filed from its finding."""
    shown = calls.Part(
        "task",
        (f"## The repository as it stands\n\n{repository}\n\n" if repository else "")
        + f"## The Task filed for the {role}, #{number}: {title}\n\n{filed}\n\n"
        "Triage it as you would a bug. A role found this outside the story it was "
        "working on. Read the code and docs it names before deciding: a finding can be "
        "wrong about the cause and right about the gap. A Task is work the role does "
        "itself, inside its own area. Work that would give the project's users new "
        "behaviour is not a Task: say it's a Goal. When accepted, say what is true once "
        "it's done, as checks the role can show passing.",
    )
    room = max(READING_CHAR_CEILING - len(shown.text), 0)
    if reader is not None:
        reader.reset(room)
    if room < READ_MIN_CHARS:
        reader = None
    return calls.ask(
        "business_analyst",
        [shown],
        TaskTriage,
        step="task_triage",
        expected="The Task's triage: its outcome, why, and what the outcome needs.",
        tools=[reader.tool()] if reader is not None else None,
    )
