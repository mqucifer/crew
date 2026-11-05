"""The gates at `done` read a session's branch against what's merged (crew#778, ADR 0032).

Today's checks ran on an answer before it was applied. A session's edits are already
on its branch, so the gates read the diff: policy, scope, the merged tests it changes
against `done`'s declaration and the story's Existing tests line, the whole checks,
and a bug's reproduction. Every finding goes back together.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from crew_org.flows import gates, role_tasks
from crew_org.tools import regression, reproduction
from crew_org.tools.github_project import Card
from crew_org.tools.workspace import CheckResult, CommandResult, check

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
    says = verdict.findings[0].says
    assert f"`{TESTS}::test_rate_is_capped` (changed)" in says
    # The merged test it would put back is shown (#316).
    assert "As merged:" in says and "assert rate(2, 1) == 100" in says


def test_a_removed_test_is_a_change_too(repo):
    (repo / TESTS).write_text(MERGED_TESTS.split("\n\n\ndef test_rate_of_nothing")[0] + "\n")
    verdict = at_done(repo, done())
    says = verdict.findings[0].says
    assert f"`{TESTS}::test_rate_of_nothing` (removed)" in says
    assert "def test_rate_of_nothing():\n    assert rate(0, 0) == 0" in says, "to put it back"


def test_a_test_only_reformatted_hasnt_changed(repo):
    (repo / TESTS).write_text(MERGED_TESTS.replace("rate(0, 0) == 0", "rate(0, 0)==0"))
    (repo / MODULE).write_text((repo / MODULE).read_text() + "\n\ndef other():\n    return 1\n")
    verdict = at_done(repo, done())
    assert verdict.passed, verdict.findings


def test_a_changed_test_declared_with_no_why_goes_back(repo):
    """Declared without why is undeclared, at the gate as at `done` (crew#839)."""
    uncap(repo)
    verdict = at_done(repo, done((f"{TESTS}::test_rate_is_capped", "  ")))
    assert [f.rule for f in verdict.findings] == ["check.gates.these_merged_tests_changed"]
    assert gates.undeclared_changed_tests(repo, done((f"{TESTS}::test_rate_is_capped", "")))


def test_done_names_the_changed_tests_it_doesnt_declare(repo):
    """`done`'s own check, before the gate (crew#839): one line that teaches the field."""
    uncap(repo)
    (repo / TESTS).write_text(
        (repo / TESTS).read_text().split("\n\n\ndef test_rate_of_nothing")[0] + "\n"
    )
    [finding] = gates.undeclared_changed_tests(repo, done())
    assert finding.rule == "check.gates.these_merged_tests_changed"
    assert finding.count == 2
    assert f"`{TESTS}::test_rate_is_capped` (changed)" in finding.says
    assert f"`{TESTS}::test_rate_of_nothing` (removed)" in finding.says
    assert "`changed_tests: [{test, why}]`" in finding.says
    assert "\n" not in finding.says.strip()


def test_a_done_that_passes_its_own_check_passes_the_gates(repo):
    uncap(repo)
    said = done((f"{TESTS}::test_rate_is_capped", "the cap is gone"))
    assert gates.undeclared_changed_tests(repo, said) == []
    assert at_done(repo, said).passed


def test_done_has_nothing_to_declare_with_no_merged_test_changed(repo):
    (repo / MODULE).write_text((repo / MODULE).read_text() + "\n\ndef other():\n    return 1\n")
    (repo / "tests/test_other.py").write_text("def test_other():\n    assert True\n")
    assert gates.undeclared_changed_tests(repo, done()) == []


# mqucifer/sprint-metrics#820's `done`, from its session's end (crew#926): `changed_tests`
# came as one object, and the gates walked its keys as entries.
SM_820 = json.loads((Path(__file__).parent / "fixtures/sm820_done.json").read_text())
WRONG_SHAPES = [
    SM_820,
    {**done(), "changed_tests": f"{TESTS}::test_rate_is_capped"},
    {**done(), "changed_tests": [f"{TESTS}::test_rate_is_capped", 3, None, {"test": 3}]},
    {**done(), "criteria": {"criterion": "x", "test": f"{TESTS}::test_rate_is_capped"}},
    {**done(), "criteria": ["x", None, {"criterion": ["x"], "test": 1}], "docs": ["a"]},
    {**done(), "summary": None, "docs": {"path": "x"}, "criteria": None, "changed_tests": 1},
]


@pytest.mark.parametrize("said", WRONG_SHAPES)
def test_a_done_in_any_wrong_shape_reaches_the_gates_without_an_exception(repo, said):
    """What `done` said is read only in its schema's shape; anything else is left out."""
    from crew_org.flows import session_end  # noqa: PLC0415

    uncap(repo)
    gates.missing_named_tests(repo, said)
    [finding] = gates.undeclared_changed_tests(repo, said)
    assert finding.rule == "check.gates.these_merged_tests_changed"
    assert not at_done(repo, said).passed
    session_end.last_done(said)


def test_sm_820s_done_passes_through_the_gates(repo):
    """Its one object declares nothing: the gates say so, as for a `done` that left it out."""
    uncap(repo)
    verdict = at_done(repo, SM_820)
    assert f"`{TESTS}::test_rate_is_capped` (changed)" in " ".join(f.says for f in verdict.findings)
    assert gates.entries(SM_820, "changed_tests") == []
    assert gates.entries(SM_820, "criteria") == SM_820["criteria"]


def test_entries_keep_only_the_schemas_fields_as_strings():
    said = {"criteria": [{"criterion": "x", "test": 3, "extra": "y"}, "z", {"test": "t"}]}
    assert gates.entries(said, "criteria") == [{"criterion": "x"}, {"test": "t"}]
    assert gates.entries({}, "criteria") == []


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
        "check.gates.these_merged_tests_changed",
        "check.delivery.lint_or_tests_failed",
    ], "that the named tests exist is `done`'s own check, not the gate's"
    assert "test_rate_of_nothing" in verdict.findings[-1].says, "the report, each cause once"


