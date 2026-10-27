"""The Goal's look: the Architect and the UX Designer read all of a Goal's epics once (crew#858).

The per-epic panel left the seams between a Goal's epics to nobody (a problem in a
sibling "belongs to that epic's review"), repeated its notes per sibling, and cost a
4-epic Goal 16 sessions. The look is one read across every approved epic of the Goal,
before any is split, by the two roles whose questions span them (ADR 0035):

- **The Architect:** what does this touch that's expensive to reverse? What the code
  supports, the decisions it makes, the risks, and the Goal's target C4 diagram.
- **The UX Designer:** is this what the reader needs? The reader, what they meet per
  epic, what's missing for them, and criteria for the stories.

It is the Three Amigos' conversation before the work is split (the business side is
the Product Owner's epics and its settle; the testing side waits for QA's own task,
M10). Each has its own form, so the answers can't repeat each other. They read in
two sessions at once, as the panel's members did, and the Product Owner settles both
into the Goal's record (`goal_settle_crew`). A later look reads only the epics added
since, against the record and diagram the Goal has.
"""

from __future__ import annotations

import contextvars
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, model_validator

from crew_org import mermaid
from crew_org.events import attributed
from crew_org.flows import record as record_flow
from crew_org.rulebook import refuse
from crew_org.session import steps

ALL = record_flow.ALL

# The Goal template's own last line, which is the crew's, not the Sponsor's words.
_GOAL_FOOTER = re.compile(r"\n-{3,}\s*\n\s*_Set by the Product Sponsor\..*?_\s*$", re.S)


def sponsors_words(goal: str) -> str:
    """A Goal's text without its template's footer (crew#807's prompt review)."""
    return _GOAL_FOOTER.sub("", goal).rstrip()


class Supported(BaseModel):
    what: str = Field(description="What the code already provides that the Goal builds on")
    where: str = Field(description="Where you saw it: a path and definition")


class Decided(BaseModel):
    """A decision across the Goal's epics, made by the Architect before any is split."""

    context: str = Field(description="What it decides, in a few words")
    decision: str = Field(description="What you decided, named so the stories can build on it")
    weighed: str = Field(description="The options you weighed, and why this one")
    consequences: str = Field(description="What it means for the stories that build on it")
    epics: list[str] = Field(
        description="The epics it binds, as owner/repo#number, or `all` for every epic of the Goal"
    )


class Callout(BaseModel):
    risk: str = Field(description="A risk or a seam between epics, said plainly")
    epics: list[str] = Field(description="The epics it concerns, as owner/repo#number, or `all`")


class BeyondTheGoal(BaseModel):
    """A call you can't make within the Goal's intent: it goes to the Sponsor."""

    question: str = Field(description="The question, as the Sponsor would answer it")
    why: str = Field(description="Why no call within the Goal answers it")


