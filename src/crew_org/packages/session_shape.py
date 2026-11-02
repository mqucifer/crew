"""The shape of one session as sprint-metrics will get it, published first (crew#1000).

**Each session carries its own model time and tokens, and sprint-metrics adds them up**
(the Sponsor, 2026-11-01). The crew sends no totals (ADR 0022, crew#963). This is the
contract and its examples only: mqucifer/sprint-metrics#935 builds against it and
mqucifer/crew#1001 sends it. Nothing here is sent, because the intake refuses an event
type it doesn't know.

**A session is one role's one go at one card or pull request:** from its `work_started`
to its `work_finished`. Three more kinds of event make one, so that every model call
with a measured time lands in exactly one session:
- a named step's session, which has no work event of its own (the criteria check, a
  panel's member, a Goal look's): its `session.started` to its `session.ended`. The
  package leaves these out today, so crew#1001 adds them as new event kinds, a MINOR 3.1.0.
- a `work_started` with no `work_finished`: it finished at its last `session_ended`, or
  its finish is unknown.
- a model call no other session holds (the retro, backlog order, a project's design): it
  started its `seconds` before its `at`, and is a session of its own.

**A session's model time and tokens are its model calls':** those by its role inside its
span, on its card or on no card. Nothing else is worked out here. A session that can't
be placed (between sprints, on a card the history doesn't know, or by no role) is left
out and counted, as the feed does with any event.

**The story's kind isn't here.** Nothing in the record says addition or change, so
`cost_by_kind` stays empty until something decides who says so. A guess isn't a measure.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from crew_org.packages import contract
from crew_org.packages import delivery_history as dh

CONTRACT = contract.ROOT / "contracts" / "session"
SCHEMA = "session.schema.json"
EXAMPLES = "examples"
# MAJOR when a consumer could break on the change, MINOR when it only adds (ADR 0021).
VERSION = "1.0.0"

# The roles a session can be. "Refinement panel" and "Goal look" write a `work_started`
# for the whole group; its members' sessions carry their own roles.
Role = Literal[
    "Product Owner",
    "Business Analyst",
    "Architect",
    "UX Designer",
    "QA Engineer",
    "Developer",
    "Code Reviewer",
    "DevOps Engineer",
    "Scrum Master",
]
# Repository and number, as the feed writes `card_id`. Issues and pull requests share one
# sequence in a repository, so this can't name two things.
_Reference = Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]+#[0-9]+$")]
_Tokens = Annotated[int, Field(ge=0)]


class SessionCard(dh._Part):
    """The card a session worked on, as far as sprint-metrics needs to read it."""

    created: date = Field(description="The day the card was filed, as on every event.")
    created_at: datetime = Field(description="The time it was filed.")
    type: Literal["Goal", "Epic", "Story", "Bug"] | None = Field(
        description="The card's type on the board. None for a role's Task, which has none."
    )


class SessionEvent(dh._Part):
    """What happened in one session: its card, role, start, finish, model time and tokens.

    The same identity as every event the intake takes, with the role added: a re-sent
    session is the same `card_id` (or none), `type`, `timestamp` and `role`. Two members
    of a panel can start on one card in the same moment.
    """

    api_version: Literal["1"] = Field(description="The intake's version.")
    type: Literal["session"] = Field(description="The new event type mqucifer/crew#1001 sends.")
    card_id: _Reference | None = Field(
        description="The card, as `repo#number`. A review's card is the one its pull request "
        "closes, when it closes exactly one. None when there's no card to name: a pull request "
        "that closes none (`pull` says which), or a session on no card, such as a retro."
    )
    timestamp: datetime = Field(description="When the session started.")
    sprint: str = Field(description="The sprint whose days it started in.", min_length=1)
    card: SessionCard | None = Field(description="None exactly when `card_id` is.")
    epic: _Reference | None = Field(
        description="The epic the card's split from, as `repo#number`, so a story's sessions "
        "add up to its epic. None for a card with no parent, as a Bug has."
    )
    pull: _Reference | None = Field(
        description="The pull request a review worked on, as `repo#number`. None for other work."
    )
    role: Role = Field(description="The role that worked. A later MINOR may add roles.")
    part: Literal["refinement", "delivery"] | None = Field(
        description="Planning or building, by the step of the crew its model calls were for. "
        "`refinement`: refine, design, panel, goal look, order, revisit. `delivery`: deliver, "
        "session, qa, review, deploy review, resolution. None: the retro, which is neither, or "
        "a session whose calls name no step, or that has none recorded."
    )
    finished: datetime | None = Field(
        description="When it finished. None when the record has no finish: the tick stopped "
        "partway, so its minutes are unknown, never zero."
    )
    model_seconds: float | None = Field(
        ge=0,
        description="Its model calls' time, in seconds. A call with no time recorded adds "
        "nothing. None, never zero, when no call of it has one.",
    )
    prompt_tokens: _Tokens | None = Field(description="Tokens sent; None as for the time.")
    cached_tokens: _Tokens | None = Field(
        description="Of its prompt tokens, those the server's prefix cache held."
    )
    completion_tokens: _Tokens | None = Field(description="Tokens answered.")
    reasoning_tokens: _Tokens | None = Field(description="Of those, the ones spent reasoning.")

    @model_validator(mode="after")
    def _says_what_it_can(self) -> SessionEvent:
        if (self.card_id is None) != (self.card is None):
            raise ValueError("a session has both card_id and card, or neither")
        if self.finished is not None and self.finished < self.timestamp:
            raise ValueError("a session can't finish before it started")
        return self


# Each field and the field of the record it's read from, so the shape carries what the
# record has and nothing it would have to invent. `part` reads a call's `step`.
SOURCES: dict[str, tuple[type[BaseModel], str]] = {
    "card_id": (dh.WorkStarted, "card"),
    "timestamp": (dh.WorkStarted, "at"),
    "sprint": (dh.Period, "sprint"),
    "card": (dh.WorkStarted, "card"),
    "card.created": (dh.Card, "created"),
    "card.created_at": (dh.Card, "created"),
    "card.type": (dh.Card, "type"),
    "epic": (dh.Card, "parent"),
    "pull": (dh.WorkStarted, "pull"),
    "role": (dh.WorkStarted, "role"),
    "part": (dh.ModelCall, "step"),
    "finished": (dh.WorkFinished, "at"),
    "model_seconds": (dh.ModelCall, "seconds"),
    "prompt_tokens": (dh.ModelCall, "prompt_tokens"),
    "cached_tokens": (dh.ModelCall, "cached_tokens"),
    "completion_tokens": (dh.ModelCall, "completion_tokens"),
    "reasoning_tokens": (dh.ModelCall, "reasoning_tokens"),
}
# What the intake itself asks of every event, which no record holds.
INTAKE_ONLY = frozenset({"api_version", "type"})


def schema() -> dict[str, Any]:
    """The session's schema, as sprint-metrics reads it."""
    return SessionEvent.model_json_schema(mode="serialization")


