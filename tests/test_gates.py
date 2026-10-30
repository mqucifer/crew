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

from crew_org.flows import gates
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


def test_a_bug_whose_fix_is_only_its_tests_is_told_it_is_a_story_s_work(repo, monkeypatch):
    """mqucifer/sprint-metrics#868 (crew#949): the defect was a stale test, so its fix was
    in the test file alone, and nothing could fail on main. The gate said the same thing
    5 times; this finding names the shape, and is final."""
    passed = reproduction.Reproduction(passed=[f"{TESTS}::test_rate_is_capped"])
    monkeypatch.setattr(reproduction, "reproduces", lambda *a, **kw: passed)
    # The test was the defect: it pinned a value, and the fix loosens it. Declared, as
    # the story's Existing tests line says.
    (repo / TESTS).write_text(MERGED_TESTS.replace("== 100", "<= 100"))
    said = done((f"{TESTS}::test_rate_is_capped", "it pinned the cap"))
    verdict = at_done(repo, said, card=card("Bug"))
    (finding,) = verdict.findings
    assert finding.rule == "guard.reproduce.check" and finding.final
    assert finding.says.startswith("The defect looks like the test itself")
    assert "The way out is back to refinement, as a story" in finding.says
    assert f"`{TESTS}`" in finding.says


def test_a_product_bug_whose_test_passes_on_main_still_goes_back_to_reproduce_it(repo, monkeypatch):
    """The branch changes the code as well as its test: the test has to show the bug."""
    passed = reproduction.Reproduction(passed=[f"{TESTS}::test_rate_is_capped"])
    monkeypatch.setattr(reproduction, "reproduces", lambda *a, **kw: passed)
    uncap(repo)
    said = done((f"{TESTS}::test_rate_is_capped", "the cap is gone"))
    (finding,) = at_done(repo, said, card=card("Bug")).findings
    assert finding.rule == "guard.reproduce.check" and not finding.final
    assert finding.says.startswith("The fix passes the checks, but ")
    assert "looks like the test itself" not in finding.says


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


# --- every story adds its own `[Unreleased]` entry (crew#816, ADR 0019) ---------

CHANGELOG = (
    "# Changelog\n\n## [Unreleased]\n\n### Added\n\n### Changed\n\n### Fixed\n\n"
    "- An entry another story added\n\n### Removed\n\n"
    "## [1.3.3] - 2026-10-19\n\n### Fixed\n\n- A released entry\n"
)


@pytest.fixture
def kept(repo: Path) -> Path:
    """The same project, keeping a changelog."""
    git(repo, "checkout", "-q", "main")
    (repo / "CHANGELOG.md").write_text(CHANGELOG)
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "changelog")
    git(repo, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    git(repo, "checkout", "-qb", "feat/663-entry")
    return repo


def _entry(repo: Path, line: str, under: str = "### Changed\n\n") -> None:
    text = (repo / "CHANGELOG.md").read_text()
    (repo / "CHANGELOG.md").write_text(text.replace(under, f"{under}{line}\n", 1))


def _rules(repo: Path, work_type: str = "Story") -> list[str]:
    return [f.rule for f in gates.no_changelog_entry(repo, card(work_type))]


def test_a_story_that_ships_a_change_and_adds_no_entry_goes_back(kept):
    uncap(kept)
    (finding,) = gates.no_changelog_entry(kept, card())
    assert finding.rule == "check.gates.no_changelog_entry"
    assert f"`{MODULE}`" in finding.says and "## [Unreleased]" in finding.says


def test_its_entry_under_unreleased_lets_it_through(kept):
    uncap(kept)
    _entry(kept, "- The escalation rate is no longer capped at 100%")
    assert _rules(kept) == []


def test_an_entry_under_a_released_version_isn_t_one(kept):
    uncap(kept)
    _entry(kept, "- Slipped in late", under="## [1.3.3] - 2026-10-19\n\n### Fixed\n\n")
    assert _rules(kept) == ["check.gates.no_changelog_entry"]


def test_tests_docs_and_settings_ship_nothing(kept):
    for path in (TESTS, "docs/metrics.md", "README.md", ".github/workflows/ci.yml", ".gitignore"):
        (kept / path).parent.mkdir(parents=True, exist_ok=True)
        (kept / path).write_text("changed\n")
    assert _rules(kept) == []


def test_a_bug_adds_its_entry_and_a_task_doesn_t(kept):
    uncap(kept)
    assert _rules(kept, "Bug") == ["check.gates.no_changelog_entry"]
    task = card("Task").model_copy(update={"labels": frozenset({"role:devops"})})
    assert gates.no_changelog_entry(kept, task) == []


def test_a_project_with_no_changelog_is_not_asked_for_one(repo):
    uncap(repo)
    assert _rules(repo) == []


def test_a_story_adding_the_unreleased_section_with_its_entry_counts(kept):
    (kept / "CHANGELOG.md").write_text("# Changelog\n\n## [1.3.3] - 2026-10-19\n")
    git(kept, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "none open")
    git(kept, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    uncap(kept)
    (kept / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Changed\n\n- Uncapped\n\n## [1.3.3] - 2026-10-19\n"
    )
    assert _rules(kept) == []


def test_the_gate_sends_it_back_and_done_says_so_first(kept):
    uncap(kept)
    verdict = at_done(kept, done())
    assert "check.gates.no_changelog_entry" in [f.rule for f in verdict.findings]
    (said,) = gates.no_changelog_entry(kept, which="done")
    assert said.says.startswith("Your branch changes what the project ships")


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
