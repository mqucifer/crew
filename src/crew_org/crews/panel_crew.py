"""The refinement panel: four roles each read an approved epic once, before it is split (crew#440).

On 2026-10-01 the epics of sprint-metrics Goal 174 were split with nobody asking
the Architect, the UX Designer, QA or DevOps what they saw. The test in
`experiments/panel-174/` ran these four calls on the epics as they stood that
morning: they named the problems between epics reliably and added few notes
that weren't needed, but missed four findings together. This is the panel with
the test's three changes:

- the Sponsor's recorded decisions are in every member's context;
- DevOps and the Architect each get a check for their blind spot, as a scope
  line in their task, not a design rule (ADR 0011);
- each member reviews the epic in front of it. How it fits a sibling is theirs
  to raise; a problem wholly inside a sibling, or in a delivered one, is not.

Each member is its own call with the same context, and the four run at once.
They don't see each other's notes. Who settles a note is the member's call too
(`settled_by`), so the settle step only has to read them.
"""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Literal

from crewai import Crew, Process, Task
from pydantic import BaseModel, Field, model_validator

from crew_org.agents import build_agent
from crew_org.events import attributed
from crew_org.permissions import load_agents

# What each member reads the epic for: the area it owns, and nothing about what
# to find. The last two carry the checks for the blind spots the test found.
FOCUS = {
    "architect": (
        "You own coherence: the data shapes, interfaces and decisions in this epic that "
        "would be expensive to reverse, and whether they fit the rest of the Goal.\n"
        "A change to a published version or format is classified by the project's "
        "versioning rule in the Sponsor's decisions: say whether it is MAJOR, MINOR or "
        "PATCH under that rule, and quote it. If the epic needs code that a sibling epic "
        "builds, name the sibling and what it has to provide."
    ),
    "ux_designer": (
        "You own what a person or a program using this sees: what they send, what they "
        "get back, and what they read to use it."
    ),
    "qa_engineer": (
        "You will have to prove what this epic delivers. You own whether what the Goal "
        "asks of it can be proven, and whether everything it promises can hold together."
    ),
    "devops_engineer": (
        "You own whether what this epic ships can be run and operated, and proven in CI. "
        "The project builds to its spec; where it runs is infra's.\n"
        "For the epic, check the runtime contract: the settings it reads (their names and "
        "defaults), what it needs at startup, how it is started, health-checked and "
        "stopped, and how CI proves it, with throwaway services and no real secrets. A gap "
        "there is a problem to settle.\n"
        "The deployed runtime is infra's, not the epic's: which server, real addresses and "
        "secrets, provisioning, backups, scaling, dashboards. Don't ask the epic to settle "
        "them. If the epic assumes something infra must provide, raise one note for it and "
        "mark it infra."
    ),
}
ROLES = tuple(FOCUS)

SCOPE = (
    "Your notes are about this epic. A problem with how it fits another epic is yours to "
    "raise. A problem wholly inside another epic belongs to that epic's review, and a "
    "delivered epic is background only."
)

SettledBy = Literal["product_owner", "architect", "sponsor", "infra"]


class PanelNote(BaseModel):
    problem: str = Field(
        description="What the epic gets wrong, leaves out or contradicts, in one or two sentences"
    )
    source: str = Field(
        description=(
            "What says so, quoted, and where: the Goal, the project's record, a Sponsor "
            "decision (name its issue), or another epic (name it)"
        )
    )
    settle: str = Field(description="What should be settled before the epic is split")
    settled_by: SettledBy = Field(
        description=(
            "Who can settle it: product_owner if the Goal, the record or a recorded Sponsor "
            "decision answers it; architect if it is a design question; sponsor only if "
            "nothing recorded answers it and it is a product choice; infra only for the "
            "deployed runtime (where it runs, real addresses and secrets, provisioning, "
            "backups), which the project doesn't build"
        )
    )


