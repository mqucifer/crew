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

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from crew_org.crews.panel_crew import PanelContext, PanelResult
from crew_org.cut_text import cut_fields
from crew_org.rulebook import Refusal, refuse, says
from crew_org.session import steps

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
    serves: str = Field(
        default="",
        max_length=GOAL_QUOTE_CHARS,
        description=(
            "Only for your own call: the Goal's words on its reader, or on what they should "
            "see or decide, quoted exactly, that the decision serves. Under "
            f"{aim(GOAL_QUOTE_CHARS)} characters"
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
        if self.own_call and not self.serves.strip():
            refuse("form.Row.a_call_stays_within_the_goal")
        return self


# A Goal written as intent has these three parts (ADR 0035), under these headings.
READER = "Who reads it"
SEE_OR_DECIDE = "What it should let them see or decide"
OUT = "What's out"
_HEADING = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.M)
# What a Goal written before the headings calls what it leaves out.
_OUT_HEADINGS = (OUT.casefold(), "out of scope", "not in scope")


def goal_intent(text: str) -> tuple[str, str]:
    """A Goal's intent, as (what a call may serve, what's out).

    A Goal written as intent serves its reader and what they should see or decide.
    One written before the headings (ADR 0035) is all intent, but for what it
    leaves out: the old Goals still refining read as they always did.
    """
    sections: dict[str, str] = {}
    marks = list(_HEADING.finditer(text))
    for at, mark in enumerate(marks):
        stop = marks[at + 1].start() if at + 1 < len(marks) else len(text)
        sections[mark.group(1).strip().casefold()] = text[mark.end() : stop]
    out = "\n".join(sections.get(name, "") for name in _OUT_HEADINGS).strip()
    reader, see = sections.get(READER.casefold()), sections.get(SEE_OR_DECIDE.casefold())
    within = f"{reader or ''}\n{see or ''}" if reader or see else text
    if not (reader or see):
        for name in _OUT_HEADINGS:
            if section := sections.get(name):
                within = within.replace(section, "\n")
    return within, out


def _plain(text: str) -> str:
    """Words only, to compare a quotation with its source: case, spacing, quote marks aside."""
    return " ".join(
        text.replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"').split()
    ).casefold()


class OpenQuestion(BaseModel):
    # Both say their limit, as every other cell does: without it these were the
    # cells cut off, 13 of 14 in one tick (crew#742).
    question: str = Field(
        min_length=2,
        max_length=QUESTION_CHARS,
        description=f"The question, under {aim(QUESTION_CHARS)} characters",
    )
    impact: str = Field(
        min_length=2,
        max_length=IMPACT_CHARS,
        description=(
            f"Who or what it changes if it goes one way, under {aim(IMPACT_CHARS)} characters"
        ),
    )
    # Who settles it is part of the record (crew#583, ADR 0023): a question the
    # stories' criteria depend on is the Architect's; one any reasonable choice
    # answers is left to whoever builds it, and no gate checks it.
    settled_by: Literal["architect", "implementer"] = Field(
        description=(
            "`architect` when the stories' criteria depend on the answer: a key, a path, a "
            "response shape. `implementer` when any reasonable choice will do and nothing a "
            "test or a reviewer checks depends on it"
        )
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


class ProposedGoal(BaseModel):
    """A Goal the Product Owner proposes, written as intent (ADR 0027, ADR 0035).

    Filed as a draft for the Sponsor, who approves it by adding the `goal` label.
    """

    title: str = Field(description="The outcome for its reader, in a line")
    reader: str = Field(description="Who reads the result")
    see_or_decide: str = Field(description="What it should let them see or decide")
    out: str = Field(description="What's out")


class Elsewhere(BaseModel):
    """The Goal points elsewhere: serving its reader needs what its intent doesn't cover."""

    why: str = Field(description="What serving the reader needs that the Goal doesn't cover")
    question: str = Field(
        default="",
        description="One question for the Sponsor, answerable in a sentence. Or a Goal",
    )
    goal: ProposedGoal | None = Field(
        default=None, description="The Goal you propose to the Sponsor instead. Or a question"
    )

    @model_validator(mode="after")
    def _one_or_the_other(self) -> Elsewhere:
        if bool(self.question.strip()) == (self.goal is not None):
            refuse("form.Elsewhere.one_or_the_other")
        return self


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
    # Brought to the Sponsor on the Goal; the epic's record goes on (crew#846).
    elsewhere: Elsewhere | None = Field(
        default=None,
        description=(
            "Where a note shows serving the Goal's reader needs something its intent doesn't "
            "cover: a question for the Sponsor about the Goal, or a Goal you propose. The note "
            "is still settled here"
        ),
    )

    @model_validator(mode="after")
    def _says_something(self) -> Conclusion:
        if not (self.rows or self.open or self.for_infra or self.dismissed):
            refuse("form.Conclusion.says_something")
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
            refuse("form.Settlement.one_or_the_other")
        return self


class Unsettled(Refusal):
    """The settlement leaves panel notes unanswered, or answers ones that don't exist."""


def numbered(panel: PanelResult) -> list[tuple[int, str, str]]:
    """The panel's items in a fixed order, as (number, role, text). N1 is the first.

    The settle is told each one's kind beside its role (ADR 0035): a reader's gap, a
    decision, a question or a runtime change. QA's examples aren't notes: they go to the
    record as they are (`PanelResult.examples`).
    """
    return [
        (
            n,
            role,
            f"{item.text}\n  Source: {item.source}\n"
            f"  The member's view of who settles it: {item.settled_by}",
        )
        for n, (role, item) in enumerate(panel.items(), 1)
    ]


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
                ("serves", GOAL_QUOTE_CHARS),
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
        refuse("check.settle_crew.cut", kind=Unsettled, cells=", ".join(cut))
    _not_cut_at_a_quote(settlement)


def _not_cut_at_a_quote(answer: BaseModel) -> None:
    """A cell cut where the model wrote a double quote reads as finished (crew#618)."""
    if found := cut_fields(answer):
        refuse("check.settle_crew.not_cut_at_a_quote", kind=Unsettled, fields=", ".join(found))


def check_grounded(settlement: Settlement, goal: str) -> None:
    """A call the Product Owner makes itself serves the Goal's intent (ADR 0018, ADR 0035).

    The leeway is the Product Owner's, as a team's owner has it: it may leave out or
    reshape what the Goal mentions, so long as the call is recorded and serves the
    Goal's reader. Words that aren't the reader or what they should see or decide
    aren't the intent: made up, a feature the Goal lists, or what it rules out.
    """
    if settlement.conclusion is None:
        return
    within, out = (_plain(part) for part in goal_intent(goal))
    calls = [
        (i, _plain(r.serves)) for i, r in enumerate(settlement.conclusion.rows, 1) if r.own_call
    ]
    ruled_out = [f"row {i}" for i, words in calls if out and words in out]
    outside = [f"row {i}" for i, words in calls if words not in within]
    if ruled_out:
        refuse("check.settle_crew.check_grounded", "out", kind=Unsettled, rows=", ".join(ruled_out))
    if outside:
        refuse("check.settle_crew.check_grounded", kind=Unsettled, rows=", ".join(outside))


def note_owners(panel: PanelResult) -> dict[int, str]:
    """Who each numbered item was marked for by the member who raised it.

    Numbered in the order `numbered` uses, so N1 is the same item in both.
    """
    return {n: item.settled_by for n, (_, item) in enumerate(panel.items(), 1)}


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
        refuse("check.settle_crew.check_product_calls", kind=Unsettled, rows="; ".join(design))


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
                says(
                    "check.settle_crew.check_covers",
                    "missing",
                    notes="N" + ", N".join(map(str, missing)),
                )
                if missing
                else ""
            )
            + (
                says(
                    "check.settle_crew.check_covers",
                    "unknown",
                    notes="N" + ", N".join(map(str, unknown)),
                )
                if unknown
                else ""
            )
            + says("check.settle_crew.check_covers", "count", count=count),
            rule="check.settle_crew.check_covers",
        )


