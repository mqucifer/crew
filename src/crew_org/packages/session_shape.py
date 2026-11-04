"""The shape of one session as sprint-metrics takes it (crew#1000, sent by crew#1001).

**Each session carries its own model time and tokens, and sprint-metrics adds them up**
(the Sponsor, 2026-11-01). The crew sends no totals (ADR 0022, crew#963).
`sprint_metrics_feed` builds each session from the record and sends it in this shape:
sprint-metrics 4.5.0's `session` event (its `docs/service.md`), which named the fields
its own way. 2.0.0 is that shape. 1.0.0 was the crew's proposal, which nothing sent.

**A session is one role's one go at one card or pull request:** from its `work_started`
to its `work_finished`. Three more kinds of event make one, so that every model call
with a measured time lands in exactly one session:
- a named step's session with no work event of its own around it (the criteria check, a
  panel's member, a Goal look's): its `step_started` to its `step_ended`.
- a `work_started` with no `work_finished`: it finished at its last `session_ended`, or
  its finish is unknown.
- a model call no other session holds (the retro, backlog order, a project's design): it
  started its `seconds` before its `at`, and is a session of its own.

**A session's model time and tokens are its model calls':** those by its role inside its
span, on its card or on no card. Nothing else is worked out here. A session that can't
be placed (between sprints, on a card the history doesn't know, by no role, or with no
recorded finish, whose minutes are unknown, never zero) is left out and counted, as the
feed does with any event.

**sprint-metrics tells planning from building** by the story's first `started`, so no
`part` is sent, and an epic's sessions add up by the `epic` its stories' board events
carry. The story's kind isn't here: nothing in the record says addition or change.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from crew_org.packages import contract
from crew_org.packages import delivery_history as dh

CONTRACT = contract.ROOT / "contracts" / "session"
SCHEMA = "session.schema.json"
EXAMPLES = "examples"
# MAJOR when a consumer could break on the change, MINOR when it only adds (ADR 0021).
VERSION = "2.0.0"

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
_Count = Annotated[int, Field(ge=0)]


class SessionCard(dh._Part):
    """The card a session worked on, as every event carries it."""

    created: date = Field(description="The day the card was filed, as on every event.")
    created_at: datetime = Field(description="The time it was filed.")


class SessionEvent(dh._Part):
    """What happened in one session: its card, role, start, end, model time and tokens.

    What's unknown is left off, never sent as zero: the card for a session on none, and
    the model time and tokens of a session with no call that recorded them. sprint-metrics
    holds a session once by its card (or none), role, start and end.
    """

    # What's None is left off the event, so the schema says it may be absent.
    model_config = ConfigDict(
        extra="forbid", frozen=True, json_schema_serialization_defaults_required=False
    )

    api_version: Literal["1"] = Field(description="The intake's version.")
    type: Literal["session"] = Field(description="sprint-metrics 4.5.0's session event.")
    card_id: _Reference | None = Field(
        default=None,
        description="The card, as `repo#number`. A review's card is the one its pull request "
        "closes, or the one its event names when it closes none. Absent for a session on no "
        "card, such as a retro or a review of a package bump.",
    )
    timestamp: datetime = Field(description="When the session started, as `start` says.")
    sprint: str = Field(description="The sprint whose days it started in.", min_length=1)
    card: SessionCard | None = Field(default=None, description="Absent exactly when card_id is.")
    role: Role = Field(description="The role that worked. A later MINOR may add roles.")
    start: datetime = Field(description="When it started.")
    end: datetime = Field(description="When it finished.")
    model_seconds: _Count | None = Field(
        default=None,
        description="Its model calls' time, in whole seconds. A call with no time recorded "
        "adds nothing; absent when no call of it has one.",
    )
    input_tokens: _Count | None = Field(
        default=None, description="Its model calls' prompt tokens; absent as for the time."
    )
    output_tokens: _Count | None = Field(
        default=None, description="Its model calls' completion tokens; absent as for the time."
    )

    @model_validator(mode="after")
    def _says_what_it_can(self) -> SessionEvent:
        if (self.card_id is None) != (self.card is None):
            raise ValueError("a session has both card_id and card, or neither")
        if self.end < self.start:
            raise ValueError("a session can't finish before it started")
        if self.timestamp != self.start:
            raise ValueError("a session's timestamp is its start")
        return self

    def sent(self) -> dict[str, Any]:
        """The event's body, as sent: what's unknown left off."""
        body: dict[str, Any] = self.model_dump(mode="json", exclude_none=True)
        return body


# Each field and the field of the record it's read from, so the shape carries what the
# record has and nothing it would have to invent.
SOURCES: dict[str, tuple[type[BaseModel], str]] = {
    "card_id": (dh.WorkStarted, "card"),
    "timestamp": (dh.WorkStarted, "at"),
    "sprint": (dh.Period, "sprint"),
    "card": (dh.WorkStarted, "card"),
    "card.created": (dh.Card, "created"),
    "card.created_at": (dh.Card, "created"),
    "role": (dh.WorkStarted, "role"),
    "start": (dh.WorkStarted, "at"),
    "end": (dh.WorkFinished, "at"),
    "model_seconds": (dh.ModelCall, "seconds"),
    "input_tokens": (dh.ModelCall, "prompt_tokens"),
    "output_tokens": (dh.ModelCall, "completion_tokens"),
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


def _session(
    role: Role,
    *,
    at: int,
    took: int,
    card_id: str | None = None,
    model: tuple[int, int, int] | None = None,
) -> SessionEvent:
    """A session `at` minutes into the night, which `took` minutes.

    `model` is its seconds, then its input and output tokens.
    """
    start = _T + timedelta(minutes=at)
    seconds, given, answered = model or (None, None, None)
    return SessionEvent(
        api_version="1",
        type="session",
        card_id=card_id,
        timestamp=start,
        sprint="Sprint 6",
        card=SessionCard(created=_FILED.date(), created_at=_FILED) if card_id else None,
        role=role,
        start=start,
        end=start + timedelta(minutes=took),
        model_seconds=seconds,
        input_tokens=given,
        output_tokens=answered,
    )


def examples() -> dict[str, SessionEvent]:
    """A session of each role, and each case the shape has to say something about."""
    return {
        "product-owner-on-a-goal": _session(
            "Product Owner", at=-180, took=6, card_id="sprint-metrics#101", model=(215, 61200, 2410)
        ),
        "product-owner-with-no-recorded-calls": _session(
            "Product Owner", at=-170, took=2, card_id=_EPIC
        ),
        "architect-on-no-card": _session("Architect", at=-160, took=14, model=(402, 118400, 5120)),
        "ux-designer-on-an-epic": _session(
            "UX Designer", at=-140, took=21, card_id=_EPIC, model=(612, 201000, 7640)
        ),
        "business-analyst-on-an-epic": _session(
            "Business Analyst", at=-110, took=9, card_id=_EPIC, model=(388, 152700, 6050)
        ),
        "qa-engineer-checking-criteria-on-an-epic": _session(
            "QA Engineer", at=-108, took=4, card_id=_EPIC, model=(176, 80300, 1880)
        ),
        "developer-on-a-story": _session(
            "Developer", at=0, took=12, card_id=_STORY, model=(413, 1510300, 11820)
        ),
        "code-reviewer-on-a-story": _session(
            "Code Reviewer", at=14, took=2, card_id=_STORY, model=(71, 6240, 1760)
        ),
        "code-reviewer-on-a-pull-request-with-no-card": _session(
            "Code Reviewer", at=20, took=1, model=(22, 3120, 410)
        ),
        "qa-engineer-on-a-story": _session(
            "QA Engineer", at=17, took=8, card_id=_STORY, model=(342, 388200, 5630)
        ),
        "devops-engineer-on-a-task": _session(
            "DevOps Engineer", at=40, took=11, card_id="qa-suite#55", model=(240, 512900, 4120)
        ),
        "scrum-master-on-a-retro": _session(
            "Scrum Master", at=300, took=4, model=(240, 40100, 2600)
        ),
    }


def example_files() -> dict[str, str]:
    """Each example's file name and its text, as written."""
    return {
        f"{name}.json": json.dumps(event.sent(), indent=2) + "\n"
        for name, event in examples().items()
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
