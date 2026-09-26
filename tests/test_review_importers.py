"""The Code Reviewer is shown who imports the names a diff changes (#215).

On sprint-metrics PR #139 the reviewer called nine imports in
`crew_performance.py` "unused". They were pass-throughs `__init__.py` imports
from there. The reviewer saw the diff alone; the regression guard already
knew.
"""

from __future__ import annotations

from pathlib import Path

from crew_org.events import EventSink
from crew_org.flows import review as review_flow
from crew_org.flows.review import review_open_pulls
from crew_org.tools.review_evidence import changed_names, importers_section
from tests.test_review import APPROVAL, BOT, FakeIssues, pull

DIFF = """diff --git a/src/sm/crew_performance.py b/src/sm/crew_performance.py
+++ b/src/sm/crew_performance.py
@@ -1,6 +1,6 @@
 from .metrics import (
-    calculate_cycle_time,
+    calculate_cycle_time,  # kept
     calculate_throughput,
 )
-def format_table(cards):
+def format_table(cards, *, wide=False):
     return render(cards)
"""


def repo(tmp_path: Path) -> Path:
    src = tmp_path / "src" / "sm"
    src.mkdir(parents=True)
    (src / "metrics.py").write_text("def calculate_cycle_time(c):\n    return 1\n")
    (src / "crew_performance.py").write_text(
        "from .metrics import (\n    calculate_cycle_time,\n    calculate_throughput,\n)\n\n"
        "def format_table(cards):\n    return render(cards)\n"
    )
    (src / "__init__.py").write_text(
        "from .crew_performance import calculate_cycle_time, format_table\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_table.py").write_text(
        "from sm.crew_performance import format_table\n"
    )
    return tmp_path


def test_the_names_a_diff_changes_are_read_from_its_lines():
    assert changed_names(DIFF) == {
        "src/sm/crew_performance.py": {"calculate_cycle_time", "format_table"}
    }


def test_a_pass_through_is_shown_as_passed_along_not_unused(tmp_path):
    text = importers_section(repo(tmp_path), DIFF)
    assert (
        "- `src/sm/crew_performance.py` `calculate_cycle_time`: passed along, not unused. "
        "Nothing in this file uses it, and `src/sm/__init__.py` imports it from here"
    ) in text


def test_a_definition_is_shown_with_every_file_that_imports_it(tmp_path):
    text = importers_section(repo(tmp_path), DIFF)
    assert (
        "- `src/sm/crew_performance.py` `format_table`: imported from here by "
        "`src/sm/__init__.py`, `tests/test_table.py`"
    ) in text


def test_names_nobody_imports_show_nothing(tmp_path):
    diff = "+++ b/src/sm/metrics.py\n+def new_one():\n+    pass\n"
    assert importers_section(repo(tmp_path), diff) == ""


def test_the_reviewer_is_given_it_when_there_is_a_clone(monkeypatch, tmp_path):
    class Diffing(FakeIssues):
        def pull_diff(self, repo, number):
            return DIFF

    seen: dict = {}

    def judge(*args, **kwargs):
        seen.update(kwargs)
        return APPROVAL

    monkeypatch.setattr(review_flow, "review_diff", judge)
    review_open_pulls(
        Diffing([pull()]),
        EventSink(None),
        repo="sprint-metrics",
        bot_login=BOT,
        clone=repo(tmp_path),
    )
    assert "passed along, not unused" in seen["importers"]

    seen.clear()
    review_open_pulls(Diffing([pull()]), EventSink(None), repo="sprint-metrics", bot_login=BOT)
    assert seen["importers"] == "", "no clone, nothing shown"
