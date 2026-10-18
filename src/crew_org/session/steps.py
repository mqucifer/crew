"""Refinement in a session: each step that states facts about code reads the code (crew#807).

The Sponsor, on crew#807: the refinement panel, the Product Owner's decomposition and
settle, the Architect's design settle, the Business Analyst's split and the UX
Designer's criteria each work as the Developer does (ADR 0032). A step is one
conversation of tool calls over the project's default branch, read-only, and it ends
with the step's own answer form as `done`. No step edits or runs anything.

What a step starts with is what it decides from (the Goal, the epic, its siblings, the
Sponsor's decisions and the record), with the session's map and hints; the repository
itself is read through the tools. A part another step writes, such as the panel's
notes, is passed in as it's written. This module says what each step is told; the
loop, its tools and its records are the Developer's (`conversation`, `tools`).
"""

from __future__ import annotations

import copy
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from crew_org import rulebook
from crew_org.calls import role_spec
from crew_org.cut_text import check_not_cut_at_a_quote
from crew_org.events import EventKind, record
from crew_org.project import RECORD_PATH, ProjectRecordError, brief, read_record
from crew_org.session import signals
from crew_org.session.conversation import COMPACT_AT, QUIET_LIMIT, Ending, Session
from crew_org.session.git import OUTPUT_CHARS
from crew_org.session.reading import ReadTools
from crew_org.session.signals import Signals
from crew_org.session.start import hints, project_map
from crew_org.session.tools import SPECS, VIEW_LINES, _spec

# One step's model time, set high to start (the Sponsor, crew#807): about three times the
# longest refinement step the log holds, a split's 43 minutes on 2026-10-15.
BACKSTOP_S = 2 * 60 * 60
# Calls in a row with no edit and no run, which for a step that never edits or runs is
# every call: the Sponsor's 100, not the Developer's 60 (crew#807). 50 views of one file
# are the Developer's.
READING_CALLS = 100
QUIET = "Call a tool to go on, or `done` with your answer."
REFUSED_DONE = (
    "`done` was refused, and the session goes on. Fix each of these, then call `done` again."
)
NOTES = "@notes/"
DELIVERED_NOTE = f"{NOTES}delivered.md"

# The tools a reading step has: the Developer's, less what edits or runs.
READS = ("view", "search", "find", "tests_for")


def reading_specs(head: str) -> list[dict[str, Any]]:
    """The read tools, as a step is given them, over the default branch at `head`.

    `git` is the Developer's tool (mqucifer/crew#808), told without what a reading
    step doesn't have: its branch, `base` and `restore`.
    """
    specs = [copy.deepcopy(s) for s in SPECS if s["function"]["name"] in READS]
    for spec in specs:
        if spec["function"]["name"] == "search":
            spec["function"]["description"] += " The notes this start names are searched too."
    specs.append(
        _spec(
            "git",
            "Run one git command line, as you'd type it, with no shell: `status`, `diff`, "
            "`log`, `show`, `blame` and `grep`, which read. The checkout is the default "
            f"branch at `{head[:12]}`; another branch is `origin/<name>`. `git log --oneline -- "
            "<path>` shows what changed a "
            "file, and `git log -S<text>` when a line came or went. Nothing else runs, nor "
            "an option that writes a file, reads outside the repository or runs a program. "
            f"Output past {OUTPUT_CHARS} characters is cut.",
            {"command": {"type": "string"}},
            ["command"],
        )
    )
    return specs


def cite(head: str) -> str:
    """How an answer cites the code (the Sponsor, crew#807): a path and definition first,
    since line numbers drift as stories land, with the line as at main's commit."""
    return (
        "Where your answer states a fact about the code, it says where you saw it: a path "
        "and definition first, with the line as at main's commit, such as "
        f"`src/sprint_metrics/thresholds.py` `_flag` (line 117 at {head[:7]}); a test, "
        "`path::name`; or a commit. The next step checks it with `view`."
    )


def done_spec(form: type[BaseModel], said: str, *, head: str) -> dict[str, Any]:
    """`done` as a step's answer form: its fields, each with the rules that check it.

    The rules are told where `calls.form_text` tells them in a one-call form (ADR 0031,
    policy 2), in the description of the field they check. `head` is the commit the
    step reads, for how it cites the code.
    """
    schema = form.model_json_schema()
    title = str(schema.get("title", form.__name__))
    _tell(schema, title, whole=False)
    for name, model in (schema.get("$defs") or {}).items():
        _tell(model, name)
    # The form's own rules, which check it whole, are said with what it is.
    told = rulebook.told(title).get("", [])
    description = " ".join([f"Your answer, which ends the session: {said}", *told, cite(head)])
    return {
        "type": "function",
        "function": {"name": "done", "description": description, "parameters": _untitled(schema)},
    }


def _untitled(schema: Any) -> Any:
    """The schema without pydantic's `title`s, which repeat each field's name. A field
    named `title`, such as a story's, stays."""
    if isinstance(schema, list):
        return [_untitled(v) for v in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        k: (
            {n: _untitled(f) for n, f in v.items()}
            if k in ("properties", "$defs")
            else _untitled(v)
        )
        for k, v in schema.items()
        if k != "title"
    }


