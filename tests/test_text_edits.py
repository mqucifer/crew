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

from crew_org.crews.delivery_crew import FileEdit, FileWrite, Implementation, TextEdit
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


def test_a_non_python_file_is_not_edited_by_name():
    # crew-presentation#130 replaced a template "block" by name; the edit read the
    # HTML as Python and failed on the template's en dash, naming the wrong fault.
    with pytest.raises(ValidationError, match="text edit"):
        FileEdit(
            path="site/templates/sprint_overview.html",
            operation="replace",
            target="attempt-row-block",
            source="<li></li>",
        )


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


def test_imports_appended_after_the_code_that_uses_them_are_refused(repo):
    """crew-presentation#73 appended its imports three times, and failed each on a
    NameError at line 10 (crew#690)."""
    edit = TextEdit(path="metrics.py", find="", replace="import json\nfrom pathlib import Path\n")
    with pytest.raises(EditError, match="appends imports to the end.*`add_import`"):
        plan(python(repo, "metrics.py", MODULE), edit)


def test_an_entry_block_may_still_be_appended(repo):
    edit = TextEdit(path="metrics.py", find="", replace='if __name__ == "__main__":\n    main()\n')
    assert plan(python(repo, "metrics.py", MODULE), edit)["metrics.py"].endswith("    main()\n")


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


def test_a_skeleton_placeholder_may_be_replaced_whole(repo):
    # crew-presentation's build.py and tests/test_tokens.py: each story reached for a
    # whole rewrite of its placeholder first (crew#690).
    (repo / "build.py").write_text('"""Build the site."""\n# crew:placeholder\nimport sys\n')
    real = FileWrite(path="build.py", content='"""Build the site."""\n\nimport json\n')
    assert regression.overwrites_existing(repo, [real]) == []
    # Kept marked, the real file could be rewritten whole by every later story.
    still = FileWrite(path="build.py", content="# crew:placeholder\nimport json\n")
    assert regression.overwrites_existing(repo, [still]) == ["build.py"]


PLACEHOLDER_BUILD = (
    '"""Build the site."""\n# crew:placeholder\n\nimport sys\n\n'
    'if __name__ == "__main__":\n    print("build: placeholder (no-op)")\n    sys.exit(0)\n'
)


def test_a_placeholder_rewritten_by_quoting_may_bring_in_definitions(repo):
    """crew-presentation#73 rewrote its placeholder `build.py` by quoting, six times (crew#731)."""
    edit = TextEdit(
        path="build.py",
        find=PLACEHOLDER_BUILD,
        replace='"""Build the site."""\n\nimport sys\n\n\ndef main():\n    return 0\n\n\n'
        'if __name__ == "__main__":\n    sys.exit(main())\n',
    )
    after = plan(python(repo, "build.py", PLACEHOLDER_BUILD), edit)["build.py"]
    assert "def main():" in after and "crew:placeholder" not in after


def test_a_placeholder_test_file_may_take_helpers_with_its_imports(repo):
    """crew-presentation#84 replaced the placeholder's skip with its imports and two helpers."""
    text = (
        '"""Token checks.\n\ncrew:placeholder\n"""\n\nimport pytest\n\n'
        'pytestmark = pytest.mark.skip(reason="later")\n\n\ndef test_placeholder_passes():\n'
        "    pass\n"
    )
    edits = [
        TextEdit(path="tests/test_tokens.py", find="crew:placeholder\n", replace="Real ones.\n"),
        TextEdit(
            path="tests/test_tokens.py",
            find='import pytest\n\npytestmark = pytest.mark.skip(reason="later")\n',
            replace="from pathlib import Path\n\n\ndef _css():\n    return Path('a.css')\n",
        ),
    ]
    (repo / "tests").mkdir()
    after = plan(python(repo, "tests/test_tokens.py", text), *edits)["tests/test_tokens.py"]
    assert "def _css():" in after and "pytestmark" not in after


def test_a_placeholder_left_marked_is_sent_to_new_files(repo):
    # Quoting only its print line keeps the marker, so the real file it becomes
    # could be rewritten whole by every later story (crew#725).
    edit = TextEdit(
        path="build.py",
        find='    print("build: placeholder (no-op)")\n',
        replace="    def run():\n        return 0\n",
    )
    with pytest.raises(EditError, match="placeholder. Replace it whole in `new_files`"):
        plan(python(repo, "build.py", PLACEHOLDER_BUILD), edit)


