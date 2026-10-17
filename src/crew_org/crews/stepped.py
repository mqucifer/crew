"""A second attempt in steps: a plan, then one small answer per file (#276, option B).

A one-shot answer can be too big to finish. sprint-metrics#268 asked for one
structured answer that rewrote the README, moved about 20 tests with exact
strings and deleted a file; the model stopped mid-thought 3 times in 5 even on a
focused 26k-token prompt (#312, runbook `docs/runbooks/empty-model-answers.md`).
When a first attempt produces no usable answer, the next one plans the work and
then answers one file at a time, each answer small. The pieces are merged into
one ordinary answer, which the guards, the checks, review and QA judge exactly as
before: nothing downstream knows it was built in steps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from crew_org import calls
from crew_org.crews.delivery_crew import (
    CODE_FIELDS,
    STANDING_INSTRUCTIONS,
    FileWrite,
    Implementation,
)
from crew_org.rulebook import refuse, says
from crew_org.tools import regression

# A plan is for splitting one answer that's too big; past this, the story is.
MAX_FILES = 8


class PlannedFile(BaseModel):
    path: str = Field(description="Repository-relative path")
    kind: Literal["new", "edit", "delete"] = Field(
        description="new: a file that doesn't exist yet; edit: change an existing file; "
        "delete: remove an existing file"
    )
    intent: str = Field(description="What changes in this file, in a sentence or two")

    @field_validator("path")
    @classmethod
    def _in_the_repository(cls, value: str) -> str:
        return FileWrite._stays_in_the_repository(value)

    @field_validator("intent")
    @classmethod
    def _says_what(cls, value: str) -> str:
        if len(value.strip()) < 10:
            refuse("form.PlannedFile.says_what")
        return value.strip()


class CriterionPlan(BaseModel):
    criterion: str = Field(description="The acceptance criterion, briefly")
    test: str = Field(description="The test that will prove it, as path::test_name")


class Plan(BaseModel):
    summary: str = Field(description="The change as a whole, in two or three sentences")
    files: list[PlannedFile] = Field(
        description=(
            f"Every file the change touches, in the order to write them (at most {MAX_FILES})"
        )
    )
    contracts: list[str] = Field(
        default_factory=list,
        description=(
            "New names and signatures one file introduces and another relies on, e.g. "
            "'report.py: format_markdown(cards, *, prior=None) -> str'"
        ),
    )
    criteria: list[CriterionPlan] = Field(
        default_factory=list, description="For each acceptance criterion, the test that proves it"
    )

    @model_validator(mode="after")
    def _one_step_per_file(self) -> Plan:
        if not self.files:
            refuse("form.Plan.one_step_per_file", "no_files")
        if len(self.files) > MAX_FILES:
            refuse("form.Plan.one_step_per_file", "too_many", limit=MAX_FILES)
        paths = [f.path for f in self.files]
        if len(set(paths)) != len(paths):
            refuse("form.Plan.one_step_per_file", "twice")
        planned = set(paths)
        for c in self.criteria:
            path = c.test.split("::", 1)[0]
            if path not in planned:
                refuse("form.Plan.one_step_per_file", "unplanned_test", test=c.test, path=path)
        return self


def check_plan(plan: Plan, worktree: Path) -> list[str]:
    """What's wrong with a plan against the repository as it is. Empty when it's sound."""
    problems: list[str] = []
    for step in plan.files:
        exists = (worktree / step.path).is_file()
        # A placeholder is replaced whole, so either kind is right for it (crew#690).
        if step.kind == "new" and exists and not regression.is_placeholder(worktree / step.path):
            problems.append(says("check.delivery.deliver_in_steps", "exists", path=step.path))
        if step.kind in ("edit", "delete") and not exists:
            problems.append(says("check.delivery.deliver_in_steps", "missing", path=step.path))
    return problems


def render_plan(plan: Plan) -> str:
    lines = ["## The plan", "", plan.summary, "", "Files, in order:"]
    lines += [f"{i}. `{f.path}` ({f.kind}): {f.intent}" for i, f in enumerate(plan.files, 1)]
    if plan.contracts:
        lines += ["", "Names and signatures the files share:"]
        lines += [f"- {c}" for c in plan.contracts]
    if plan.criteria:
        lines += ["", "Which test proves each criterion:"]
        lines += [f"- {c.criterion}: `{c.test}`" for c in plan.criteria]
    return "\n".join(lines)


PLAN_INSTRUCTIONS = (
    "Plan this story before writing any of it. An earlier attempt at it didn't produce a "
    "usable answer, so it will be written one file at a time, each file its own small "
    "answer, from this plan.\n\n"
    "Name every file the change touches, in the order to write them: a file others rely "
    "on first, and a test with the code it tests. Say what changes in each. List the names "
    "and signatures one file introduces and another relies on, so the separate answers "
    "agree. For each acceptance criterion, name the test that proves it, in a planned file.\n\n"
    "Keep it short: the plan is the map, not the code."
)

STEP_INSTRUCTIONS = (
    "Write one file's part of this story, following the plan above. Change only the file "
    "named below: other files are other steps, some already written (shown below) and some "
    "still to come. Use exactly the names and signatures the plan lists. The plan already "
    "says which test proves each criterion: list `criteria_tests` only for tests you write "
    "in this file.\n"
)


class StepAnswer(Implementation):
    """One file's part of a planned change. Refused if it reaches into another file."""

    def _may_change_nothing(self) -> bool:
        # A delete step's whole answer is `deleted_files`; a file the plan already
        # got right may need nothing.
        return True

    def touched(self) -> set[str]:
        return (
            {f.path for f in self.new_files}
            | {e.path for e in self.edits}
            | {t.path for t in self.text_edits}
            | set(self.deleted_files)
            | {c.path for c in self.criteria_tests}
            | {r.path for r in self.retired_tests}
        )


