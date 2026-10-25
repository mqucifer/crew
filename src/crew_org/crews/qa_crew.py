"""QA: does the running code satisfy the acceptance criteria?

The Reviewer judges the diff. QA judges behaviour, and judges it criterion by
criterion — because "the suite passes" is not the same claim as "every
criterion is proven". A test that exists but exercises something adjacent is
the failure this role is here to catch.

Acceptance is all-or-nothing by construction: a verdict cannot accept a story
while any criterion is unproven.

QA verifies in a session (crew#895, ADR 0006 in mqucifer/project-print), as the
criteria check does (`session/steps.py`): it reads the story's branch, and runs its
tests in the story's held workspace, with the project's services. Its verdict cites
what each criterion rests on, and `done` holds every citation to what the session
did and what the branch says, so evidence is cited, not asserted. The single call it
replaces judged pasted tests it couldn't run, and a filter over its words overturned
proof it had (mqucifer/sprint-metrics#763, #770: removed tests named in the evidence
read as tests it hadn't seen).

The standard is ISTQB's acceptance testing (CTFL 4.0, §2.2: each criterion is a test
condition traced to its execution), with ISO/IEC/IEEE 29119-3's test log: what ran, and
its result, as recorded. Its checklist, each item checked at `done`:

- every criterion has a verdict, and the story is accepted only if each is proven;
- a criterion about what the code does is proven by a `run` QA made, which exited 0
  and passed a test, or by a CI check on this commit that passed and whose workflow
  step QA quotes;
- a criterion about the pull request itself (a changelog entry, a version left as it
  was, a doc's words) is proven by quoting that artifact as the branch has it, never
  by quoting source or tests in place of running them;
- each cited command is one the session ran, with its exit code as it was;
- each quote is in its file at the branch's commit, or in what `git diff base` removed.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from crew_org.crews import epic_rows
from crew_org.crews.outside import OUTSIDE_DESCRIPTION, OutsideFinding
from crew_org.rulebook import Refusal, refuse, says
from crew_org.session import steps

MIN_EVIDENCE_CHARS = 20

# What a criterion about the pull request itself is about, and is proven by reading:
# its docs, its changelog, its version, what it publishes (the operator, crew#895). A
# path the criterion names counts too. Source and tests are proven by running them.
ARTIFACT_NAMES = frozenset({"pyproject.toml", "package.json", "Cargo.toml"})
# What a person reads, not what runs: judged by reading it (§7.1, crew#324).
DOC_SUFFIXES = (".md", ".rst", ".txt")
CONTRACT_DIRS = ("contracts", "schemas")
WORKFLOWS = ".github/workflows/"


class Ran(BaseModel):
    command: str = Field(
        description="The command as `run`'s reply showed it after `$`, such as "
        "`uv run pytest -q tests/test_service.py::test_gone`"
    )
    exit_code: int = Field(description="Its exit code, as the reply showed it")


class Quote(BaseModel):
    path: str = Field(description="The file it's from, from the repository's root")
    text: str = Field(
        description="The words, copied exactly from the file at the branch's commit, or "
        "from a line `git diff base` shows removed"
    )


class CriterionVerdict(BaseModel):
    criterion: str = Field(description="The acceptance criterion, quoted")
    proven: bool = Field(description="Is this criterion proven by what you cite?")
    by: Literal["run", "check", "read"] = Field(
        description=(
            "What it rests on. `run`: tests you ran with `run`, which exercise it. "
            "`check`: a CI check on this commit, for what only CI runs (an image build, a "
            "workflow); quote the step of its workflow that runs it. `read`: a criterion "
            "about the pull request itself (a changelog entry, a version left as it was, "
            "what a doc says), proven by quoting that file as the branch has it"
        )
    )
    ran: list[Ran] = Field(default_factory=list, description="The runs it rests on, for `run`")
    check: str = Field(default="", description="The CI check's name, for `check`")
    quoted: list[Quote] = Field(
        default_factory=list,
        description="The words it rests on, for `read`, and the workflow step, for `check`",
    )
    evidence: str = Field(
        description=(
            "Which test proves it and how, or what is missing. A criterion whose named "
            "check doesn't run it is unproven, and this says so"
        )
    )

    @field_validator("evidence")
    @classmethod
    def _is_specific(cls, value: str) -> str:
        if len(value.strip()) < MIN_EVIDENCE_CHARS:
            refuse("form.CriterionVerdict.is_specific")
        return value

    @model_validator(mode="after")
    def _cites_what_it_rests_on(self) -> CriterionVerdict:
        if not self.proven:
            return self
        if self.by == "run" and not self.ran:
            refuse("form.CriterionVerdict.cites_what_it_rests_on", "run")
        if self.by == "check" and not (self.check.strip() and self.quoted):
            refuse("form.CriterionVerdict.cites_what_it_rests_on", "check")
        if self.by == "read" and not self.quoted:
            refuse("form.CriterionVerdict.cites_what_it_rests_on", "read")
        return self


class QAVerdict(BaseModel):
    summary: str = Field(description="What was verified and what the result was")
    accepted: bool = Field(description="True only if every criterion is proven")
    criteria: list[CriterionVerdict] = Field(description="One verdict per criterion")
    outside: list[OutsideFinding] = Field(default_factory=list, description=OUTSIDE_DESCRIPTION)

    @model_validator(mode="after")
    def _a_verdict(self) -> QAVerdict:
        if not self.criteria:
            refuse("form.QAVerdict.a_verdict")
        return self

    @model_validator(mode="after")
    def _acceptance_requires_every_criterion(self) -> QAVerdict:
        unproven = [c.criterion for c in self.criteria if not c.proven]
        if self.accepted and unproven:
            refuse("form.QAVerdict.acceptance_requires_every_criterion", count=len(unproven))
        return self

    @property
    def unproven(self) -> list[CriterionVerdict]:
        return [c for c in self.criteria if not c.proven]


# --- what the session ran ------------------------------------------------------------------


class Recorded:
    """The story's held workspace, keeping each command the session ran and how it
    ended: `done` holds the verdict's `ran` to them."""

    def __init__(self, held: Any) -> None:
        self.held = held
        self.tools = held.tools
        self.ran: list[Any] = []

    def start(self) -> Any:
        return self.held.start()

    def setup(self, timeout: int = 900) -> Any:
        return self.held.setup(timeout)

    def resync(self, timeout: int = 900) -> Any:
        return self.held.resync(timeout)

    def exec(self, command: list[str], timeout: int = 300, stdin: str | None = None) -> Any:
        done = self.held.exec(command, timeout=timeout, stdin=stdin)
        self.ran.append(done)
        return done

    def stop(self) -> None:
        self.held.stop()