class Resyncing:
    """A held container whose setup, run again for changed dependency files, failed."""

    def __init__(self) -> None:
        self.ran: list[list[str]] = []

    def resync(self) -> CommandResult:
        return CommandResult("uv sync --extra dev", 2, "error: Failed to fetch: dns error")

    def exec(self, command, timeout=0):
        self.ran.append(list(command))
        return CommandResult(" ".join(command), 0, "")


def test_a_failed_setup_run_again_at_done_goes_back_with_its_output(repo):
    """Like any failed check (crew#814): the branch changed what setup reads."""
    uncap(repo)
    held = Resyncing()
    verdict = at_done(repo, done(), run_checks=lambda at: check(at, held=held))
    assert held.ran == [], "nothing runs on a setup that failed"
    assert verdict.check.resetup is False and not verdict.passed
    (finding,) = [f for f in verdict.findings if f.rule == "check.delivery.lint_or_tests_failed"]
    assert "$ uv sync --extra dev\n(exit 2)\nerror: Failed to fetch: dns error" in finding.says


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

    def reproduces(worktree, merged, tests, **kw):
        seen.append(tests)
        return reproduction.Reproduction(passed=[f"{TESTS}::test_rate_is_capped"])

    monkeypatch.setattr(reproduction, "reproduces", reproduces)
    uncap(repo)
    said = done((f"{TESTS}::test_rate_is_capped", "the cap is gone"))
    assert at_done(repo, said).passed, "a story's tests show new behaviour: not checked"
    verdict = at_done(repo, said, card=card("Bug"))
    assert seen == [[(TESTS, "test_rate_is_capped")]]
    assert [f.rule for f in verdict.findings] == ["guard.reproduce.check"]


def _all_pass(monkeypatch, seen: list | None = None) -> None:
    """The named tests, run as merged with only their files from the branch, all pass."""

    def reproduces(worktree, merged, tests, **kw):
        if seen is not None:
            seen.append(tests)
        return reproduction.Reproduction(passed=[f"{p}::{t}" for p, t in tests])

    monkeypatch.setattr(reproduction, "reproduces", reproduces)