def test_appending_to_a_placeholder_is_sent_to_new_files(repo):
    edit = TextEdit(path="build.py", find="", replace="import json\n\n\ndef main():\n    pass\n")
    with pytest.raises(EditError, match="placeholder. Replace it whole in `new_files`"):
        plan(python(repo, "build.py", PLACEHOLDER_BUILD), edit)


def test_the_developer_is_told_placeholders_are_replaced_whole():
    from crew_org.crews.delivery_crew import STANDING_INSTRUCTIONS

    text = " ".join(STANDING_INSTRUCTIONS.split())
    assert "marked `crew:placeholder`" in text and "replace it whole in `new_files`" in text


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


def test_a_refused_quote_comes_back_as_the_definition_with_the_change_made(repo):
    """sprint-metrics#585 was blocked after three tries at changing one cell of a test's
    expected table with a quoted edit; the refusal only said what not to do."""
    edit = TextEdit(path="metrics.py", find="    return 0", replace="    return 1")
    with pytest.raises(EditError) as refused:
        plan(python(repo, "metrics.py", MODULE), edit)
    said = str(refused.value)
    assert "Here is `throughput` with your change made" in said
    assert "send it back whole as a `replace` edit of `throughput`" in said
    assert "def throughput(cards):\n    return 1" in said


def test_a_quote_inside_a_method_comes_back_as_that_method(repo):
    module = (
        "class Report:\n    def rows(self):\n        return []\n\n"
        "    def total(self):\n        return 0\n"
    )
    edit = TextEdit(path="report.py", find="        return []", replace="        return [1]")
    with pytest.raises(EditError) as refused:
        plan(python(repo, "report.py", module), edit)
    said = str(refused.value)
    assert "Here is `Report.rows` with your change made" in said
    assert "    def rows(self):\n        return [1]" in said and "def total" not in said


# --- 4: a quote off by a known slip is matched where it can only mean one place (crew#734) ---


TEMPLATE = '<div class="values">{{ entry.measure }}: <span>{{ entry.now }}</span></div>\n'

SCHEMA = """{
  "sprints": {
    "items": {
      "required": [
        "cycle_time_days",
        "throughput"
      ]
    }
  }
}
"""


def test_part_of_a_line_quoted_with_a_blocks_newline_is_matched(repo):
    """crew-presentation#120 quoted `{{ entry.measure }}:` from a block, which ends in a newline."""
    (repo / "page.html").write_text(TEMPLATE)
    loose: list[dict] = []
    edit = TextEdit(path="page.html", find="{{ entry.measure }}:\n", replace="{{ entry.label }}:\n")
    after = workspace.plan_text_edits(repo, [edit], loose=loose)["page.html"]
    assert after == TEMPLATE.replace("{{ entry.measure }}:", "{{ entry.label }}:")
    assert loose == [{"fallback": "quote.final_newline", "path": "page.html", "line": 1}]


def test_a_quote_without_its_newline_in_two_places_is_still_refused(repo):
    (repo / "page.html").write_text(TEMPLATE + TEMPLATE)
    edit = TextEdit(path="page.html", find="{{ entry.measure }}:\n", replace="{{ entry.label }}:\n")
    with pytest.raises(EditError, match="is not in the file"):
        plan(repo, edit)


def test_lines_quoted_at_another_indent_are_matched_and_moved_with_it(repo):
    """sprint-metrics#604 quoted a schema's lines eight spaces deeper than the file has them."""
    (repo / "schema.json").write_text(SCHEMA)
    loose: list[dict] = []
    edit = TextEdit(
        path="schema.json",
        find='            "cycle_time_days",\n            "throughput"\n',
        replace='            "cycle_time_days",\n            "cycle_time_minutes",\n'
        '            "throughput"\n',
    )
    after = workspace.plan_text_edits(repo, [edit], loose=loose)["schema.json"]
    assert (
        '        "cycle_time_days",\n        "cycle_time_minutes",\n        "throughput"\n' in after
    )
    assert loose == [{"fallback": "quote.indent", "path": "schema.json", "line": 5}]


def test_a_replacement_keeps_its_own_indentation_when_moved(repo):
    (repo / "schema.json").write_text(SCHEMA)
    edit = TextEdit(
        path="schema.json",
        find='          "throughput"\n',
        replace='          "throughput",\n          {\n            "nested": true\n          }\n',
    )
    after = plan(repo, edit)["schema.json"]
    assert '        "throughput",\n        {\n          "nested": true\n        }\n' in after