def _tell(model: dict[str, Any], name: str, *, whole: bool = True) -> None:
    """Each field's description followed by the rules that check it, and a nested
    form's own rules after its description."""
    told = rulebook.told(name)
    for field, spec in (model.get("properties") or {}).items():
        if rules := told.get(field, []):
            spec["description"] = ". ".join(
                p for p in (str(spec.get("description") or "").rstrip("."), " ".join(rules)) if p
            )
    if whole and told.get(""):
        model["description"] = " ".join(
            p for p in (str(model.get("description") or ""), *told[""]) if p
        )


def system_text(role: str, step: str | None = None) -> str:
    """Who the role is, as the Developer's session says it: the role, its backstory and
    its goal, a step's own over them. The constitution's sections aren't sent: the
    form's descriptions and the step's own section say what it's held to."""
    spec = role_spec(role, step)
    return (
        f"You are {spec['role']}. {spec['backstory'].strip()}\n\nYour goal: {spec['goal'].strip()}"
    )


RULES = """\
## How this session works

You work by calling tools, one or more per turn, until you call `done` with your answer. \
Nothing is shown to you that you didn't ask for, beyond this start.

- **The repository** is {repo} at its default branch, commit `{head}`. You read it; \
nothing here changes or runs it.
- **See and find:** `view` shows part of a file with line numbers ({view_lines} lines \
at most, or one Python definition by name), or a note this start names. `search` finds \
text across the repository and the notes. `find` and `tests_for` are Python only. `git` \
runs one git command that reads, such as `log` or `blame`.
- What the Goal, an epic, a note or a row says the code *does today* can be out of date \
or wrong: check it in the code. What they *decide* is binding.
- **Tool output** stays in the conversation whole, unless it grows very long.
- **`done`** is your answer, in its form. If it's refused, the reply says why: fix that \
and call `done` again.
- The session ends after {calls} calls in a row without `done`, or after {hours} hours of \
model time.
"""


def rules(repo: str, head: str) -> str:
    return RULES.format(
        repo=repo,
        head=head[:12],
        view_lines=VIEW_LINES,
        calls=READING_CALLS,
        hours=BACKSTOP_S // 3600,
    )


def project_section(root: Path) -> str:
    """The project's record, said to be the whole project's: a step's scope is its own.

    How it's released and what its CI runs aren't refinement's, and stay in the file.
    """
    try:
        record = read_record(root)
    except ProjectRecordError:
        record = None
    if record is None:
        return "## The project\n\n(no record)"
    lines = [
        line
        for line in brief(record).splitlines()
        if not line.startswith(("- release how:", "- ci checks:"))
    ]
    # The record's own heading says "Work here is judged against them", which reads as
    # the step's scope (the Developer's review, crew#778).
    while lines and not lines[0].startswith("**Purpose:**"):
        lines.pop(0)
    return (
        "## The project\n\n"
        f"From `{RECORD_PATH}`, the whole project's record: what it is for and its "
        "bounds, not this step's scope. How it's released and what its CI runs are in "
        "the file.\n\n" + "\n".join(lines)
    )


def delivered_section(delivered: str, instead: str) -> tuple[str, dict[str, str]]:
    """What the project has delivered, as a note to search: 205k characters for
    sprint-metrics on 2026-10-18, most of the split's 283k-character prompt on
    sprint-metrics#660."""
    if not delivered:
        return "", {}
    count = sum(line.startswith("- #") for line in delivered.splitlines())
    return (
        "## What this project has already delivered\n\n"
        f"`{DELIVERED_NOTE}`: {count} stories, each its title and what its criteria "
        f"promised ({len(delivered) / 1000:.0f}k characters). Search it or view it. Don't "
        f"propose {instead}. `git log --oneline --grep=<words>` finds the commit that "
        "delivered one.",
        {DELIVERED_NOTE: delivered},
    )


@dataclass(frozen=True)
class Start:
    """A step's first message, each part's size, and the notes it names."""

    text: str
    sizes: dict[str, int]
    notes: dict[str, str]


def start(
    parts: dict[str, str],
    root: Path,
    *,
    repo: str,
    head: str,
    about: str,
    named_in: str,
    coverage: Any = None,
    notes: dict[str, str] | None = None,
) -> Start:
    """The step's own parts, then the project, the map, the hints and the rules.

    `parts` begins with the step's own section, which says its scope, and the parts it
    decides from, in the order it reads them. `about` is what the hints are picked for,
    and `named_in` what it is, as the hints say why: "the epic".
    """
    whole = {
        **{k: v for k, v in parts.items() if v},
        "project": project_section(root),
        "map": "## The map: every file, its size, and a Python file's top-level names\n\n"
        + project_map(root),
        "hints": "## Likely relevant, as hints (paths only; view what you need)\n\n"
        + ("\n".join(hints(root, about, coverage, named_in=named_in)) or "- (none)"),
        "rules": rules(repo, head),
    }
    sizes = {k: len(v) for k, v in whole.items()}
    sizes["total"] = sum(sizes.values())
    return Start("\n\n".join(whole.values()), sizes, dict(notes or {}))