# --- the examples: their fields are real, their values made up, as in the mock ---------------

# 18:30 on Sprint 6's day in Chicago, the mock's night.
_T = datetime(2026, 9, 25, 23, 30, tzinfo=UTC)
_FILED = _T - timedelta(days=2)
_EPIC = "sprint-metrics#120"
_STORY = "sprint-metrics#127"


def _card(type_: Literal["Goal", "Epic", "Story", "Bug"] | None) -> SessionCard:
    return SessionCard(created=_FILED.date(), created_at=_FILED, type=type_)


def _session(
    role: Role,
    part: Literal["refinement", "delivery"] | None,
    *,
    at: int,
    took: int | None,
    card_id: str | None = None,
    type_: Literal["Goal", "Epic", "Story", "Bug"] | None = None,
    epic: str | None = None,
    pull: str | None = None,
    model: tuple[float, int, int, int, int] | None = None,
) -> SessionEvent:
    """A session `at` minutes into the night, which `took` minutes, or never recorded a finish.

    `model` is its seconds, then its prompt, cached, completion and reasoning tokens.
    """
    start = _T + timedelta(minutes=at)
    seconds, prompt, cached, completion, reasoning = model or (None, None, None, None, None)
    return SessionEvent(
        api_version="1",
        type="session",
        card_id=card_id,
        timestamp=start,
        sprint="Sprint 6",
        card=_card(type_) if card_id else None,
        epic=epic,
        pull=pull,
        role=role,
        part=part,
        finished=None if took is None else start + timedelta(minutes=took),
        model_seconds=seconds,
        prompt_tokens=prompt,
        cached_tokens=cached,
        completion_tokens=completion,
        reasoning_tokens=reasoning,
    )