def test_a_bug_whose_failure_shows_off_main_passes_with_a_fix_in_its_tests(repo, monkeypatch):
    """mqucifer/crew-presentation#300 (crew#1005): its test failed only on the pin's branch,
    so on main it passed. The fix branches the test on what it's given and adds one, in
    the test file alone: nothing could fail on main, and the gate refused it as final."""
    seen: list = []
    _all_pass(monkeypatch, seen)
    (repo / TESTS).write_text(
        MERGED_TESTS.replace(
            "    assert rate(2, 1) == 100\n",
            "    assert rate(2, 1) == 100\n    assert rate(1, 1) == 100\n",
        )
        + "\n\ndef test_rate_of_one():\n    assert rate(1, 2) == 50\n"
    )
    said = done(
        (f"{TESTS}::test_rate_is_capped", "it only held for one pin"),
        criteria=[
            {"criterion": "capped on either pin", "test": f"{TESTS}::test_rate_is_capped"},
            {"criterion": "a half", "test": f"{TESTS}::test_rate_of_one"},
        ],
    )
    verdict = at_done(repo, said, card=card("Bug"))
    assert verdict.passed, verdict.findings
    assert seen == [[(TESTS, "test_rate_is_capped"), (TESTS, "test_rate_of_one")]], "they ran"
    assert verdict.fixed_in_tests == [f"{TESTS}::test_rate_is_capped"]
    text = "\n".join(gates.pr_sections(verdict))
    assert gates.FIXED_IN_TESTS_HEADING in text
    assert "not in the code they test" in text, "the Code Reviewer judges where the defect is"


def test_a_bug_whose_defect_is_a_stale_test_passes_with_the_test_removed(repo, monkeypatch):
    """mqucifer/sprint-metrics#868 (crew#949, crew#1005): the Sponsor dropped a stale test;
    its other tests show the file holds. Removing a merged test is declared, and ruled
    on as any other merged test's change is."""
    _all_pass(monkeypatch)
    (repo / TESTS).write_text(MERGED_TESTS.split("\n\n\ndef test_rate_of_nothing")[0] + "\n")
    said = done(
        (f"{TESTS}::test_rate_of_nothing", "stale: the Sponsor dropped it"),
        criteria=[{"criterion": "the file holds", "test": f"{TESTS}::test_rate_is_capped"}],
    )
    story = CHANGES.replace("test_rate_is_capped", "test_rate_of_nothing")
    verdict = at_done(repo, said, story=story, card=card("Bug"))
    assert verdict.passed, verdict.findings
    assert verdict.declared == {f"{TESTS}::test_rate_of_nothing": "stale: the Sponsor dropped it"}


@pytest.mark.parametrize(
    "weaker",
    [
        "    rate(2, 1)\n",  # its assertion removed
        "    assert True\n",  # an assertion that can't fail
    ],
)
def test_a_fix_in_its_tests_that_drops_what_a_test_checks_goes_back(repo, monkeypatch, weaker):
    """Nothing failing on main, the test passing is the proof: a test made to pass by
    checking less proves nothing (crew#1005)."""
    _all_pass(monkeypatch)
    (repo / TESTS).write_text(MERGED_TESTS.replace("    assert rate(2, 1) == 100\n", weaker))
    said = done((f"{TESTS}::test_rate_is_capped", "it pinned the cap"))
    verdict = at_done(repo, said, card=card("Bug"))
    (finding,) = verdict.findings
    assert finding.rule == "guard.reproduce.check"
    assert f"- `{TESTS}::test_rate_is_capped`: `assert rate(2, 1) == 100`" in finding.says
    assert "replaced by the right one, never dropped" in finding.says
    assert not verdict.fixed_in_tests


# mqucifer/crew-presentation#300's test_build_check_exits_zero, its checks cut to a few:
# as merged, and as its fix left them, each attempt-chart check rewritten in place.
MERGED_CHECKS = [
    "assert result.returncode == 0",
    "assert sprint_files",
    "assert 'attempt-legend' in page",
    "assert 'attempt-row' in page",
]
FIXED_CHECKS = [
    "assert result.returncode == 0",
    "assert sprint_files",
    "assert 'class=\"attempt-legend\"' in page",
    "assert 'class=\"attempt-row\"' in page",
    "assert 'class=\"story-list\"' in page",
    "assert 'class=\"attempt-row\"' not in page",
]


def test_a_check_rewritten_in_place_is_kept_and_one_deleted_is_dropped():
    """Counting let a fix that added checks delete a merged one: on the real branch,
    blanking `assert sprint_files` still left 18 checks of 15 (crew#1005)."""
    assert gates.dropped_checks(MERGED_CHECKS, FIXED_CHECKS) == []
    without = [c for c in FIXED_CHECKS if not c.startswith("assert sprint_files")]
    assert gates.dropped_checks(MERGED_CHECKS, without) == [MERGED_CHECKS[1]]
    # A rewritten check blanked: its `not in` twin checks the reverse, so it can't stand in.
    without = [c for c in FIXED_CHECKS if "attempt-row\"' in page" not in c]
    assert gates.dropped_checks(MERGED_CHECKS, without) == [MERGED_CHECKS[3]]


