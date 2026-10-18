"""A step is shown the files its work names, and reads the rest (#231).

sprint-metrics#268's prompt was ~100k tokens, over half of it the whole test
suite, for a story that needed ~25k. Empty answers only happen on long prompts
(#312): none under 50k tokens in 99 calls; the same 100k prompt came back empty
on every serving setup tried. Focused on what the story names, #268's
repository part drops from ~91k to ~15k tokens. The split reads this way; the
Developer's session starts from a map instead (crew#778).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crew_org.tools import repo_context
from crew_org.tools.repo_context import focused_context, repository_context, select_files

PKG = "src/sprint_metrics"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / PKG).mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()
    files = {
        "pyproject.toml": "[project]\nname = 'sprint-metrics'\n",
        "README.md": "# sprint-metrics\n",
        f"{PKG}/__init__.py": "",
        f"{PKG}/report.py": "def format_table(cards):\n    return ''\n",
        f"{PKG}/metrics.py": "def calculate_cycle_time(cards):\n    return 0\n",
        f"{PKG}/serve.py": "def serve_metrics(port):\n    return None\n",
        f"{PKG}/_docs_gen.py": "def generate():\n    return ''\n",
        "tests/test_report.py": "def test_table():\n    assert True\n",
        "tests/test_metrics.py": "def test_cycle():\n    assert True\n",
        "tests/test_serve.py": "def test_serve():\n    assert True\n" * 50,
        "docs/usage.md": "# Usage\n",
        "docs/metrics.md": "# Metrics\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).write_text(text)
    return tmp_path


# --- which files a story names --------------------------------------------------------------


def test_a_path_the_story_names_is_chosen(repo: Path):
    chosen, _ = select_files(repo, "Update docs/usage.md with the new flag.")
    assert "docs/usage.md" in chosen and "docs/metrics.md" not in chosen


def test_a_unique_file_name_is_enough(repo: Path):
    chosen, _ = select_files(repo, "Extend _docs_gen.py so it writes the section.")
    assert f"{PKG}/_docs_gen.py" in chosen


def test_a_module_by_dotted_name(repo: Path):
    chosen, _ = select_files(repo, "The formatter in sprint_metrics.report gets a new column.")
    assert f"{PKG}/report.py" in chosen


def test_a_definition_named_brings_its_file_and_that_files_tests(repo: Path):
    chosen, _ = select_files(repo, "Round `calculate_cycle_time` to whole days.")
    assert f"{PKG}/metrics.py" in chosen and "tests/test_metrics.py" in chosen
    assert f"{PKG}/serve.py" not in chosen and "tests/test_serve.py" not in chosen


def test_a_called_name_counts_too(repo: Path):
    chosen, _ = select_files(repo, "Make serve_metrics(port) bind to all interfaces.")
    assert f"{PKG}/serve.py" in chosen


def test_what_the_developer_asked_for_is_added_and_nonsense_reported(repo: Path):
    chosen, unknown = select_files(
        repo, "", ["docs/metrics.md", "./src/sprint_metrics/serve.py", "nope.py"]
    )
    assert "docs/metrics.md" in chosen and f"{PKG}/serve.py" in chosen
    assert unknown == ["nope.py"]


# --- the context itself ----------------------------------------------------------------------


def test_a_small_repository_is_shown_whole(repo: Path):
    text, focus = focused_context(repo, about="anything")
    assert not focus.focused
    assert text == repository_context(repo)


def test_a_small_repository_shows_its_code_wherever_it_sits(repo: Path):
    # crew-presentation's code is build.py and tools/setup.py, outside src/: its
    # first story never saw the placeholder it replaced (crew#690).
    (repo / "build.py").write_text('"""Build the site."""\n# crew:placeholder\n')
    (repo / "tools").mkdir()
    (repo / "tools" / "setup.py").write_text("def design_system_css(version):\n    return 1\n")
    text = repository_context(repo)
    source = text.split("### Current source", 1)[1]
    assert "`build.py`" in source and "# crew:placeholder" in source
    assert "`tools/setup.py`" in source and "return 1" in source
    assert "def test_table" not in source, "tests by name only, in the map"


def test_a_large_one_shows_the_map_and_the_named_files(repo: Path):
    text, focus = focused_context(repo, about="Round `calculate_cycle_time`.", above=10)
    assert focus.focused
    assert "### Files and what they define" in text, "the map stays"
    assert "serve_metrics" in text, "every signature stays in the map"
    assert "def calculate_cycle_time(cards):\n    return 0" in text, "the named file in full"
    assert "def test_serve():" not in text, "an unnamed file's body is left out"
    assert "read it with `read_file`" in text
    assert focus.shown == [f"{PKG}/metrics.py", "tests/test_metrics.py"]


def test_the_threshold_is_read_when_called(repo: Path, monkeypatch):
    monkeypatch.setattr(repo_context, "FOCUS_ABOVE_CHARS", 10)
    _, focus = focused_context(repo, about="x")
    assert focus.focused


# --- reading inside the call (crew#583, step C4) ------------------------------------------------


def test_a_lower_ceiling_leaves_room_to_read(repo: Path):
    text, focus = focused_context(repo, about="Round `calculate_cycle_time`.", above=10, ceiling=10)
    assert "read it with `read_file`" in text and "`need_files`" not in text
    assert focus.shown == [] and focus.omitted == [f"{PKG}/metrics.py", "tests/test_metrics.py"]


def test_an_asked_file_keeps_to_the_ceiling(repo: Path, monkeypatch):
    """Shown past it, asks grew mqucifer/sprint-metrics#567's prompt to 82k tokens."""
    monkeypatch.setattr(repo_context, "FOCUSED_CHAR_CEILING", 1)
    _, focus = focused_context(repo, about="Round it.", extra=["docs/usage.md"], above=0)
    assert focus.shown == []
    assert focus.omitted[0] == "docs/usage.md", "what it asked for, first in line"


