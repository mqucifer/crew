"""A CI workflow the crew writes is guarded, and a push it can't make is refused early (#279).

The crew's GitHub App had no `workflows` permission, so the first story to
write a release workflow would have failed at push time, looking like a crew
bug. And once it may write workflows, LLM-written CI runs with the
repository's secrets.
"""

from __future__ import annotations

import pytest

from crew_org import auth
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


# Already on main, and unsafe: not this change's doing.
OLD = {".github/workflows/old.yml": "on: [pull_request_target]\njobs: {}\n"}


def judged(files: dict[str, str]) -> list[str]:
    """What the gates at `done` find in a branch that writes `files` (crew#778)."""
    after = {**OLD, **{path: text for path, text in files.items() if ci_guard.is_workflow(path)}}
    return bounds.judged(list(files), parse(RECORD), workflows=(dict(OLD), after), dockerfiles={})


def test_a_workflow_may_change_with_the_code_it_runs():
    """sprint-metrics#307: an ENTRYPOINT breaks the smoke job and the tests that
    invoke the image, so all three change together (Sponsor, 2026-09-29, crew#335)."""
    files = {".github/workflows/release.yml": RELEASE, "src/pkg/version.py": "V = 1\n"}
    assert judged(files) == []


def test_a_workflow_changed_with_code_is_still_held_to_the_guard():
    unsafe = "on: [pull_request_target]\njobs: {}\n"
    files = {".github/workflows/release.yml": unsafe, "src/pkg/version.py": "V = 1\n"}
    assert any("pull_request_target" in r for r in judged(files))


def test_only_the_workflows_a_change_writes_are_judged():
    assert judged({".github/workflows/release.yml": RELEASE}) == []


# --- a push the app can't make ----------------------------------------------------------

WORKFLOW_CHANGE = [".github/workflows/release.yml"]


def test_a_workflow_change_without_the_permission_is_refused_naming_it():
    """#279, criterion 1."""
    why = workflows_not_permitted(WORKFLOW_CHANGE, {"contents": "write"})
    assert "`workflows: write`" in why and ".github/workflows/release.yml" in why


@pytest.mark.parametrize(
    ("change", "held"),
    [
        (WORKFLOW_CHANGE, {"contents": "write", "workflows": "write"}),  # granted
        (WORKFLOW_CHANGE, {}),  # not an app, or not known: GitHub will say
        (["README.md"], {"contents": "write"}),
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


PAGES_DEPLOY = """name: deploy
on:
  push:
    branches: [main]
    paths: [delivery-history.tag]
permissions:
  contents: read
  pages: write
  id-token: write
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/deploy-pages@v4
"""


def test_pages_may_be_written_only_where_the_record_deploys_to_pages():
    """crew-presentation#74: its deploy to GitHub Pages needs `pages: write`."""
    path = ".github/workflows/deploy.yml"
    assert "write pages" in " ".join(ci_guard.unsafe(path, PAGES_DEPLOY, set()))
    writes = ci_guard.RELEASE_WRITES | ci_guard.PAGES_WRITES
    assert ci_guard.unsafe(path, PAGES_DEPLOY, set(), writes) == []


def test_a_record_deploying_to_pages_says_so():
    from crew_org.project import Release

    assert Release(deploys=True, where="https://mqucifer.github.io/crew-presentation/").to_pages
    assert not Release(deploys=True, where="a server on the Spark").to_pages
    assert not Release(deploys=False, publishes=True, where="ghcr.io").to_pages
