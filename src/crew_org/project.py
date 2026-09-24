"""A project's onboarding record: `.crew/project.yaml` in the project's repo (#111).

What the Sponsor decided a project is for, how it is released, and what
"done" means there, in one file the project owns. Every phase reads the same
answers instead of inferring them from the code.

Two sections with two owners. `intent` is the Sponsor's, written through the
`crew onboard` interview (#130). `learned` is the crew's, proposed by pull
request with evidence and a date (#112); it is empty until then, and exists now
so adding to it never means migrating the file.

Required, decided on #111 on 2026-09-24: purpose and scope, deployment and
release, definition of done. Build needs and priority are optional.

Loading a record that is missing required answers fails with every missing
answer named in words, all at once. The interview asks about exactly those, and
a person fixing the file by hand is told everything at once, not one validation
error per attempt.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError

RECORD_PATH = ".crew/project.yaml"
FORMAT_VERSION = 1


class Scope(BaseModel):
    """What the project is for, and where its edges are."""

    purpose: str = Field(description="What the project is for, and for whom, in a sentence or two")
    in_scope: list[str] = Field(default_factory=list, description="What it does")
    out_of_scope: list[str] = Field(
        default_factory=list, description="What it deliberately doesn't"
    )


class Release(BaseModel):
    """How the project reaches the people it is for. Answers #90 per project."""

    deploys: bool = Field(description="Whether anything is deployed, or the merge is the end")
    where: str | None = Field(default=None, description="Where it is deployed, if it is")
    how: str | None = Field(default=None, description="How a release happens, if it deploys")

    @property
    def release_is(self) -> str:
        """What counts as a release. A project that doesn't deploy releases on merge."""
        return "a deployment" if self.deploys else "the merge"


class Done(BaseModel):
    """What 'done' means in this project, beyond the crew's own definition."""

    checks: list[str] = Field(
        description="Commands that must pass for a change to be done, e.g. its test suite"
    )
    also: list[str] = Field(
        default_factory=list, description="Anything else done requires here, in words"
    )
    never_touch: list[str] = Field(
        default_factory=list, description="Paths or areas agents must not change"
    )


class Build(BaseModel):
    """Optional: what building and testing the project needs."""

    language: str | None = None
    dependencies: str | None = None
    sandbox: str | None = Field(default=None, description="Anything the sandbox must provide")


class Intent(BaseModel):
    """The Sponsor's answers."""

    scope: Scope
    release: Release
    done: Done
    build: Build | None = None
    priority: int | None = Field(default=None, description="Against other projects; 1 is first")


class Learned(BaseModel):
    """One thing the crew found out about this project (#112). Proposed by PR."""

    fact: str
    evidence: str = Field(description="The retro, issue or escalation it came from")
    found: date


class ProjectRecord(BaseModel):
    version: int = FORMAT_VERSION
    intent: Intent
    learned: list[Learned] = Field(default_factory=list)

    @property
    def release_is(self) -> str:
        return self.intent.release.release_is


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
    ("intent", "done", "checks"): "the checks a change must pass to be done",
}


def missing(raw: Any) -> list[str]:
    """Every required answer the raw record lacks, in words. Empty when complete.

    Checked by hand before validation, because a validator stops describing a
    section the moment the section itself is absent, and the point is to name
    every gap at once.
    """
    gaps: list[str] = []
    for path, words in REQUIRED.items():
        value: Any = raw
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value is None or (isinstance(value, str) and not value.strip()) or value == []:
            gaps.append(words)
    release = ((raw or {}).get("intent") or {}).get("release") if isinstance(raw, dict) else None
    if isinstance(release, dict) and release.get("deploys") is True and not release.get("where"):
        gaps.append("where it is deployed, since it deploys")
    return gaps


def parse(text: str) -> ProjectRecord:
    """A record from the file's text, or `ProjectRecordError` naming what is wrong."""
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ProjectRecordError([f"{RECORD_PATH} is not valid YAML: {exc}"]) from None
    if not isinstance(raw, dict):
        raise ProjectRecordError([f"{RECORD_PATH} must be a mapping, not {type(raw).__name__}"])

    gaps = missing(raw)
    if gaps:
        raise ProjectRecordError([f"missing: {gap}" for gap in gaps])
    try:
        return ProjectRecord.model_validate(raw)
    except ValidationError as exc:
        raise ProjectRecordError(
            [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()]
        ) from None


def render(record: ProjectRecord) -> str:
    """The file's text, for `crew onboard` to write. Optional sections left out if unset."""
    data = record.model_dump(mode="json", exclude_none=True)
    return (
        "# The project's onboarding record (#111). Intent is the Sponsor's, written\n"
        "# through `crew onboard`; learned is proposed by the crew, by pull request.\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    )
