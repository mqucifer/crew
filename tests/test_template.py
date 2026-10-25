"""Every crew project's own files come from mqucifer/project-template (crew#690, ADR 0030).

crew-presentation, the first project taken from an empty repository, had no CI on
pull requests and committed `.DS_Store` and `.pyc` files: sprint-metrics had both
by hand, so no plan had named them.
"""

from __future__ import annotations

from pathlib import Path

from crew_org.flows import template
from crew_org.flows.onboard import open_record_pr, validate
from crew_org.project import is_protected, protected
from tests.test_onboard import COMPLETE, TEMPLATE, FakeIssues, FakeWorkspace


def test_the_template_s_files_come_without_its_own_readme():
    found = template.files(FakeIssues())
    assert "README.md" not in found
    assert found[".github/workflows/checks.yml"] == "name: checks\n"


def test_a_project_without_them_is_proposed_all_of_them(tmp_path: Path):
    ws, issues = FakeWorkspace(tmp_path), FakeIssues()
    open_record_pr(ws, issues, "site", validate(COMPLETE), base="main")
    for path in TEMPLATE:
        assert (tmp_path / path).exists() == (path != "README.md"), path
    body = issues.pulls[0]["body"]
    assert "mqucifer/project-template" in body and "`.github/workflows/checks.yml`" in body


def test_the_crew_s_files_catch_up_and_the_project_s_own_are_kept(tmp_path: Path):
    """A `.gitignore` the project added to is its own; CI is the crew's."""
    merged = tmp_path / "merged"
    for path, text in {
        ".gitignore": ".DS_Store\nsite/build/\n",
        ".github/workflows/checks.yml": "name: checks (old)\n",
        ".github/workflows/board.yml": "name: board\n",
    }.items():
        (merged / path).parent.mkdir(parents=True, exist_ok=True)
        (merged / path).write_text(text)

    proposed = template.wanted(template.files(FakeIssues()), merged)

    assert ".gitignore" not in proposed
    assert proposed[".github/workflows/checks.yml"] == "name: checks\n"
    assert ".github/crew/run_checks.py" in proposed
    assert ".github/workflows/board.yml" not in proposed, "already the template's"


def test_no_story_changes_the_crew_s_files():
    rules = protected(None)
    for path in (
        ".github/workflows/checks.yml",
        ".github/workflows/board.yml",
        ".github/crew/run_checks.py",
    ):
        assert is_protected(path, rules), path
    assert not is_protected(".github/workflows/deploy.yml", rules)


# --- the template's later changes, as a pull request in each project (crew#829) ---

OLD, NEW = "a" * 40, "b" * 40


class Template:
    """mqucifer/project-template at two commits, and the projects' own files on GitHub."""

    def __init__(self, at: dict[str, dict[str, str]], projects=None, pulls=None) -> None:
        self.at = at
        self.projects = projects or {}
        self.earlier = pulls or {}
        self.pulls: list[dict] = []
        self.assigned: list = []

    def repository(self, repo):
        return {"default_branch": "main"}

    def default_branch(self, repo):
        return "main"

    def head_sha(self, repo, branch):
        assert repo == template.TEMPLATE and branch == "main"
        return NEW

    def tree(self, repo, ref):
        return list(self.at[ref])

    def file_at(self, repo, path, ref):
        if repo == template.TEMPLATE:
            return self.at[ref].get(path)
        return self.projects.get(repo, {}).get(path)

    def pulls_for_branch(self, repo, branch):
        return self.earlier.get((repo, branch), [])

    def create_pull(self, repo, **kwargs):
        self.pulls.append({"repo": repo, **kwargs})
        return {"html_url": f"https://github.com/o/{repo}/pull/9", "number": 9}

    def assign(self, repo, number, logins):
        self.assigned.append((repo, number))


class Sink:
    def __init__(self) -> None:
        self.notes: list[str] = []

    def note(self, kind, summary, **detail):
        self.notes.append(summary)


def _project(root: Path, found: dict[str, str]) -> Path:
    for path, text in found.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    return root