# --- `done`'s checks -----------------------------------------------------------------------


def _squashed(text: str) -> str:
    return " ".join(text.split())


# A test runner's summary: pytest's and Playwright's "3 passed", and what says none did.
_PASSED = re.compile(r"\b\d+ passed\b")
_NONE_RAN = re.compile(r"\bno tests ran\b|\b\d+ skipped\b")


def is_doc(path: str) -> bool:
    """A documentation file: prose, outside the tests."""
    pure = PurePosixPath(path)
    return pure.suffix.lower() in DOC_SUFFIXES and "tests" not in pure.parts


def is_artifact(path: str, criterion: str) -> bool:
    """A file a criterion about the pull request is proven by reading (crew#895)."""
    pure = PurePosixPath(path)
    if path in criterion or (pure.name in criterion and "." in pure.name):
        return True
    if "tests" in pure.parts or pure.name.startswith("test_"):
        return False
    return (
        is_doc(path)
        or pure.name in ARTIFACT_NAMES
        or pure.name.endswith(".schema.json")
        or pure.name.startswith("openapi.")
        or (len(pure.parts) > 1 and pure.parts[0] in CONTRACT_DIRS)
    )


def _removed(root: Path, base: str) -> str:
    """What `git diff base` removed, each line without its `-`."""
    if not base:
        return ""
    diff = subprocess.run(
        ["git", "-C", str(root), "diff", base, "HEAD"], capture_output=True, text=True
    )
    return "\n".join(
        line[1:]
        for line in diff.stdout.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )


