"""The refinement panel: four roles each ask their own question of an epic, before the split.

On 2026-10-01 the epics of sprint-metrics Goal 174 were split with nobody asking
the Architect, the UX Designer, QA or DevOps what they saw. The test in
`experiments/panel-174/` ran these four calls on the epics as they stood that
morning, and the panel was built from it.

Each member asks its own question, with its own goal and its own kind of answer
(the Sponsor, 2026-10-21, ADR 0035, crew#828). This is the Three Amigos as Example
Mapping runs them: each role brings a different kind of card. Before, all four had
one task and one form with a different area of ownership, and 44% of their notes
repeated each other (`experiments/panel-overlap-828/`). An example can't repeat a
design decision, and a small epic gets short answers: no runtime change, nothing
expensive to reverse.

| Member | Its goal | What it produces |
|---|---|---|
| UX Designer | the reader | the reader's view, and what's missing for them (`ReaderView`) |
| Architect | the system | the decisions to make before building (`Decisions`) |
| QA | proof | the examples that become the stories' criteria (`Examples`) |
| DevOps | running it | the runtime changes, or none (`RuntimeChanges`) |

Each member is its own session over the code with the same context, and the four run
at once (the Sponsor, crew#807). They don't see each other's answers. Who settles an
item is the member's call (`settled_by`), and every form gives the settle its items
in one shape (`Item`), so the settle reads them all the same way.
"""

from __future__ import annotations

import contextvars
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from crew_org.events import attributed
from crew_org.permissions import load_agents
from crew_org.session import steps

SettledBy = Literal["product_owner", "architect", "sponsor", "infra"]
Kind = Literal["reader", "decision", "example", "question", "runtime"]


def settled_by(**kw: Any) -> Any:
    """Who settles an item, as each form asks it: one rule, told in each form's field."""
    return Field(
        description=(
            "Who can settle it: product_owner if the Goal, the record or a recorded Sponsor "
            "decision answers it; architect if it is a design question; sponsor only if "
            "nothing recorded answers it and it is a product choice; infra only for the "
            "deployed runtime (where it runs, real addresses and secrets, provisioning, "
            "backups), which the project doesn't build"
        ),
        **kw,
    )


_SOURCE = (
    "What it rests on, quoted, and where: the Goal, the project's record, a Sponsor "
    "decision (name its issue), another epic (name it), or the code"
)


@dataclass(frozen=True)
class Item:
    """One thing a member raised, as the settle reads it, whatever the member's form."""

    kind: Kind
    text: str
    source: str
    settled_by: SettledBy


# --- the UX Designer: the reader --------------------------------------------------------------


class ReaderGap(BaseModel):
    gap: str = Field(
        description="What the reader needs from this epic that it leaves out or makes hard "
        "to use, in one or two sentences"
    )
    source: str = Field(description=_SOURCE)
    settled_by: SettledBy = settled_by()


class ReaderView(BaseModel):
    reader: str = Field(description="Who reads or uses what this epic delivers, from the Goal")
    sees: str = Field(
        description="What they will see or do once it is delivered, as they meet it: the "
        "page, the answer, the output"
    )
    matters: str = Field(
        description="What it lets them see or decide that they couldn't before, or that it "
        "doesn't matter to them, and why"
    )
    missing: list[ReaderGap] = Field(
        default_factory=list,
        description="What is missing for the reader. Empty when the epic gives them what they need",
    )

    def items(self) -> list[Item]:
        return [Item("reader", g.gap, g.source, g.settled_by) for g in self.missing]

    def view(self) -> str:
        return f"Reader: {self.reader}\n  Sees: {self.sees}\n  Why it matters: {self.matters}"


# --- the Architect: the system ----------------------------------------------------------------


class Decision(BaseModel):
    decision: str = Field(
        description="What has to be decided before the stories are written: a data shape, "
        "an interface, a published version or format"
    )
    why: str = Field(description="What it would cost to reverse once stories build on it")
    options: str = Field(description="The options you see, and the one the code or record favours")
    source: str = Field(description=_SOURCE)
    settled_by: SettledBy = settled_by(default="architect")