class PanelAnswer(BaseModel):
    nothing_to_add: bool = Field(
        description="True if there is nothing in your area to add. Then notes is empty"
    )
    notes: list[PanelNote] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_or_the_other(self) -> PanelAnswer:
        if self.nothing_to_add and self.notes:
            raise ValueError("nothing_to_add with notes: give notes, or nothing to add")
        if not self.nothing_to_add and not self.notes:
            raise ValueError("no notes: give at least one, or say nothing to add")
        return self


@dataclass(frozen=True)
class Sibling:
    ref: str
    # "open" or "delivered", shown with its record; or "superseded", set aside,
    # shown only by the binding rows it carries (crew#611).
    state: str
    text: str


@dataclass(frozen=True)
class PanelContext:
    """What every member is shown. The same for all four."""

    goal_ref: str
    goal: str
    project: str
    decisions: str
    epic_ref: str
    epic: str
    siblings: list[Sibling] = field(default_factory=list)
    # The project's own decision log: what applies to every epic, whatever its Goal (crew#468).
    project_log: str = ""


@dataclass(frozen=True)
class PanelResult:
    answers: dict[str, PanelAnswer]
    # A member whose call failed: its role and why. The others' notes still stand.
    failed: dict[str, str]


def describe(context: PanelContext, role: str) -> str:
    title = load_agents()[role]["role"]
    siblings = "\n\n".join(f"### {s.ref} ({s.state})\n\n{s.text}" for s in context.siblings)
    return (
        f"## The Goal ({context.goal_ref}), set by the Sponsor\n\n{context.goal}\n\n"
        + (f"{context.project}\n\n" if context.project else "")
        + (f"{context.decisions}\n\n" if context.decisions else "")
        + (f"{context.project_log}\n\n" if context.project_log else "")
        + (f"## The other epics under this Goal\n\n{siblings}\n\n" if siblings else "")
        + f"## The epic ({context.epic_ref})\n\n{context.epic}\n\n"
        "## Your task\n\n"
        "This epic is about to be split into stories. Before it is, four roles each read "
        f"it once, for what they own. You are the {title}. {FOCUS[role]}\n\n"
        "Name each problem in your area that should be settled before the split: "
        "something the epic gets wrong, leaves out or contradicts, against the Goal, the "
        "project's record, the Sponsor's decisions, the project's decision log or the other "
        "epics. Quote its source.\n\n"
        f"{SCOPE}\n\n"
        'If there is nothing in your area to add, say so: "nothing to add" is a complete '
        "answer, and better than a note that isn't needed. Don't restate the epic, propose "
        "stories or a design, or note things outside your area; the other roles cover theirs.\n\n"
        "Write every issue or pull request as owner/repo#number, as given above, never as a "
        "bare number."
    )


def review(context: PanelContext, role: str) -> PanelAnswer:
    """One member's read of the epic."""
    agent = build_agent(role)
    task = Task(
        description=describe(context, role),
        expected_output="Your notes on this epic, or nothing to add.",
        agent=agent,
        output_pydantic=PanelAnswer,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    answer = getattr(crew.kickoff(), "pydantic", None)
    if not isinstance(answer, PanelAnswer):
        raise ValueError(f"the {role} gave no answer in the PanelAnswer form")
    return answer


def run_panel(context: PanelContext, roles: tuple[str, ...] = ROLES) -> PanelResult:
    """All members at once. One failing doesn't lose the others' notes."""

    def one(role: str) -> PanelAnswer:
        # The caller attributes the card and repo; each member adds its own purpose.
        answer: PanelAnswer = attributed(lambda: review(context, role), purpose=f"panel {role}")()
        return answer

    answers: dict[str, PanelAnswer] = {}
    failed: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(roles)) as pool:
        # Each thread runs in its own copy of this context, so its call is
        # attributed to the tick's span and card.
        futures = {role: pool.submit(contextvars.copy_context().run, one, role) for role in roles}
        for role, future in futures.items():
            try:
                answers[role] = future.result()
            except Exception as exc:  # noqa: BLE001 - a failed member is a result, not a crash
                failed[role] = f"{type(exc).__name__}: {exc}"[:300]
    return PanelResult(answers, failed)