def examples() -> dict[str, SessionEvent]:
    """A session of each role, and each case the shape has to say something about."""
    return {
        "product-owner-on-a-goal": _session(
            "Product Owner",
            "refinement",
            at=-180,
            took=6,
            card_id="sprint-metrics#101",
            type_="Goal",
            model=(214.5, 61200, 48800, 2410, 1120),
        ),
        "product-owner-with-no-recorded-calls": _session(
            "Product Owner", None, at=-170, took=2, card_id=_EPIC, type_="Epic"
        ),
        "architect-on-no-card": _session(
            "Architect", "refinement", at=-160, took=14, model=(402.1, 118400, 90300, 5120, 2980)
        ),
        "ux-designer-on-an-epic": _session(
            "UX Designer",
            "refinement",
            at=-140,
            took=21,
            card_id=_EPIC,
            type_="Epic",
            model=(611.8, 201000, 170900, 7640, 3310),
        ),
        "business-analyst-on-an-epic": _session(
            "Business Analyst",
            "refinement",
            at=-110,
            took=9,
            card_id=_EPIC,
            type_="Epic",
            model=(388.2, 152700, 121400, 6050, 2260),
        ),
        "qa-engineer-checking-criteria-on-an-epic": _session(
            "QA Engineer",
            "refinement",
            at=-108,
            took=4,
            card_id=_EPIC,
            type_="Epic",
            model=(176.0, 80300, 66100, 1880, 940),
        ),
        "developer-on-a-story": _session(
            "Developer",
            "delivery",
            at=0,
            took=12,
            card_id=_STORY,
            type_="Story",
            epic=_EPIC,
            model=(412.6, 1510300, 1462800, 11820, 5940),
        ),
        "developer-with-no-recorded-finish": _session(
            "Developer",
            "delivery",
            at=30,
            took=None,
            card_id="sprint-metrics#128",
            type_="Story",
            epic=_EPIC,
            model=(92.4, 310200, 288100, 3340, 1720),
        ),
        "code-reviewer-on-a-story": _session(
            "Code Reviewer",
            "delivery",
            at=14,
            took=2,
            card_id=_STORY,
            type_="Story",
            epic=_EPIC,
            pull="sprint-metrics#140",
            model=(71.3, 6240, 0, 1760, 1130),
        ),
        "code-reviewer-on-a-pull-request-with-no-card": _session(
            "Code Reviewer",
            "delivery",
            at=20,
            took=1,
            pull="sprint-metrics#141",
            model=(21.6, 3120, 0, 410, 220),
        ),
        "qa-engineer-on-a-story": _session(
            "QA Engineer",
            "delivery",
            at=17,
            took=8,
            card_id=_STORY,
            type_="Story",
            epic=_EPIC,
            model=(341.9, 388200, 362700, 5630, 2810),
        ),
        "devops-engineer-on-a-task": _session(
            "DevOps Engineer",
            "delivery",
            at=40,
            took=11,
            card_id="qa-suite#55",
            model=(240.3, 512900, 480200, 4120, 1880),
        ),
        "scrum-master-on-a-retro": _session(
            "Scrum Master", None, at=300, took=4, model=(240.0, 40100, 0, 2600, 1400)
        ),
    }


def example_files() -> dict[str, str]:
    """Each example's file name and its text, as written."""
    return {
        f"{name}.json": event.model_dump_json(indent=2) + "\n" for name, event in examples().items()
    }


def write(directory: Path = CONTRACT) -> list[Path]:
    """Write the schema, its version's fingerprint and the examples, replacing the old ones."""
    written = contract.write(directory, {SCHEMA: schema()}, VERSION)
    target = directory / EXAMPLES
    target.mkdir(parents=True, exist_ok=True)
    for old in target.glob("*.json"):
        old.unlink()
    for name, text in example_files().items():
        (target / name).write_text(text, encoding="utf-8")
        written.append(target / name)
    return written
