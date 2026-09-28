"""A story's declared contract change lets the merged tests it names go (#316, #311).

The Business Analyst writes `contract change: <the tests it updates>` when a
story changes what merged tests assert. Nothing in the regression guard read
it: sprint-metrics's docs redesign ends by removing `tests/test_readme.py`,
declared, and would have been refused three times and blocked, as #200 was.
And #200's Developer, asked to restore tests its own earlier attempt deleted,
was never shown them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crew_org.crews.delivery_crew import FileEdit, Implementation, RetiredTest
from crew_org.events import EventKind
from crew_org.flows.board_flow import existing_tests_line, render_story_body
from crew_org.flows.delivery import (
    DeliveryOutcome,
    _pr_body,
    branch_name,
    carried_retirements,
    declared_contract,
)
from crew_org.tools.regression import (
    REMOVED,
    broken_contracts,
    describe_contracts,
    draft_breaks,
    lost_names,
    merged_base,
    without_declared,
    without_moves,
    without_retired,
)
from tests.test_delivery_flow import (  # noqa: F401
    IMPL,
    FakeIssues,
    FakeWorkspace,
    green,
    harness,
    story,
)
from tests.test_pinning import story as split_story
from tests.test_regression_merged import git

PKG = "src/sprint_metrics"
README_TESTS = "tests/test_readme.py"
REPORT_TESTS = "tests/test_report.py"
LABEL_TEST = "test_markdown_shows_first_attempt_rate"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """sprint-metrics before its docs redesign: README tests, and a report test."""
    (tmp_path / PKG).mkdir(parents=True)
    (tmp_path / PKG / "report.py").write_text("def label():\n    return 'First attempt rate'\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / README_TESTS).write_text(
        "def test_readme_installation():\n    assert True\n\n\n"
        "def test_readme_worked_example():\n    assert True\n"
    )
    (tmp_path / REPORT_TESTS).write_text(
        "import pytest\n\n\n"
        "@pytest.mark.parametrize('fmt', ['markdown'])\n"
        f"def {LABEL_TEST}(fmt):\n"
        "    assert 'First attempt rate' in fmt\n\n\n"
        "def test_table():\n    assert True\n"
    )
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "merged")
    git(tmp_path, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    git(tmp_path, "checkout", "-qb", "feat/docs")
    return tmp_path


def judge(repo: Path, implementation: Implementation, body: str) -> tuple[dict, list[str]]:
    """Every guard, in delivery's order, with the story's declaration last."""
    merged = merged_base(repo)
    broken = broken_contracts(repo, implementation.all_edits, merged)
    broken = draft_breaks(repo, implementation, merged) | broken
    broken = without_moves(repo, implementation, broken, merged)
    broken |= lost_names(repo, implementation, merged)
    broken = without_retired(broken, implementation)
    return without_declared(broken, declared_contract(body))


def change(**parts) -> Implementation:
    return Implementation(summary="Replace the README tests", **parts)


REMOVE_README_TESTS = change(deleted_files=[README_TESTS])
DECLARED = existing_tests_line(f"contract change: {README_TESTS}")


# --- the story says it, and the guard reads it --------------------------------------------


def test_a_declared_test_file_may_be_removed(repo: Path):
    """The last step of sprint-metrics's docs epic."""
    broken, excused = judge(repo, REMOVE_README_TESTS, f"Story\n\n{DECLARED}\n")
    assert broken == {}
    assert excused == [
        f"{README_TESTS}::test_readme_installation",
        f"{README_TESTS}::test_readme_worked_example",
    ]


def test_the_same_removal_undeclared_is_still_refused(repo: Path):
    broken, excused = judge(repo, REMOVE_README_TESTS, "Story with no Existing tests line\n")
    assert set(broken) == {
        f"{README_TESTS}::test_readme_installation",
        f"{README_TESTS}::test_readme_worked_example",
    }
    assert excused == []


def test_an_opt_in_story_lets_no_merged_test_go(repo: Path):
    body = existing_tests_line("opt-in: only with --docs")
    broken, _ = judge(repo, REMOVE_README_TESTS, body)
    assert f"{README_TESTS}::test_readme_installation" in broken


def test_declaring_one_file_does_not_excuse_another(repo: Path):
    remove_label = FileEdit(path=REPORT_TESTS, operation="delete", target=LABEL_TEST)
    broken, _ = judge(repo, change(deleted_files=[README_TESTS], edits=[remove_label]), DECLARED)
    assert set(broken) == {f"{REPORT_TESTS}::{LABEL_TEST}"}