THEN = {
    "README.md": "# project-template\n",
    ".github/workflows/board.yml": "name: board\n",
    ".github/workflows/checks.yml": "name: checks\n",
    ".gitignore": ".DS_Store\n",
    "old.txt": "gone soon\n",
}
NOW = {
    "README.md": "# project-template, revised\n",
    ".github/workflows/board.yml": "name: board v2\n",
    ".github/workflows/checks.yml": "name: checks v2\n",
    ".github/ISSUE_TEMPLATE/goal-template.md": "a Goal\n",
    ".gitignore": ".DS_Store\n*.pyc\n",
}


def test_a_project_takes_what_the_template_changed_since_it_last_took_it(tmp_path: Path):
    merged = _project(
        tmp_path,
        {
            ".github/workflows/board.yml": "name: board\n",  # as taken: updated
            ".github/workflows/checks.yml": "name: checks v2\n",  # already the new text
            ".gitignore": ".DS_Store\nsite/\n",  # the project's own lines: a conflict
            "old.txt": "gone soon\n",  # removed by the template, unchanged here
        },
    )

    found = template.take(Template({OLD: THEN, NEW: NOW}), merged, OLD, NEW)

    assert found.files == {
        ".github/workflows/board.yml": "name: board v2\n",
        ".github/ISSUE_TEMPLATE/goal-template.md": "a Goal\n",
        "old.txt": None,
    }
    assert found.conflicts == [".gitignore"], "never overwritten"


def test_a_project_with_none_recorded_takes_the_crew_s_files_and_not_the_ci_it_lacks(
    tmp_path: Path,
):
    """sprint-metrics has its own CI: its first pull request leaves `checks.yml` out."""
    merged = _project(
        tmp_path, {".github/workflows/board.yml": "name: board\n", ".gitignore": "x\n"}
    )
    now = {**NOW, ".github/crew/run_checks.py": "print('checks')\n"}

    found = template.take(Template({NEW: now}), merged, None, NEW)

    assert found.files == {
        ".github/workflows/board.yml": "name: board v2\n",
        ".github/ISSUE_TEMPLATE/goal-template.md": "a Goal\n",
    }
    assert found.conflicts == []


def test_the_record_is_read_back():
    assert template.taken(template.taken_text(NEW)) == NEW
    assert template.taken(None) is None and template.taken("# nothing\n") is None


def _tick(tmp_path: Path, issues: Template, permissions=None):
    import crew_org.flows.delivery as delivery

    original = delivery.workflows_not_permitted
    delivery.workflows_not_permitted = lambda paths, held=None: original(
        paths, permissions or {"workflows": "write"}
    )
    try:
        sink = Sink()
        result = template.propose_changes(issues, sink, FakeWorkspace(tmp_path), repos={"site"})
    finally:
        delivery.workflows_not_permitted = original
    return result, sink


def test_a_project_behind_the_template_gets_one_pull_request(tmp_path: Path):
    issues = Template(
        {OLD: THEN, NEW: NOW},
        projects={"site": {template.TAKEN_PATH: template.taken_text(OLD)}},
    )
    merged = tmp_path / ".merged"
    _project(merged, {".github/workflows/board.yml": "name: board\n", ".gitignore": "mine\n"})

    result, _ = _tick(tmp_path, issues)

    assert result.opened == [("site", "https://github.com/o/site/pull/9")]
    (pull,) = issues.pulls
    assert pull["head"] == "chore/829-project-template-bbbbbbb" and pull["base"] == "main"
    assert "`.github/workflows/board.yml`" in pull["body"]
    assert "## Not taken: changed in this project too" in pull["body"]
    assert "- `.gitignore`" in pull["body"]
    assert (tmp_path / ".github/workflows/board.yml").read_text() == "name: board v2\n"
    assert not (tmp_path / ".gitignore").exists(), "the project's own file isn't touched"
    assert template.taken((tmp_path / template.TAKEN_PATH).read_text()) == NEW
    assert issues.assigned == [("site", 9)], "the Sponsor merges it"


