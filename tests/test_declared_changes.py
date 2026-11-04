"""A release declares the answers it changes, and api_version moves once, at release
(crew#1019).

On 2026-11-03 sprint-metrics' stories took the responses' api_version from "10" to
"16" in a day, so the stories planned to set "12" and "13" couldn't be met. And its
additive check was loosened for good to let one planned change through. Stories now
list what they change in `declared-changes.json`, and only the release moves the
version and empties the list.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crew_org.flows import declared_changes, gates, role_tasks, unreleased
from crew_org.flows.role_tasks import DEVOPS, RELEASE, marker
from tests.role_task_fakes import Board, Issues, sink
from tests.test_gates import at_done, card, done, git, green, repo, uncap  # noqa: F401

FILE = declared_changes.FILE
WORKING_TIME = {
    "response": "GET /range",
    "path": "$.sprints[*].working_time",
    "why": "working time counts session minutes (mqucifer/sprint-metrics#938)",
}


def _write(root: Path, api_version: str = "16", changes: list | None = None) -> None:
    (root / FILE).write_text(
        json.dumps({"api_version": api_version, "changes": changes or []}, indent=2) + "\n"
    )


@pytest.fixture
def declares(repo: Path) -> Path:  # noqa: F811
    """A project that keeps the declaration, with nothing declared yet."""
    git(repo, "checkout", "-q", "main")
    _write(repo)
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "declaration")
    git(repo, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    git(repo, "checkout", "-qb", "feat/664-declare")
    return repo


@pytest.fixture
def declared(declares: Path) -> Path:
    """The same, with a story's change to working time merged and not yet released."""
    git(declares, "checkout", "-q", "main")
    _write(declares, changes=[WORKING_TIME])
    git(declares, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qam", "story")
    git(declares, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    git(declares, "checkout", "-qB", "feat/665-release")
    return declares


def _release() -> tuple:
    task = card("Task").model_copy(update={"labels": frozenset({DEVOPS.label})})
    return (
        task,
        f"Release for epic mqucifer/sprint-metrics#650\n\n{marker(DEVOPS, RELEASE, 'epic-650')}",
    )


def _story_rules(root: Path) -> list[str]:
    return [f.rule for f in gates.declared_changes(root, card(), "A story")]


def _release_rules(root: Path) -> list[str]:
    task, story = _release()
    return [f.rule for f in gates.declared_changes(root, task, story)]


# --- reading it -------------------------------------------------------------------------


def test_the_declaration_reads_its_version_and_changes():
    found = declared_changes.read(json.dumps({"api_version": "16", "changes": [WORKING_TIME]}))
    assert found == declared_changes.Declared("16", (WORKING_TIME,))
    assert declared_changes.read(None) is None


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        '{"changes": []}',
        '{"api_version": 16, "changes": []}',
        '{"api_version": "16"}',
        '{"api_version": "16", "changes": {}}',
        '{"api_version": "16", "changes": ["$.sprints"]}',
    ],
)
def test_anything_else_isn_t_a_declaration(text):
    with pytest.raises(ValueError):
        declared_changes.read(text)


@pytest.mark.parametrize(("before", "after"), [("16", "17"), ("9", "10"), ("v2", "")])
def test_a_release_moves_a_numbered_version_by_one(before, after):
    assert declared_changes.next_version(before) == after


# --- a story declares, and sets no version ----------------------------------------------


def test_a_story_that_declares_its_change_goes_on(declares):
    uncap(declares)
    _write(declares, changes=[WORKING_TIME])
    assert _story_rules(declares) == []


def test_a_story_that_sets_the_version_goes_back(declares):
    uncap(declares)
    _write(declares, api_version="17", changes=[WORKING_TIME])
    (finding,) = gates.declared_changes(declares, card(), "A story")
    assert finding.rule == "check.gates.a_story_sets_the_api_version"
    assert f"`{FILE}`" in finding.says and '"16"' in finding.says and '"17"' in finding.says


def test_a_story_that_breaks_the_declaration_goes_back(declares):
    (declares / FILE).write_text('{"api_version": "16", "changes": "working_time"}\n')
    (finding,) = gates.declared_changes(declares, card(), "A story")
    assert finding.rule == "check.gates.a_story_sets_the_api_version"
    assert "changes" in finding.says


