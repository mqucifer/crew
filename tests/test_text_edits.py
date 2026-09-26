"""Changing an existing file by quoting what it replaces (#140, #204).

Python definitions are changed by name, which is what the §15 guards read; a
Python file's imports and entry block, which have no name, are quoted. pyproject.toml, a
README or a CI workflow has no names, so the Developer could not change one at
all: its only other route, a new file, is refused for a file that exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from crew_org.crews.delivery_crew import FileWrite, Implementation, TextEdit
from crew_org.tools import regression, workspace
from crew_org.tools.ast_edit import EditError
from crew_org.tools.repo_context import repository_context

PYPROJECT = """[project]
name = "sprint-metrics"
version = "0.1.0"
dependencies = []

[tool.ruff]
line-length = 100
"""

WORKFLOW = """name: tests
jobs:
  tests:
    steps:
      - run: uv run ruff check .
      - run: uv run pytest -q
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    (tmp_path / "README.md").write_text("# sprint-metrics\n\nReports metrics.\n")
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "tests.yml").write_text(WORKFLOW)
    (tmp_path / "metrics.py").write_text("def throughput():\n    return 0\n")
    return tmp_path


def implementation(*text_edits: TextEdit, **more) -> Implementation:
    return Implementation(summary="s", text_edits=list(text_edits), **more)


# --- 1: an existing non-Python file can be changed -----------------------------------


@pytest.mark.parametrize(
    "path, find, replace",
    [
        ("pyproject.toml", 'version = "0.1.0"', 'version = "0.2.0"'),
        ("README.md", "Reports metrics.", "Reports metrics for the crew."),
        (
            ".github/workflows/tests.yml",
            "      - run: uv run pytest -q\n",
            "      - run: uv run pytest -q\n      - run: uv run sprint-metrics --help\n",
        ),
    ],
)
def test_an_existing_non_python_file_is_changed(repo, path, find, replace):
    before = (repo / path).read_text()
    written = workspace.apply_implementation(
        repo, implementation(TextEdit(path=path, find=find, replace=replace))
    )
    assert written == [path]
    assert (repo / path).read_text() == before.replace(find, replace)


def test_an_empty_find_adds_to_the_end(repo):
    workspace.apply_implementation(
        repo, implementation(TextEdit(path="README.md", replace="## Usage\n"))
    )
    assert (repo / "README.md").read_text().endswith("Reports metrics.\n## Usage\n")


def test_several_edits_to_one_file_apply_in_order(repo):
    workspace.apply_implementation(
        repo,
        implementation(
            TextEdit(
                path="pyproject.toml", find="dependencies = []", replace='dependencies = ["httpx"]'
            ),
            TextEdit(path="pyproject.toml", find='["httpx"]', replace='["httpx>=0.27"]'),
        ),
    )
    assert 'dependencies = ["httpx>=0.27"]' in (repo / "pyproject.toml").read_text()


def test_a_change_of_only_non_python_files_is_an_implementation():
    assert implementation(TextEdit(path="README.md", find="a", replace="b")).text_edits


def test_the_developer_is_shown_the_text_it_would_quote(repo):
    context = repository_context(repo)
    assert "### .github/workflows/tests.yml" in context
    assert "      - run: uv run pytest -q" in context


# --- 2: Python definitions are still changed by name ----------------------------------------------


# A Python file's module-level lines have no name, so they are quoted (#204).
# sprint-metrics#133 could not repoint `__main__.py`'s one import any other way.

MAIN = """\"\"\"Allow the command to run as ``python -m sprint_metrics``.\"\"\"

import sys

from sprint_metrics.crew_performance import main

if __name__ == "__main__":
    sys.exit(main())
"""

MODULE = """from dataclasses import dataclass
from datetime import date


@dataclass
class Card:
    created: date


def throughput(cards):
    return 0
"""


def python(repo: Path, name: str, text: str) -> Path:
    (repo / name).write_text(text)
    return repo


def plan(repo: Path, *edits: TextEdit) -> dict[str, str]:
    return workspace.plan_text_edits(repo, list(edits))


def test_an_import_in_a_python_file_is_repointed_by_quoting(repo):
    """What sprint-metrics#133 needed."""
    edit = TextEdit(
        path="__main__.py",
        find="from sprint_metrics.crew_performance import main",
        replace="from sprint_metrics.cli import main",
    )
    after = plan(python(repo, "__main__.py", MAIN), edit)["__main__.py"]
    assert "from sprint_metrics.cli import main" in after and "crew_performance" not in after


def test_an_import_nothing_uses_any_more_can_be_removed(repo):
    """After a move, the old module's leftover imports fail lint (F401)."""
    edit = TextEdit(path="metrics.py", find="from dataclasses import dataclass\n", replace="")
    after = plan(python(repo, "metrics.py", MODULE), edit)["metrics.py"]
    assert after.startswith("from datetime import date")