@dataclass(frozen=True)
class Reading:
    """What a step reads: one checkout of the default branch, which a tick's refinement
    steps share (the Sponsor, crew#807), at `head`, and its coverage map, or None."""

    root: Path
    repo: str
    head: str
    coverage: Any = None

    @classmethod
    def at(cls, root: Path, repo: str, coverage: Any = None) -> Reading:
        found = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True
        )
        if found.returncode != 0:
            raise NoCheckout(f"{root} has no commit to read: {found.stderr.strip()[:200]}")
        return cls(root, repo, found.stdout.strip(), coverage)


class NoCheckout(RuntimeError):
    """There's no checkout of the project for a step to read: it can't be worked."""


@dataclass(frozen=True)
class Step:
    """One refinement step as a session: who works it, its answer form, and what its
    hints are picked from, as they say it ("the epic")."""

    name: str
    role: str
    form: type[BaseModel]
    said: str
    named_in: str
    step: str | None = None


@dataclass(frozen=True)
class Finding:
    """One thing `done`'s checks refused, by its rule when a declared one refused it."""

    rule: str
    says: str


class StepStopped(RuntimeError):
    """A step's session ended without its answer: a stop, or replies that called no tool.

    Its caller handles it as a call refused every time: the member failed, the settle
    or split failed, the design questions go to a person.
    """

    def __init__(self, step: str, ending: Ending) -> None:
        missing = str(ending.said.get("missing") or ending.why or ending.how)
        super().__init__(f"{step}'s session stopped ({ending.stop or ending.why}): {missing}")
        self.ending = ending


def own_checks(
    form: type[BaseModel], check: Callable[[Any], None] | None = None
) -> Callable[[dict[str, Any]], list[Finding]]:
    """`done`'s checks for a step: its form, then the step's own rules on the answer.

    What a one-call step was refused for and asked again (ADR 0025) is a reply in the
    session instead, and the session goes on to answer it (ADR 0032, decision 5).
    """

    def checked(said: dict[str, Any]) -> list[Finding]:
        try:
            found = form.model_validate(said)
            check_not_cut_at_a_quote(found)
            if check is not None:
                check(found)
        except ValidationError as exc:
            return [_finding(e) for e in exc.errors()[:10]]
        except ValueError as exc:
            return [Finding(str(getattr(exc, "rule", "")), str(exc))]
        return []

    return checked


def _finding(error: Any) -> Finding:
    where = ".".join(str(p) for p in error["loc"]) or "the answer"
    refused = (error.get("ctx") or {}).get("error")
    return Finding(
        refused.rule if isinstance(refused, rulebook.Refusal) else "", f"{where}: {error['msg']}"
    )


def run(
    step: Step,
    parts: dict[str, str],
    reading: Reading | None,
    *,
    about: str,
    check: Callable[[Any], None] | None = None,
    notes: dict[str, str] | None = None,
    send: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> Any:
    """The step's answer, from a session over `reading`, or `StepStopped`.

    A model call that fails is raised as it was, for the caller to handle as it handles
    any (a server that's down). With no checkout to read, the step can't be worked.
    """
    if reading is None:
        raise NoCheckout(f"{step.name}: there's no checkout of the project to read")
    begun = start(
        parts,
        reading.root,
        repo=reading.repo,
        head=reading.head,
        about=about,
        named_in=step.named_in,
        coverage=reading.coverage,
        notes=notes,
    )
    who = str(role_spec(step.role, step.step)["role"])
    record(
        EventKind.SESSION_STARTED,
        f"{step.name}: {begun.sizes['total']} characters to start",
        role=who,
        step=step.name,
        start_chars=begun.sizes,
        head=reading.head,
        limits={
            "backstop_s": BACKSTOP_S,
            "compact_at": COMPACT_AT,
            "reading_calls": READING_CALLS,
            "views_of_one_file": signals.VIEWS_OF_ONE_FILE,
            "quiet_limit": QUIET_LIMIT,
        },
    )
    session = Session(
        ReadTools(reading.root, reading.coverage, begun.notes),
        system_text(step.role, step.step),
        begun.text,
        send=send,
        check_done=own_checks(step.form, check),
        backstop_s=BACKSTOP_S,
        role=step.role,
        step=step.step,
        name=step.name,
        specs=[*reading_specs(reading.head), done_spec(step.form, step.said, head=reading.head)],
        endings=("done",),
        watch=Signals(reading_calls=READING_CALLS),
        quiet=QUIET,
        refused_done=REFUSED_DONE,
    )
    ending = session.run()
    if ending.how == "done":
        return step.form.model_validate(ending.said)
    if ending.error is not None:
        raise ending.error
    raise StepStopped(step.name, ending)
