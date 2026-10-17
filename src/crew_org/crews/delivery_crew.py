"""Implementation: a story becomes files, and files become a pull request.

The Developer returns a *typed set of file writes* rather than driving a
tool-use loop. That is a deliberate bet on where this model is strong: schema-
constrained output was measured at 5/5 on the substrate gate, whereas a
multi-turn edit loop compounds every tool-call mistake. One structured answer is
easier to validate, repair, and reason about than ten small ones.

Definition of Done is enforced structurally: an implementation with no test is
rejected by the schema, not noticed later by a reviewer.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from crew_org import calls
from crew_org.crews.asks import FileAsk
from crew_org.profiles import profile_for
from crew_org.rulebook import refuse
from crew_org.tools.ast_edit import Operation
from crew_org.tools.ci_guard import is_workflow

MAX_FILE_BYTES = 120_000
# The fields that hold code or file text, which come back as blocks after the answer's
# JSON (ADR 0026): written inside a JSON string, a double quote in code closed the
# string early, and mqucifer/sprint-metrics#567 slipped on 3 of 6 answers that way.
CODE_FIELDS = frozenset({"content", "source", "find", "replace"})


def in_the_repository(value: str) -> tuple[str, str]:
    """The path as written in the repository, and which way it leaves it, if it does: each
    form that takes a path refuses that by its own rule (ADR 0031)."""
    cleaned = value.strip().replace("\\", "/")
    # NOT lstrip("./") — that strips any run of '.' and '/', turning
    # "../../etc/passwd" into "etc/passwd" and defeating the check below.
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if not cleaned:
        return cleaned, "empty"
    pure = PurePosixPath(cleaned)
    if pure.is_absolute() or ".." in pure.parts:
        return cleaned, "escapes"
    if pure.parts[0] == ".git":
        return cleaned, "git"
    return str(pure), ""


class FileWrite(BaseModel):
    """One file to create or replace, relative to the repository root."""

    path: str = Field(description="Repository-relative path, e.g. src/pkg/module.py")
    content: str = Field(description="The complete file contents")

    @field_validator("path")
    @classmethod
    def _stays_in_the_repository(cls, value: str) -> str:
        cleaned, outside = in_the_repository(value)
        if outside == "empty":
            refuse("form.path.stays_in_the_repository", "empty")
        if outside == "escapes":
            refuse("form.path.stays_in_the_repository", "escapes", path=repr(value))
        if outside == "git":
            refuse("form.path.stays_in_the_repository", "git")
        return cleaned

    @field_validator("content")
    @classmethod
    def _is_plausible(cls, value: str) -> str:
        if not value.strip():
            refuse("form.FileWrite.is_plausible", "empty")
        if len(value.encode()) > MAX_FILE_BYTES:
            refuse("form.FileWrite.is_plausible", "too_large", limit=MAX_FILE_BYTES)
        return value

    @property
    def is_test(self) -> bool:
        return profile_for(self.path).is_test_file(self.path)

    @model_validator(mode="after")
    def _tests_declare_their_fixtures(self) -> FileWrite:
        # A test using tmp_path without taking it failed four first attempts (#196).
        if self.is_test:
            self.content = profile_for(self.path).prepare_test(self.path, self.content)
        return self


class FileEdit(BaseModel):
    """One change to an existing file, addressed by name rather than position."""

    path: str = Field(description="Repository-relative path of the Python file to edit")
    operation: Operation = Field(
        description=(
            "replace (an existing definition), add (a new top-level definition), "
            "add_method (to an existing class), add_import, or delete"
        )
    )
    target: str = Field(
        description=(
            "Qualified name: `calculate_throughput`, or `Card.is_completed` for a "
            "method. For add_import, any short label."
        )
    )
    source: str = Field(
        default="",
        description="The complete new definition, or the import statement. Empty only for delete.",
    )

    @field_validator("path")
    @classmethod
    def _stays_in_the_repository(cls, value: str) -> str:
        cleaned, outside = in_the_repository(value)
        if outside == "empty":
            refuse("form.path.stays_in_the_repository", "empty")
        if outside == "escapes":
            refuse("form.path.stays_in_the_repository", "escapes", path=repr(value))
        if outside == "git":
            refuse("form.path.stays_in_the_repository", "git")
        # A named edit reads the file as Python. On a template it failed as the
        # template's own en dash not parsing, which named the wrong fault (crew#744).
        if PurePosixPath(cleaned).suffix != ".py":
            refuse("form.FileEdit.stays_in_the_repository", "not_python", path=repr(cleaned))
        return cleaned

    @model_validator(mode="after")
    def _source_matches_the_operation(self) -> FileEdit:
        if self.operation is not Operation.DELETE and not self.source.strip():
            refuse("form.FileEdit.source_matches_the_operation", operation=self.operation)
        if self.is_test and self.operation is not Operation.DELETE:
            self.source = profile_for(self.path).prepare_test(self.path, self.source)  # #196
        return self

    @property
    def is_test(self) -> bool:
        return profile_for(self.path).is_test_file(self.path)


class Move(BaseModel):
    """Move a top-level definition to another module, in one step (#216)."""

    name: str = Field(description="The function, class or constant to move, by name")
    from_path: str = Field(description="The file it's in now")
    to_path: str = Field(description="The file it moves to; created if it doesn't exist")

    @field_validator("from_path", "to_path")
    @classmethod
    def _stays_in_the_repository(cls, value: str) -> str:
        cleaned, outside = in_the_repository(value)
        if outside == "empty":
            refuse("form.path.stays_in_the_repository", "empty")
        if outside == "escapes":
            refuse("form.path.stays_in_the_repository", "escapes", path=repr(value))
        if outside == "git":
            refuse("form.path.stays_in_the_repository", "git")
        if PurePosixPath(cleaned).suffix != ".py":
            refuse("form.Move.stays_in_the_repository", "not_python", path=repr(cleaned))
        return cleaned

    @model_validator(mode="after")
    def _somewhere_else(self) -> Move:
        if self.from_path == self.to_path:
            refuse("form.Move.somewhere_else", name=self.name)
        return self


# What a person reads, not what runs: judged by reading it (§7.1, crew#324).
DOC_SUFFIXES = (".md", ".rst", ".txt")


def is_doc(path: str) -> bool:
    """A documentation file: prose, outside the tests."""
    pure = PurePosixPath(path)
    return pure.suffix.lower() in DOC_SUFFIXES and "tests" not in pure.parts


class TextEdit(BaseModel):
    """One change to an existing file by quoting what it replaces.

    Python definitions are changed by name (`FileEdit`), which the §15 guards
    read. What has no name to address (pyproject.toml, a README, a CI workflow,
    or a Python file's imports and entry block, #204) is changed by quoting
    the text it replaces (#140). Everything outside that text
    is left as it is by construction: nothing unquoted is reproduced, so nothing
    unquoted can be lost, and a file can't come back truncated or regenerated.
    """

    path: str = Field(
        description=(
            "Repository-relative path of an existing file: not Python, or a Python file's "
            "module-level lines"
        )
    )
    find: str = Field(
        default="",
        description=(
            "The exact text to replace, copied from the file as shown, occurring once in "
            "it. Empty to add `replace` at the end of the file."
        ),
    )
    replace: str = Field(default="", description="What takes its place. Empty to remove it.")

    @field_validator("path")
    @classmethod
    def _stays_in_the_repository(cls, value: str) -> str:
        # Python is allowed for its module-level lines only; that needs the
        # file's text to judge, so it's checked where the edit is applied (#204).
        cleaned, outside = in_the_repository(value)
        if outside == "empty":
            refuse("form.path.stays_in_the_repository", "empty")
        if outside == "escapes":
            refuse("form.path.stays_in_the_repository", "escapes", path=repr(value))
        if outside == "git":
            refuse("form.path.stays_in_the_repository", "git")
        return cleaned

    @model_validator(mode="after")
    def _changes_something(self) -> TextEdit:
        if self.find == self.replace:
            refuse("form.TextEdit.changes_something", path=repr(self.path))
        return self


class ExistingProof(BaseModel):
    """A criterion the tests already in the repository prove (#217).

    "The full existing test suite continues to pass" needs no new test: the
    suite is the proof. A first attempt had to write one anyway (#172), and
    invented `test_full_suite_passes` bodies failed validation on
    sprint-metrics#127 and got stuck behind the regression guard on #126.
    """

    criterion: str = Field(description="The acceptance criterion, briefly, as the story words it")
    tests: list[str] = Field(
        min_length=1,
        description="The existing tests that prove it, each as path::test_name",
    )


class CriterionTest(BaseModel):
    """One acceptance criterion, and the test that proves it, written in full (#172).

    The test's source lives here, not in `edits`: named in one place and
    written in another, a first attempt at sprint-metrics#73 and #75 named its
    tests and wrote none of them in three runs of six. Here a test can't be
    named without being written. The crew adds each one to its file.
    """

    criterion: str = Field(description="The acceptance criterion, briefly, as the story words it")
    path: str = Field(
        description="The test file it goes in, e.g. tests/test_sprint_range.py. A new one "
        "also goes in new_files, with its imports and fixtures"
    )
    test: str = Field(
        description="The test function's name, e.g. test_range_as_json. In a part that "
        "isn't Python, the test's title, as in test(\"the health summary is first\", ...)"
    )
    source: str = Field(
        "",
        description="The complete test function, as it should read in the file. In a part "
        "that isn't Python, leave it empty and write the test in new_files or text_edits",
    )

    @field_validator("path")
    @classmethod
    def _stays_in_the_repository(cls, value: str) -> str:
        cleaned, outside = in_the_repository(value)
        if outside == "empty":
            refuse("form.path.stays_in_the_repository", "empty")
        if outside == "escapes":
            refuse("form.path.stays_in_the_repository", "escapes", path=repr(value))
        if outside == "git":
            refuse("form.path.stays_in_the_repository", "git")
        return cleaned

    @field_validator("test")
    @classmethod
    def _a_name_not_its_code(cls, value: str) -> str:
        # The first real call on the crew's own call layer (crew#583) put the whole
        # function in `test` twice, told only that "the source for '<the function>'
        # doesn't define it", which named no field to change.
        if "\n" in value.strip() or value.lstrip().startswith(("def ", "async def ")):
            refuse("form.CriterionTest.a_name_not_its_code")
        return value.strip()

    @model_validator(mode="after")
    def _is_that_test(self) -> CriterionTest:
        profile = profile_for(self.path)
        if is_doc(self.path):
            # "Name it tests/test_*.py" can't be followed for a doc criterion:
            # sprint-metrics#381 was refused that way twice in a row.
            refuse("form.CriterionTest.is_that_test", "a_doc", path=repr(self.path))
        if not profile.is_test_file(self.path):
            refuse(
                "form.CriterionTest.is_that_test",
                "not_a_test_file",
                path=repr(self.path),
                hint=profile.test_file_hint,
            )
        if not profile.edit_by_name:
            # Written in new_files or text_edits; delivery checks it's there (#404).
            if self.source.strip() and not profile.defines_test(self.source, self.test):
                refuse("form.CriterionTest.is_that_test", "undefined", test=repr(self.test))
            return self
        if not self.source.strip():
            # Named with no code: almost always a test already in the repository.
            # "Doesn't define it" never said where that goes, and sprint-metrics#386
            # spent four attempts and the sprint's escalation finding out (#448).
            refuse(
                "form.CriterionTest.is_that_test",
                "no_source",
                test=repr(self.test),
                path=self.path,
                name=self.test,
            )
        if not profile.defines_test(self.source, self.test):
            refuse("form.CriterionTest.is_that_test", "undefined", test=repr(self.test))
        self.source = profile.prepare_test(self.path, self.source)  # #196
        return self

    def as_edit(self) -> FileEdit:
        return FileEdit(path=self.path, operation="add", target=self.test, source=self.source)


class RetiredTest(BaseModel):
    """A merged test this story deliberately ends, and why.

    §15: declared changes are fine; accidents are not. A test that pins
    behaviour the story is meant to end (sprint-metrics#132 removes the
    module whose pass-throughs earlier tests checked) can only go by being
    named here, with the reason, where the Code Reviewer reads it.
    """

    path: str = Field(description="The test file, e.g. tests/test_report.py")
    test: str = Field(description="The test function's name")
    why: str = Field(description="What this story ends that the test pinned")

    @field_validator("path")
    @classmethod
    def _a_test_file(cls, value: str) -> str:
        cleaned, outside = in_the_repository(value)
        if outside == "empty":
            refuse("form.path.stays_in_the_repository", "empty")
        if outside == "escapes":
            refuse("form.path.stays_in_the_repository", "escapes", path=repr(value))
        if outside == "git":
            refuse("form.path.stays_in_the_repository", "git")
        if not profile_for(cleaned).is_test_file(cleaned):
            refuse("form.RetiredTest.a_test_file", path=repr(cleaned))
        return cleaned

    @field_validator("why")
    @classmethod
    def _says_why(cls, value: str) -> str:
        if len(value.strip()) < 10:
            refuse("form.RetiredTest.says_why")
        return value.strip()


class Implementation(BaseModel):
    summary: str = Field(description="What changed and why, for the pull request body")
    # Before the code, on purpose: the test is planned first, as the standing
    # instructions ask. Required only of a first attempt (FirstAttempt).
    criteria_tests: list[CriterionTest] = Field(
        default_factory=list,
        description=(
            "For each acceptance criterion a test proves, the test in this change that "
            "proves it. A criterion about what a doc says has no entry here."
        ),
    )
    new_files: list[FileWrite] = Field(
        default_factory=list,
        description=(
            "Files that do not exist yet, in full, and placeholders marked "
            "`crew:placeholder` replaced whole. Never another existing file."
        ),
    )
    edits: list[FileEdit] = Field(
        default_factory=list,
        description="Changes to Python files that already exist, one per definition.",
    )
    text_edits: list[TextEdit] = Field(
        default_factory=list,
        description=(
            "Changes to existing files that are not Python (pyproject.toml, README.md, "
            "CI workflows), and to a Python file's lines outside any function or class "
            "(its imports, an `if __name__` block, its docstring), each quoting the text "
            "it replaces."
        ),
    )
    moves: list[Move] = Field(
        default_factory=list,
        description=(
            "Definitions moving to another module. The crew moves each whole, carries the "
            "imports it uses, and imports it back where anything still uses it"
        ),
    )
    deleted_files: list[str] = Field(
        default_factory=list,
        description="Existing files this story removes entirely",
    )
    retired_tests: list[RetiredTest] = Field(
        default_factory=list,
        description="Merged tests this story deliberately ends, each with why",
    )
    # Asked for, not guessed at (#231): the context shows the files the work
    # names in full and the rest by name and signature only.
    need_files: list[FileAsk] = Field(
        default_factory=list,
        description=(
            "Only when you can't do the work correctly without seeing files listed by "
            "name but not shown in full: each path, and in a line why. The rest of your "
            "answer is applied, and the next attempt shows them; an answer that only "
            "names files is asked again with them shown. Leave empty otherwise."
        ),
    )

    @field_validator("deleted_files")
    @classmethod
    def _deleted_stay_in_the_repository(cls, value: list[str]) -> list[str]:
        kept = []
        for v in value:
            cleaned, outside = in_the_repository(v)
            if outside == "empty":
                refuse("form.path.stays_in_the_repository", "empty")
            if outside == "escapes":
                refuse("form.path.stays_in_the_repository", "escapes", path=repr(v))
            if outside == "git":
                refuse("form.path.stays_in_the_repository", "git")
            kept.append(cleaned)
        return kept

    @model_validator(mode="before")
    @classmethod
    def _drops_doc_criteria_with_no_test(cls, data: Any) -> Any:
        """A doc criterion listed with no test is dropped, not refused.

        A doc is proven by reading it, and the refusal says so. "For each
        criterion" still drew one: sprint-metrics#529 listed `docs/service.md`
        with an empty test five times on 2026-10-09, each refusal costing a
        call, while its doc change was elsewhere in the answer. An entry with
        a test in it is still refused, since a doc's wording isn't tested.
        """
        if not isinstance(data, dict) or not isinstance(data.get("criteria_tests"), list):
            return data

        def empty_doc(c: Any) -> bool:
            return (
                isinstance(c, dict)
                and is_doc(str(c.get("path") or ""))
                and not str(c.get("source") or "").strip()
            )

        return {**data, "criteria_tests": [c for c in data["criteria_tests"] if not empty_doc(c)]}

    @model_validator(mode="before")
    @classmethod
    def _drops_tests_it_only_names(cls, data: Any) -> Any:
        """A repair or rework's tests named with no code are dropped, not refused.

        They're the tests already on the branch, and naming them changes nothing.
        Refusing them lost the fix beside them. sprint-metrics#453 and #450 named
        their branch's tests next to edits that fixed lint and an import, and were
        refused until they blocked, though the refusal said to leave them out
        (crew#489). When #450 did, it left out its fix too (crew#511). A first
        attempt keeps its refusal, which points at `proven_by_existing`.
        """
        if "proven_by_existing" in cls.model_fields or not isinstance(data, dict):
            return data
        named = data.get("criteria_tests")
        if not isinstance(named, list):
            return data

        def only_named(c: Any) -> bool:
            if not isinstance(c, dict) or str(c.get("source") or "").strip():
                return False
            path = str(c.get("path") or "")
            return bool(profile_for(path).edit_by_name and profile_for(path).is_test_file(path))

        return {**data, "criteria_tests": [c for c in named if not only_named(c)]}

    @property
    def changes_nothing(self) -> bool:
        return not (
            self.new_files
            or self.moves
            or self.edits
            or self.text_edits
            or self.criteria_tests
            or self.deleted_files
        )

    @property
    def all_edits(self) -> list[FileEdit]:
        """The named edits, then each criterion's test as an `add` to its file (#172).

        After `new_files` are written, so a test for a new test file is added
        after that file's own imports and fixtures.
        """
        # A new test file often carries its tests already: every first attempt at
        # sprint-metrics#73 wrote them in the file and here too. Adding them again
        # would be refused as already defined, so a test the same answer's new
        # file already defines isn't added twice.
        written = {f.path: f.content for f in self.new_files}
        # Written as an edit too: sprint-metrics#95's first attempt added the same
        # test in `edits` and here, and the second `add` was refused (#183).
        edited = {(e.path, e.target) for e in self.edits}
        return [
            *self.edits,
            *(
                c.as_edit()
                for c in self.criteria_tests
                # Only where tests are added by name: elsewhere they're written as files (#404).
                if profile_for(c.path).edit_by_name
                and (c.path, c.test) not in edited
                and not profile_for(c.path).defines_test(written.get(c.path, ""), c.test)
            ),
        ]

    @property
    def criteria_edits(self) -> set[tuple[str, str]]:
        """(path, test) for each criterion's test, as `all_edits` adds them."""
        return {(c.path, c.test) for c in self.criteria_tests}

    def _may_change_nothing(self) -> bool:
        return False

    @property
    def asks(self) -> bool:
        """It asks to see files first (#231): not applied, so nothing else is required of it."""
        return bool(self.need_files)

    @model_validator(mode="after")
    def _does_something(self) -> Implementation:
        if self.changes_nothing and not self._may_change_nothing() and not self.asks:
            refuse("form.Implementation.does_something")
        return self


class Satisfied(BaseModel):
    """A finding the code already answers, and the evidence that it does."""

    finding: str = Field(description="The finding, as the gate put it")
    evidence: str = Field(
        description=(
            "Where the code that satisfies it is (file and definition), and which tests exercise it"
        )
    )


class Rework(Implementation):
    """The answer to a story a gate sent back (#161).

    Either a change, or evidence that the code as it stands already satisfies
    the findings. The second used to be impossible to say: sprint-metrics#74 was
    asked to add an implementation #73 had already landed, answered "it's
    already there" three times, and was blocked as a SCHEMA failure each time.

    No new test is demanded, as for a repair: the first attempt's tests are
    already in the branch.
    """

    already_satisfied: list[Satisfied] = Field(
        default_factory=list,
        description=(
            "Only when no change is needed: for each finding, the evidence that the code "
            "as it stands already satisfies it. Leave empty if you change anything."
        ),
    )

    def _may_change_nothing(self) -> bool:
        return bool(self.already_satisfied)


class FirstAttempt(Implementation):
    """An implementation written from the story, with nothing yet on disk.

    The test requirement lives here rather than on `Implementation` because a
    repair cannot satisfy it and should not have to. A repair is told to return
    only the edits that fix the failure, and the test its first attempt wrote is
    already in the worktree and usually already correct — so demanding a test in
    every submission asks the model to choose which instruction to disobey.
    Story #11 chose correctly, returned the fix alone, and was rejected for it
    twice.

    Nothing is lost by scoping it: QA reads the worktree and will not accept a
    story until it can name the test that proves each criterion, which is a
    claim the schema could never check anyway.
    """

    # Required here, and first among the changes (#172). With tests enforced only
    # by the check below, a first attempt at sprint-metrics#75 came back with no
    # test three times out of three: the answer's format asked for source
    # changes and nothing else, and the rule arrived only as a refusal. A field
    # the format requires is asked for; a rule that refuses afterwards is not.
    # Still required, so the format asks for it; it may be empty only when every
    # criterion rests on existing tests (#217), which is a pure refactor.
    criteria_tests: list[CriterionTest] = Field(
        description=(
            "Before the code: for each acceptance criterion a new test proves, that test, "
            "written in full. The crew adds each test to its file, so don't write these "
            "tests again in new_files or edits."
        ),
    )
    proven_by_existing: list[ExistingProof] = Field(
        default_factory=list,
        description=(
            "Each acceptance criterion the tests already in the repository prove, e.g. "
            "'existing behaviour is unchanged', with those tests named. Don't write a new "
            "test for it."
        ),
    )

    def _paths(self) -> list[str]:
        paths = [f.path for f in self.new_files] + [t.path for t in self.text_edits]
        return paths + list(self.deleted_files)

    def _touched_only(self, kind) -> bool:
        if self.edits or self.moves:
            return False
        paths = self._paths()
        return bool(paths) and all(kind(p) for p in paths)

    @property
    def docs_only(self) -> bool:
        """Every change is to documentation: judged by reading it, not by a test (§7.1)."""
        return self._touched_only(is_doc)

    @property
    def ci_only(self) -> bool:
        """Every change is to a CI workflow: proven by its own run, not by a test (§7.1).

        sprint-metrics#260 was refused both ways: with a test for mixing, and
        without one for having none (crew#331). Mixing is allowed now (crew#335);
        a change of workflows alone still needs no test, and nor does one that
        also documents them: crew-presentation#74's deploy workflow and its README
        note were neither docs only nor CI only, and were refused for no test.
        """
        return self._touched_only(lambda p: is_workflow(p) or is_doc(p)) and any(
            is_workflow(p) for p in self._paths()
        )

    @model_validator(mode="after")
    def _has_a_test(self) -> FirstAttempt:
        # An answer that only asks isn't applied, so it needs no test; one that also
        # changes code is applied (crew#591), and brings its tests.
        if self.asks and self.changes_nothing:
            return self
        """Definition of Done §7.1: every acceptance criterion needs a test.

        Satisfied by a new test file or by adding to an existing one — a story
        extending a module usually adds cases rather than a whole file — or, for
        a criterion the existing suite already proves, by naming those tests (#217).
        """
        if (
            self.criteria_tests
            or self.proven_by_existing
            or any(f.is_test for f in self.new_files)
            or any(e.is_test for e in self.edits)
            or self.docs_only
            or self.ci_only
        ):
            return self
        refuse("form.FirstAttempt.has_a_test")


class CriterionMet(BaseModel):
    """One acceptance criterion the code already meets, and the proof (#221).

    The proof is an existing test, or for a criterion only CI can prove (what a
    workflow does), the workflow step that meets it (#397). sprint-metrics#368's
    right answer was refused because a workflow has no test to name.
    """

    criterion: str = Field(description="The acceptance criterion, as the story states it")
    code: str = Field(description="The code that meets it: file and definition")
    test: str = Field(
        "",
        description="The existing test that proves it, as path::test_name, e.g. "
        "tests/test_cli.py::test_schema_flag. Empty only when `ci_step` is given.",
    )
    ci_step: str = Field(
        "",
        description=(
            "Only for a criterion about what a CI workflow does, which no test can prove: "
            "the workflow file and the step that meets it, as "
            "`.github/workflows/release.yml#Create GitHub Release`. Its latest run on the "
            "default branch must have passed. Leave `test` empty then."
        ),
    )

    @model_validator(mode="after")
    def _one_proof(self) -> CriterionMet:
        if bool(self.test.strip()) == bool(self.ci_step.strip()):
            refuse("form.CriterionMet.one_proof")
        return self


class FirstOrDone(FirstAttempt):
    """A first attempt that may answer that the story is already done (#221).

    Only rework could say "the code already satisfies this" (#161). A first
    attempt had to change something, so a story that duplicated delivered work,
    like sprint-metrics#149 to #155, forced redundant code or a fight with the
    regression guards. Offered once per story: an answer QA refuses isn't given
    this choice again.
    """

    criteria_tests: list[CriterionTest] = Field(
        default_factory=list,
        description=(
            "Before the code: for each acceptance criterion a test proves, that test, "
            "written in full. The crew adds each test to its file, so don't write these "
            "tests again in new_files or edits. Empty only for an already-done answer."
        ),
    )
    already_done: list[CriterionMet] = Field(
        default_factory=list,
        description=(
            "Only when the code as it stands already meets every acceptance criterion: for "
            "each one, the code that meets it and the existing test that proves it. Change "
            "nothing, then. Leave empty if you change anything."
        ),
    )

    def _may_change_nothing(self) -> bool:
        return bool(self.already_done)

    @model_validator(mode="after")
    def _has_a_test(self) -> FirstOrDone:
        if self.asks and self.changes_nothing:
            return self
        if self.already_done:
            if not self.changes_nothing:
                refuse("form.FirstOrDone.has_a_test", "changes_something")
            return self
        if not (self.criteria_tests or self.proven_by_existing or self.docs_only or self.ci_only):
            refuse("form.FirstOrDone.has_a_test", "no_test")
        return self


# How the Developer sees a file it is shown only by name: asked for, and since
# crew#583's step C4 read inside the call, with asking the last resort.
ASK_FOR_FILES = (
    "The repository below may show some files only by name and signature. If you need "
    "one in full to do the work correctly, don't guess at it: name it in `need_files` "
    "(and nothing else), and you'll be asked again with it shown.\n"
)
READ_FILES = (
    "The repository below may show some files only by name and signature. If you need "
    "one to do the work correctly, don't guess at it: read it with `read_file` before "
    "you answer, the whole file or one definition by name. Only if you can't read it "
    "that way, name it in `need_files`: do the rest of the work, which is applied, and "
    "the next attempt shows the file.\n"
)

# Identical for every story and every repair, so it is the cacheable prefix.
STANDING_INSTRUCTIONS = (
    "Implement one story.\n\n"
    "Write the test that expresses each acceptance criterion, then the code that "
    "satisfies it.\n\n"
    "## How to return your work\n\n"
    "On a first attempt, start with `criteria_tests`: for each acceptance criterion, "
    "the test that proves it, written in full, with the test file it goes in. The crew "
    "adds each one to its file. A criterion the tests already in the repository prove "
    "(such as 'existing behaviour is unchanged') goes in `proven_by_existing` instead, "
    "naming those tests.\n"
    "A criterion about what a user doc says needs no test: the doc is the proof, and "
    "the Code Reviewer and QA read it. Don't write a test that matches a doc's wording. "
    "A command example a doc shows is still run by the project's doc tests, where it "
    "has them.\n"
    + ASK_FOR_FILES
    + "A change to CI workflows alone needs no test: it's judged by review, and proven "
    "when it runs. Leave `criteria_tests` and `proven_by_existing` empty for it: a CI "
    "run isn't a test to name. A workflow may change together with the code or tests "
    "it runs, when changing one breaks the other.\n"
    "For a file that does not exist yet, return it in `new_files`, in full.\n"
    "A file marked `crew:placeholder` is the project's walking skeleton holding its "
    "place: replace it whole in `new_files`, without the marker once it does the "
    "real job.\n"
    "For a file that already exists, return `edits` — one per definition, addressed "
    "by name. You never reproduce code you are not changing, and anything you do not "
    "name is left exactly as it is.\n\n"
    "  replace     an existing function, method or constant, by name\n"
    "  add         a new top-level function or class\n"
    "  add_method  a method on an existing class\n"
    "  add_import  an import the new code needs\n"
    "  delete      remove a definition - deliberate, and rarely what a story wants\n\n"
    "Name a method as `Class.method`. Give the complete definition as `source`; "
    "indentation is corrected for you.\n\n"
    "If your code uses a name the file does not already import, add an `add_import` "
    "edit for it. A spliced definition referring to something unimported is the most "
    "common way these edits fail.\n\n"
    "For an existing file that is not Python (pyproject.toml, README.md, a CI "
    "workflow), return `text_edits`: `find` quotes the exact text to change, copied "
    "from the file as shown, and must occur in it once; `replace` is what takes its "
    "place. Quote enough to be unique and no more. An empty `find` adds to the end of "
    "the file.\n"
    "A Python file's lines outside any function or class (its imports, an `if "
    "__name__` block, its docstring) have no name, so they change with `text_edits` "
    "too: repointing an import, or removing one nothing uses any more. Functions and "
    "classes always change with `edits`.\n\n"
    "To move a definition to another module, list it in `moves` (its name, the file "
    "it's in, the file it goes to). The crew moves it whole, brings the imports it "
    "uses, and imports it back where anything still uses it. Don't also delete it or "
    "write it yourself. Move together the definitions that use each other.\n"
    "To remove a file entirely, list it in `deleted_files`; whatever it defined must "
    "live elsewhere by then and nothing may still import it.\n"
    "A merged test that pins behaviour this story deliberately ends goes in "
    "`retired_tests`, with what the story ends; the crew deletes it for you. That is "
    "the only way a merged test may be deleted.\n\n"
    "The project's lint rules are in pyproject.toml and are enforced. Write code that "
    "satisfies them rather than code you would then have to fix.\n\n"
    "Match the surrounding code's idiom. Implement only this story - work belonging "
    "to other stories is not yours to add, even when it looks adjacent. Configuration "
    "may change when the story needs it, never to loosen a check so a change passes."
)


def standing_instructions(*, reading: bool = False) -> str:
    """The standing instructions, telling a call with the read tool to read, not ask."""
    return (
        STANDING_INSTRUCTIONS.replace(ASK_FOR_FILES, READ_FILES)
        if reading
        else STANDING_INSTRUCTIONS
    )


def implement_story(
    story: str,
    *,
    context: str,
    feedback: str = "",
    prior: str = "",
    returned: bool = False,
    may_be_done: bool = False,
    read: calls.Tool | None = None,
) -> Implementation:
    """Produce the files that satisfy one story.

    `feedback` carries the previous attempt's failure, so a repair sees what
    went wrong instead of starting blind.

    `prior` carries the card's own history — what the last attempt did, what
    became of it, and whether its code is in the worktree. A different thing
    entirely, and composed by the caller (`delivery.prior_context`) because only
    the caller knows which of those two worlds this attempt is in. Without it
    the crew returns a card for a named defect and then implements it again
    knowing nothing about the defect, which is how a story is returned twice for
    the same reason. With only half of it — a verdict describing files a reset
    worktree no longer holds — the model tries to edit a phantom file, which is
    worse.

    `read` is the read tool (crew#583, step C4): with it, a file shown only by
    name is read inside the call instead of asked for in `need_files`. The call
    is the crew's own (ADR 0025), so a refused answer is asked for again with why.
    """
    repair = (
        f"A previous attempt failed. Fix it.\n\n{feedback}\n\n"
        "## What is already in the repository\n\n"
        "Your previous attempt has ALREADY BEEN WRITTEN. Every definition it "
        "added is in the listing above and is in the files right now. That "
        "changes what you return:\n\n"
        "- Return ONLY the edits that fix the failure. Not the implementation "
        "again — re-sending work that already landed is how a repair fails.\n"
        "- To change something your last attempt added, use `replace`. Using "
        "`add` on a name already in the listing is rejected, and that name is "
        "there because you put it there.\n"
        "- A definition the failure says was removed is not in the listing: your "
        "last attempt deleted it. Add it back with `add`, from the source the "
        "failure shows.\n"
        "- If a definition is already correct, say nothing about it.\n\n"
        "Change what is broken and keep the rest. Do not redesign the module "
        "layout — a layout change that leaves the tests importing from the old "
        "location fails in exactly the same way."
        if feedback
        else ""
    )
    # Ordered stable-first for the prefix cache, then by how much each part
    # varies: the story is fixed for this card, what the gates said is fixed for
    # this delivery, the repository changes per attempt, the failure most of all.
    # Measured cache hit rate before ordering this way: 31%.
    parts = [
        calls.Part("instructions", standing_instructions(reading=read is not None)),
        calls.Part("story", f"## The story\n\n{story}"),
        calls.Part("prior", f"# This story has been delivered before\n\n{prior}" if prior else ""),
        calls.Part("repository", f"## The repository as it stands\n\n{context}"),
        calls.Part("repair", repair),
    ]
    # A repair fixes a failure; returned work answers a gate, and may answer
    # that nothing needs to change (#161); a first attempt builds the story.
    # A first attempt may instead answer that the story is already done (#221).
    form: type[Implementation] = (
        Implementation
        if feedback
        else Rework
        if returned
        else FirstOrDone
        if may_be_done
        else FirstAttempt
    )
    return calls.ask(
        "developer",
        parts,
        form,
        expected="A summary, plus the new files and the named edits that implement the story.",
        tools=[read] if read is not None else None,
        blocks=CODE_FIELDS,
    )