def test_a_test_may_be_declared_by_name_alone(repo: Path):
    """How the Business Analyst tends to write it: `updates test_default_table_rows`."""
    remove_label = FileEdit(path=REPORT_TESTS, operation="delete", target=LABEL_TEST)
    body = existing_tests_line(f"contract change: updates {LABEL_TEST}")
    broken, excused = judge(repo, change(edits=[remove_label]), body)
    assert broken == {}
    assert excused == [f"{REPORT_TESTS}::{LABEL_TEST}"]


def test_a_declaration_never_excuses_code():
    """Only tests: a declared `report.py` still can't lose `label`."""
    broken = {f"{PKG}/report.py::label": ("def label()", REMOVED)}
    kept, excused = without_declared(broken, {f"{PKG}/report.py", "label"})
    assert kept == broken
    assert excused == []


def test_stories_written_before_311_are_still_read():
    body = "**Behaviour merged tests pin** — contract change: tests/test_readme.py\n"
    assert declared_contract(body) == {"tests/test_readme.py"}


def test_what_is_declared_is_read_from_the_line_only():
    body = "Criterion: tests/test_docs.py regenerates each section.\n\n" + existing_tests_line(
        "contract change: tests/test_readme.py::test_readme_installation"
    )
    assert declared_contract(body) == {"tests/test_readme.py::test_readme_installation"}


# --- the story reads right (#311) ------------------------------------------------------------


def test_opt_in_no_longer_reads_as_optional_tests():
    body = render_story_body(
        split_story(pinned_behaviour="opt-in: only with --thresholds"), 54, "F"
    )
    assert (
        "**Existing tests** — unchanged: the new behaviour is opt-in (only with --thresholds)"
        in (body)
    )
    assert "Behaviour merged tests pin" not in body


def test_a_contract_change_names_what_it_changes():
    line = existing_tests_line("contract change: tests/test_readme.py")
    assert line == "**Existing tests** — this story changes what they assert: tests/test_readme.py"


def test_a_story_that_touches_no_merged_test_has_no_line():
    assert "Existing tests" not in render_story_body(split_story(), 54, "F")


# --- the pull request says which tests went, and the refusal shows them ---------------------


def test_the_pull_request_lists_what_the_declaration_let_go():
    outcome = DeliveryOutcome(
        card=6, contract_changed=[f"{README_TESTS}::test_readme_installation"]
    )
    body = _pr_body(story(), IMPL, outcome)
    assert "## Tests the story declares it changes" in body
    assert f"`{README_TESTS}::test_readme_installation`" in body


def test_a_pull_request_without_one_says_nothing_about_it():
    assert "declares it changes" not in _pr_body(story(), IMPL, DeliveryOutcome(card=6))


def test_a_refused_removal_shows_the_test_as_merged_decorators_and_all(repo: Path):
    """#200: asked three times to restore tests its own earlier attempt deleted, never shown."""
    remove_label = FileEdit(path=REPORT_TESTS, operation="delete", target=LABEL_TEST)
    broken, _ = judge(repo, change(edits=[remove_label]), "")
    told = describe_contracts(broken, merged_base(repo))
    assert "add it back with an `add` edit" in told
    assert "@pytest.mark.parametrize('fmt', ['markdown'])" in told
    assert f"def {LABEL_TEST}(fmt):" in told
    assert "def test_table" not in told


def test_without_the_merged_base_the_refusal_still_reads(repo: Path):
    broken = {f"{REPORT_TESTS}::{LABEL_TEST}": ("def(fmt)", REMOVED)}
    told = describe_contracts(broken)
    assert "list it in `retired_tests`" in told
    assert "add it back" not in told


# --- delivery reads it from the story it is building ---------------------------------------


def _through_delivery(harness, monkeypatch, tmp_path, body, cards=None):  # noqa: F811
    def seeded_open(self, branch, *, resume=False):
        self.resumed = False
        path = tmp_path / branch.replace("/", "__")
        (path / "tests").mkdir(parents=True, exist_ok=True)
        (path / README_TESTS).write_text(
            "def test_readme_installation():\n    assert True\n\n\n"
            "def test_readme_basic_usage():\n    assert True\n"
        )
        return path

    monkeypatch.setattr(FakeWorkspace, "open", seeded_open, raising=False)
    monkeypatch.setattr(FakeIssues, "get", lambda self, repo, number: {"body": body})
    removal = change(
        edits=[FileEdit(path=README_TESTS, operation="delete", target="test_readme_installation")]
    )
    result, _, _, _, _, seen = harness(checks=[green()], implement=lambda n: removal, cards=cards)
    refused = [
        e
        for e in seen
        if e.kind == EventKind.ESCALATION_DECIDED and e.detail["failure_class"] == "REGRESSION"
    ]
    return result, refused