# --- named in prose (sprint-metrics#281) -------------------------------------------------------


@pytest.fixture
def described(tmp_path: Path) -> Path:
    (tmp_path / PKG).mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / f"{PKG}/metrics.py").write_text(
        "def calculate_first_attempt_rate(cards):\n    return 0\n\n\n"
        "def calculate_failure_breakdown(cards):\n    return []\n"
    )
    (tmp_path / f"{PKG}/report.py").write_text(
        "def report(cards):\n    return ''\n\n\ndef format_markdown_table(rows):\n    return ''\n"
    )
    (tmp_path / "tests/test_metrics.py").write_text("def test_rate():\n    assert True\n")
    (tmp_path / "docs/metrics.md").write_text("# Metrics\n")
    return tmp_path


def test_a_docs_story_is_shown_the_code_its_prose_describes(described: Path):
    """#281 documented the first-attempt rate and was shown only the doc."""
    about = "Document how first-attempt rate and failure breakdown are computed in docs/metrics.md."
    chosen, _ = select_files(described, about)
    assert chosen == ["docs/metrics.md", f"{PKG}/metrics.py", "tests/test_metrics.py"]


def test_the_verb_is_dropped_and_separators_are_alike(described: Path):
    chosen, _ = select_files(described, "Widen the Markdown-table columns.")
    assert f"{PKG}/report.py" in chosen


def test_a_single_word_name_is_not_matched_in_prose(described: Path):
    chosen, _ = select_files(described, "The report should read better.")
    assert f"{PKG}/report.py" not in chosen


# --- files named for what they are (#335) ----------------------------------------------------

DOCKERFILE = "FROM python:3.12-slim\nRUN pip install .\n"


def test_a_dockerfile_asked_for_is_shown(repo: Path):
    """sprint-metrics#308 asked for its Dockerfile three times and changed only a test."""
    (repo / "Dockerfile").write_text(DOCKERFILE)
    text, focus = focused_context(repo, about="Add a non-root USER", extra=["Dockerfile"], above=0)
    assert "FROM python:3.12-slim" in text and "Dockerfile" in focus.shown


def test_a_small_repository_shows_its_dockerfile_whole(repo: Path):
    (repo / "Dockerfile").write_text(DOCKERFILE)
    (repo / "Dockerfile.dev").write_text("FROM python:3.12\n")
    text = repository_context(repo)
    assert "FROM python:3.12-slim" in text and "FROM python:3.12\n" in text