class SystemLook(BaseModel):
    supported: list[Supported] = Field(default_factory=list)
    decided: list[Decided] = Field(default_factory=list)
    callouts: list[Callout] = Field(default_factory=list)
    beyond_the_goal: list[BeyondTheGoal] = Field(default_factory=list)
    diagram: str = Field(
        description=(
            "The Goal's target C4 diagram in Mermaid's C4 syntax (C4Context, C4Container "
            "or C4Component), without the fence: the repository's system as it is, with "
            "the Goal's new parts and connections"
        )
    )
    new: list[str] = Field(
        default_factory=list, description="The aliases of the parts the Goal adds to the diagram"
    )
    sequence: str = Field(
        default="",
        description=(
            "A Mermaid sequence diagram, without the fence, only when the Goal adds an "
            "interaction between parts; empty otherwise"
        ),
    )
    needs: list[str] = Field(
        default_factory=list,
        description=(
            "Each new service or toolchain the Goal needs that the project's workspace "
            "doesn't have, by its kind and what it's for: \"a Postgres for the tests\", "
            '"Node for the site build". Not versions, images or configuration: DevOps\'s '
            "Task works those out. Empty when the Goal needs none"
        ),
    )

    @model_validator(mode="after")
    def _diagram_well_formed(self) -> SystemLook:
        blocks = [self.diagram, *([self.sequence] if self.sequence.strip() else [])]
        found = mermaid.problems(blocks)
        if mermaid.kind(self.diagram) not in mermaid.C4_TYPES:
            first = mermaid.kind(self.diagram)[:40]
            found.append(f"the diagram's first line is `{first}`, not one of the C4 types")
        if len(blocks) > 1 and mermaid.kind(self.sequence) != "sequenceDiagram":
            first = mermaid.kind(self.sequence)[:40]
            found.append(f"the sequence's first line is `{first}`, not `sequenceDiagram`")
        if found:
            refuse("form.SystemLook.diagram_well_formed", problems="; ".join(found))
        return self

    @model_validator(mode="after")
    def _new_parts_drawn(self) -> SystemLook:
        missing = sorted(set(self.new) - mermaid.aliases(self.diagram))
        if missing:
            refuse("form.SystemLook.new_parts_drawn", aliases=", ".join(missing))
        return self


class ReaderView(BaseModel):
    epic: str = Field(description="The epic, as owner/repo#number")
    meets: str = Field(description="What the reader meets once it's delivered")
    missing: str = Field(description="What's missing for the reader, or `nothing`")
    criteria: list[str] = Field(
        default_factory=list,
        description=(
            "What the reader sees, as criteria the stories carry: each something a test "
            "can observe, with the widths it holds at"
        ),
    )


class ReaderLook(BaseModel):
    reader: str = Field(
        description="Who reads the Goal's result, and what they come to see or decide"
    )
    epics: list[ReaderView]


# The epics a look reads: those it reads for, then the ones the record already covers.
APPROVED = "approved"
LOOKED_AT = "looked at"


@dataclass(frozen=True)
class EpicText:
    ref: str
    # APPROVED (being looked at), LOOKED_AT (already in the record), or "delivered".
    state: str
    text: str


@dataclass(frozen=True)
class GoalContext:
    """What both roles are shown: the same for each."""

    goal_ref: str
    goal: str
    decisions: str
    epics: list[EpicText]
    project_log: str = ""
    # For a later look: the Goal's record and target diagram as they are.
    record: str = ""
    diagram: str = ""

    @property
    def looking_at(self) -> list[str]:
        return [e.ref for e in self.epics if e.state == APPROVED]


@dataclass(frozen=True)
class LookResult:
    system: SystemLook | None
    reader: ReaderLook | None
    # A role whose session failed: why. The other's answer still stands.
    failed: dict[str, str] = field(default_factory=dict)


def check_epics(look: SystemLook | ReaderLook, context: GoalContext) -> None:
    """Every epic an answer names is one of the Goal's; the UX Designer's covers each it read."""
    known = {e.ref for e in context.epics} | {ALL}
    if isinstance(look, SystemLook):
        named = {r for d in look.decided for r in d.epics} | {
            r for c in look.callouts for r in c.epics
        }
    else:
        named = {v.epic for v in look.epics}
        missing = sorted(set(context.looking_at) - named)
        if missing:
            refuse("check.goal_look_crew.check_epics", "missing", epics=", ".join(missing))
    unknown = sorted(named - known)
    if unknown:
        refuse("check.goal_look_crew.check_epics", "unknown", epics=", ".join(unknown))


QUESTION = {
    "architect": (
        "You own the system: what across these epics would be expensive to reverse. Say "
        "what the code already supports, decide each one-way door the stories will build "
        "on, and call out the risks and seams between epics. Draw the Goal's target C4 "
        "diagram: the system as the repository has it, with the Goal's new parts and "
        "connections. A call outside the Goal's intent is the Sponsor's: name it in "
        "`beyond_the_goal`."
    ),
    "ux_designer": (
        "You own the reader: who reads the Goal's result, what they meet once each epic is "
        "delivered, and whether it lets them see or decide what they came for. Say what's "
        "missing for them, and write what they see as criteria the stories will carry."
    ),
}
ROLES = tuple(QUESTION)