def _shared(context: PanelContext, *, decisions: bool = True) -> dict[str, str]:
    """What a settle reads the notes or questions against, in the order it reads them."""
    from crew_org.crews.panel_crew import _decided, goal_part, siblings_text  # noqa: PLC0415

    siblings = siblings_text(context)
    return {
        "goal": goal_part(context),
        "decisions": context.decisions if decisions and _decided(context.decisions) else "",
        "project_log": context.project_log,
        "siblings": (
            f"## The other epics under this Goal, with their records\n\n{siblings}"
            if siblings
            else ""
        ),
        "epic": f"## The epic ({context.epic_ref})\n\n{context.epic}",
    }


def _title(role: str) -> str:
    from crew_org.permissions import load_agents  # noqa: PLC0415

    return str(load_agents()[role]["role"])


def opening(context: PanelContext, panel: PanelResult, *, reply: str = "") -> dict[str, str]:
    """The Product Owner's settle as a session's start (crew#807): its step and scope
    first, the notes after what they are read against."""
    views = "".join(
        f"The {_title(role)}'s reader view: {view}\n\n"
        for role, answer in panel.answers.items()
        if (view := answer.view())
    )
    kinds = [item.kind for _, item in panel.items()]
    notes = views + "\n\n".join(
        f"N{n} ({role}, {kind}): {text}"
        for (n, role, text), kind in zip(numbered(panel), kinds, strict=True)
    )
    return {
        "step": "## Your step: settle the panel's notes into the epic's record\n\n"
        "Four roles read this epic before it is split and raised the notes below. What you "
        "settle becomes the epic's record, which the split and every later step read; the "
        "epic's own text is the Sponsor's and stays as it is. Settle each note, in the "
        "fewest words:\n" + _SETTLE_EACH,
        **_shared(context),
        "notes": f"## The panel's notes\n\n{notes or '(none)'}",
        "reply": f"## The Sponsor's answer to your question\n\n{reply}" if reply else "",
    }


