"""The settle step: the Product Owner turns the panel's notes into the epic's conclusion (crew#440).

Four roles each read an epic and raised notes. A split reading ten comments
would miss some, and a model asked to be brief isn't, so the discussion stays as
the panel's comment and what is handed on is a short table the schema holds
short: each note becomes a decision row, a question left for the design note, or
a dismissal with its reason. Or, when nothing written down answers a note that
needs a product choice, one question to the Sponsor and no conclusion yet.

The format follows Nygard's ADR fields (the same as the Goal's decision log),
with Example Mapping's question cards for what can't be settled yet. The IDs,
the bottom line and the layout are the code's (`flows/settle.py`), not the
model's.
"""

from __future__ import annotations

from crewai import Crew, Process, Task
from pydantic import BaseModel, Field, field_validator, model_validator

from crew_org.agents import build_agent
from crew_org.crews.panel_crew import PanelContext, PanelResult

# Cell limits. Asking a model to be brief doesn't hold (crew#440); a schema does.
CONTEXT_CHARS = 60
DECISION_CHARS = 140
CONSEQUENCES_CHARS = 140
SOURCE_CHARS = 160
QUESTION_CHARS = 140
IMPACT_CHARS = 100
REASON_CHARS = 100
ASK_CHARS = 300
INFRA_CHARS = 160
GOAL_QUOTE_CHARS = 160


def aim(limit: int) -> int:
    """What the model is told to stay under. The limit is a backstop, not the target.

    The server enforces a schema's length limit while it decodes, so a cell that
    runs past it is cut off mid-sentence, not refused. Found on the first real
    run: two cells ended in half a word, at the limit.
    """
    return limit * 3 // 4


class Row(BaseModel):
    context: str = Field(
        min_length=2,
        max_length=CONTEXT_CHARS,
        description=f"What it is about, a few words, under {aim(CONTEXT_CHARS)} characters",
    )
    decision: str = Field(
        min_length=2,
        max_length=DECISION_CHARS,
        description=(
            f"What is decided: a short fragment, not prose, under {aim(DECISION_CHARS)} characters"
        ),
    )
    consequences: str = Field(
        min_length=2,
        max_length=CONSEQUENCES_CHARS,
        description=(
            f"What follows from it for the stories, a fragment under "
            f"{aim(CONSEQUENCES_CHARS)} characters"
        ),
    )
    source: str = Field(
        min_length=2,
        max_length=SOURCE_CHARS,
        description=(
            "What settles it, named: a Goal decision by its ID (D3), the project's record, "
            "or an issue or pull request as owner/repo#number, as given in the context. "
            "For your own call, why you decided it. "
            f"Under {aim(SOURCE_CHARS)} characters"
        ),
    )
    own_call: bool = Field(
        default=False,
        description=(
            "True when no source answers it and you decided it yourself, as a product owner "
            "does for the team. It is recorded as your call"
        ),
    )
    goal_wording: str = Field(
        default="",
        max_length=GOAL_QUOTE_CHARS,
        description=(
            "Only for your own call: the Goal's own words, quoted exactly, that the decision "
            f"stays within. Under {aim(GOAL_QUOTE_CHARS)} characters"
        ),
    )
    settles: list[int] = Field(
        min_length=1, description="The numbers of the panel notes this answers (N1 is 1)"
    )
    # Recorded in the project's decision log too, for the epics that come after (crew#468).
    project_wide: bool = Field(
        default=False,
        description=(
            "True when it decides something for every epic in the project, not only this "
            "one: how a sprint counts a story, a standard the project follows, a versioning "
            "rule. It is added to the project's decision log. False when the log already "
            "says it"
        ),
    )

    @model_validator(mode="after")
    def _a_call_stays_within_the_goal(self) -> Row:
        if self.own_call and not self.goal_wording.strip():
            raise ValueError("your own call quotes the Goal's wording that it stays within")
        return self


def _plain(text: str) -> str:
    """Words only, to compare a quotation with its source: case, spacing, quote marks aside."""
    return " ".join(
        text.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"').split()
    ).casefold()


class OpenQuestion(BaseModel):
    question: str = Field(min_length=2, max_length=QUESTION_CHARS)
    impact: str = Field(
        min_length=2,
        max_length=IMPACT_CHARS,
        description="Who or what it changes if it goes one way",
    )
    settles: list[int] = Field(min_length=1, description="The panel notes it stands for")


class ForInfra(BaseModel):
    """Something about the deployed runtime the epic assumes and infra must provide."""

    item: str = Field(
        min_length=2,
        max_length=INFRA_CHARS,
        description=f"What infra has to provide, under {aim(INFRA_CHARS)} characters",
    )
    settles: list[int] = Field(min_length=1, description="The panel notes it stands for")


class Dismissal(BaseModel):
    note: int = Field(description="The number of the panel note")
    why: str = Field(
        min_length=2,
        max_length=REASON_CHARS,
        description=(
            f"Why it isn't needed: already settled, outside the epic, or wrong. "
            f"Under {aim(REASON_CHARS)} characters"
        ),
    )


