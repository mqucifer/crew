"""The gates at `done` read a session's branch against what's merged (crew#778, ADR 0032).

Today's checks ran on an answer before it was applied. A session's edits are already
on its branch, so the gates read the diff: policy, scope, the merged tests it changes
against `done`'s declaration and the story's Existing tests line, the whole checks,
and a bug's reproduction. Every finding goes back together.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from crew_org.flows import gates
from crew_org.flows.delivery import _LISTED_TEST
from crew_org.tools import reproduction
from crew_org.tools.github_project import Card
from crew_org.tools.workspace import CheckResult, CommandResult

MODULE = "src/sprint_metrics/metrics.py"
TESTS = "tests/test_metrics.py"
MERGED_TESTS = (
    "from sprint_metrics.metrics import rate\n\n\n"
    "def test_rate_is_capped():\n    assert rate(2, 1) == 100\n\n\n"
    "def test_rate_of_nothing():\n    assert rate(0, 0) == 0\n"
)
UNCHANGED = "**Existing tests** — unchanged: the new behaviour is opt-in"
CHANGES = f"**Existing tests** — this story changes what they assert: {TESTS}::test_rate_is_capped"


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A clone whose default branch has one module and its tests, and a story's branch."""
    (tmp_path / "src/sprint_metrics").mkdir(parents=True)
    (tmp_path / MODULE).write_text(
        "def rate(escalations, cards):\n    return min(100, escalations * 100 // max(cards, 1))\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / TESTS).write_text(MERGED_TESTS)
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "merged")
    git(tmp_path, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    git(tmp_path, "checkout", "-qb", "feat/662-uncap")
    return tmp_path


def card(work_type: str = "Story") -> Card:
    return Card(
        item_id="S662",
        number=662,
        title="Remove the 100% cap",
        repo="sprint-metrics",
        status="In Progress",
        state="OPEN",
        work_type=work_type,
        parent=650,
        sprint="Sprint 30",
    )


def green(worktree: Path) -> CheckResult:
    return CheckResult(results=[CommandResult("uv run pytest -q", 0, "1 passed")])


def red(worktree: Path) -> CheckResult:
    return CheckResult(
        results=[
            CommandResult(
                "uv run pytest -q",
                1,
                f"FAILED {TESTS}::test_rate_of_nothing - assert 1 == 0\n1 failed in 0.1s",
            )
        ]
    )


def done(*changed: tuple[str, str], criteria=None) -> dict:
    return {
        "summary": "Removed the cap.",
        "criteria": criteria
        if criteria is not None
        else [{"criterion": "2 escalations, 1 card: 200", "test": f"{TESTS}::test_rate_is_capped"}],
        "changed_tests": [{"test": t, "why": why} for t, why in changed],
        "docs": "docs/metrics.md",
    }


def uncap(repo: Path) -> None:
    """The story's change: the cap goes, and the test that pinned it now asserts 200."""
    (repo / MODULE).write_text(
        "def rate(escalations, cards):\n    return escalations * 100 // max(cards, 1)\n"
    )
    (repo / TESTS).write_text(MERGED_TESTS.replace("== 100", "== 200"))


def at_done(repo: Path, said: dict, story: str = CHANGES, **kw) -> gates.Verdict:
    kw.setdefault("run_checks", green)
    kw.setdefault("permissions", {})
    return gates.at_done(repo, said, card=kw.pop("card", card()), story=story, record=None, **kw)


def test_a_changed_test_the_story_names_and_done_declares_goes_on(repo):
    uncap(repo)
    verdict = at_done(repo, done((f"{TESTS}::test_rate_is_capped", "the cap is gone")))
    assert verdict.passed, verdict.findings
    assert verdict.declared == {f"{TESTS}::test_rate_is_capped": "the cap is gone"}
    assert not verdict.questions and not verdict.disagree


def test_a_changed_test_done_doesnt_declare_goes_back(repo):
    uncap(repo)
    verdict = at_done(repo, done())
    assert [f.rule for f in verdict.findings] == ["check.gates.these_merged_tests_changed"]
    assert f"`{TESTS}::test_rate_is_capped` (changed)" in verdict.findings[0].says


def test_a_removed_test_is_a_change_too(repo):
    (repo / TESTS).write_text(MERGED_TESTS.split("\n\n\ndef test_rate_of_nothing")[0] + "\n")
    verdict = at_done(repo, done())
    assert f"`{TESTS}::test_rate_of_nothing` (removed)" in verdict.findings[0].says


def test_a_test_only_reformatted_hasnt_changed(repo):
    (repo / TESTS).write_text(MERGED_TESTS.replace("rate(0, 0) == 0", "rate(0, 0)==0"))
    (repo / MODULE).write_text((repo / MODULE).read_text() + "\n\ndef other():\n    return 1\n")
    verdict = at_done(repo, done())
    assert verdict.passed, verdict.findings


def test_one_the_changes_line_doesnt_name_is_a_question_for_review(repo):
    """The operator's call inside decision 6 (crew#778), on mqucifer/sprint-metrics#662's test."""
    uncap(repo)
    (repo / TESTS).write_text(
        (repo / TESTS).read_text().replace("rate(0, 0) == 0", "rate(0, 1) == 0")
    )
    verdict = at_done(
        repo,
        done(
            (f"{TESTS}::test_rate_is_capped", "the cap is gone"),
            (f"{TESTS}::test_rate_of_nothing", "one card, so the rate is defined"),
        ),
    )
    assert verdict.passed
    assert list(verdict.declared) == [f"{TESTS}::test_rate_is_capped"]
    assert "doesn't name it" in verdict.questions[f"{TESTS}::test_rate_of_nothing"]
    assert "one card, so the rate is defined" in verdict.questions[f"{TESTS}::test_rate_of_nothing"]


def test_a_story_that_says_its_tests_stay_unchanged_disagrees(repo):
    uncap(repo)
    verdict = at_done(
        repo, done((f"{TESTS}::test_rate_is_capped", "the cap is gone")), story=UNCHANGED
    )
    assert verdict.passed, "the ruling settles it, not a refusal"
    assert verdict.disagree == {f"{TESTS}::test_rate_is_capped": "the cap is gone"}


def test_a_declared_test_the_diff_doesnt_change_is_a_question(repo):
    (repo / MODULE).write_text((repo / MODULE).read_text() + "\n\ndef other():\n    return 1\n")
    verdict = at_done(repo, done((f"{TESTS}::test_rate_of_nothing", "it should change")))
    assert verdict.passed
    assert "the diff doesn't change it" in verdict.questions[f"{TESTS}::test_rate_of_nothing"]


def test_a_whole_file_or_a_class_method_declares_its_tests(repo):
    (repo / TESTS).write_text(
        MERGED_TESTS.replace("== 100", "== 200")
        + "\n\nclass TestRates:\n    def test_one(self):\n        assert True\n"
    )
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "draft")
    verdict = at_done(repo, done((TESTS, "the cap is gone")))
    assert f"{TESTS}::test_rate_is_capped" in verdict.declared