def test_a_story_that_removes_the_declaration_goes_back(declares):
    (declares / FILE).unlink()
    assert _story_rules(declares) == ["check.gates.a_story_sets_the_api_version"]


def test_a_project_with_no_declaration_is_not_asked_for_one(repo):  # noqa: F811
    uncap(repo)
    assert _story_rules(repo) == []
    assert _release_rules(repo) == []


def test_a_task_that_isn_t_the_release_sets_no_version_either(declares):
    _write(declares, api_version="17")
    task = card("Task").model_copy(update={"labels": frozenset({DEVOPS.label})})
    story = f"CI\n\n{marker(DEVOPS, 'need', 'ci')}"
    assert [f.rule for f in gates.declared_changes(declares, task, story)] == [
        "check.gates.a_story_sets_the_api_version"
    ]


# --- the release moves the version once and empties the list ------------------------------


def test_a_release_moves_the_version_once_and_empties_the_declaration(declared):
    _write(declared, api_version="17")
    assert _release_rules(declared) == []


def test_a_release_that_leaves_the_changes_goes_back(declared):
    _write(declared, api_version="17", changes=[WORKING_TIME])
    task, story = _release()
    (finding,) = gates.declared_changes(declared, task, story)
    assert finding.rule == "check.gates.a_release_clears_the_declared_changes"
    assert "$.sprints[*].working_time" in finding.says


@pytest.mark.parametrize("version", ["16", "18"])
def test_a_release_with_changes_moves_the_version_by_exactly_one(declared, version):
    _write(declared, api_version=version)
    task, story = _release()
    (finding,) = gates.declared_changes(declared, task, story)
    assert finding.rule == "check.gates.a_release_clears_the_declared_changes"
    assert '"17"' in finding.says


def test_a_release_with_nothing_declared_leaves_the_version(declares):
    assert _release_rules(declares) == []
    _write(declares, api_version="17")
    task, story = _release()
    (finding,) = gates.declared_changes(declares, task, story)
    assert finding.rule == "check.gates.a_release_clears_the_declared_changes"
    assert "nothing" in finding.says


def test_a_release_of_unreleased_fixes_is_held_to_it_too(declared):
    task, _ = _release()
    story = f"Release\n\n{marker(DEVOPS, RELEASE, 'unreleased-4.5.0')}"
    assert [f.rule for f in gates.declared_changes(declared, task, story)] == [
        "check.gates.a_release_clears_the_declared_changes"
    ]


def test_the_gates_at_done_send_a_release_back_that_leaves_them(declared):
    task, story = _release()
    (declared / "CHANGELOG.md").write_text("# Changelog\n")
    verdict = at_done(
        declared, done(criteria=[]), story=story, card=task, run_checks=green, permissions={}
    )
    assert "check.gates.a_release_clears_the_declared_changes" in [f.rule for f in verdict.findings]


def test_the_gates_at_done_send_a_story_back_that_sets_the_version(declares):
    uncap(declares)
    _write(declares, api_version="17")
    verdict = at_done(declares, done())
    assert "check.gates.a_story_sets_the_api_version" in [f.rule for f in verdict.findings]


# --- the release Task says so, and may change it -------------------------------------------


def test_devops_may_change_the_declaration_and_a_version_file():
    """sprint-metrics keeps its responses' api_version in its own version file, so the
    release, which changes no code, can move it."""
    assert role_tasks.outside_scope(DEVOPS, [FILE, "src/sprint_metrics/version.py"]) == []
    assert role_tasks.outside_scope(DEVOPS, ["src/sprint_metrics/report.py"])


def test_an_epic_s_release_task_carries_the_api_version_step(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.add(10)
    from tests.test_role_tasks import _epic

    role_tasks.epic_release_task(issues, board, s, repo="sm", epic=_epic(), stories=[11], cards=[])
    body = issues.issues[901]["body"]
    assert declared_changes.RELEASE_STEP in body
    assert f"- [ ] {declared_changes.RELEASE_DONE}" in body


def test_a_release_of_unreleased_changes_carries_it_too(tmp_path):
    from tests.test_unreleased import FIXES

    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.files["CHANGELOG.md"] = FIXES
    unreleased.unreleased_releases(issues, board, s, repos={"cp"}, cards=[])
    body = issues.issues[901]["body"]
    assert declared_changes.RELEASE_STEP in body
    assert f"- [ ] {declared_changes.RELEASE_DONE}" in body