def _run(description: str, output: type[BaseModel]) -> Any:
    """One step's call, on the crew's own call layer (ADR 0025)."""
    return calls.ask(
        "developer",
        [calls.Part("task", description)],
        output,
        expected="The structured answer the schema describes.",
        blocks=CODE_FIELDS,
    )


def plan_story(story: str, *, context: str, why: str = "") -> Plan:
    """The plan: ordered stable-first like every Developer prompt, for the prefix cache."""
    return _run(
        STANDING_INSTRUCTIONS
        + f"\n\n## The story\n\n{story}\n"
        + f"\n## The repository as it stands\n\n{context}\n"
        + (f"\n## What happened to the last attempt\n\n{why}\n" if why else "")
        + f"\n## Now\n\n{PLAN_INSTRUCTIONS}",
        Plan,
    )


def implement_file(
    story: str,
    *,
    plan: Plan,
    step: int,
    context: str,
    earlier: str,
    written: frozenset[tuple[str, str]] = frozenset(),
) -> StepAnswer:
    """One file's answer. The story and the plan come first, identical for every step."""
    target = plan.files[step]
    answer: StepAnswer = _run(
        STANDING_INSTRUCTIONS
        + f"\n\n## The story\n\n{story}\n\n{render_plan(plan)}\n"
        + f"\n## The repository as it stands\n\n{context}\n"
        + (f"\n## Already written in earlier steps\n\n{earlier}\n" if earlier else "")
        + f"\n## Now\n\n{STEP_INSTRUCTIONS}\n"
        + f"This step: `{target.path}` ({target.kind}): {target.intent}",
        StepAnswer,
    )
    # A test an earlier step wrote, cited again as a criterion's proof, is a
    # repeat of evidence, not a change to that file: sprint-metrics#268's delete
    # step listed three of step 2's tests. Anything else in another file is.
    answer.criteria_tests = [
        c for c in answer.criteria_tests if c.path == target.path or (c.path, c.test) not in written
    ]
    stray = answer.touched() - {target.path}
    if stray:
        refuse(
            "check.stepped.implement_file",
            step=step + 1,
            path=target.path,
            others=", ".join(f"`{p}`" for p in sorted(stray)),
        )
    return answer


def written_tests(answers: list[StepAnswer]) -> frozenset[tuple[str, str]]:
    """(path, test) for every test the answers so far write: edits, criterion tests, new files."""
    import re  # noqa: PLC0415

    found = {(e.path, e.target) for a in answers for e in a.edits}
    found |= {(c.path, c.test) for a in answers for c in a.criteria_tests}
    for a in answers:
        for f in a.new_files:
            found |= {(f.path, name) for name in re.findall(r"^def (test_\w+)", f.content, re.M)}
    return frozenset(found)


def describe(answer: StepAnswer) -> str:
    """An earlier step's answer, for the steps after it: what it wrote, in full."""
    parts: list[str] = []
    for f in answer.new_files:
        parts += [f"`{f.path}` (new)", "```", f.content.strip(), "```"]
    for e in answer.edits:
        parts += [f"`{e.path}`: {e.operation} `{e.target}`", "```python", e.source.strip(), "```"]
    for t in answer.text_edits:
        parts += [
            f"`{t.path}`: replaced",
            "```",
            t.find.strip(),
            "```",
            "with",
            "```",
            t.replace.strip(),
            "```",
        ]
    for c in answer.criteria_tests:
        parts += [f"`{c.path}`: test `{c.test}`", "```python", c.source.strip(), "```"]
    parts += [f"`{p}` (deleted)" for p in answer.deleted_files]
    return "\n".join(parts)


def merge(plan: Plan, answers: list[StepAnswer]) -> Implementation:
    """The pieces as one ordinary answer, which the gates judge as they always have."""
    return Implementation(
        summary=plan.summary,
        criteria_tests=[c for a in answers for c in a.criteria_tests],
        new_files=[f for a in answers for f in a.new_files],
        edits=[e for a in answers for e in a.edits],
        text_edits=[t for a in answers for t in a.text_edits],
        moves=[m for a in answers for m in a.moves],
        deleted_files=[p for a in answers for p in a.deleted_files],
        retired_tests=[r for a in answers for r in a.retired_tests],
    )