def test_a_project_that_took_the_template_s_head_is_left_alone(tmp_path: Path):
    issues = Template(
        {NEW: NOW}, projects={"site": {template.TAKEN_PATH: template.taken_text(NEW)}}
    )
    result, _ = _tick(tmp_path, issues)
    assert (result.opened, result.held, result.failed) == ([], [], [])


def test_a_pull_request_already_opened_for_that_commit_is_never_opened_again(tmp_path: Path):
    branch = "chore/829-project-template-bbbbbbb"
    for state, merged_at, says in (
        ("open", None, "template pull request open"),
        ("closed", None, "closed unmerged"),
    ):
        issues = Template(
            {OLD: THEN, NEW: NOW},
            projects={"site": {template.TAKEN_PATH: template.taken_text(OLD)}},
            pulls={("site", branch): [{"state": state, "merged_at": merged_at, "html_url": "u"}]},
        )
        result, _ = _tick(tmp_path, issues)
        assert issues.pulls == []
        ((repo, why),) = result.held
        assert repo == "site" and says in why


def test_without_the_workflows_permission_it_is_held_and_says_why(tmp_path: Path):
    issues = Template({NEW: NOW})
    result, _ = _tick(tmp_path, issues, permissions={"workflows": "read"})
    assert issues.pulls == []
    ((repo, why),) = result.held
    assert repo == "site" and "board.yml" in why


def test_a_change_the_project_never_had_moves_nothing(tmp_path: Path):
    """The template's README changed, which no project takes."""
    issues = Template(
        {OLD: THEN, NEW: {**THEN, "README.md": "# revised\n"}},
        projects={"site": {template.TAKEN_PATH: template.taken_text(OLD)}},
    )
    _project(tmp_path / ".merged", {k: v for k, v in THEN.items() if k != "README.md"})
    result, _ = _tick(tmp_path, issues)
    assert issues.pulls == [] and result.held == [] and result.failed == []


def test_a_template_it_can_t_read_is_tried_again_next_pass(tmp_path: Path):
    class Down(Template):
        def head_sha(self, repo, branch):
            raise RuntimeError("502")

    result, _ = _tick(tmp_path, Down({}))
    ((repo, why),) = result.failed
    assert repo == "site" and "502" in why


def test_no_story_changes_the_record_of_what_was_taken():
    assert is_protected(template.TAKEN_PATH, protected(None))


def test_a_preview_says_what_the_pull_request_would_carry_and_writes_nothing(tmp_path: Path):
    issues = Template(
        {OLD: THEN, NEW: NOW},
        projects={"site": {template.TAKEN_PATH: template.taken_text(OLD)}},
    )
    _project(tmp_path / ".merged", {".github/workflows/board.yml": "name: board\n"})
    ws = FakeWorkspace(tmp_path)

    said = template.preview(issues, ws, "site")

    assert said.startswith("site: would open chore/829-project-template-bbbbbbb against main")
    assert "- `.github/workflows/board.yml`" in said
    assert issues.pulls == [] and ws.opened is None and not ws.pushed


def test_a_preview_of_a_project_up_to_date_says_so(tmp_path: Path):
    issues = Template(
        {NEW: NOW}, projects={"site": {template.TAKEN_PATH: template.taken_text(NEW)}}
    )
    assert "nothing to propose" in template.preview(issues, FakeWorkspace(tmp_path), "site")


def test_a_project_with_none_recorded_catches_up_on_the_goal_form(tmp_path: Path):
    """The Goal form says how a Goal is parked and released: it's the crew's file."""
    merged = _project(tmp_path, {".github/ISSUE_TEMPLATE/goal-template.md": "an older Goal form\n"})
    found = template.take(Template({NEW: NOW}), merged, None, NEW)
    assert found.files[".github/ISSUE_TEMPLATE/goal-template.md"] == "a Goal\n"
    assert is_protected(".github/ISSUE_TEMPLATE/goal-template.md", protected(None))