def test_a_bug_whose_branch_only_adds_a_test_that_passes_on_main_goes_back(repo, monkeypatch):
    """No merged test changed and no code: the bug isn't fixed, and its test shows nothing."""
    _all_pass(monkeypatch)
    (repo / TESTS).write_text(MERGED_TESTS + "\n\ndef test_rate_of_one():\n    assert True\n")
    said = done(criteria=[{"criterion": "a half", "test": f"{TESTS}::test_rate_of_one"}])
    (finding,) = at_done(repo, said, card=card("Bug")).findings
    assert finding.says.startswith("The fix passes the checks, but ")


def test_a_fix_in_its_tests_whose_named_test_skipped_goes_back(repo, monkeypatch):
    """A skipped test never ran, so it shows nothing, whatever the branch changes."""
    skipped = reproduction.Reproduction(skipped={f"{TESTS}::test_rate_is_capped": "no db"})
    monkeypatch.setattr(reproduction, "reproduces", lambda *a, **kw: skipped)
    (repo / TESTS).write_text(MERGED_TESTS.replace("== 100", "== 100 * 1"))
    said = done((f"{TESTS}::test_rate_is_capped", "it pinned the cap"))
    (finding,) = at_done(repo, said, card=card("Bug")).findings
    assert "was skipped, not run: no db" in finding.says


def test_a_product_bug_whose_test_passes_on_main_still_goes_back_to_reproduce_it(repo, monkeypatch):
    """The branch changes the code as well as its test: the test has to show the bug."""
    _all_pass(monkeypatch)
    uncap(repo)
    said = done((f"{TESTS}::test_rate_is_capped", "the cap is gone"))
    verdict = at_done(repo, said, card=card("Bug"))
    (finding,) = verdict.findings
    assert finding.rule == "guard.reproduce.check"
    assert finding.says.startswith("The fix passes the checks, but ")
    assert not verdict.fixed_in_tests


def test_the_pull_request_lists_the_declared_tests_apart_from_the_questions(repo):
    uncap(repo)
    verdict = at_done(repo, done((f"{TESTS}::test_rate_is_capped", "the cap is gone")))
    verdict.questions["tests/test_other.py::test_x"] = "a question"
    text = "\n".join(gates.pr_sections(verdict))
    assert "## Tests the story declares it changes" in text
    assert gates.QUESTIONS_HEADING in text
    listed = re.findall(r"^- `([^`\s]+\.py::[\w.]+)`", text.split(gates.QUESTIONS_HEADING)[0], re.M)
    assert listed == [f"{TESTS}::test_rate_is_capped"]


def test_done_checks_the_tests_it_names_exist_and_says_write_one_first(repo):
    """The Sponsor's one check inside `done` (crew#778): a fact, not a judgement."""
    invented = done(criteria=[{"criterion": "200", "test": f"{TESTS}::test_rate_exceeds_100"}])
    [missing] = gates.missing_named_tests(repo, invented)
    assert missing.rule == "check.delivery.these_named_tests_dont_exist"
    assert missing.says.startswith(
        f"No test by these names exists: `{TESTS}::test_rate_exceeds_100`. If no test proves"
        " the criterion yet, write one and run it."
    )
    assert gates.missing_named_tests(repo, done()) == []


# --- every story adds its own changelog fragment (crew#816, crew#1010) -----------------------

# A project set up for towncrier's fragments: what waits is in `changes/`, and only a
# release writes `CHANGELOG.md`.
CHANGELOG = (
    "# Changelog\n\n<!-- towncrier release notes start -->\n\n"
    "## [1.3.3] - 2026-10-19\n\n### Fixed\n\n- A released entry\n"
)