class Decisions(BaseModel):
    decisions: list[Decision] = Field(
        default_factory=list,
        description="The decisions to make before building. Empty when nothing this epic "
        "touches is expensive to reverse",
    )

    def items(self) -> list[Item]:
        return [
            Item(
                "decision",
                f"{d.decision}\n  Expensive to reverse because: {d.why}\n  Options: {d.options}",
                d.source,
                d.settled_by,
            )
            for d in self.decisions
        ]

    def view(self) -> str:
        return ""


# --- QA: proof --------------------------------------------------------------------------------


class Example(BaseModel):
    rule: str = Field(description="The rule from the epic or the Goal that the example illustrates")
    example: str = Field(
        description="One concrete situation and what the reader sees: real values in, the "
        "result out"
    )
    edge: bool = Field(default=False, description="True for an edge case or a failure")
    source: str = Field(description=_SOURCE)


class Question(BaseModel):
    question: str = Field(
        description="What has to be answered before an example can be written for a rule"
    )
    source: str = Field(description=_SOURCE)
    settled_by: SettledBy = settled_by()


class Examples(BaseModel):
    examples: list[Example] = Field(
        default_factory=list,
        description="The examples that prove the epic worked for its reader, the edge cases "
        "with them. They become the stories' criteria",
    )
    questions: list[Question] = Field(
        default_factory=list,
        description="Questions an example waits on. Empty when there are none",
    )

    def items(self) -> list[Item]:
        return [
            Item(
                "example",
                f"{e.rule}\n  {'Edge case' if e.edge else 'Example'}: {e.example}",
                e.source,
                "product_owner",
            )
            for e in self.examples
        ] + [Item("question", q.question, q.source, q.settled_by) for q in self.questions]

    def view(self) -> str:
        return ""


# --- DevOps: running it -----------------------------------------------------------------------


class RuntimeChange(BaseModel):
    area: Literal["settings", "startup", "health", "stop", "ci"] = Field(
        description="Which part of the runtime contract it changes"
    )
    change: str = Field(
        description="What changes in it, and what the epic leaves unsaid about it: a "
        "setting's name and default, what is needed at startup, how it's health-checked or "
        "stopped, how CI proves it"
    )
    source: str = Field(description=_SOURCE)
    settled_by: SettledBy = settled_by()


class RuntimeChanges(BaseModel):
    changes: list[RuntimeChange] = Field(
        default_factory=list,
        description="The runtime changes this epic ships. Empty when it changes nothing that runs",
    )

    def items(self) -> list[Item]:
        return [
            Item("runtime", f"{c.area}: {c.change}", c.source, c.settled_by) for c in self.changes
        ]

    def view(self) -> str:
        return ""


MemberAnswer = ReaderView | Decisions | Examples | RuntimeChanges


@dataclass(frozen=True)
class Ask:
    """A member's own question: what it reads the epic for, and the answer it gives."""

    goal: str
    question: str
    how: str
    form: type[BaseModel]
    said: str
    # What its answer says when it has nothing to raise, as the comment shows it.
    none: str


ASKS: dict[str, Ask] = {
    "ux_designer": Ask(
        "the reader",
        "Is this what the reader needs? What will they see or do, and does it matter to them?",
        "Say who the reader is, from the Goal, what they will meet once the epic is "
        "delivered, and what it lets them see or decide. Then say what is missing for them.",
        ReaderView,
        "the reader's view of this epic, and what's missing for them.",
        "Nothing missing for the reader.",
    ),
    "architect": Ask(
        "the system",
        "What does this touch that's expensive to reverse?",
        "Name each decision the stories will build on that would be costly to undo: a data "
        "shape, an interface, a published version or format. A change to a published version "
        "or format is classified by the project's versioning rule in its decision log or "
        "record: say whether it is MAJOR, MINOR or PATCH under that rule, and quote it. If "
        "the epic needs code that a sibling epic builds, name the sibling and what it has to "
        "provide.",
        Decisions,
        "the decisions to make before building, or none.",
        "Nothing expensive to reverse.",
    ),
    "qa_engineer": Ask(
        "proof",
        "How will we know it worked for the reader?",
        "Write the examples that would prove it: for each rule the epic sets, concrete "
        "situations with real values and what the reader sees, the edge cases with them. "
        "Where an example waits on an answer, ask for it as a question.",
        Examples,
        "the examples that prove this epic, and any questions that stop one.",
        "No examples.",
    ),
    "devops_engineer": Ask(
        "running it",
        "Can this be shipped, run and watched?",
        "List the runtime changes the epic ships: the settings it reads (their names and "
        "defaults), what it needs at startup, how it is started, health-checked and stopped, "
        "and how CI proves it, with throwaway services and no real secrets. The project "
        "builds to its spec; where it runs is infra's: which server, real addresses and "
        "secrets, provisioning, backups, scaling, dashboards. If the epic assumes something "
        "infra must provide, mark that change infra.",
        RuntimeChanges,
        "the runtime changes, or none.",
        "No runtime change.",
    ),
}
ROLES = tuple(ASKS)

