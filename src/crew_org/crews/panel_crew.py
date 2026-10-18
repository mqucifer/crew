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

Each member is its own session over the code with the same context, one at a time
(crew#807, P1). They don't see each other's notes. Who settles a note is the
member's call too (`settled_by`), so the settle step only has to read them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from crew_org.events import attributed
from crew_org.permissions import load_agents
from crew_org.rulebook import refuse
from crew_org.session import steps

# What each member reads the epic for: the area it owns, and nothing about what
# to find. The last two carry the checks for the blind spots the test found.
FOCUS = {
    "architect": (
        "You own coherence: the data shapes, interfaces and decisions in this epic that "
        "would be expensive to reverse, and whether they fit the rest of the Goal.\n"
        "A change to a published version or format is classified by the project's "
        "versioning rule in the project's decision log or record: say whether it is MAJOR, "
        "MINOR or "
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
            refuse("form.PanelAnswer.one_or_the_other", "with_notes")
        if not self.nothing_to_add and not self.notes:
            refuse("form.PanelAnswer.one_or_the_other", "no_notes")
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


# The Goal template's own last line, which is the crew's, not the Sponsor's words.
_GOAL_FOOTER = re.compile(r"\n-{3,}\s*\n\s*_Set by the Product Sponsor\..*?_\s*$", re.S)


def sponsors_words(goal: str) -> str:
    """A Goal's text without its template's footer (crew#807's prompt review)."""
    return _GOAL_FOOTER.sub("", goal).rstrip()


def goal_part(context: PanelContext) -> str:
    return f"## The Goal ({context.goal_ref}), set by the Sponsor\n\n{sponsors_words(context.goal)}"


def siblings_text(context: PanelContext) -> str:
    return "\n\n".join(f"### {s.ref} ({s.state})\n\n{s.text}" for s in context.siblings)


def opening(context: PanelContext, role: str) -> dict[str, str]:
    """A member's session start, before the project, map and hints (crew#807): its
    step and scope first, then what it reads the epic against."""
    title = load_agents()[role]["role"]
    siblings = siblings_text(context)
    return {
        "step": f"## Your step: the refinement panel, as the {title}\n\n"
        "This epic is about to be split into stories. Before it is, four roles each read "
        "it once, for what they own, and the Product Owner settles what they raise into "
        f"the epic's record. You are the {title}. {FOCUS[role]}\n\n"
        "Name each problem in your area that should be settled before the split: "
        "something the epic gets wrong, leaves out or contradicts, against the Goal, the "
        "project's record, the Sponsor's decisions, the project's decision log, the other "
        "epics or the code. Quote its source.\n\n"
        f"{SCOPE}\n\n"
        'If there is nothing in your area to add, say so: "nothing to add" is a complete '
        "answer, and better than a note that isn't needed. Don't restate the epic, propose "
        "stories or a design, or note things outside your area; the other roles cover theirs. "
        "Write every issue or pull request as owner/repo#number, never as a bare number.",
        "goal": goal_part(context),
        "decisions": context.decisions if _decided(context.decisions) else "",
        "project_log": context.project_log,
        "siblings": f"## The other epics under this Goal\n\n{siblings}" if siblings else "",
        "epic": f"## The epic ({context.epic_ref})\n\n{context.epic}",
    }


def _decided(decisions: str) -> bool:
    """The decisions block holds a decision, or says where it couldn't look, not only
    its heading (`decisions.render`)."""
    return "\n## " in decisions or "Not searched" in decisions


# The members' own role text in refinement, where one has it (crew#807).
ROLE_TEXT = {"architect": "refinement", "qa_engineer": "panel"}
STEPS = {
    role: steps.Step(
        f"panel {role}",
        role,
        PanelAnswer,
        "your notes on this epic, or nothing to add.",
        "the epic",
        step=ROLE_TEXT.get(role),
    )
    for role in FOCUS
}


def review(context: PanelContext, role: str, reading: steps.Reading | None = None) -> PanelAnswer:
    """One member's read of the epic, in a session over the code at `reading`."""
    answer: PanelAnswer = steps.run(
        STEPS[role], opening(context, role), reading, about=f"{context.epic}\n\n{context.goal}"
    )
    return answer


def run_panel(
    context: PanelContext,
    roles: tuple[str, ...] = ROLES,
    reading: steps.Reading | None = None,
) -> PanelResult:
    """Each member in turn, one session at a time (P1). One failing doesn't lose the
    others' notes."""
    answers: dict[str, PanelAnswer] = {}
    failed: dict[str, str] = {}
    for role in roles:
        try:
            # The caller attributes the card and repo; each member adds its own purpose.
            answers[role] = attributed(
                lambda r=role: review(context, r, reading), purpose=f"panel {role}"
            )()
        except Exception as exc:  # noqa: BLE001 - a failed member is a result, not a crash
            failed[role] = f"{type(exc).__name__}: {exc}"[:300]
    return PanelResult(answers, failed)
