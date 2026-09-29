"""Anything that runs or deploys is judged as it will be run (#335).

sprint-metrics#269 merged a Dockerfile whose image nobody could reach: the
server it runs binds 127.0.0.1. The Code Reviewer judged the diff and QA the
criteria, and neither was shown the code the image runs, or asked whether it
could be run as deployed.
"""

from __future__ import annotations

from crew_org.crews.review_crew import Finding, ReviewVerdict
from crew_org.flows import review as review_flow
from crew_org.tools.deploy_evidence import (
    deploy_evidence,
    entry_modules,
    is_runnable,
    touches_runnable,
)
from tests.test_review import (
    APPROVAL,
    FakeIssues,
    crew_pull,
    run_with_board,
    waiting_card,
)

DOCKERFILE_DIFF = (
    "diff --git a/Dockerfile b/Dockerfile\nnew file mode 100644\n--- /dev/null\n"
    "+++ b/Dockerfile\n@@ -0,0 +1,2 @@\n+FROM python:3.12-slim\n+RUN pip install .\n"
)
PYTHON_DIFF = (
    "diff --git a/src/a.py b/src/a.py\n--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x\n+y\n"
)
PYPROJECT = (
    '[project]\nname = "sprint-metrics"\n\n'
    '[project.scripts]\nsprint-metrics = "sprint_metrics.cli:main"\n'
)
SERVE = (
    "from http.server import HTTPServer\n\n"
    'def serve(port):\n    HTTPServer(("127.0.0.1", port), H)\n'
)

ROOT_FINDING = Finding(
    file="Dockerfile",
    concern="The image runs as root: there is no USER instruction.",
    action="Add a non-root USER before the entrypoint; hadolint DL3002 would catch it.",
)
BLOCKED = ReviewVerdict(
    summary="Not fit to run as deployed.", approve=False, findings=[ROOT_FINDING]
)


# --- what counts as runnable ----------------------------------------------------------------


def test_images_stacks_and_workflows_are_runnable():
    for path in [
        "Dockerfile",
        "docker/Dockerfile.dev",
        "app.dockerfile",
        "compose.yaml",
        "docker-compose.prod.yml",
        ".github/workflows/tests.yml",
    ]:
        assert is_runnable(path), path


def test_code_and_docs_are_not():
    for path in ["src/serve.py", "README.md", "pyproject.toml", ".github/CODEOWNERS"]:
        assert not is_runnable(path), path


def test_a_diff_is_judged_by_the_paths_it_touches():
    assert touches_runnable(DOCKERFILE_DIFF)
    assert not touches_runnable(PYTHON_DIFF)


def test_console_scripts_name_the_modules_they_run():
    assert entry_modules(PYPROJECT) == ["sprint_metrics.cli"]
    assert entry_modules("not toml [") == [] and entry_modules(None) == []


# --- what it's shown ---------------------------------------------------------------------