@pytest.fixture
def kept(repo: Path) -> Path:
    """The same project, keeping a changelog, with another story's fragment merged."""
    git(repo, "checkout", "-q", "main")
    (repo / "CHANGELOG.md").write_text(CHANGELOG)
    (repo / "changes").mkdir()
    (repo / "changes/README.md").write_text("One file per change.\n")
    (repo / "changes/661.added.md").write_text("An entry another story added\n")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "changelog")
    git(repo, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    git(repo, "checkout", "-qb", "feat/663-entry")
    return repo


def _fragment(repo: Path, name: str = "663.changed.md", text: str = "Uncapped\n") -> None:
    (repo / "changes").mkdir(exist_ok=True)
    (repo / "changes" / name).write_text(text)


def _rules(repo: Path, work_type: str = "Story") -> list[str]:
    return [f.rule for f in gates.no_changelog_entry(repo, card(work_type))]


def test_a_story_that_ships_a_change_and_adds_no_fragment_goes_back(kept):
    uncap(kept)
    (finding,) = gates.no_changelog_entry(kept, card())
    assert finding.rule == "check.gates.no_changelog_entry"
    assert f"`{MODULE}`" in finding.says and "`changes/<n>.<kind>.md`" in finding.says


def test_its_fragment_lets_it_through(kept):
    uncap(kept)
    _fragment(kept)
    assert _rules(kept) == []


def test_a_fragment_with_no_issue_or_with_a_counter_is_one(kept):
    uncap(kept)
    _fragment(kept, "+uncap.fixed.md")
    assert _rules(kept) == []
    (kept / "changes/+uncap.fixed.md").unlink()
    _fragment(kept, "663.fixed.2.md")
    assert _rules(kept) == []


def test_a_file_that_isnt_a_fragment_or_is_empty_isnt_one(kept):
    uncap(kept)
    for name, text in (("663.md", "Uncapped\n"), ("663.improved.md", "Uncapped\n")):
        _fragment(kept, name, text)
    _fragment(kept, "663.changed.md", "\n")
    assert _rules(kept) == ["check.gates.no_changelog_entry"]


def test_changing_a_merged_fragment_isnt_adding_one(kept):
    uncap(kept)
    _fragment(kept, "661.added.md", "Reworded\n")
    assert _rules(kept) == ["check.gates.no_changelog_entry"]


def test_an_entry_written_into_changelog_goes_back_with_or_without_a_fragment(kept):
    """The old way, which conflicted with every release: the Code Reviewer refuses it
    too, and the gate catches it first."""
    uncap(kept)
    _fragment(kept)
    (kept / "CHANGELOG.md").write_text(
        CHANGELOG.replace(
            "<!-- towncrier release notes start -->\n",
            "<!-- towncrier release notes start -->\n\n## [Unreleased]\n\n### Changed\n\n"
            "- Uncapped\n",
        )
    )
    (finding,) = gates.no_changelog_entry(kept, card())
    assert finding.rule == "check.gates.no_changelog_entry"
    assert "`[Unreleased]`" in finding.says and "`changes/" in finding.says
    # Even on a branch that ships nothing: a docs story writing the old way.
    git(kept, "checkout", "-q", "--", MODULE, TESTS)
    (kept / "changes/663.changed.md").unlink()
    assert _rules(kept) == ["check.gates.no_changelog_entry"]


def test_tests_docs_and_settings_ship_nothing(kept):
    for path in (TESTS, "docs/metrics.md", "README.md", ".github/workflows/ci.yml", ".gitignore"):
        (kept / path).parent.mkdir(parents=True, exist_ok=True)
        (kept / path).write_text("changed\n")
    assert _rules(kept) == []


def test_a_bug_adds_its_fragment_and_a_task_doesn_t(kept):
    uncap(kept)
    assert _rules(kept, "Bug") == ["check.gates.no_changelog_entry"]
    task = card("Task").model_copy(update={"labels": frozenset({"role:devops"})})
    assert gates.no_changelog_entry(kept, task) == []


def test_a_project_with_no_changelog_is_not_asked_for_one(repo):
    uncap(repo)
    assert _rules(repo) == []


def test_the_folder_comes_from_the_projects_towncrier_setup(kept):
    (kept / "pyproject.toml").write_text('[tool.towncrier]\ndirectory = "newsfragments"\n')
    git(kept, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(kept, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "setup")
    git(kept, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    uncap(kept)
    _fragment(kept)
    assert _rules(kept) == ["check.gates.no_changelog_entry"]
    (kept / "newsfragments").mkdir()
    (kept / "newsfragments/663.changed.md").write_text("Uncapped\n")
    assert _rules(kept) == []


def test_the_gate_sends_it_back_and_done_says_so_first(kept):
    uncap(kept)
    verdict = at_done(kept, done())
    assert "check.gates.no_changelog_entry" in [f.rule for f in verdict.findings]
    (said,) = gates.no_changelog_entry(kept, which="done")
    assert said.says.startswith("Your branch changes what the project ships")


# --- a release collects every fragment (crew#1010) ---------------------------------------------

RELEASE_STORY = role_tasks.render(
    role_tasks.DEVOPS,
    kind=role_tasks.RELEASE,
    key="epic-650",
    why="Epic #650 was split.",
    work="Release it.",
    done_when=["Released"],
)


def _release_rules(repo: Path) -> list[str]:
    return [f.rule for f in gates.release_collects(repo, RELEASE_STORY)]


def test_a_release_built_from_the_fragments_goes_on(kept):
    from crew_org.session.tools import Tools

    reply = Tools(kept, None, SimpleNamespace(verify=[])).call(
        "towncrier", {"command": "build --version 1.4.0 --date 2026-11-04"}
    )
    assert reply.kind == "ok", reply.text
    assert _release_rules(kept) == []


def test_a_release_that_leaves_a_fragment_or_an_unreleased_entry_goes_back(kept):
    text = CHANGELOG.replace(
        "## [1.3.3]", "## [1.4.0] - 2026-11-04\n\n### Added\n\n- x\n\n## [1.3.3]"
    )
    (kept / "CHANGELOG.md").write_text(text)
    (finding,) = gates.release_collects(kept, RELEASE_STORY)
    assert finding.rule == "check.gates.a_release_collects_the_changelog"
    assert "`changes/661.added.md`" in finding.says and "towncrier build" in finding.says
    (kept / "changes/661.added.md").unlink()
    assert _release_rules(kept) == []
    (kept / "CHANGELOG.md").write_text(
        text.replace("## [1.4.0]", "## [Unreleased]\n\n### Fixed\n\n- left\n\n## [1.4.0]")
    )
    (finding,) = gates.release_collects(kept, RELEASE_STORY)
    assert "1 entry under `[Unreleased]`" in finding.says


def test_only_a_release_is_asked(kept):
    assert gates.release_collects(kept, "**Work** Uncap the rate.") == []


# --- a deletion is a change the gate sees (crew#839) -------------------------------------------


def merged_with(repo: Path, *files: str) -> None:
    """Add `files` to what's merged, under the story's branch."""
    for name in files:
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(f"# {name}\n")
    git(repo, "checkout", "-q", "main")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "more")
    git(repo, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    git(repo, "checkout", "-q", "-B", "feat/662-uncap")


def delete(repo: Path, path: str) -> None:
    from crew_org.session.tools import Tools

    reply = Tools(repo, None, SimpleNamespace(verify=[])).call("delete_file", {"path": path})
    assert reply.kind == "ok", reply.text


def test_a_file_the_session_deletes_is_in_the_branchs_diff(repo):
    merged_with(repo, "docs/scrape.md")
    delete(repo, "docs/scrape.md")
    merged = regression.merged_base(repo)
    assert "docs/scrape.md" in reproduction._changed(repo, merged.sha)
    verdict = at_done(repo, done(criteria=[{"criterion": "docs/scrape.md is removed"}]))
    rules = [f.rule for f in verdict.findings]
    assert "check.gates.the_branch_changes_nothing" not in rules
    assert "guard.bounds.out_of_bounds" not in rules


def test_a_protected_path_deleted_goes_back(repo):
    """The tool holds a deletion to the worktree, as `write_file`; the record's protected
    paths are the gate's, read from the diff, for both."""
    merged_with(repo, ".crew/project.yaml")
    delete(repo, ".crew/project.yaml")
    verdict = at_done(repo, done())
    assert "guard.bounds.out_of_bounds" in [f.rule for f in verdict.findings]
    assert "`.crew/project.yaml` is protected" in verdict.findings[0].says


# --- a test reads a changelog entry wherever it sits (crew#1014) ---------------------------------

# sprint-metrics' own tests at 41865bb, cut to what each needs. A release moves
# `[Unreleased]`'s entries to its dated section and can't change a test, so one that
# reads an entry only from `[Unreleased]` fails at the next release: 4.5.0 did, twice.
SM_HELPERS = '''
import re
from pathlib import Path

CHANGELOG = Path(__file__).parent.parent / "CHANGELOG.md"
_HEADING_RE = re.compile(r"^## \\[([^\\]]+)\\](.*)$", re.MULTILINE)


def _unreleased_body_span(content: str) -> tuple[int, int]:
    """Return (start, end) char positions of the [Unreleased] section body."""
    headings = list(_HEADING_RE.finditer(content))
    first = headings[0]
    assert first.group(1) == "Unreleased"
    end = headings[1].start() if len(headings) > 1 else len(content)
    return first.end(), end


def _subheading_bodies(content: str, subheading: str) -> list[str]:
    """Return the body text of every section that has the given subheading line."""
    bodies = []
    for m in re.finditer(rf"^{re.escape(subheading)}\\s*$", content, re.MULTILINE):
        start = m.end()
        next_h = re.search(r"^### |^## \\[", content[start:], re.MULTILINE)
        end = start + next_h.start() if next_h else len(content)
        bodies.append(content[start:end])
    return bodies


def check_unreleased_first_and_subheadings(changelog_text: str) -> list[str]:
    violations = []
    headings = list(_HEADING_RE.finditer(changelog_text))
    first = headings[0]
    if first.group(1) != "Unreleased":
        violations.append(f"First section is {first.group(0)!r}")
    return violations
'''
# Reads `[Unreleased]` for its structure: an entry test isn't one (mqucifer/sprint-metrics#977).
SM_STRUCTURE = '''

def test_rule_unreleased_first_with_subheadings_in_order():
    """AC4: [Unreleased] is the first section with Added, Changed, Fixed,
    Removed subheadings in that order."""
    content = CHANGELOG.read_text()
    assert check_unreleased_first_and_subheadings(content) == []
    mutated = content.replace(
        "## [Unreleased]",
        "## [1.3.3] - 2026-10-19\\n\\n### Added\\n\\n### Changed\\n\\n### Fixed\\n"
        "\\n### Removed\\n\\n## [Unreleased]",
        1,
    )
    violations = check_unreleased_first_and_subheadings(mutated)
    assert violations, "expected a violation when [Unreleased] is not first"
    assert "Unreleased" in violations[0], (
        f"message must name the offending heading: {violations[0]}"
    )
    start, end = _unreleased_body_span(content)
    body = content[start:end]
    mutated = content[:start] + body.replace("### Removed\\n", "", 1) + content[end:]
    violations = check_unreleased_first_and_subheadings(mutated)
    missing = [v for v in violations if "missing" in v]
    assert missing, f"expected a missing-subheading violation: {violations}"
    assert "### Removed" in missing[0], f"message must name the missing subheading: {missing[0]}"
'''
# As mqucifer/sprint-metrics#969 fixed them: in whichever section holds the entry.
SM_EVERY_SECTION = '''

def test_unreleased_added_names_session_event():
    """The changelog names the intake accepting a 'session' event,
    in whichever section the entry currently sits in."""
    content = CHANGELOG.read_text()
    added = _subheading_bodies(content, "### Added")
    assert any(
        "session" in body and "intake" in body and "accepts" in body for body in added
    )


def test_unreleased_changed_names_api_version_move():
    content = CHANGELOG.read_text()
    changed = _subheading_bodies(content, "### Changed")
    assert any('"11"' in body for body in changed)
    assert any("api_version" in body for body in changed)
'''
# The two mqucifer/sprint-metrics#977 names, as merged: each reads `[Unreleased]` alone.
SM_UNRELEASED_ONLY = '''

def test_unreleased_added_names_session_epics():
    """AC6: CHANGELOG.md's [Unreleased] Added section records that /range now
    answers each epic's in-sprint session time."""
    content = CHANGELOG.read_text()
    start = content.index("## [Unreleased]")
    end = content.index("## [", start + 1)
    unreleased = content[start:end]
    added_section = unreleased[unreleased.index("### Added") :]
    assert "session_epics" in added_section
    assert "epic" in added_section
    assert "in-sprint session minutes" in added_section


def test_changelog_records_to_date():
    content = CHANGELOG.read_text()
    start = content.index("## [Unreleased]")
    end = content.index("## [", start + 1)
    unreleased = content[start:end]
    added_section = unreleased[unreleased.index("### Added") :]
    assert "session_epics_to_date" in added_section
    assert "to date" in added_section or "to-date" in added_section
'''
CHANGELOG_TESTS = "tests/test_changelog.py"


def _pinned(repo: Path) -> list[gates.Finding]:
    merged = regression.merged_base(repo)
    return gates.reads_only_unreleased(repo, merged, reproduction._changed(repo, merged.sha))


def test_a_test_reading_an_entry_only_from_unreleased_goes_back_by_name(kept):
    (kept / CHANGELOG_TESTS).write_text(SM_HELPERS + SM_UNRELEASED_ONLY)
    (finding,) = _pinned(kept)
    assert finding.rule == "check.gates.reads_only_unreleased"
    assert finding.count == 2
    for name in ("test_unreleased_added_names_session_epics", "test_changelog_records_to_date"):
        assert f"`{CHANGELOG_TESTS}::{name}`" in finding.says
    assert "whichever section" in finding.says and "in neither" in finding.says
    verdict = at_done(kept, done())
    assert "check.gates.reads_only_unreleased" in [f.rule for f in verdict.findings]


def test_a_structure_test_and_one_reading_every_section_go_on(kept):
    (kept / CHANGELOG_TESTS).write_text(SM_HELPERS + SM_STRUCTURE + SM_EVERY_SECTION)
    assert _pinned(kept) == []


def test_a_test_reading_unreleased_through_a_helper_goes_back(kept):
    (kept / CHANGELOG_TESTS).write_text(
        SM_HELPERS + "\n\ndef test_names_the_move():\n"
        "    content = CHANGELOG.read_text()\n"
        "    start, end = _unreleased_body_span(content)\n"
        '    assert "api_version" in content[start:end]\n'
    )
    (finding,) = _pinned(kept)
    assert f"`{CHANGELOG_TESTS}::test_names_the_move`" in finding.says


def test_only_the_tests_the_branch_adds_or_changes_are_read(kept):
    """One already merged is another bug's to fix, as mqucifer/sprint-metrics#977 is."""
    (kept / CHANGELOG_TESTS).write_text(SM_HELPERS + SM_UNRELEASED_ONLY)
    git(kept, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(kept, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "merged")
    git(kept, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    (kept / CHANGELOG_TESTS).write_text(
        SM_HELPERS + SM_UNRELEASED_ONLY + SM_STRUCTURE + SM_EVERY_SECTION
    )
    assert _pinned(kept) == []


class JavaScriptProject:
    class design:  # noqa: N801
        from crew_org.project import Part

        parts = [Part(path="", language="javascript", tests=["tests/**/*.test.js"])]


@pytest.fixture
def javascript():
    from crew_org import profiles

    profiles.set_project(JavaScriptProject())
    yield
    profiles.clear()


def test_a_javascript_test_reading_only_unreleased_goes_back(kept, javascript):
    (kept / "tests/changelog.test.js").write_text(
        'import { readFileSync } from "node:fs";\n'
        'const text = readFileSync("CHANGELOG.md", "utf8");\n\n'
        'test("unreleased is first", () => {\n'
        '  expect(text.indexOf("## [Unreleased]")).toBeLessThan(text.indexOf("## [1."));\n'
        '  expect(text.split("## [Unreleased]")[1]).toContain("### Added");\n'
        "});\n\n"
        'test("names the attempt chart", () => {\n'
        '  const unreleased = text.split("## [Unreleased]")[1].split("## [")[0];\n'
        '  expect(unreleased).toContain("attempt chart");\n'
        "});\n\n"
        'test("names the story list", () => {\n'
        '  expect(text).toContain("story list");\n'
        "});\n"
    )
    (finding,) = _pinned(kept)
    assert "`tests/changelog.test.js::names the attempt chart`" in finding.says
    assert finding.count == 1


def test_a_test_that_a_doc_names_the_heading_goes_on(kept):
    """sprint-metrics' README test: it checks the README says `## [Unreleased]`, and
    reads nothing from the changelog."""
    (kept / "tests/test_docs.py").write_text(
        "def test_readme_versioning_states_unreleased_first_with_subheadings():\n"
        "    section = _versioning_section()\n"
        '    assert "## [Unreleased]" in section\n'
        '    assert "Added, Changed, Fixed" in section\n'
    )
    assert _pinned(kept) == []