def test_merged_code_removed_goes_back_but_a_private_helper_doesnt(repo):
    (repo / MODULE).write_text("def _rate(escalations, cards):\n    return 0\n")
    verdict = at_done(repo, done())
    rules = [f.rule for f in verdict.findings]
    assert "guard.regression.broken_contracts" in rules
    assert "`rate` in `src/sprint_metrics/metrics.py` removed entirely" in verdict.findings[0].says


def test_a_protected_path_goes_back(repo):
    (repo / ".crew").mkdir()
    (repo / ".crew/project.yaml").write_text("intent: {}\n")
    verdict = at_done(repo, done())
    assert [f.rule for f in verdict.findings] == ["guard.bounds.out_of_bounds"]
    assert "`.crew/project.yaml` is protected" in verdict.findings[0].says


def test_a_workflow_the_crew_cant_push_goes_back(repo):
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / ".github/workflows/ci.yml").write_text("on: push\njobs: {}\n")
    verdict = at_done(repo, done(), permissions={"contents": "write"})
    assert "check.delivery.workflows_not_permitted" in [f.rule for f in verdict.findings]


def test_every_finding_goes_back_together(repo):
    uncap(repo)
    verdict = at_done(
        repo,
        done(criteria=[{"criterion": "200", "test": f"{TESTS}::test_rate_exceeds_100"}]),
        run_checks=red,
    )
    assert [f.rule for f in verdict.findings] == [
        "check.delivery.these_named_tests_dont_exist",
        "check.gates.these_merged_tests_changed",
        "check.delivery.lint_or_tests_failed",
    ]
    assert "test_rate_of_nothing" in verdict.findings[-1].says, "the report, each cause once"


def test_an_empty_diff_whose_criteria_existing_tests_prove_is_already_done(repo):
    """The operator's call (crew#778): today's already-done path (mqucifer/crew#221)."""
    verdict = at_done(repo, done())
    assert verdict.already_done and verdict.passed


def test_an_empty_diff_that_doesnt_prove_each_criterion_goes_back(repo):
    unproven = done(criteria=[{"criterion": "200", "test": ""}])
    verdict = at_done(repo, unproven)
    assert not verdict.already_done
    assert [f.rule for f in verdict.findings] == ["check.gates.the_branch_changes_nothing"]
    assert [f.rule for f in at_done(repo, done(), run_checks=red).findings] == [
        "check.gates.the_branch_changes_nothing",
        "check.delivery.lint_or_tests_failed",
    ]


def test_a_bugs_tests_must_fail_without_the_fix(repo, monkeypatch):
    seen = []

    def reproduces(worktree, merged, tests):
        seen.append(tests)
        return reproduction.Reproduction(passed=[f"{TESTS}::test_rate_is_capped"])

    monkeypatch.setattr(reproduction, "reproduces", reproduces)
    uncap(repo)
    said = done((f"{TESTS}::test_rate_is_capped", "the cap is gone"))
    assert at_done(repo, said).passed, "a story's tests show new behaviour: not checked"
    verdict = at_done(repo, said, card=card("Bug"))
    assert seen == [[(TESTS, "test_rate_is_capped")]]
    assert [f.rule for f in verdict.findings] == ["guard.reproduce.check"]


def test_the_pull_requests_sections_are_the_ones_a_rework_reads_back(repo):
    uncap(repo)
    verdict = at_done(repo, done((f"{TESTS}::test_rate_is_capped", "the cap is gone")))
    verdict.questions["tests/test_other.py::test_x"] = "a question"
    text = "\n".join(gates.pr_sections(verdict))
    assert "## Tests the story declares it changes" in text
    assert gates.QUESTIONS_HEADING in text
    assert _LISTED_TEST.findall(text.split(gates.QUESTIONS_HEADING)[0]) == [
        f"{TESTS}::test_rate_is_capped"
    ]