# How each note is settled: the one-call task's words, which the session's step keeps.
_SETTLE_EACH = (
    "- **A row** when you can decide it. If the Goal, the project's record, a Sponsor "
    "decision, the project's decision log, a sibling's conclusion or the code answers it, "
    "say what is decided and what follows, and name the source. If none does, decide it "
    "yourself, as the product owner does for the team, by what serves the Goal's reader: "
    "you may leave out or reshape what the Goal mentions. Set `own_call`, say why, and "
    "quote the Goal's words on its reader, or on what they should see or decide, that the "
    "decision serves. It is recorded as your call. It must not contradict a Sponsor "
    "decision or reach into what the Goal rules out. Your own calls are for what the "
    "product does. A note a member marked for the Architect is a design question (a path, "
    "a response shape, a parameter): it is an open question for the design note, unless a "
    "source settles it.\n"
    "- **An open question** when it is a design question. Say who settles it: the "
    "Architect, next and before the split, when the stories' criteria depend on the answer "
    "(a key, a path, a response shape); or the implementer, when any reasonable choice "
    "will do and nothing a test or a reviewer checks depends on it.\n"
    "- **For infra** when a member marked it infra: it is about the deployed runtime "
    "(where it runs, real addresses and secrets, provisioning, backups), which the project "
    "doesn't build. List what infra has to provide, in a few words. It is not a row, and "
    "no story waits on it.\n"
    "- **A dismissal** when it isn't needed: already settled, wholly inside another epic, "
    "or wrong. Say why.\n"
    "When a note shows that serving the Goal's reader needs something the Goal's intent "
    "doesn't cover, the Goal points elsewhere: say so in `elsewhere`, with a question about "
    "the Goal or a Goal you propose, and still settle the note.\n"
    "Every note gets one of the four, and a row may answer several. Ask the Sponsor one "
    "question, with no conclusion, only when you can't tell which way the Goal points, so "
    "that any call you made might contradict it. Don't ask what you can decide, and don't "
    "restate the epic."
)


SETTLE = steps.Step(
    "settle",
    "product_owner",
    Settlement,
    "a conclusion that answers every note, or one question for the Sponsor.",
    "the epic and its notes",
    step="settle",
    # The real data its reader would be shown (crew#846).
    tools=("data",),
)


def settle(
    context: PanelContext,
    panel: PanelResult,
    *,
    reply: str = "",
    reading: steps.Reading | None = None,
) -> Settlement:
    """The Product Owner's settlement of the panel's notes, in a session over the code.

    What was refused after the answer and asked for again (crew#440) is refused at
    `done`, in the same session: a cut cell, an own call the Goal's words don't hold,
    a design question decided as a product call, a note left unanswered.
    """
    count = len(numbered(panel))

    def check(found: Settlement) -> None:
        check_not_cut(found)
        check_grounded(found, context.goal)
        check_product_calls(found, panel)
        check_covers(found, count)

    notes = "\n\n".join(text for _, _, text in numbered(panel))
    answer: Settlement = steps.run(
        SETTLE,
        opening(context, panel, reply=reply),
        reading,
        about=f"{context.epic}\n\n{notes}",
        check=check,
    )
    return answer


# --- the Architect settles the design questions, before the split (ADR 0018) -------------------