def test_a_story_that_declares_it_is_delivered(harness, monkeypatch, tmp_path):  # noqa: F811
    result, refused = _through_delivery(harness, monkeypatch, tmp_path, f"Story\n\n{DECLARED}\n")
    assert refused == []
    assert result.delivered


def test_a_story_that_does_not_is_refused(harness, monkeypatch, tmp_path):  # noqa: F811
    result, refused = _through_delivery(harness, monkeypatch, tmp_path, "Story\n")
    assert refused
    assert not result.delivered


# --- a rework keeps what its pull request already retired (sprint-metrics#200) --------------

PR_247 = """Change the markdown label.

## Changes

- `tests/test_prior_sprint.py` — add `test_prior_sprint_markdown_first_attempt_rate_with_change`

## Criteria the existing tests prove

- AC2: `tests/test_prior_sprint.py::test_markdown_without_prior_sprint_has_no_was`

## Tests retired

- `tests/test_crew_performance.py::test_flags_low_rate`: Pins the old label
- `tests/test_crew_performance.py::test_prior_rate_change`: Pins it

## Tests the story declares it changes

- `tests/test_readme.py::test_readme_installation`

Closes #200
"""


class PullWithBody:
    def __init__(self, body):
        self.body = body

    def pull(self, repo, number):
        return {"body": self.body}


def test_a_rework_reads_back_what_its_pull_request_retired():
    assert carried_retirements(PullWithBody(PR_247), "sprint-metrics", 247) == {
        "tests/test_crew_performance.py::test_flags_low_rate",
        "tests/test_crew_performance.py::test_prior_rate_change",
        "tests/test_readme.py::test_readme_installation",
    }


def test_only_those_sections_are_read():
    """`Criteria the existing tests prove` names a test too, and it isn't let go."""
    found = carried_retirements(PullWithBody(PR_247), "sprint-metrics", 247)
    assert "tests/test_prior_sprint.py::test_markdown_without_prior_sprint_has_no_was" not in found


def test_a_pull_request_that_cannot_be_read_carries_nothing():
    class Down:
        def pull(self, repo, number):
            raise RuntimeError("502")

    assert carried_retirements(Down(), "sprint-metrics", 247) == set()


def test_what_the_pr_body_writes_the_rework_reads():
    retired = RetiredTest(
        path=REPORT_TESTS, test=LABEL_TEST, why="pins the old label, which this story changes"
    )
    outcome = DeliveryOutcome(card=6, contract_changed=[f"{README_TESTS}::test_readme_basic"])
    body = _pr_body(story(), IMPL.model_copy(update={"retired_tests": [retired]}), outcome)
    assert carried_retirements(PullWithBody(body), "r", 1) == {
        f"{REPORT_TESTS}::{LABEL_TEST}",
        f"{README_TESTS}::test_readme_basic",
    }


def test_the_rework_of_200_is_not_refused_for_what_its_review_let_go(
    harness,  # noqa: F811
    monkeypatch,
    tmp_path,
):
    """The fifth test: retired in the pull request, allowed by the review, deleted on the branch."""
    branch = branch_name(6, story().title)
    pull = {"number": 247, "head": {"ref": branch, "sha": "abc"}}
    monkeypatch.setattr(FakeIssues, "landable", {branch: pull}, raising=False)
    monkeypatch.setattr(FakeIssues, "open_pulls", lambda self, repo: [pull], raising=False)
    retired = f"{README_TESTS}::test_readme_installation"
    monkeypatch.setattr(
        FakeIssues,
        "pull",
        lambda self, repo, n: {
            "body": f"## Tests retired\n\n- `{retired}`: the story ends it\n",
            "mergeable_state": "clean",
            "mergeable": True,
        },
        raising=False,
    )
    monkeypatch.setattr(
        FakeIssues,
        "pull_reviews",
        lambda self, repo, n: [{"state": "CHANGES_REQUESTED", "commit_id": "abc"}],
        raising=False,
    )
    returned = story().model_copy(update={"status": "In Progress"})
    result, refused = _through_delivery(harness, monkeypatch, tmp_path, "Story\n", cards=[returned])
    assert refused == []
    assert [o.pr for o in result.delivered] == [247]