SCOPE = (
    "Your answer is about this epic. How it fits another epic is yours to raise. "
    "Something wholly inside another epic belongs to that epic's review, and a "
    "delivered epic is background only."
)


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
    answers: dict[str, MemberAnswer]
    # A member whose call failed: its role and why. The others' answers still stand.
    failed: dict[str, str]

    def items(self) -> list[tuple[str, Item]]:
        """Every member's items, as (role, item), in the order the settle numbers them."""
        return [(role, item) for role, answer in self.answers.items() for item in answer.items()]


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
    step, question and scope first, then what it reads the epic against."""
    title = load_agents()[role]["role"]
    ask = ASKS[role]
    siblings = siblings_text(context)
    return {
        "step": f"## Your step: the refinement panel, as the {title}\n\n"
        "This epic is about to be split into stories. Before it is, four roles each ask "
        "their own question of it, and the Product Owner settles their answers into the "
        f"epic's record. Your goal is {ask.goal}, and your question is: {ask.question}\n\n"
        f"{ask.how} Read it against the Goal's reader and intent, the project's record, the "
        "Sponsor's decisions, the project's decision log, the other epics and the code, and "
        "quote what each answer rests on.\n\n"
        f"{SCOPE}\n\n"
        "A short answer is a complete one when the epic is small. Write every issue or pull "
        "request as owner/repo#number, never as a bare number.",
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


# The members' own role text in the panel (crew#807, ADR 0035).
ROLE_TEXT = {
    "architect": "refinement",
    "qa_engineer": "panel",
    "ux_designer": "panel",
    "devops_engineer": "panel",
}
STEPS = {
    role: steps.Step(
        f"panel {role}",
        role,
        ask.form,
        ask.said,
        "the epic",
        step=ROLE_TEXT.get(role),
    )
    for role, ask in ASKS.items()
}


def review(
    context: PanelContext,
    role: str,
    reading: steps.Reading | None = None,
    on_end: Any = None,
) -> MemberAnswer:
    """One member's read of the epic, in a session over the code at `reading`."""
    answer: MemberAnswer = steps.run(
        STEPS[role],
        opening(context, role),
        reading,
        about=f"{context.epic}\n\n{context.goal}",
        on_end=on_end,
    )
    return answer


def run_panel(
    context: PanelContext,
    roles: tuple[str, ...] = ROLES,
    reading: steps.Reading | None = None,
) -> PanelResult:
    """All members at once: they read independently, and the settle waits for all of
    them (the Sponsor, crew#807). One failing doesn't lose the others' answers.

    Each member's model time while they all ran, and what the Spark served across
    them, are recorded on one event: a first measure of several sessions at once
    (mqucifer/crew#790).
    """
    measured: dict[str, dict[str, Any]] = {}

    def measure(role: str) -> Any:
        return lambda session, ending: measured.__setitem__(role, steps.measured(session, ending))

    def one(role: str) -> MemberAnswer:
        # The caller attributes the card and repo; each member adds its own purpose.
        answer: MemberAnswer = attributed(
            lambda: review(context, role, reading, on_end=measure(role)), purpose=f"panel {role}"
        )()
        return answer

    answers: dict[str, MemberAnswer] = {}
    failed: dict[str, str] = {}
    began = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(roles)) as pool:
        # Each thread runs in its own copy of this context, so its calls are
        # attributed to the tick's span and card.
        futures = {role: pool.submit(contextvars.copy_context().run, one, role) for role in roles}
        for role, future in futures.items():
            try:
                answers[role] = future.result()
            except Exception as exc:  # noqa: BLE001 - a failed member is a result, not a crash
                failed[role] = f"{type(exc).__name__}: {exc}"[:300]
    steps.at_once("panel", measured, failed, time.monotonic() - began)
    return PanelResult(answers, failed)