def _ran_as_cited(c: CriterionVerdict, n: int, ran: dict[str, list[Any]]) -> list[str]:
    found = []
    for cited in c.ran:
        runs = ran.get(_squashed(cited.command), [])
        # The same command can run more than once: the cited one is the one that ended so.
        done = next((r for r in runs if r.code == cited.exit_code), runs[-1] if runs else None)
        if done is None:
            found.append(
                says("check.acceptance.ran_as_cited", "not_run", n=n, command=cited.command)
            )
        elif done.code != cited.exit_code:
            found.append(
                says(
                    "check.acceptance.ran_as_cited",
                    "code",
                    n=n,
                    command=cited.command,
                    code=done.code,
                    said=cited.exit_code,
                )
            )
        elif c.proven and c.by == "run" and done.code != 0:
            found.append(
                says(
                    "check.acceptance.ran_as_cited",
                    "failed",
                    n=n,
                    command=cited.command,
                    code=done.code,
                )
            )
        elif (
            c.proven
            and c.by == "run"
            and _NONE_RAN.search(done.output)
            and not _PASSED.search(done.output)
        ):
            found.append(
                says("check.acceptance.ran_as_cited", "none_passed", n=n, command=cited.command)
            )
    return found


def _quoted_as_it_reads(
    c: CriterionVerdict, n: int, root: Path, removed: Callable[[], str]
) -> list[str]:
    found = []
    for quote in c.quoted:
        words = _squashed(quote.text)
        file = (root / quote.path).resolve()
        there = file.is_relative_to(root.resolve()) and file.is_file()
        if there and words in _squashed(file.read_text(encoding="utf-8", errors="replace")):
            continue
        if words and words in removed():
            continue
        found.append(
            says("check.acceptance.quoted_as_it_reads", "not_found", n=n, path=quote.path)
            if there
            else says("check.acceptance.quoted_as_it_reads", "no_file", n=n, path=quote.path)
        )
    return found


def _read_proves_the_artifact(c: CriterionVerdict, n: int) -> list[str]:
    if not (c.proven and c.by == "read"):
        return []
    return [
        says("check.acceptance.read_proves_the_artifact", n=n, path=q.path)
        for q in c.quoted
        if not is_artifact(q.path, c.criterion)
    ]


def _proven_by_a_check_that_runs_it(
    c: CriterionVerdict, n: int, checks: dict[str, str]
) -> list[str]:
    if not (c.proven and c.by == "check"):
        return []
    found = []
    outcome = checks.get(c.check.strip().strip("`"))
    if outcome is None:
        found.append(
            says(
                "check.acceptance.proven_by_a_check_that_runs_it",
                "no_check",
                n=n,
                check=c.check,
                checks=", ".join(f"`{k}`" for k in checks) or "none",
            )
        )
    elif outcome != "success":
        found.append(
            says(
                "check.acceptance.proven_by_a_check_that_runs_it",
                "outcome",
                n=n,
                check=c.check,
                outcome=outcome,
            )
        )
    if not any(q.path.startswith(WORKFLOWS) for q in c.quoted):
        found.append(
            says("check.acceptance.proven_by_a_check_that_runs_it", "no_step", n=n, check=c.check)
        )
    return found


def check_cited(
    verdict: QAVerdict,
    *,
    ran: Sequence[Any],
    root: Path,
    base: str,
    checks: dict[str, str],
) -> None:
    """`done` refuses a verdict whose citations aren't what the session did and the branch
    says (crew#895). Each rule's findings are said together; the first rule found refuses.

    `ran` is what the session ran (`Recorded.ran`), `base` the commit the branch left the
    default branch at, and `checks` each CI check on this commit with its outcome.
    """
    by_command: dict[str, list[Any]] = {}
    for r in ran:
        by_command.setdefault(_squashed(r.command), []).append(r)
    removed: list[str] = []

    def removed_once() -> str:
        if not removed:
            removed.append(_squashed(_removed(root, base)))
        return removed[0]

    found: dict[str, list[str]] = {}
    for n, c in enumerate(verdict.criteria, start=1):
        for rule, said in (
            ("check.acceptance.ran_as_cited", _ran_as_cited(c, n, by_command)),
            ("check.acceptance.quoted_as_it_reads", _quoted_as_it_reads(c, n, root, removed_once)),
            ("check.acceptance.read_proves_the_artifact", _read_proves_the_artifact(c, n)),
            (
                "check.acceptance.proven_by_a_check_that_runs_it",
                _proven_by_a_check_that_runs_it(c, n, checks),
            ),
        ):
            if said:
                found.setdefault(rule, []).extend(said)
    if found:
        rule, said = next(iter(found.items()))
        raise Refusal("; ".join(said), rule=rule)


