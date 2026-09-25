"""The Code Reviewer is shown a PR's checks and the code its diff imports (#160).

The case: sprint-metrics PR #88 added tests for behaviour #73 had already put on
`main`. CI passed. The Reviewer, shown only the diff, claimed the tests "would
fail" without an implementation.
"""

from __future__ import annotations

from crew_org.events import EventSink
from crew_org.flows import review as review_flow
from crew_org.flows.review import review_open_pulls
from crew_org.tools.review_evidence import (
    MAX_IMPORTED_CHARS,
    checks_section,
    imported_code,
    imported_modules,
)
from tests.test_review import APPROVAL, BOT, FakeIssues

PR_88_DIFF = """diff --git a/tests/test_sprint_range.py b/tests/test_sprint_range.py
+import json
+from pathlib import Path
+
+from sprint_metrics.crew_performance import main
+
+def test_wip_limits_apply_to_every_sprint_in_a_range(tmp_path):
+    assert main(["--sprints", "s.json", "--wip-limits", "w.json"]) == 0
"""

ON_MAIN = {
    "src/sprint_metrics/crew_performance.py": (
        "def main(argv=None):\n"
        '    """Report a range, applying --wip-limits and --escalations to each sprint."""\n'
        "    return 0\n"
    )
}

PR_88_BODY = (
    "Apply WIP limits and escalations to every sprint in a range report.\n\n"
    "## Verification\n\nLint and the full test suite pass in an isolated worktree.\n\n"
    "## Changes\n\n- `tests/test_sprint_range.py` (new)\n"
)


# --- 1: the checks on this head ------------------------------------------------------


def test_the_checks_on_the_head_are_shown_with_their_conclusions():
    text = checks_section(
        [{"name": "tests", "status": "completed", "conclusion": "success"}], PR_88_BODY
    )
    assert "- `tests`: success" in text


def test_a_check_still_running_says_so():
    text = checks_section([{"name": "tests", "status": "in_progress", "conclusion": None}], "")
    assert "- `tests`: in_progress" in text


def test_what_delivery_ran_itself_is_shown_labelled_as_such():
    text = checks_section([], PR_88_BODY)
    assert "- none reported yet" in text
    assert "What delivery reported from its own run" in text
    assert "Lint and the full test suite pass in an isolated worktree." in text
    assert "tests/test_sprint_range.py" not in text, "only the Verification section"


# --- 2: the code the diff leans on --------------------------------------------------


def test_the_modules_a_diff_imports_are_read_from_its_added_lines():
    assert imported_modules(PR_88_DIFF) == ["json", "pathlib", "sprint_metrics.crew_performance"]
    assert imported_modules("-from gone import thing\n") == []


def test_the_projects_own_modules_are_shown_whole_and_the_standard_library_left_out():
    text = imported_code(ON_MAIN.get, PR_88_DIFF)
    assert "`src/sprint_metrics/crew_performance.py`" in text
    assert "applying --wip-limits and --escalations to each sprint" in text
    assert "json" not in text.split("```python")[0], "no file, so not the project's code"


def test_a_module_that_does_not_fit_is_named_not_cut():
    huge = {"src/big.py": "x = 1\n" * (MAX_IMPORTED_CHARS // 5)}
    text = imported_code(huge.get, "+import big\n")
    assert "Not shown, because they did not fit: `src/big.py`" in text
    assert "x = 1" not in text


def test_imports_outside_the_diff_are_read_from_the_changed_file_at_the_head():
    """PR #88 appended tests to a file whose imports are at its top, outside the diff."""
    appended = (
        "+++ b/tests/test_sprint_range.py\n"
        "@@ -185,3 +185,5 @@\n"
        "+def test_wip_limits_apply(tmp_path):\n"
        "+    assert main([]) == 0\n"
    )
    at_head = {"tests/test_sprint_range.py": "from sprint_metrics.crew_performance import main\n"}
    assert imported_code(ON_MAIN.get, appended) == "", "the diff alone shows no import"
    text = imported_code(ON_MAIN.get, appended, read_head=at_head.get)
    assert "applying --wip-limits and --escalations to each sprint" in text


def test_a_package_that_re_exports_is_followed_to_the_code():
    """`from sprint_metrics import main` reaches an __init__ that only re-exports."""
    package = {
        "src/sprint_metrics/__init__.py": "from sprint_metrics.crew_performance import main\n",
        **ON_MAIN,
    }
    text = imported_code(package.get, "+from sprint_metrics import main\n")
    assert "`src/sprint_metrics/__init__.py`" in text
    assert "applying --wip-limits and --escalations to each sprint" in text


def test_a_diff_importing_nothing_of_the_projects_adds_nothing():
    assert imported_code(ON_MAIN.get, "+import json\n") == ""


# --- 3: PR #88's case, through the review flow ------------------------------------------


def test_the_reviewer_of_pr_88_is_shown_ci_passing_and_the_code_on_main(monkeypatch):
    issues = FakeIssues(
        [
            {
                "number": 88,
                "user": {"login": BOT},
                "draft": False,
                "title": "Apply WIP limits and escalations to every sprint in a range report",
                "head": {"sha": "abc"},
                "base": {"ref": "main"},
                "body": PR_88_BODY,
            }
        ]
    )
    issues.pull_diff = lambda repo, number: PR_88_DIFF
    issues.runs = [{"name": "tests", "status": "completed", "conclusion": "success"}]
    issues.files = ON_MAIN
    shown = {}

    def reviewer(title, diff, **evidence):
        shown.update(evidence)
        return APPROVAL

    monkeypatch.setattr(review_flow, "review_diff", reviewer)
    review_open_pulls(issues, EventSink(None), repo="sprint-metrics", bot_login="reviewer[bot]")

    assert "- `tests`: success" in shown["checks"]
    assert "applying --wip-limits and --escalations to each sprint" in shown["imported"]