class Conclusion(BaseModel):
    rows: list[Row] = Field(default_factory=list)
    open: list[OpenQuestion] = Field(
        default_factory=list,
        description="Design questions that can't be settled until the stories exist",
    )
    for_infra: list[ForInfra] = Field(
        default_factory=list,
        description=(
            "What the epic assumes of the deployed runtime that infra provides. Not rows: "
            "no story waits on them"
        ),
    )
    dismissed: list[Dismissal] = Field(default_factory=list)

    @model_validator(mode="after")
    def _says_something(self) -> Conclusion:
        if not (self.rows or self.open or self.for_infra or self.dismissed):
            raise ValueError("a conclusion answers the notes: give rows, questions or dismissals")
        return self

    def answered(self) -> set[int]:
        return (
            {n for r in self.rows for n in r.settles}
            | {n for q in self.open for n in q.settles}
            | {n for i in self.for_infra for n in i.settles}
            | {d.note for d in self.dismissed}
        )


class Settlement(BaseModel):
    """A conclusion, or one question for the Sponsor. Never both."""

    conclusion: Conclusion | None = None
    sponsor_question: str = Field(
        default="",
        max_length=ASK_CHARS,
        description=(
            "Only when a note needs a product choice that the Goal, the record, the "
            "Sponsor's decisions and the sibling epics don't answer: one question, "
            f"answerable in a sentence, under {aim(ASK_CHARS)} characters. Then give no conclusion"
        ),
    )

    @field_validator("sponsor_question")
    @classmethod
    def _stripped(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _one_or_the_other(self) -> Settlement:
        if (self.conclusion is None) == (not self.sponsor_question):
            raise ValueError("give a conclusion, or one question for the Sponsor; not both")
        return self


class Unsettled(ValueError):
    """The settlement leaves panel notes unanswered, or answers ones that don't exist."""


def numbered(panel: PanelResult) -> list[tuple[int, str, str]]:
    """The panel's notes in a fixed order, as (number, role, text). N1 is the first."""
    found: list[tuple[int, str, str]] = []
    for role, answer in panel.answers.items():
        for note in answer.notes:
            found.append(
                (
                    len(found) + 1,
                    role,
                    f"{note.problem}\n  Source: {note.source}\n  To settle: {note.settle}\n"
                    f"  The member's view of who settles it: {note.settled_by}",
                )
            )
    return found


def check_not_cut(settlement: Settlement) -> None:
    """No cell ran into its limit: a cell at the limit was cut off, not finished."""
    cut: list[str] = []
    if len(settlement.sponsor_question) >= ASK_CHARS:
        cut.append("the question for the Sponsor")
    conclusion = settlement.conclusion
    if conclusion is not None:
        for i, r in enumerate(conclusion.rows, 1):
            for name, limit in (
                ("context", CONTEXT_CHARS),
                ("decision", DECISION_CHARS),
                ("consequences", CONSEQUENCES_CHARS),
                ("source", SOURCE_CHARS),
                ("goal wording", GOAL_QUOTE_CHARS),
            ):
                if len(getattr(r, name.replace(" ", "_"))) >= limit:
                    cut.append(f"row {i} {name}")
        for i, q in enumerate(conclusion.open, 1):
            for name, limit in (("question", QUESTION_CHARS), ("impact", IMPACT_CHARS)):
                if len(getattr(q, name)) >= limit:
                    cut.append(f"open question {i} {name}")
        cut += [
            f"for infra {i}"
            for i, f in enumerate(conclusion.for_infra, 1)
            if len(f.item) >= INFRA_CHARS
        ]
        cut += [
            f"dismissal of N{d.note}" for d in conclusion.dismissed if len(d.why) >= REASON_CHARS
        ]
    if cut:
        raise Unsettled(
            f"These cells reached their length limit and were cut off: {', '.join(cut)}. "
            "Say each in fewer words, well under the limit, and finish the thought."
        )


def check_grounded(settlement: Settlement, goal: str) -> None:
    """A call the Product Owner makes itself stays within the Goal, in the Goal's own words.

    The leeway is the Product Owner's, as a team's owner has it, so long as the call is
    recorded and doesn't contradict the Goal. A quotation that isn't in the Goal is
    wording it made up, and no call rests on it.
    """
    if settlement.conclusion is None:
        return
    text = _plain(goal)
    ungrounded = [
        f"row {i}"
        for i, r in enumerate(settlement.conclusion.rows, 1)
        if r.own_call and _plain(r.goal_wording) not in text
    ]
    if ungrounded:
        raise Unsettled(
            f"{', '.join(ungrounded)}: your own call quotes wording that isn't in the Goal. "
            "Quote the Goal's own words exactly, or if the Goal doesn't say which way it points, "
            "ask the Sponsor one question instead."
        )


def note_owners(panel: PanelResult) -> dict[int, str]:
    """Who each numbered note was marked for by the member who raised it.

    Numbered in the order `numbered` uses, so N1 is the same note in both.
    """
    owners: dict[int, str] = {}
    for answer in panel.answers.values():
        for note in answer.notes:
            owners[len(owners) + 1] = note.settled_by
    return owners


def check_product_calls(settlement: Settlement, panel: PanelResult) -> None:
    """The Product Owner's own calls are product choices; a design question is the Architect's.

    A note a member marked for the Architect (a path, a response shape, a parameter) stays
    an open question for the design note unless a source settles it. The leeway to decide
    is for what the product does (ADR 0018).
    """
    if settlement.conclusion is None:
        return
    owners = note_owners(panel)
    design = [
        f"row {i} (N{', N'.join(str(n) for n in r.settles if owners.get(n) == 'architect')})"
        for i, r in enumerate(settlement.conclusion.rows, 1)
        if r.own_call and any(owners.get(n) == "architect" for n in r.settles)
    ]
    if design:
        raise Unsettled(
            f"{'; '.join(design)} are your own call on a note a member marked for the Architect. "
            "That is a design question: list it as an open question for the design note, or give "
            "a source that settles it. Your own calls are for what the product does."
        )


def check_covers(settlement: Settlement, count: int) -> None:
    """Every note is answered, and nothing answers a note that isn't there."""
    if settlement.conclusion is None:
        return
    answered = settlement.conclusion.answered()
    missing = sorted(set(range(1, count + 1)) - answered)
    unknown = sorted(answered - set(range(1, count + 1)))
    if missing or unknown:
        raise Unsettled(
            (
                f"No row, question or dismissal for N{', N'.join(map(str, missing))}. "
                if missing
                else ""
            )
            + (f"N{', N'.join(map(str, unknown))} are not notes. " if unknown else "")
            + f"There are {count} notes, N1 to N{count}."
        )


def describe(
    context: PanelContext, panel: PanelResult, *, reply: str = "", feedback: str = ""
) -> str:
    siblings = "\n\n".join(f"### {s.ref} ({s.state})\n\n{s.text}" for s in context.siblings)
    notes = "\n\n".join(f"N{n} ({role}): {text}" for n, role, text in numbered(panel))
    return (
        f"## The Goal ({context.goal_ref}), set by the Sponsor\n\n{context.goal}\n\n"
        + (f"{context.project}\n\n" if context.project else "")
        + (f"{context.decisions}\n\n" if context.decisions else "")
        + (f"{context.project_log}\n\n" if context.project_log else "")
        + (f"## The other epics under this Goal\n\n{siblings}\n\n" if siblings else "")
        + f"## The epic ({context.epic_ref})\n\n{context.epic}\n\n"
        f"## The panel's notes\n\n{notes or '(none)'}\n\n"
        + (f"## The Sponsor's answer to your question\n\n{reply}\n\n" if reply else "")
        + (f"## Your last conclusion was refused\n\n{feedback}\n\n" if feedback else "")
        + "## Your task\n\n"
        "Four roles read this epic before it is split and raised the notes above. Settle each "
        "one, in the fewest words:\n"
        "- **A row** when you can decide it. If the Goal, the project's record, a Sponsor "
        "decision, the project's decision log or a sibling's conclusion answers it, say what is "
        "decided and what follows, and name the source. If none does, decide it yourself, as "
        "the product owner does for the team: set `own_call`, say why, and quote the Goal's own "
        "words that the decision stays within. It is recorded as your call. It must not "
        "contradict the Goal or a Sponsor decision. Your own calls are for what the product "
        "does. A note a member marked for the Architect is a design question (a path, a "
        "response shape, a parameter): it is an open question for the design note, unless a "
        "source settles it.\n"
        "- **An open question** when it is a design question that can't be settled until the "
        "stories exist. The Architect settles those after the split.\n"
        "- **For infra** when a member marked it infra: it is about the deployed runtime "
        "(where it runs, real addresses and secrets, provisioning, backups), which the "
        "project doesn't build. List what infra has to provide, in a few words. It is not a "
        "row, and no story waits on it.\n"
        "- **A dismissal** when it isn't needed: already settled, wholly inside another epic, "
        "or wrong. Say why.\n"
        "Every note gets one of the four, and a row may answer several. Ask the Sponsor one "
        "question, with no conclusion, only when you can't tell which way the Goal points, so "
        "that any call you made might contradict it. Don't ask what you can decide, and don't "
        "restate the epic."
    )


def settle(
    context: PanelContext, panel: PanelResult, *, reply: str = "", feedback: str = ""
) -> Settlement:
    """The Product Owner's settlement of the panel's notes."""
    agent = build_agent("product_owner")
    task = Task(
        description=describe(context, panel, reply=reply, feedback=feedback),
        expected_output="A conclusion that answers every note, or one question for the Sponsor.",
        agent=agent,
        output_pydantic=Settlement,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    answer = getattr(crew.kickoff(), "pydantic", None)
    if not isinstance(answer, Settlement):
        raise ValueError("the Product Owner gave no answer in the Settlement form")
    return answer