@pytest.mark.parametrize(
    "text, find",
    [
        # Two places the quote could mean, at two different indents.
        ("a:\n  x: 1\nb:\n    x: 1\n", "      x: 1\n"),
        # Its lines at different shifts: not one indent.
        ("a:\n  x: 1\n  y: 2\n", "    x: 1\n      y: 2\n"),
        # Tabs are never shifted.
        ("a:\n\tx: 1\n", "  x: 1\n"),
    ],
)
def test_a_shift_that_could_mean_another_place_or_isnt_one_indent_is_refused(repo, text, find):
    (repo / "c.yml").write_text(text)
    with pytest.raises(EditError, match="is not in the file"):
        plan(repo, TextEdit(path="c.yml", find=find, replace=find.replace("1", "3")))


def test_a_replacement_that_cant_keep_its_indentation_is_refused(repo):
    (repo / "c.yml").write_text("a:\n  x: 1\n")
    edit = TextEdit(path="c.yml", find="      x: 1\n", replace="      x: 3\n  y: 2\n")
    with pytest.raises(EditError, match="is not in the file"):
        plan(repo, edit)


def test_an_exact_quote_records_no_fallback(repo):
    loose: list[dict] = []
    edit = TextEdit(path="README.md", find="Reports metrics.", replace="Reports more.")
    workspace.plan_text_edits(repo, [edit], loose=loose)
    assert loose == []


# --- 5: quotes are of the file as shown, before named edits change it (crew#734) -----------


def test_a_quoted_rewrite_and_named_adds_to_one_file_both_apply(repo):
    """crew-presentation#73 quoted its whole placeholder and added each definition by name;
    the adds, landing before its `__main__` block, split the quote."""
    python(repo, "build.py", PLACEHOLDER_BUILD)
    header = (
        '"""Build the site."""\n\nimport sys\n\n\n'
        'if __name__ == "__main__":\n    sys.exit(main())\n'
    )
    loose: list[dict] = []
    workspace.apply_implementation(
        repo,
        Implementation(
            summary="s",
            text_edits=[TextEdit(path="build.py", find=PLACEHOLDER_BUILD, replace=header)],
            edits=[
                FileEdit(
                    path="build.py",
                    operation="add",
                    target="_load",
                    source="def _load():\n    return {}\n",
                ),
                FileEdit(
                    path="build.py",
                    operation="add",
                    target="main",
                    source="def main():\n    return 0\n",
                ),
            ],
        ),
        loose=loose,
    )
    after = (repo / "build.py").read_text()
    assert after.index("def _load") < after.index("def main") < after.index("if __name__")
    assert "crew:placeholder" not in after and loose == []


def test_a_repointed_import_and_a_named_replace_in_one_file_both_apply(repo):
    python(repo, "metrics.py", "from a import x\n\n\ndef throughput(cards):\n    return x\n")
    workspace.apply_implementation(
        repo,
        Implementation(
            summary="s",
            text_edits=[
                TextEdit(path="metrics.py", find="from a import x", replace="from b import x")
            ],
            edits=[
                FileEdit(
                    path="metrics.py",
                    operation="replace",
                    target="throughput",
                    source="def throughput(cards):\n    return x + len(cards)\n",
                )
            ],
        ),
    )
    assert (repo / "metrics.py").read_text() == (
        "from b import x\n\n\ndef throughput(cards):\n    return x + len(cards)\n"
    )


def test_an_answer_with_no_text_edits_applies_as_its_named_edits_alone(repo):
    from crew_org.tools.ast_edit import Edit, Operation, apply_edits

    python(repo, "metrics.py", MODULE)
    named = [
        FileEdit(path="metrics.py", operation="add_import", target="json", source="import json"),
        FileEdit(
            path="metrics.py",
            operation="replace",
            target="throughput",
            source="def throughput(cards):\n    return len(cards)\n",
        ),
        FileEdit(path="metrics.py", operation="delete", target="Card"),
    ]
    workspace.apply_implementation(repo, Implementation(summary="s", edits=named))
    expected = apply_edits(
        MODULE,
        [Edit(operation=Operation(e.operation), target=e.target, source=e.source) for e in named],
    )
    assert (repo / "metrics.py").read_text() == expected