class DesignAnswer(BaseModel):
    """One of the record's design questions, decided by the Architect."""

    question: str = Field(description="The question's ID, like Q1")
    decision: str = Field(
        min_length=2,
        max_length=DECISION_CHARS,
        description=(
            "What you decide: the key, the path, the shape, named exactly. A short fragment, "
            f"under {aim(DECISION_CHARS)} characters"
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
            "What you based it on: a file and its definition, a binding row by its ID, the "
            f"Goal's words, or why it is your call. Under {aim(SOURCE_CHARS)} characters"
        ),
    )

    @field_validator("question")
    @classmethod
    def _an_id(cls, value: str) -> str:
        found = value.strip().upper()
        if not re.fullmatch(r"Q\d+", found):
            refuse("form.DesignAnswer.an_id")
        return found


class DesignSettlement(BaseModel):
    settled: list[DesignAnswer] = Field(
        description="Every question the record left for you, answered by its ID"
    )


def check_design_cut(settlement: DesignSettlement) -> None:
    """No cell ran into its limit: a cell at the limit was cut off, not finished."""
    cut = [
        f"{a.question} {name}"
        for a in settlement.settled
        for name, limit in (
            ("decision", DECISION_CHARS),
            ("consequences", CONSEQUENCES_CHARS),
            ("source", SOURCE_CHARS),
        )
        if len(getattr(a, name)) >= limit
    ]
    if cut:
        refuse("check.settle_crew.cut", kind=Unsettled, cells=", ".join(cut))
    _not_cut_at_a_quote(settlement)


def check_design_covers(settlement: DesignSettlement, asked: list[str]) -> None:
    """Every question the record left for the Architect is answered, and only those."""
    answered = [a.question for a in settlement.settled]
    missing = [q for q in asked if q not in answered]
    unknown = sorted(set(answered) - set(asked))
    twice = sorted({q for q in answered if answered.count(q) > 1})
    if missing or unknown or twice:
        raise Unsettled(
            (
                says(
                    "check.settle_crew.check_design_covers", "missing", questions=", ".join(missing)
                )
                if missing
                else ""
            )
            + (
                says(
                    "check.settle_crew.check_design_covers", "unknown", questions=", ".join(unknown)
                )
                if unknown
                else ""
            )
            + (
                says("check.settle_crew.check_design_covers", "twice", questions=", ".join(twice))
                if twice
                else ""
            )
            + says("check.settle_crew.check_design_covers", "asked", questions=", ".join(asked)),
            rule="check.settle_crew.check_design_covers",
        )


def design_opening(context: PanelContext, *, record: str, questions: str) -> dict[str, str]:
    """The Architect's design settle as a session's start (crew#807). The code it was
    shown, focused on the questions, is read through the tools instead."""
    return {
        "step": "## Your step: settle the record's design questions before the split\n\n"
        "The Product Owner settled the panel's notes into the epic's record below and left "
        "these design questions to you. Settle each one now, before the epic is split into "
        "stories, so their criteria are written against your answer: name the key, the path, "
        "the response shape or the parameter exactly, as the code has it or as it will be. "
        "Each answer is your own call as the Architect, within the Goal and the record's "
        "binding rows, and it becomes a binding row the stories follow. Where another epic's "
        "record already decides the same thing, answer with its decision and name its row "
        "as your source. Answer every question by its ID, once. Say what is decided, what "
        "follows for the stories, and what you based it on. The stories and their criteria "
        "are the Business Analyst's, after you.",
        **_shared(context, decisions=False),
        "record": record,
        "questions": f"## The questions the record leaves for you\n\n{questions}",
    }


DESIGN = steps.Step(
    "design settle",
    "architect",
    DesignSettlement,
    "every question the record left you, answered by its ID.",
    "the epic and its record",
    step="refinement",
)


def settle_design(
    context: PanelContext,
    *,
    record: str,
    questions: str,
    asked: list[str],
    reading: steps.Reading | None = None,
) -> DesignSettlement:
    """The Architect's answers to the record's design questions, in a session over the
    code; each question answered once, every cell whole, refused at `done` if not."""

    def check(found: DesignSettlement) -> None:
        check_design_cut(found)
        check_design_covers(found, asked)

    answer: DesignSettlement = steps.run(
        DESIGN,
        design_opening(context, record=record, questions=questions),
        reading,
        about=f"{context.epic}\n\n{record}",
        check=check,
    )
    return answer
