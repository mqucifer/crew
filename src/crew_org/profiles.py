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


def profile_for(path: str = "") -> PythonProfile:
    """The profile for a file. Every file is Python's until a project declares parts (#404)."""
    return PYTHON
