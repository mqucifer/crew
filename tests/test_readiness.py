"""Whether a repository is ready for the crew, and what is missing (crew#690, ADR 0030).

It replaces the usable-record precondition (#132): level 0 isn't worked, level 1
works only the skeleton Goal, level 2 any Goal. Each missing item says who fixes
it. What the crew's App can read was checked live on 2026-10-13: sprint-metrics
reached level 2, crew-presentation level 1, and infra level 0 (the App can't
read it).
"""

from __future__ import annotations

from pathlib import Path

from crew_org.flows import readiness
from crew_org.flows.loop import readiness_of
from crew_org.flows.standup import write_standup
from crew_org.project import RECORD_PATH
from tests.test_standup import AT, SPRINT, run

INTENT = """version: 2
intent:
  scope:
    purpose: Show how the crew works.
  release:
    deploys: {deploys}
    where: {where}
  done:
    bar: Its acceptance criteria are met and CI is green.
"""
DESIGN = "design:\n  checks: [uv run pytest -q]\n"
OWN = "design:\n  sandbox_image: x@sha256:abc\n  checks: [python -m pytest]\n"
GREEN = [{"path": ".github/workflows/tests.yml"}]
PROTECTED = {"required_status_checks": {"contexts": ["tests"]}}


class Clones:
    """A workspace whose clones are directories, one per repo."""

    def __init__(self, root: Path, broken: set[str] = frozenset()) -> None:
        self.root, self.broken, self.repo = root, broken, None

    def for_repo(self, repo):
        clone = Clones(self.root, self.broken)
        clone.repo = repo
        return clone

    def current(self) -> Path:
        if self.repo in self.broken:
            raise RuntimeError("git fetch failed: could not resolve host")
        path = self.root / self.repo
        path.mkdir(parents=True, exist_ok=True)
        return path


def onboard(root: Path, repo: str, record: str) -> None:
    (root / repo / ".crew").mkdir(parents=True, exist_ok=True)
    (root / repo / RECORD_PATH).write_text(record)


def record(design: str = DESIGN, *, deploys: str = "false", where: str = "nowhere") -> str:
    return INTENT.format(deploys=deploys, where=where) + design


class GitHub:
    """GitHub as the crew's App reads it, for one repository."""

    def __init__(
        self,
        *,
        settings: dict | None = None,
        files: set[str] = frozenset(
            {".github/workflows/board.yml", ".github/workflows/tests.yml", "uv.lock"}
        ),
        green: list | None = None,
        protection: dict | None = None,
        pages: dict | None = None,
        readable: bool = True,
        commits: bool = True,
        records: dict[str, str] | None = None,
    ) -> None:
        self.settings = {
            "default_branch": "main",
            "has_issues": True,
            "allow_squash_merge": True,
            "delete_branch_on_merge": True,
            **(settings or {}),
        }
        self.files, self.green, self.protection = files, green or [], protection
        self.page, self.readable, self.commits = pages, readable, commits
        self.records = records or {}

    def repository(self, repo):
        if not self.readable:
            raise RuntimeError(f"GET /repos/o/{repo} -> 404: Not Found")
        return self.settings

    def branches(self, repo):
        return [{"name": "main"}] if self.commits else []

    def file_at(self, repo, path, ref):
        if path == RECORD_PATH and ref in self.records:
            return self.records[ref]
        return "x" if path in self.files else None

    def list_dir(self, repo, path, ref):
        return [f.rsplit("/", 1)[1] for f in self.files if f.startswith(path + "/")]

    def green_runs(self, repo, branch):
        return self.green

    def branch_protection(self, repo, branch):
        return self.protection

    def pages(self, repo):
        return self.page


def check(tmp_path, github, text: str | None = record(), *, broken=frozenset()):
    if text is not None:
        onboard(tmp_path, "site", text)
    return readiness.check(github, Clones(tmp_path, broken), "site")


def whats(found) -> str:
    return "\n".join(f"{m.what} ({m.who})" for m in found.missing)


# --- level 0: not worked ---------------------------------------------------------------


def test_a_repository_the_crew_cannot_read_is_the_sponsors_to_open(tmp_path):
    found = check(tmp_path, GitHub(readable=False))
    assert found.level == 0
    assert "give the crew's Apps access (the Sponsor)" in whats(found)


def test_a_repository_with_no_commits_needs_a_first_one(tmp_path):
    found = check(tmp_path, GitHub(commits=False))
    assert found.level == 0 and "no commits" in whats(found)


def test_a_repository_without_a_record_is_onboarded_first(tmp_path):
    found = check(tmp_path, GitHub(), None)
    assert found.level == 0
    assert "has no `.crew/project.yaml`" in whats(found)
    assert "crew onboard site" in whats(found)


def test_a_record_missing_a_required_answer_names_it(tmp_path):
    found = check(tmp_path, GitHub(), record().split("  done:")[0])
    assert "what must be true for a change to count as done" in whats(found)


def test_a_record_that_cannot_be_read_says_so(tmp_path):
    found = check(tmp_path, GitHub(), broken={"site"})
    assert "couldn't be read: git fetch failed" in whats(found)


def test_a_project_with_no_design_waits_for_the_architect(tmp_path):
    found = check(tmp_path, GitHub(), record(""))
    assert found.level == 0 and "`crew design site`" in whats(found)


def test_each_setting_the_crew_relies_on_is_named_with_why(tmp_path):
    found = check(tmp_path, GitHub(settings={"delete_branch_on_merge": False, "has_issues": False}))
    assert found.level == 0
    assert "Delete branch on merge is off: the crew never deletes its own branches" in whats(found)
    assert "Issues are off" in whats(found)


