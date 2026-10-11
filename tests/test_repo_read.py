"""The read tool: a file read inside the call, under one budget (crew#583, step C4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from crew_org.tools.repo_read import Reader

METRICS = (
    "from datetime import date\n"
    "from sprint_metrics.cards import (\n    Card,\n)\n\n"
    "THRESHOLD = 3\n\n\n"
    "def cycle_time(cards):\n    return 0\n\n\n"
    "class Report:\n    @property\n    def rows(self):\n        return []\n"
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/metrics.py").write_text(METRICS)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/metrics.md").write_text("# Metrics\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    return tmp_path


def test_a_whole_file_is_read_and_spends_the_budget(repo: Path):
    reader = Reader(repo, budget=1_000)
    said = reader.tool().run({"path": "src/metrics.py"})
    assert "def cycle_time(cards):" in said and said.startswith("`src/metrics.py`")
    assert reader.read == ["src/metrics.py"] and reader.left == 1_000 - len(METRICS.strip())


def test_one_definition_is_read_by_name_with_its_decorators(repo: Path):
    reader = Reader(repo, budget=1_000)
    said = reader.run({"path": "src/metrics.py", "name": "Report.rows"})
    assert "@property\n    def rows(self):" in said and "cycle_time" not in said
    assert reader.read == ["src/metrics.py::Report.rows"]


def test_too_big_for_what_is_left_names_the_definitions_to_read_instead(repo: Path):
    reader = Reader(repo, budget=20)
    said = reader.run({"path": "src/metrics.py"})
    assert "more than is left to read" in said
    assert "THRESHOLD, cycle_time, Report, Report.rows" in said and "`need_files`" in said
    assert reader.read == [] and reader.left == 20


@pytest.mark.parametrize(
    ("arguments", "told"),
    [
        ({"path": "../secrets.env"}, "isn't a path in the repository"),
        ({"path": ".git/config"}, "isn't a path in the repository"),
        ({"path": "src/nope.py"}, "There's no file"),
        ({"path": "logo.png"}, "isn't text"),
        ({"path": "src/metrics.py", "name": "missing"}, "defines no `missing`"),
    ],
)
def test_what_cant_be_read_is_told_not_raised(repo: Path, arguments, told):
    assert told in Reader(repo, budget=1_000).run(arguments)


def test_a_file_shown_or_read_already_costs_nothing_again(repo: Path):
    reader = Reader(repo, budget=1_000, shown=["docs/metrics.md"])
    assert "already shown in full above" in reader.run({"path": "docs/metrics.md"})
    reader.run({"path": "src/metrics.py"})
    left = reader.left
    assert "read `src/metrics.py` already" in reader.run({"path": "src/metrics.py"})
    assert reader.left == left


def test_a_large_python_file_is_read_a_definition_at_a_time(repo: Path, monkeypatch):
    """The first real call spent 43k of 74k characters on one whole `report.py`."""
    from crew_org.tools import repo_read  # noqa: PLC0415

    monkeypatch.setattr(repo_read, "WHOLE_FILE_CHARS", 20)
    reader = Reader(repo, budget=1_000)
    said = reader.run({"path": "src/metrics.py"})
    assert "too large to read whole" in said and "cycle_time" in said and reader.read == []
    assert "from datetime import date\nfrom sprint_metrics.cards import (\n    Card,\n)" in said
    assert "def cycle_time" in reader.run({"path": "src/metrics.py", "name": "cycle_time"})
    assert "# Metrics" in reader.run({"path": "docs/metrics.md"}), "not Python: read whole"


def test_a_large_written_file_is_shown_as_the_definitions_changed():
    """mqucifer/sprint-metrics#567's repair was shown all of a 111k-character test file."""
    from crew_org.tools.repo_context import changed_only  # noqa: PLC0415

    shown = changed_only(METRICS, ["cycle_time", "add_import label", "Report.rows"])
    assert "from datetime import date" in shown and "def cycle_time(cards):" in shown
    assert "@property\n    def rows(self):" in shown and "THRESHOLD = 3" not in shown
    assert "Read any other by name." in shown
    assert changed_only(METRICS, ["nothing_here"]) == METRICS