def epics_text(context: GoalContext) -> str:
    return "\n\n".join(f"### {e.ref} ({e.state})\n\n{e.text}" for e in context.epics)


def opening(context: GoalContext, role: str) -> dict[str, str]:
    """A role's session start, before the project, map and hints: its step and question
    first, then what it reads the epics against."""
    later = (
        "The Goal has a record already, and its epics marked `looked at` are settled in "
        "it. Read the epics marked `approved`, against that record and the diagram."
        if context.record
        else "None of them is split yet, and nothing is settled for them."
    )
    return {
        "step": "## Your step: the Goal's look\n\n"
        "The Sponsor approved this Goal's epics. Before any is split into stories, the "
        "Architect and the UX Designer each read all of them once, and the Product Owner "
        f"settles what you both find into the Goal's record. {later}\n\n{QUESTION[role]}\n\n"
        "Start from what the epics flag as built on, new or risky. Write every issue as "
        "owner/repo#number.",
        "goal": f"## The Goal ({context.goal_ref}), set by the Sponsor\n\n"
        f"{sponsors_words(context.goal)}",
        "decisions": context.decisions,
        "project_log": context.project_log,
        "record": f"## The Goal's record\n\n{context.record}" if context.record else "",
        "diagram": (
            f"## The Goal's target diagram\n\n```mermaid\n{context.diagram}\n```"
            if context.diagram
            else ""
        ),
        "epics": f"## The Goal's epics\n\n{epics_text(context)}",
    }


STEPS = {
    "architect": steps.Step(
        "goal look architect",
        "architect",
        SystemLook,
        "what's supported, what you decided, the risks, and the Goal's target diagram.",
        "the Goal's epics",
        step="goal_look",
        tools=("contracts",),
    ),
    "ux_designer": steps.Step(
        "goal look ux_designer",
        "ux_designer",
        ReaderLook,
        "the reader, what they meet per epic, what's missing, and the criteria.",
        "the Goal's epics",
        step="goal_look",
        tools=("look",),
    ),
}


def read(
    context: GoalContext,
    role: str,
    reading: steps.Reading | None = None,
    on_end: Any = None,
) -> Any:
    """One role's look at the Goal's epics, in a session over the code at `reading`."""

    def check(found: SystemLook | ReaderLook) -> None:
        check_epics(found, context)

    return steps.run(
        STEPS[role],
        opening(context, role),
        reading,
        about=f"{context.goal}\n\n{epics_text(context)}",
        check=check,
        on_end=on_end,
    )


def run_look(context: GoalContext, reading: steps.Reading | None = None) -> LookResult:
    """Both roles at once: they read independently, and the settle waits for both. One
    failing doesn't lose the other's answer; each session is measured on one event."""
    measured: dict[str, dict[str, Any]] = {}

    def measure(role: str) -> Any:
        return lambda session, ending: measured.__setitem__(role, steps.measured(session, ending))

    def one(role: str) -> Any:
        return attributed(
            lambda: read(context, role, reading, on_end=measure(role)), purpose=f"goal look {role}"
        )()

    answers: dict[str, Any] = {}
    failed: dict[str, str] = {}
    began = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(ROLES)) as pool:
        futures = {role: pool.submit(contextvars.copy_context().run, one, role) for role in ROLES}
        for role, future in futures.items():
            try:
                answers[role] = future.result()
            except Exception as exc:  # noqa: BLE001 - a failed role is a result, not a crash
                failed[role] = f"{type(exc).__name__}: {exc}"[:300]
    steps.at_once("goal look", measured, failed, time.monotonic() - began)
    return LookResult(answers.get("architect"), answers.get("ux_designer"), failed)
