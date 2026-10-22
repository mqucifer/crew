"""The settle step: the Product Owner turns the Goal's look into the Goal's record (crew#858).

The Architect and the UX Designer read a Goal's epics once (`goal_look_crew`). What
the Architect decided and the reader's criteria go into the record as they set
them; what's left (the risks, what's missing for the reader, the calls beyond the
Goal) is the Product Owner's to settle, each item into a row, a question for the
implementer, an item for infra or a dismissal. A model asked to be brief isn't, so
the schema holds each cell short. Or, when the Goal can't tell which way to go, one
question to the Sponsor and no record yet.

The format follows Nygard's ADR fields (the same as the Goal's decision log),
with Example Mapping's question cards for what can't be settled yet. The IDs,
the bottom line and the layout are the code's (`flows/goal_look.py`), not the
model's.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

from crew_org.crews.goal_look_crew import GoalContext, LookResult
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
        min_length=1, description="The numbers of the look's items this answers (N1 is 1)"
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


class ForInfra(BaseModel):
    """Something about the deployed runtime the epic assumes and infra must provide."""

    item: str = Field(
        min_length=2,
        max_length=INFRA_CHARS,
        description=f"What infra has to provide, under {aim(INFRA_CHARS)} characters",
    )
    settles: list[int] = Field(min_length=1, description="The look's items it stands for")


class Dismissal(BaseModel):
    note: int = Field(description="The number of the look's item")
    why: str = Field(
        min_length=2,
        max_length=REASON_CHARS,
        description=(
            f"Why it isn't needed: already settled, outside the Goal, or wrong. "
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


class Unsettled(Refusal):
    """The settlement leaves panel notes unanswered, or answers ones that don't exist."""


def check_not_cut(settlement: GoalSettlement) -> None:
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


def check_grounded(settlement: GoalSettlement, goal: str) -> None:
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


# --- the Product Owner settles the Goal's look, once for the Goal (crew#858) -------------------


class GoalRow(Row):
    """A row of the Goal's record, naming the epics it binds."""

    epics: list[str] = Field(
        min_length=1,
        description="The epics it binds, as owner/repo#number, or `all` for every epic of the Goal",
    )


class GoalQuestion(BaseModel):
    """A question left to whoever builds it: any reasonable choice will do."""

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
    settles: list[int] = Field(min_length=1, description="The look's items it stands for")
    epics: list[str] = Field(min_length=1, description="The epics it concerns, or `all`")


class GoalConclusion(BaseModel):
    rows: list[GoalRow] = Field(default_factory=list)
    open: list[GoalQuestion] = Field(
        default_factory=list,
        description=(
            "Questions for the implementer: nothing a test or a reviewer checks waits on them"
        ),
    )
    for_infra: list[ForInfra] = Field(
        default_factory=list,
        description=(
            "What the epics assume of the deployed runtime that infra provides. Not rows: "
            "no story waits on them"
        ),
    )
    dismissed: list[Dismissal] = Field(default_factory=list)
    elsewhere: Elsewhere | None = Field(
        default=None,
        description=(
            "Where an item shows serving the Goal's reader needs something its intent doesn't "
            "cover: a question for the Sponsor about the Goal, or a Goal you propose. The item "
            "is still settled here"
        ),
    )

    def answered(self) -> set[int]:
        return (
            {n for r in self.rows for n in r.settles}
            | {n for q in self.open for n in q.settles}
            | {n for i in self.for_infra for n in i.settles}
            | {d.note for d in self.dismissed}
        )