def test_a_file_asked_for_that_cant_be_shown_is_said_to_be(repo: Path):
    (repo / "logo.png").write_bytes(b"\x89PNG")
    text, focus = focused_context(repo, about="x", extra=["logo.png"], above=0)
    assert "Not shown, because they aren't text: `logo.png`" in text
    assert "logo.png" not in focus.shown


def test_text_is_known_by_name_as_well_as_suffix():
    for name in [
        "Dockerfile",
        "Dockerfile.dev",
        "app.dockerfile",
        "Containerfile",
        "Makefile",
        ".dockerignore",
        "compose.yaml",
    ]:
        assert repo_context.is_text(Path(name)), name
    assert not repo_context.is_text(Path("logo.png"))


# --- what a test imports (crew#591) ---------------------------------------------------------


@pytest.fixture
def repairing(repo: Path) -> Path:
    (repo / "tests/test_metrics.py").write_text(
        "from sprint_metrics import metrics\n"
        "from sprint_metrics.report import format_table\n\n\n"
        "def test_cycle():\n    assert metrics.calculate_cycle_time([]) == 0\n"
    )
    return repo


def test_the_code_a_failing_test_imports_is_found(repairing: Path):
    found = repo_context.imported_files(repairing, "tests/test_metrics.py")
    assert found == [f"{PKG}/metrics.py", f"{PKG}/report.py"]


# --- the graph: imports shown, importers and covering tests named (crew#583, C3) ------------


@pytest.fixture
def graph(repo: Path) -> Path:
    (repo / PKG / "card.py").write_text("class Card:\n    points: int = 0\n")
    (repo / PKG / "metrics.py").write_text(
        "from sprint_metrics.card import Card\n\n\ndef calculate_cycle_time(cards):\n    return 0\n"
    )
    (repo / PKG / "report.py").write_text(
        "from sprint_metrics.metrics import calculate_cycle_time\n\n\ndef format_table(cards):\n"
        "    return calculate_cycle_time(cards)\n"
    )
    return repo


class Map:
    """The coverage map, standing in: which tests run which definitions."""

    tests = {
        "tests/test_report.py::test_table": {f"{PKG}/metrics.py::calculate_cycle_time", "a", "b"},
        "tests/test_schema.py::test_keys": {f"{PKG}/metrics.py::calculate_cycle_time"},
    }

    def covering(self, files=(), names=()):
        return sorted(t for t, units in self.tests.items() if units & set(names))

    def executed_by(self, test):
        return self.tests[test]


def test_the_named_codes_own_imports_are_shown_with_it(graph: Path, monkeypatch):
    monkeypatch.setattr(repo_context, "FOCUS_ABOVE_CHARS", 10)
    _, focus = focused_context(graph, about=f"Change {PKG}/metrics.py")
    assert f"{PKG}/card.py" in focus.shown


def test_without_a_map_only_the_importers_are_named(graph, monkeypatch):
    monkeypatch.setattr(repo_context, "FOCUS_ABOVE_CHARS", 10)
    text, _ = focused_context(graph, about=f"Change {PKG}/metrics.py")
    assert "tests/test_schema.py::test_keys" not in text and f"`{PKG}/report.py`" in text


def test_a_far_test_file_is_listed_in_brief(repo: Path):
    """Lean prompts, L1: test names were 80% of sprint-metrics#567's map."""
    (repo / "tests/test_serve.py").write_text(
        "def run_it(tmp_path):\n    return 1\n\n\n"
        + "".join(f"def test_case_{i}():\n    assert True\n\n\n" for i in range(40))
    )
    brief, _ = focused_context(repo, about="Round `calculate_cycle_time`.", above=10)
    assert "`tests/test_serve.py` — 40 tests, read any by name; helpers: run_it(tmp_path)" in brief
    assert "test_case_7" not in brief
    assert "def calculate_cycle_time" in brief and "test_cycle" in brief, "near tests stay whole"
    assert "serve_metrics(port)" in brief, "source files keep their signatures"
