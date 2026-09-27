"""A CI workflow the crew writes is guarded, and a push it can't make is refused early (#279).

The crew's GitHub App had no `workflows` permission, so the first story to
write a release workflow would have failed at push time, looking like a crew
bug. And once it may write workflows, LLM-written CI runs with the
repository's secrets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crew_org import auth
from crew_org.crews.delivery_crew import FileWrite, Implementation
from crew_org.flows.delivery import workflows_not_permitted
from crew_org.project import parse
from crew_org.tools import bounds, ci_guard

RELEASE = """
on:
  push:
    tags: ["v*"]
permissions:
  contents: write
jobs:
  publish:
    runs-on: ubuntu-latest
    permissions:
      contents: write
      id-token: write
      packages: write
    steps:
      - run: echo ${{ secrets.GITHUB_TOKEN }} ${{ secrets.PYPI_TOKEN }}
"""
RECORD = """
intent:
  scope: {purpose: p}
  release: {publishes: true, where: a tag on main}
  done: {bar: tests pass}
design:
  checks: [uv run pytest -q]
  secrets: [PYPI_TOKEN]
"""


def reasons(text: str, secrets=frozenset({"PYPI_TOKEN"})) -> list[str]:
    return ci_guard.unsafe(".github/workflows/release.yml", text, set(secrets))


def test_a_release_workflow_within_the_rules_is_allowed():
    assert reasons(RELEASE) == []


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (("on:\n  push:", "on:\n  pull_request_target:\n  push:"), "pull_request_target"),
        (("permissions:\n  contents: write\njobs", "permissions: write-all\njobs"), "write-all"),
        (("      packages: write", "      packages: write\n      issues: write"), "write issues"),
        (("secrets.PYPI_TOKEN", "secrets.DEPLOY_KEY"), "DEPLOY_KEY"),
    ],
)
def test_what_a_crew_workflow_may_not_do_is_refused_by_name(change, said):
    old, new = change
    assert old in RELEASE
    found = reasons(RELEASE.replace(old, new))
    assert len(found) == 1 and said in found[0]


def test_a_secret_is_allowed_only_when_the_design_names_it():
    assert any("PYPI_TOKEN" in r for r in reasons(RELEASE, secrets=frozenset()))


def repo(tmp_path: Path) -> Path:
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "old.yml").write_text(
        "on: [pull_request_target]\njobs: {}\n"  # already there: not this change's doing
    )
    return tmp_path


def test_a_ci_change_travels_alone(tmp_path):
    change = Implementation(
        summary="s",
        new_files=[
            FileWrite(path=".github/workflows/release.yml", content=RELEASE),
            FileWrite(path="src/pkg/version.py", content="V = 1\n"),
        ],
    )
    found = bounds.out_of_bounds(repo(tmp_path), change, parse(RECORD))
    assert any("travels alone" in r for r in found)


def test_only_the_workflows_a_change_writes_are_judged(tmp_path):
    change = Implementation(
        summary="s", new_files=[FileWrite(path=".github/workflows/release.yml", content=RELEASE)]
    )
    assert bounds.out_of_bounds(repo(tmp_path), change, parse(RECORD)) == []


# --- a push the app can't make ----------------------------------------------------------

WORKFLOW_CHANGE = Implementation(
    summary="s", new_files=[FileWrite(path=".github/workflows/release.yml", content=RELEASE)]
)


def test_a_workflow_change_without_the_permission_is_refused_naming_it():
    """#279, criterion 1."""
    why = workflows_not_permitted(WORKFLOW_CHANGE, {"contents": "write"})
    assert "`workflows: write`" in why and ".github/workflows/release.yml" in why


@pytest.mark.parametrize(
    ("change", "held"),
    [
        (WORKFLOW_CHANGE, {"contents": "write", "workflows": "write"}),  # granted
        (WORKFLOW_CHANGE, {}),  # not an app, or not known: GitHub will say
        (
            Implementation(summary="s", new_files=[FileWrite(path="README.md", content="x")]),
            {"contents": "write"},
        ),
    ],
)
def test_nothing_is_refused_when_it_can_be_pushed_or_it_can_t_be_told(change, held):
    assert workflows_not_permitted(change, held) == ""


def test_each_app_keeps_its_own_permissions(monkeypatch):
    """The reviewer's token minted after the crew's no longer overwrites its grant."""
    monkeypatch.setattr(
        auth,
        "_APP_PERMISSIONS",
        {"GITHUB_APP_": {"workflows": "write"}, auth.REVIEW_APP_PREFIX: {"pull_requests": "write"}},
    )
    assert auth.app_permissions()["workflows"] == "write"
    assert "workflows" not in auth.app_permissions(auth.REVIEW_APP_PREFIX)
