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

import re
from pathlib import PurePosixPath

from crewai import Crew, Process, Task
from pydantic import BaseModel, Field, field_validator, model_validator

from crew_org.agents import build_agents
from crew_org.tools.ast_edit import Operation

MAX_FILE_BYTES = 120_000


class FileWrite(BaseModel):
    """One file to create or replace, relative to the repository root."""

    path: str = Field(description="Repository-relative path, e.g. src/pkg/module.py")
    content: str = Field(description="The complete file contents")

    @field_validator("path")
    @classmethod
    def _stays_in_the_repository(cls, value: str) -> str:
        cleaned = value.strip().replace("\\", "/")
        # NOT lstrip("./") — that strips any run of '.' and '/', turning
        # "../../etc/passwd" into "etc/passwd" and defeating the check below.
        while cleaned.startswith("./"):
            cleaned = cleaned[2:]
        if not cleaned:
            raise ValueError("path is empty")
        pure = PurePosixPath(cleaned)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError(
                f"{value!r} escapes the repository. Paths must be relative and must not "
                "contain '..'."
            )
        if pure.parts[0] == ".git":
            raise ValueError("refusing to write inside .git")
        return str(pure)

    @field_validator("content")
    @classmethod
    def _is_plausible(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("file content is empty")
        if len(value.encode()) > MAX_FILE_BYTES:
            raise ValueError(
                f"file exceeds {MAX_FILE_BYTES} bytes. Split the work into smaller "
                "changes rather than generating one very large file."
            )
        return value

    @property
    def is_test(self) -> bool:
        name = PurePosixPath(self.path).name
        return name.startswith("test_") or name.endswith("_test.py")


class FileEdit(BaseModel):
    """One change to an existing file, addressed by name rather than position."""

    path: str = Field(description="Repository-relative path of the file to edit")
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
        return FileWrite._stays_in_the_repository(value)

    @model_validator(mode="after")
    def _source_matches_the_operation(self) -> FileEdit:
        if self.operation is not Operation.DELETE and not self.source.strip():
            raise ValueError(f"{self.operation} needs source. Only delete may omit it.")
        return self

    @property
    def is_test(self) -> bool:
        name = PurePosixPath(self.path).name
        return name.startswith("test_") or name.endswith("_test.py")


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
        return FileWrite._stays_in_the_repository(value)

    @model_validator(mode="after")
    def _changes_something(self) -> TextEdit:
        if self.find == self.replace:
            raise ValueError(f"the edit to {self.path!r} changes nothing")
        return self


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
    test: str = Field(description="The test function's name, e.g. test_range_as_json")
    source: str = Field(description="The complete test function, as it should read in the file")

    @field_validator("path")
    @classmethod
    def _stays_in_the_repository(cls, value: str) -> str:
        return FileWrite._stays_in_the_repository(value)

    @model_validator(mode="after")
    def _is_that_test(self) -> CriterionTest:
        if not re.search(rf"def {re.escape(self.test)}\b", self.source):
            raise ValueError(f"the source for {self.test!r} doesn't define it")
        name = PurePosixPath(self.path).name
        if not (name.startswith("test_") or name.endswith("_test.py")):
            raise ValueError(f"{self.path!r} isn't a test file: name it tests/test_*.py")
        return self

    def as_edit(self) -> FileEdit:
        return FileEdit(path=self.path, operation="add", target=self.test, source=self.source)


class Implementation(BaseModel):
    summary: str = Field(description="What changed and why, for the pull request body")
    # Before the code, on purpose: the test is planned first, as the standing
    # instructions ask. Required only of a first attempt (FirstAttempt).
    criteria_tests: list[CriterionTest] = Field(
        default_factory=list,
        description="For each acceptance criterion, the test in this change that proves it",
    )
    new_files: list[FileWrite] = Field(
        default_factory=list,
        description="Files that do not exist yet, in full. Never an existing file.",
    )
    edits: list[FileEdit] = Field(
        default_factory=list,
        description="Changes to Python files that already exist, one per definition.",
    )
    text_edits: list[TextEdit] = Field(
        default_factory=list,
        description=(
            "Changes to existing files that are not Python (pyproject.toml, README.md, "
            "CI workflows), each quoting the text it replaces."
        ),
    )

    @property
    def changes_nothing(self) -> bool:
        return not (self.new_files or self.edits or self.text_edits or self.criteria_tests)

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
                if (c.path, c.test) not in edited
                and not re.search(rf"def {re.escape(c.test)}\b", written.get(c.path, ""))
            ),
        ]

    @property
    def criteria_edits(self) -> set[tuple[str, str]]:
        """(path, test) for each criterion's test, as `all_edits` adds them."""
        return {(c.path, c.test) for c in self.criteria_tests}

    def _may_change_nothing(self) -> bool:
        return False

    @model_validator(mode="after")
    def _does_something(self) -> Implementation:
        if self.changes_nothing and not self._may_change_nothing():
            raise ValueError("an implementation must create a file or edit one")
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
    criteria_tests: list[CriterionTest] = Field(
        min_length=1,
        description=(
            "Before the code: for each acceptance criterion, the test that proves it, "
            "written in full. The crew adds each test to its file, so don't write these "
            "tests again in new_files or edits."
        ),
    )

    @model_validator(mode="after")
    def _has_a_test(self) -> FirstAttempt:
        """Definition of Done §7.1: every acceptance criterion needs a test.

        Satisfied by a new test file or by adding to an existing one — a story
        extending a module usually adds cases rather than a whole file.
        """
        if (
            self.criteria_tests
            or any(f.is_test for f in self.new_files)
            or any(e.is_test for e in self.edits)
        ):
            return self
        raise ValueError(
            "no test. Every acceptance criterion needs an automated test that proves "
            "it — add one to an existing test file, or create a new test_*.py."
        )