def sprint_metrics(tmp_path):
    """sprint-metrics#269 as it merged: an image, a console script, and a server on 127.0.0.1."""
    files = {
        "Dockerfile": "FROM python:3.12-slim\nRUN pip install .\n",
        "pyproject.toml": PYPROJECT,
        "src/sprint_metrics/cli.py": "from sprint_metrics.serve import serve\n\ndef main(): ...\n",
        "src/sprint_metrics/serve.py": SERVE,
        "src/sprint_metrics/metrics.py": "def rate(): ...\n",
        "tests/test_serve.py": "from http.server import HTTPServer\n",
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path, lambda rel: files.get(rel)


def test_it_is_shown_the_code_the_image_runs(tmp_path):
    root, read = sprint_metrics(tmp_path)
    shown = deploy_evidence(root, DOCKERFILE_DIFF, read)
    assert "`Dockerfile`" in shown
    assert "`src/sprint_metrics/cli.py`" in shown, "the console script"
    assert '("127.0.0.1", port)' in shown, "the server it runs, where the host is decided"


def test_code_that_neither_runs_nor_listens_is_left_out(tmp_path):
    root, read = sprint_metrics(tmp_path)
    shown = deploy_evidence(root, DOCKERFILE_DIFF, read)
    assert "metrics.py" not in shown and "tests/test_serve.py" not in shown


def test_a_file_the_change_removes_is_not_shown(tmp_path):
    root, read = sprint_metrics(tmp_path)
    shown = deploy_evidence(
        root, DOCKERFILE_DIFF, lambda rel: None if rel == "Dockerfile" else read(rel)
    )
    assert "`Dockerfile`" not in shown and "serve.py" in shown


# --- in the review pass ------------------------------------------------------------------


class DiffIssues(FakeIssues):
    def __init__(self, pulls, diff):
        super().__init__(pulls)
        self.diff = diff

    def pull_diff(self, repo, number):
        return self.diff


def reviewed(monkeypatch, diff, deploy, code=APPROVAL):
    """One crew pull request through the review pass; what the deploy review was shown."""
    calls: list[dict] = []

    def fake_deploy(title, diff, **kw):
        calls.append(kw)
        if isinstance(deploy, Exception):
            raise deploy
        return deploy

    monkeypatch.setattr(review_flow, "review_deploy", fake_deploy)
    issues = DiffIssues([crew_pull()], diff)
    result, board = run_with_board(issues, code, monkeypatch, [waiting_card()])
    return issues, result, board, calls


def test_a_change_to_code_alone_gets_no_deploy_review(monkeypatch):
    issues, _, board, calls = reviewed(monkeypatch, PYTHON_DIFF, APPROVAL)
    assert calls == []
    assert "DevOps" not in issues.submitted[0][2]
    assert board.moves == [("S6", "QAing")]


def test_a_dockerfile_is_judged_by_both_in_one_review(monkeypatch):
    issues, _, board, calls = reviewed(monkeypatch, DOCKERFILE_DIFF, APPROVAL)
    assert len(calls) == 1
    [(_, event, body)] = issues.submitted
    assert event == "APPROVE"
    assert "## DevOps review" in body
    assert body.index("*Code Reviewer*") < body.index("*DevOps Engineer*")
    assert board.moves == [("S6", "QAing")]


def test_the_deploy_review_can_hold_it_up_alone(monkeypatch):
    issues, _, board, _ = reviewed(monkeypatch, DOCKERFILE_DIFF, BLOCKED)
    [(_, event, body)] = issues.submitted
    assert event == "REQUEST_CHANGES"
    assert "hadolint DL3002" in body, "the finding names the check that would catch it"
    assert board.moves == [("S6", "In Progress")]
    assert ("S6", "DevOps Engineer") in board.owners, "returned by the gate that held it"


def test_a_deploy_review_that_fails_posts_nothing(monkeypatch):
    issues, result, board, _ = reviewed(monkeypatch, DOCKERFILE_DIFF, RuntimeError("no answer"))
    assert issues.submitted == [] and board.moves == []
    [(pr, why)] = result.failed
    assert pr == 14 and why.startswith("deploy review:")


def test_it_is_told_how_the_project_is_released(monkeypatch):
    record = (
        "version: 2\nintent:\n  scope:\n    purpose: metrics\n  release:\n    publishes: true\n"
        "    where: an image on GHCR\n  done:\n    bar: CI passes\n"
        "design:\n  checks: [uv run pytest]\n  release_how: tag on merge\n"
    )
    monkeypatch.setattr(
        FakeIssues,
        "file_at",
        lambda self, repo, path, ref: record if path == ".crew/project.yaml" else None,
    )
    _, _, _, [kw] = reviewed(monkeypatch, DOCKERFILE_DIFF, APPROVAL)
    assert "a published version: an image on GHCR" in kw["release"]
    assert "tag on merge" in kw["release"] and "`uv run pytest`" in kw["release"]