class GoalSettlement(BaseModel):
    """The look settled into the Goal's record, or one question for the Sponsor. Never both."""

    conclusion: GoalConclusion | None = None
    sponsor_question: str = Field(
        default="",
        max_length=ASK_CHARS,
        description=(
            "When an item needs a call outside the Goal's intent, or contradicts a row the "
            "Goal's record holds: one question, answerable in a sentence, under "
            f"{aim(ASK_CHARS)} characters. Then give no conclusion"
        ),
    )

    @field_validator("sponsor_question")
    @classmethod
    def _stripped(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _one_or_the_other(self) -> GoalSettlement:
        if (self.conclusion is None) == (not self.sponsor_question):
            refuse("form.GoalSettlement.one_or_the_other")
        return self


def look_items(look: LookResult) -> list[tuple[int, str, str]]:
    """What the look leaves the Product Owner, in a fixed order, as (number, kind, text).

    The Architect's decisions and the reader's criteria aren't here: they go into the
    record as the Architect and the UX Designer set them. What's left is the risks,
    what's missing for the reader, and the calls outside the Goal.
    """
    found: list[tuple[int, str, str]] = []

    def add(kind: str, text: str) -> None:
        found.append((len(found) + 1, kind, text))

    if look.system is not None:
        for c in look.system.callouts:
            add("risk", f"{c.risk} (the Architect; {', '.join(c.epics)})")
    if look.reader is not None:
        for v in look.reader.epics:
            if v.missing.strip().casefold().rstrip(".") not in ("", "nothing", "none"):
                add("missing", f"Missing for the reader of {v.epic}: {v.missing} (the UX Designer)")
    if look.system is not None:
        for b in look.system.beyond_the_goal:
            add("beyond", f"Beyond the Goal: {b.question} {b.why} (the Architect)")
    return found


def check_goal_covers(settlement: GoalSettlement, items: list[tuple[int, str, str]]) -> None:
    """Every item is answered, nothing that isn't, and a call beyond the Goal is asked."""
    if settlement.conclusion is None:
        return
    count = len(items)
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


def check_goal_epics(settlement: GoalSettlement, epics: list[str]) -> None:
    """Every epic a row or question names is one the look read, or `all`."""
    if settlement.conclusion is None:
        return
    named = {e for r in settlement.conclusion.rows for e in r.epics} | {
        e for q in settlement.conclusion.open for e in q.epics
    }
    unknown = sorted(named - set(epics) - {"all"})
    if unknown:
        refuse("check.settle_crew.check_goal_epics", kind=Unsettled, epics=", ".join(unknown))


def goal_opening(
    context: GoalContext, look: LookResult, items: list[tuple[int, str, str]], *, reply: str = ""
) -> dict[str, str]:
    """The Product Owner's settle of the Goal's look, as a session's start."""
    from crew_org.flows.goal_look import render  # noqa: PLC0415

    listed = "\n".join(f"N{n} ({kind}): {text}" for n, kind, text in items)
    later = (
        "The Goal already has a record, below. Its rows hold as they are: settle the new "
        "items beside them, and where an item contradicts a row, that is your one question "
        "for the Sponsor. "
        if context.record
        else ""
    )
    return {
        "step": "## Your step: settle the Goal's look into the Goal's record\n\n"
        "The Architect and the UX Designer read this Goal's approved epics before any is "
        "split. What the Architect decided and the reader's criteria go into the Goal's "
        "record as they set them. The items below are yours to settle, each into a row, a "
        "question for the implementer, an item for infra or a dismissal, naming the epics a "
        "row binds. A row answers what it can from the Goal, the Sponsor's decisions, the "
        "project's log or the code, and names that source; where none does, decide it "
        "yourself by what serves the Goal's reader, set `own_call`, and quote the Goal's "
        "words it serves. A call beyond the Goal is the Sponsor's: ask it, as one question "
        f"with no conclusion. {later}Every item gets one answer, and a row may answer several.",
        "goal": f"## The Goal ({context.goal_ref}), set by the Sponsor\n\n{context.goal}",
        "decisions": context.decisions,
        "project_log": context.project_log,
        "record": f"## The Goal's record\n\n{context.record}" if context.record else "",
        "look": render(look, context),
        "items": f"## The items to settle\n\n{listed or '(none)'}",
        "reply": f"## The Sponsor's answer to your question\n\n{reply}" if reply else "",
    }


GOAL_SETTLE = steps.Step(
    "goal settle",
    "product_owner",
    GoalSettlement,
    "the Goal's record from every item, or one question for the Sponsor.",
    "the Goal's epics and the look",
    step="settle",
)


def settle_goal(
    context: GoalContext,
    look: LookResult,
    *,
    reply: str = "",
    reading: steps.Reading | None = None,
) -> GoalSettlement:
    """The Product Owner's settle of the Goal's look, in a session over the code.

    Refused at `done`, in the session: a cut cell, an own call outside the Goal's
    intent, an item left unanswered, an epic the look didn't read, and a conclusion
    while a call beyond the Goal waits for the Sponsor.
    """
    items = look_items(look)
    beyond = [n for n, kind, _ in items if kind == "beyond"]

    def check(found: GoalSettlement) -> None:
        check_not_cut(found)
        check_grounded(found, context.goal)
        check_goal_covers(found, items)
        check_goal_epics(found, context.looking_at)
        if beyond and not reply and found.conclusion is not None:
            refuse(
                "check.settle_crew.asks_beyond_the_goal",
                kind=Unsettled,
                items="N" + ", N".join(map(str, beyond)),
            )

    answer: GoalSettlement = steps.run(
        GOAL_SETTLE,
        goal_opening(context, look, items, reply=reply),
        reading,
        about=f"{context.goal}\n\n" + "\n".join(text for _, _, text in items),
        check=check,
    )
    return answer