def test_the_entry_block_is_module_level_too(repo):
    edit = TextEdit(
        path="__main__.py", find="    sys.exit(main())", replace="    raise SystemExit(main())"
    )
    assert "raise SystemExit" in plan(python(repo, "__main__.py", MAIN), edit)["__main__.py"]


@pytest.mark.parametrize(
    "find",
    ["    return 0", "def throughput(cards):", "@dataclass\nclass Card:", "date\n\n\n@dataclass"],
)
def test_a_quote_reaching_into_a_definition_is_refused(repo, find):
    edit = TextEdit(path="metrics.py", find=find, replace=find.replace("0", "1") + " ")
    with pytest.raises(EditError, match="reaches into `(Card|throughput)`.*with `edits`, by name"):
        plan(python(repo, "metrics.py", MODULE), edit)


def test_a_definition_cannot_be_brought_in_by_quoting(repo):
    edit = TextEdit(path="metrics.py", find="", replace="def cycle_time(cards):\n    return 0\n")
    with pytest.raises(EditError, match="brings in a definition"):
        plan(python(repo, "metrics.py", MODULE), edit)


def test_a_misquoted_python_edit_says_it_is_not_in_the_file(repo):
    edit = TextEdit(path="__main__.py", find="import os", replace="import sys")
    with pytest.raises(EditError, match="is not in the file"):
        plan(python(repo, "__main__.py", MAIN), edit)


def test_a_python_file_left_invalid_is_refused(repo):
    edit = TextEdit(path="__main__.py", find="import sys", replace="import (")
    with pytest.raises(EditError, match="no longer valid Python"):
        plan(python(repo, "__main__.py", MAIN), edit)


def test_the_developer_is_told_which_to_use_for_python():
    from crew_org.crews.delivery_crew import STANDING_INSTRUCTIONS

    text = " ".join(STANDING_INSTRUCTIONS.split())
    assert "outside any function or class" in text and "always change with `edits`" in text


def test_a_python_file_returned_whole_is_still_an_overwrite(repo):
    rewrite = FileWrite(path="metrics.py", content="def throughput():\n    return 1\n")
    assert regression.overwrites_existing(repo, [rewrite]) == ["metrics.py"]


# --- 3: nothing outside the quote changes, or nothing changes at all ------------------


def test_everything_outside_the_quote_is_byte_identical(repo):
    workspace.apply_implementation(
        repo,
        implementation(
            TextEdit(
                path="pyproject.toml",
                find="line-length = 100",
                replace='line-length = 100\ntarget-version = "py312"',
            )
        ),
    )
    after = (repo / "pyproject.toml").read_text()
    assert after.startswith(PYPROJECT.split("line-length")[0])
    assert after.endswith('line-length = 100\ntarget-version = "py312"\n')


def test_a_quote_not_in_the_file_is_refused_by_name(repo):
    with pytest.raises(EditError, match="is not in the file"):
        workspace.apply_implementation(
            repo, implementation(TextEdit(path="pyproject.toml", find='version = "9"', replace="x"))
        )


def test_a_quote_that_names_two_places_is_refused(repo):
    with pytest.raises(EditError, match="occurs 2 times"):
        workspace.apply_implementation(
            repo,
            implementation(
                TextEdit(path=".github/workflows/tests.yml", find="uv run", replace="x")
            ),
        )


def test_a_file_that_does_not_exist_belongs_in_new_files(repo):
    with pytest.raises(EditError, match="belongs in new_files"):
        workspace.apply_implementation(
            repo, implementation(TextEdit(path="CHANGELOG.md", find="a", replace="b"))
        )


def test_one_bad_edit_leaves_every_file_as_it_was(repo):
    before = {p: (repo / p).read_text() for p in ("pyproject.toml", "README.md")}
    with pytest.raises(EditError):
        workspace.apply_implementation(
            repo,
            implementation(
                TextEdit(path="README.md", find="Reports metrics.", replace="Changed."),
                TextEdit(path="pyproject.toml", find="not there", replace="x"),
            ),
        )
    assert {p: (repo / p).read_text() for p in before} == before


def test_an_edit_that_changes_nothing_is_refused():
    with pytest.raises(ValidationError, match="changes nothing"):
        TextEdit(path="README.md", find="same", replace="same")


def test_a_path_outside_the_repository_is_refused():
    with pytest.raises(ValidationError, match="escapes the repository"):
        TextEdit(path="../elsewhere/config.toml", find="a", replace="b")


def test_an_import_directly_above_a_function_can_be_removed(repo):
    """The quote's trailing newline ends its last line; it doesn't reach the def below."""
    source = "import os\nimport sys\ndef main():\n    return os.sep\n"
    edit = TextEdit(path="entry.py", find="import sys\n", replace="")
    assert plan(python(repo, "entry.py", source), edit)["entry.py"].startswith("import os\ndef")