# Identical for every story and every repair, so it is the cacheable prefix.
STANDING_INSTRUCTIONS = (
    "Implement one story.\n\n"
    "Write the test that expresses each acceptance criterion, then the code that "
    "satisfies it.\n\n"
    "## How to return your work\n\n"
    "On a first attempt, start with `criteria_tests`: for each acceptance criterion, "
    "the test that proves it, written in full, with the test file it goes in. The crew "
    "adds each one to its file.\n"
    "For a file that does not exist yet, return it in `new_files`, in full.\n"
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
    "The project's lint rules are in pyproject.toml and are enforced. Write code that "
    "satisfies them rather than code you would then have to fix.\n\n"
    "Match the surrounding code's idiom. Implement only this story - work belonging "
    "to other stories is not yours to add, even when it looks adjacent. Configuration "
    "may change when the story needs it, never to loosen a check so a change passes."
)


def implement_story(
    story: str, *, context: str, feedback: str = "", prior: str = "", returned: bool = False
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
    """
    agents = build_agents("developer")
    repair = (
        f"\n\nA previous attempt failed. Fix it.\n\n{feedback}\n\n"
        "## What is already in the repository\n\n"
        "Your previous attempt has ALREADY BEEN WRITTEN. Every definition it "
        "added is in the listing above and is in the files right now. That "
        "changes what you return:\n\n"
        "- Return ONLY the edits that fix the failure. Not the implementation "
        "again — re-sending work that already landed is how a repair fails.\n"
        "- To change something your last attempt added, use `replace`. Using "
        "`add` on a name already in the listing is rejected, and that name is "
        "there because you put it there.\n"
        "- If a definition is already correct, say nothing about it.\n\n"
        "Change what is broken and keep the rest. Do not redesign the module "
        "layout — a layout change that leaves the tests importing from the old "
        "location fails in exactly the same way."
        if feedback
        else ""
    )
    # Ordered stable-first. Prefix caching matches a common prefix, so anything
    # after the first varying block re-tokenises every call. These standing
    # instructions are identical for every story and every repair, so they go
    # first; the story varies per card, the repository per attempt, and the
    # failure most of all. Measured cache hit rate before reordering: 31%.
    # Ordered stable-first for the prefix cache, then by how much each part
    # varies: the story is fixed for this card, what the gates said is fixed for
    # this delivery, the repository changes per attempt, the failure most of all.
    previously = f"\n\n# This story has been delivered before\n\n{prior}\n" if prior else ""
    task = Task(
        description=(
            STANDING_INSTRUCTIONS
            + f"\n\n## The story\n\n{story}\n"
            + previously
            + f"\n## The repository as it stands\n\n{context}\n"
            + repair
        ),
        expected_output=(
            "A summary, plus the new files and the named edits that implement the story."
        ),
        agent=agents["developer"],
        # A repair fixes a failure; returned work answers a gate, and may answer
        # that nothing needs to change (#161); a first attempt builds the story.
        output_pydantic=Implementation if feedback else (Rework if returned else FirstAttempt),
    )
    crew = Crew(
        agents=list(agents.values()), tasks=[task], process=Process.sequential, verbose=False
    )
    return crew.kickoff().pydantic
