"""A project's onboarding record: `.crew/project.yaml` in the project's repo (#111).

What the Sponsor decided a project is for, how it is released, and what
"done" means there, in one file the project owns. Every phase reads the same
answers instead of inferring them from the code.

Three sections with three owners (#143):

- `intent` is the Sponsor's, written through the `crew onboard` interview
  (#130): purpose and scope, what a release is, the bar for done in words,
  the project's own guidelines on top of the crew-wide ones (constitution §19),
  and what agents must not touch.
- `design` is the project's Architect's, proposed by pull request (#144): the
  language, build and sandbox needs, the commands that enforce done, and how a
  release happens. The Sponsor sets the rules; the Architect picks the tools.
- `learned` is the crew's, proposed by pull request with evidence and a date
  (#112).

Required, decided on #111 and revised on #143: purpose, whether it deploys,
and the definition of done in words. Everything in `design` is optional here,
because a project can be onboarded before it has an Architect's answer, or
any code at all.

Loading a record that is missing required answers fails with every missing
answer named in words, all at once. An unknown field, or a file in an older
format, is refused by name rather than read with parts of it silently dropped.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

RECORD_PATH = ".crew/project.yaml"
# 2 split the Architect's `design` out of the Sponsor's `intent` (#143).
FORMAT_VERSION = 2


class Section(BaseModel):
    # A field this crew does not know is a typo or a newer format, and either
    # way reading past it would drop an answer without a word.
    model_config = ConfigDict(extra="forbid")


class Scope(Section):
    """What the project is for, and where its edges are."""

    purpose: str = Field(description="What the project is for, and for whom, in a sentence or two")
    in_scope: list[str] = Field(default_factory=list, description="What it does")
    out_of_scope: list[str] = Field(
        default_factory=list, description="What it deliberately doesn't"
    )


class Release(Section):
    """What counts as the project reaching the people it is for. Answers #90 per project."""

    deploys: bool = Field(description="Whether anything is deployed, or the merge is the end")
    where: str | None = Field(default=None, description="Where it is deployed, if it is")

    @property
    def release_is(self) -> str:
        """What counts as a release. A project that doesn't deploy releases on merge."""
        return "a deployment" if self.deploys else "the merge"


class Done(Section):
    """The Sponsor's bar for 'done' in this project. The commands enforcing it are design."""

    bar: str = Field(description="What must be true for a change to count as done, in words")
    also: list[str] = Field(
        default_factory=list, description="Anything else done requires here, in words"
    )
    never_touch: list[str] = Field(
        default_factory=list, description="Paths or areas agents must not change"
    )


class Intent(Section):
    """The Sponsor's answers."""

    scope: Scope
    release: Release
    done: Done
    guidelines: list[str] = Field(
        default_factory=list,
        description=(
            "This project's own rules, on top of the crew-wide ones (constitution §19). "
            "They can add to those, never relax one"
        ),
    )
    priority: int | None = Field(default=None, description="Against other projects; 1 is first")


class Design(Section):
    """The Architect's choices for this project (#144). Proposed by pull request."""

    language: str | None = None
    dependencies: str | None = None
    sandbox: str | None = Field(default=None, description="Anything the sandbox must provide")
    checks: list[str] = Field(
        default_factory=list, description="The commands that enforce the definition of done"
    )
    release_how: str | None = Field(default=None, description="How a release happens")


class Learned(Section):
    """One thing the crew found out about this project (#112). Proposed by PR."""

    fact: str
    evidence: str = Field(description="The retro, issue or escalation it came from")
    found: date


class ProjectRecord(Section):
    version: int = FORMAT_VERSION
    intent: Intent
    design: Design | None = None
    learned: list[Learned] = Field(default_factory=list)

    @property
    def release_is(self) -> str:
        return self.intent.release.release_is

    @property
    def checks(self) -> list[str]:
        """The commands a change must pass here, from the Architect's design.

        Raises rather than returning nothing: a phase that ran no checks because
        none were defined would report a change as done that nothing verified.
        """
        if self.design is None or not self.design.checks:
            raise ProjectRecordError(
                ["no checks are defined: the record has no design section with checks yet (#144)"]
            )
        return self.design.checks


