"""What a language's code looks like to the crew: its tests, its definitions (#404).

The crew assumed Python in eight places, each with its own copy of a rule: what a
test file is (five copies, which didn't quite agree), how a test is named and
found, what a file defines, which test goes with which module, and how a test
declares its fixtures. They ask a profile now. Phase 2a is only that move: the
Python profile holds today's rules exactly, and every caller behaves as before.
Phase 2b adds a project's parts and a generic profile for other languages
(crew#376).
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from pathlib import PurePosixPath

# A test named in prose: `test_api_version`, not the file in `tests/test_report.py`.
_TEST_NAME = re.compile(r"\btest_\w+\b(?!\.py)")
# The tests a file defines.
_TEST_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)", re.M)


class PythonProfile:
    """Python with pytest: every rule the crew had, in one place."""

    name = "python"
    # Edits by definition name, and the regression guard, read Python's syntax
    # tree (tools/ast_edit.py, tools/regression.py).
    edit_by_name = True
    guarded = True
    test_file_hint = "name it tests/test_*.py"

    def is_test_file(self, path: str) -> bool:
        """Named as a test file: `test_*` or `*_test.py`."""
        name = PurePosixPath(path).name
        return name.startswith("test_") or name.endswith("_test.py")

    def is_test_path(self, path: str) -> bool:
        """Among the tests: under a `tests/` directory, or named as a test file."""
        path = str(path)
        return (
            path.startswith("tests/")
            or "/tests/" in path
            or path == "tests"
            or self.is_test_file(path)
        )

    def defines_test(self, source: str, name: str) -> bool:
        """Does this source define the test? As written by the Developer."""
        return re.search(rf"def {re.escape(name)}\b", source) is not None

    def test_exists(self, text: str, name: str) -> bool:
        """Is this test defined in the file's text? As cited for evidence."""
        pattern = rf"^\s*(?:async\s+)?def\s+{re.escape(name)}\s*\("
        return re.search(pattern, text, re.M) is not None

    def split_test_id(self, test_id: str) -> tuple[str, str]:
        """`path::name` or `path::Class::name` -> (path, name), a parametrisation dropped."""
        path, _, rest = test_id.partition("::")
        return path, rest.rsplit("::", 1)[-1].split("[", 1)[0]

    def test_names_in(self, text: str) -> list[str]:
        """Tests named in prose."""
        return _TEST_NAME.findall(text)

    def tests_defined(self, text: str) -> list[str]:
        """The tests a file defines."""
        return _TEST_DEF.findall(text)

    def prepare_test(self, path: str, source: str) -> str:
        """A test's source with the fixtures it uses declared (#196)."""
        if not path.endswith(".py"):
            return source
        from crew_org.tools.fixtures import declare_fixtures  # noqa: PLC0415

        return declare_fixtures(source)

    def definitions(self, source: str) -> dict[str, str]:
        """What a file defines, name to signature, for the map."""
        from crew_org.tools.regression import signatures_for_context  # noqa: PLC0415

        return signatures_for_context(source)

    def paired_test(self, path: str) -> str | None:
        """The test module that goes with a source module: `tests/test_<stem>.py`."""
        if not path.endswith(".py") or self.is_test_path(path):
            return None
        return f"tests/test_{PurePosixPath(path).stem}.py"


PYTHON = PythonProfile()


# --- phase 2b: a project's parts, and a generic profile for other languages ------------

_TITLE = re.compile(r"""\b(?:test|it)(?:\.\w+)?\(\s*(['"`])(.+?)\1""")


class GenericProfile:
    """A part in a language the crew has no deeper tools for (#404).

    Tests are the part's declared test files, named by their titles, as in
    `test("the health summary is first", ...)`. There's no edit-by-name and no
    regression guard: edits are whole files or exact find-and-replace, and the
    Code Reviewer and QA are told a change had no guard.
    """

    edit_by_name = False
    guarded = False

    def __init__(self, language: str, tests: list[str]):
        self.name = language
        self._tests = list(tests)
        self.test_file_hint = (
            "name it to match this part's test files: " + ", ".join(f"`{t}`" for t in tests)
            if tests
            else "this part declares no test files"
        )

    def is_test_file(self, path: str) -> bool:
        return any(PurePosixPath(path).match(glob) or _glob(path, glob) for glob in self._tests)

    def is_test_path(self, path: str) -> bool:
        return self.is_test_file(path)

    def defines_test(self, source: str, name: str) -> bool:
        return name in self.tests_defined(source)

    def test_exists(self, text: str, name: str) -> bool:
        return name in self.tests_defined(text)

    def split_test_id(self, test_id: str) -> tuple[str, str]:
        path, _, rest = test_id.partition("::")
        return path, rest

    def test_names_in(self, text: str) -> list[str]:
        # Titles aren't recognisable in prose; a citation names `path::title`.
        return []

    def tests_defined(self, text: str) -> list[str]:
        return [m.group(2) for m in _TITLE.finditer(text)]

    def prepare_test(self, path: str, source: str) -> str:
        return source

    def definitions(self, source: str) -> dict[str, str]:
        return {}

    def paired_test(self, path: str) -> str | None:
        return None


def _glob(path: str, pattern: str) -> bool:
    """`**` matching any depth, which `PurePosixPath.match` doesn't do from the left."""
    from fnmatch import fnmatch  # noqa: PLC0415

    return fnmatch(path, pattern) or fnmatch(path, pattern.replace("**/", ""))


# The parts of the project being worked on, set when its record is read (#404).
_PARTS: ContextVar[tuple] = ContextVar("crew_project_parts", default=())  # noqa: B039


def set_project(record) -> None:
    """Resolve profiles by this project's parts until cleared."""
    design = getattr(record, "design", None) if record is not None else None
    _PARTS.set(tuple(getattr(design, "parts", None) or ()))


def clear() -> None:
    _PARTS.set(())


def profile_for(path: str = ""):
    """The profile for a file: its part's, by the longest matching path, or Python's."""
    parts = _PARTS.get()
    matching = [p for p in parts if not p.path or str(path).startswith(p.path)]
    if not matching:
        return PYTHON
    part = max(matching, key=lambda p: len(p.path))
    if part.language.strip().lower() == "python":
        return PYTHON
    return GenericProfile(part.language, part.tests)