# --- the step ------------------------------------------------------------------------------


def opening(
    story: str,
    *,
    prior_verdicts: str = "",
    decisions: str = "",
    diagram: str = "",
    answered: str = "",
    checks: dict[str, str] | None = None,
    changed: Sequence[str] = (),
    unguarded: Sequence[str] = (),
) -> dict[str, str]:
    """QA's acceptance as a session's start: its step, what it said about this card before,
    the epic's rows and diagram, the story, what the Developer answered, the CI checks on
    this commit and the files the branch changed. The tests and docs are read and run
    through the tools.

    `prior_verdicts` is what QA itself said about this card on earlier attempts. Without
    it every attempt was judged cold, so a story returned for one unproven criterion
    could come back for a different one that was never mentioned.

    `unguarded` are the changed files no regression guard read (#404).
    """
    return {
        "step": "## Your step: verify this story against its acceptance criteria\n\n"
        "The Developer's work is on this branch, and the Code Reviewer has read its diff. "
        "For each criterion, find the test that exercises it, run it with `run`, and cite "
        "the run. A passing suite is not the same claim as a proven criterion: a test that "
        "exists but checks something adjacent proves nothing, and neither does one that was "
        "skipped. A criterion about the pull request itself is proven by reading the file "
        "it's about. Report what you found, including what went badly. Do not soften a "
        "failure.",
        "previously": "## What you said about this card before\n\n"
        f"{prior_verdicts}\n\n"
        "Your own earlier verdicts. Judge this attempt consistently with them: a criterion "
        "you proved before stays proven unless the code that proved it changed. If you "
        "return this card again, return it for a reason already named above, or say plainly "
        "why a new one has appeared."
        if prior_verdicts
        else "",
        "decisions": epic_rows.block(
            decisions,
            "These are decided for the epic. A criterion is proven only by work that also "
            "keeps to them: hold it unproven if the code does what a row rules out.",
        ),
        "diagram": epic_rows.diagram(
            diagram,
            "What the system will be once the Goal is delivered: the story's criteria are "
            "proven through the parts it draws.",
        ),
        "story": f"## The story\n\n{story}",
        "answered": answered,
        "checks": "## The CI checks on this commit\n\n"
        + "\n".join(f"- `{name}`: {outcome}" for name, outcome in checks.items())
        + "\n\nA check proves only what its steps execute: read its workflow under "
        "`.github/workflows/` before you cite it. A check that failed or is still running "
        "proves nothing yet."
        if checks
        else "",
        "changed": "## What the branch changed, against `base`\n\n"
        + "\n".join(f"- `{p}`" for p in changed)
        + (
            "\n\n**No regression guard ran on:** "
            + ", ".join(f"`{p}`" for p in unguarded)
            + ". The crew's guard reads Python only: judge these criteria by their tests, and "
            "name anything existing the change seems to have removed."
            if unguarded
            else ""
        )
        if changed
        else "",
    }


STEP = steps.Step(
    "acceptance",
    "qa_engineer",
    QAVerdict,
    "a verdict per acceptance criterion, each citing what it rests on.",
    "the story",
)


def verify_story(
    story: str,
    *,
    worktree: Path,
    repo: str,
    workspace: Recorded,
    base: str,
    checks: dict[str, str],
    github: Any = None,
    send: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    **parts: Any,
) -> QAVerdict:
    """Judge an implementation against its acceptance criteria, in a session over the
    story's branch and its held workspace (crew#895).

    `worktree` is the story's branch, checked out, which `workspace` holds; `base` is the
    commit it left the default branch at. `checks` is each CI check on the commit, with
    its outcome; `parts` are `opening`'s. `send` stands in for the model.
    """
    reading = steps.Reading.at(worktree, repo, None, github)

    def cited(verdict: QAVerdict) -> None:
        check_cited(verdict, ran=workspace.ran, root=reading.root, base=base, checks=checks)

    verdict: QAVerdict = steps.run(
        STEP,
        opening(story, checks=checks, **parts),
        reading,
        about=story,
        check=cited,
        send=send,
        workspace=workspace,
    )
    return verdict