class ProjectRecordError(ValueError):
    """A record that cannot be used, with every reason named."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


# The required answers, in the words the interview and a person reading the
# error use. Keyed by where each lives in the file.
REQUIRED: dict[tuple[str, ...], str] = {
    ("intent", "scope", "purpose"): "what the project is for (purpose)",
    ("intent", "release", "deploys"): "whether it deploys, or the merge is the release",
    ("intent", "done", "bar"): "what must be true for a change to count as done",
}


WHERE = ("intent", "release", "where")
WHERE_WORDS = "where it is deployed, since it deploys"


def lookup(raw: Any, path: tuple[str, ...]) -> Any:
    """The value at `path` in a raw record, or None if any step is absent."""
    value: Any = raw
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def gaps(raw: Any) -> dict[tuple[str, ...], str]:
    """Every required answer the raw record lacks, by where it lives, in words."""
    found: dict[tuple[str, ...], str] = {}
    for path, words in REQUIRED.items():
        value = lookup(raw, path)
        if value is None or (isinstance(value, str) and not value.strip()) or value == []:
            found[path] = words
    if lookup(raw, WHERE[:-1] + ("deploys",)) is True and not lookup(raw, WHERE):
        found[WHERE] = WHERE_WORDS
    return found


def missing(raw: Any) -> list[str]:
    """Every required answer the raw record lacks, in words. Empty when complete.

    Checked by hand before validation, because a validator stops describing a
    section the moment the section itself is absent, and the point is to name
    every gap at once.
    """
    return list(gaps(raw).values())


def load_raw(text: str) -> dict[str, Any]:
    """A record's text as a mapping, however incomplete. For a draft being finished."""
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ProjectRecordError([f"{RECORD_PATH} is not valid YAML: {exc}"]) from None
    if not isinstance(raw, dict):
        raise ProjectRecordError([f"{RECORD_PATH} must be a mapping, not {type(raw).__name__}"])
    version = raw.get("version", FORMAT_VERSION)
    if version != FORMAT_VERSION:
        raise ProjectRecordError(
            [
                f"{RECORD_PATH} is format version {version}; this crew reads version "
                f"{FORMAT_VERSION}. Version 1 kept the Architect's choices (build, check "
                "commands, how a release happens) in the Sponsor's intent; they now live "
                "in `design` (#143)."
            ]
        )
    return raw


def parse(text: str) -> ProjectRecord:
    """A record from the file's text, or `ProjectRecordError` naming what is wrong."""
    return validate(load_raw(text))


def validate(raw: dict[str, Any]) -> ProjectRecord:
    """A record from its raw mapping, or `ProjectRecordError` naming what is wrong."""
    absent = missing(raw)
    if absent:
        raise ProjectRecordError([f"missing: {gap}" for gap in absent])
    try:
        return ProjectRecord.model_validate(raw)
    except ValidationError as exc:
        raise ProjectRecordError(
            [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()]
        ) from None


def _answered(data: Any) -> Any:
    """`data` without its empty lists and sections.

    An empty list reads as an answer: `never_touch: []` says agents may touch
    anything, and nobody said that. Unasked is not the same as none, so it is
    left out, and reads back as the same default.
    """
    if isinstance(data, dict):
        kept = {k: _answered(v) for k, v in data.items()}
        return {k: v for k, v in kept.items() if v not in ([], {})}
    return data


def render(record: ProjectRecord) -> str:
    """The file's text, for `crew onboard` to write. Unanswered optional answers left out."""
    data = _answered(record.model_dump(mode="json", exclude_none=True))
    return (
        "# The project's onboarding record (#111). Intent is the Sponsor's, written\n"
        "# through `crew onboard`. Design is the project's Architect's, and learned\n"
        "# is the crew's; both are proposed by pull request.\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    )
