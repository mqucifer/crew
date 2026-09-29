"""The Developer sees the files its work names, and asks for more (#231).

sprint-metrics#268's prompt was ~100k tokens, over half of it the whole test
suite, for a story that needed ~25k. Empty answers only happen on long prompts
(#312): none under 50k tokens in 99 calls; the same 100k prompt came back empty
on every serving setup tried. Focused on what the story names, #268's
repository part drops from ~91k to ~15k tokens.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crew_org.crews.delivery_crew import FileWrite, FirstAttempt, FirstOrDone, Implementation
from crew_org.events import EventKind
from crew_org.tools import repo_context
from crew_org.tools.repo_context import focused_context, repository_context, select_files
from tests.test_delivery_flow import FakeIssues, FakeWorkspace, green, harness, story  # noqa: F401

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


def test_a_large_one_shows_the_map_and_the_named_files(repo: Path):
    text, focus = focused_context(repo, about="Round `calculate_cycle_time`.", above=10)
    assert focus.focused
    assert "### Files and what they define" in text, "the map stays"
    assert "serve_metrics" in text, "every signature stays in the map"
    assert "def calculate_cycle_time(cards):\n    return 0" in text, "the named file in full"
    assert "def test_serve():" not in text, "an unnamed file's body is left out"
    assert "`need_files`" in text
    assert focus.shown == [f"{PKG}/metrics.py", "tests/test_metrics.py"]


def test_the_threshold_is_read_when_called(repo: Path, monkeypatch):
    monkeypatch.setattr(repo_context, "FOCUS_ABOVE_CHARS", 10)
    _, focus = focused_context(repo, about="x")
    assert focus.focused


# --- asking ----------------------------------------------------------------------------------


@pytest.mark.parametrize("answer", [FirstAttempt, FirstOrDone, Implementation])
def test_an_answer_that_only_asks_is_valid(answer):
    kwargs = {"criteria_tests": []} if answer is not Implementation else {}
    asked = answer(summary="Need to see it first", need_files=["docs/metrics.md"], **kwargs)
    assert asked.asks and asked.changes_nothing


def test_an_answer_that_neither_asks_nor_changes_anything_is_still_refused():
    with pytest.raises(ValueError, match="must create a file or edit one"):
        Implementation(summary="nothing")


def _asking_delivery(harness, monkeypatch, tmp_path, answers):  # noqa: F811
    """A large-enough repository, and a Developer that answers in turn with `answers`."""

    def seeded_open(self, branch, *, resume=False):
        self.resumed = False
        path = tmp_path / branch.replace("/", "__")
        (path / "docs").mkdir(parents=True, exist_ok=True)
        (path / "docs/metrics.md").write_text("# Metrics\nCycle time counts completed cards.\n")
        (path / "README.md").write_text("# x\n")
        return path

    monkeypatch.setattr(FakeWorkspace, "open", seeded_open, raising=False)
    monkeypatch.setattr(repo_context, "FOCUS_ABOVE_CHARS", 10)
    result, _, _, _, calls, seen = harness(
        checks=[green()], implement=lambda n: answers[min(n, len(answers)) - 1]
    )
    return result, calls, seen


ASK = FirstAttempt(
    summary="need the metrics doc", criteria_tests=[], need_files=["docs/metrics.md"]
)
WORK = FirstAttempt(
    summary="done",
    criteria_tests=[],
    new_files=[FileWrite(path="tests/test_x.py", content="def test_x():\n    assert True\n")],
)


def test_asking_shows_the_file_next_time_and_is_not_a_failure(harness, monkeypatch, tmp_path):  # noqa: F811
    result, calls, seen = _asking_delivery(harness, monkeypatch, tmp_path, [ASK, WORK])
    assert calls["implement"] == 2
    assert "Cycle time counts completed cards." in calls["context"][1]
    assert "Cycle time counts completed cards." not in calls["context"][0]
    assert not [e for e in seen if e.kind == EventKind.ESCALATION_DECIDED], "no failure counted"
    assert any("asked to see docs/metrics.md" in (e.summary or "") for e in seen)
    assert result.delivered


def test_asking_forever_is_capped_and_told_to_work(harness, monkeypatch, tmp_path):  # noqa: F811
    never = FirstAttempt(
        summary="more", criteria_tests=[], need_files=["docs/metrics.md", "nope.md"]
    )
    _, calls, _ = _asking_delivery(harness, monkeypatch, tmp_path, [never, never, WORK])
    assert calls["implement"] == 3
    assert "nope.md" in calls["feedback"][2] and "leave `need_files` empty" in calls["feedback"][2]


def test_each_attempt_records_its_context_size(harness, monkeypatch, tmp_path):  # noqa: F811
    _, _, seen = _asking_delivery(harness, monkeypatch, tmp_path, [WORK])
    sizes = [e for e in seen if (e.summary or "").startswith("#6 context:")]
    assert sizes and sizes[0].detail["focused"] is True


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