def test_the_board_workflow_is_the_crew_s_to_add(tmp_path):
    found = check(tmp_path, GitHub(files={"uv.lock"}))
    assert "no `.github/workflows/board.yml`; `crew onboard site` adds it (the crew)" in whats(
        found
    )


def test_a_project_with_no_ci_gets_the_template_s(tmp_path):
    """crew-presentation had none: no story wrote it, so CI never ran (crew#690)."""
    found = check(tmp_path, GitHub(files={".github/workflows/board.yml", "uv.lock"}))
    assert (
        "no CI on pull requests; `crew onboard site` adds the template's `checks.yml` (the crew)"
        in whats(found)
    )


def test_a_pages_release_needs_pages_from_github_actions(tmp_path):
    """crew-presentation's Pages was never enabled; the first deploy would have failed."""
    deploys = record(deploys="true", where="GitHub Pages at https://o.github.io/site/")
    none = check(tmp_path, GitHub(), deploys)
    assert "GitHub Pages isn't enabled" in whats(none)
    branch = readiness.check(GitHub(pages={"build_type": "legacy"}), Clones(tmp_path), "site")
    assert "set its source to GitHub Actions" in whats(branch)
    ready = readiness.check(GitHub(pages={"build_type": "workflow"}), Clones(tmp_path), "site")
    assert ready.level == 1


def test_a_release_that_is_not_pages_asks_nothing_of_pages(tmp_path):
    found = check(tmp_path, GitHub(), record(deploys="true", where="a registry"))
    assert found.level == 1


# --- level 1: the skeleton only -------------------------------------------------------


def test_before_its_skeleton_a_project_works_that_goal_only(tmp_path):
    """crew-presentation on 2026-10-13: nothing had run green and nothing was protected."""
    found = check(tmp_path, GitHub(green=[{"path": ".github/workflows/board.yml"}]), record(OWN))
    assert found.level == 1
    assert "CI hasn't run green on `main`" in whats(found), "board.yml's runs don't count"
    assert (
        "requires no check; once CI is green, `scripts/new-project.sh site --protect` (the Sponsor)"
        in whats(found)
    )
    assert "the design names no lockfile" in whats(found)


def test_the_lockfile_the_design_names_must_be_on_the_branch(tmp_path):
    named = OWN + "  lockfile: package-lock.json\n"
    found = check(tmp_path, GitHub(green=GREEN, protection=PROTECTED), record(named))
    assert found.level == 1 and "`package-lock.json` isn't on `main`" in whats(found)


# --- level 2: any Goal -----------------------------------------------------------------


def test_a_project_with_ci_green_once_protection_and_its_lockfile_is_ready(tmp_path):
    """sprint-metrics as it stands: uv's lockfile, with no lockfile named."""
    found = check(tmp_path, GitHub(green=GREEN, protection=PROTECTED))
    assert found.level == 2 and found.missing == []


TEMPLATE_CI = {".github/workflows/board.yml", ".github/workflows/checks.yml", "uv.lock"}


def test_the_template_s_green_before_the_design_proves_nothing(tmp_path):
    """Its `checks.yml` passes with nothing to check until the design is merged."""
    runs = [{"path": ".github/workflows/checks.yml", "head_sha": "early"}]
    early = GitHub(
        files=TEMPLATE_CI, green=runs, protection=PROTECTED, records={"early": record("")}
    )
    found = check(tmp_path, early)
    assert found.level == 1 and "CI hasn't run green" in whats(found)

    later = GitHub(
        files=TEMPLATE_CI,
        green=[{"path": ".github/workflows/checks.yml", "head_sha": "later"}, *runs],
        protection=PROTECTED,
        records={"later": record(), "early": record("")},
    )
    assert check(tmp_path, later).level == 2


def test_what_the_app_cannot_read_is_said_not_guessed(tmp_path):
    found = check(tmp_path, GitHub(green=GREEN, protection=PROTECTED))
    assert any("Actions" in u for u in found.unchecked)


# --- the tick and the standup ----------------------------------------------------------


def test_the_tick_leaves_level_0_alone_and_holds_level_1_to_its_skeleton(tmp_path):
    onboard(tmp_path, "ready", record())
    onboard(tmp_path, "new", record(OWN))

    class Repos(GitHub):
        def green_runs(self, repo, branch):
            return GREEN if repo == "ready" else []

        def branch_protection(self, repo, branch):
            return PROTECTED if repo == "ready" else None

        def branches(self, repo):
            return [] if repo == "empty" else [{"name": "main"}]

    not_ready, skeleton_only = readiness_of(Repos(), Clones(tmp_path), {"ready", "new", "empty"})
    assert set(not_ready) == {"empty"} and "no commits" in not_ready["empty"]
    assert set(skeleton_only) == {"new"}


def test_the_standup_says_which_repo_isnt_worked_and_which_works_its_skeleton():
    text = write_standup(
        run(),
        sprint=SPRINT,
        at=AT,
        waiting=[],
        aging=[],
        not_ready={"infra": "the crew can't read the repository (the Sponsor)"},
        skeleton_only={"site": "CI hasn't run green on `main` (the crew)"},
    ).text
    assert "**Not worked: not ready**\n- infra: the crew can't read the repository" in text
    assert "**Only the skeleton Goal: not ready for others**\n- site: CI hasn't run green" in text


def test_a_repo_not_worked_does_not_make_a_quiet_tick_loud():
    """A quiet standup is posted once, then held back: the note isn't repeated every tick."""
    standup = write_standup(
        run(), sprint=SPRINT, at=AT, waiting=[], aging=[], not_ready={"r": "no record"}
    )
    assert standup.quiet
