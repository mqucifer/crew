"""Anything that runs or deploys is judged as it will be run (#335).

sprint-metrics#269 merged a Dockerfile whose image nobody could reach: the
server it runs binds 127.0.0.1. The Code Reviewer judged the diff and QA the
criteria, and neither was shown the code the image runs, or asked whether it
could be run as deployed.
"""

from __future__ import annotations

from crew_org.crews.review_crew import Finding, ReviewVerdict
from crew_org.events import CrewEvent, EventKind, EventSink
from crew_org.flows import review as review_flow
from crew_org.flows import role_tasks
from crew_org.tools.deploy_evidence import (
    deploy_evidence,
    deploy_trigger,
    entry_modules,
    is_runnable,
)
from crew_org.work_types import TASK
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
        # The workspace and the services it declares (ADR 0032).
        ".devcontainer/devcontainer.json",
        ".devcontainer/postgres.yml",
        ".devcontainer.json",
    ]:
        assert is_runnable(path), path


def test_code_and_docs_are_not():
    for path in ["src/serve.py", "README.md", "pyproject.toml", ".github/CODEOWNERS"]:
        assert not is_runnable(path), path


def test_a_diff_is_judged_by_the_paths_it_touches():
    assert deploy_trigger(DOCKERFILE_DIFF) is not None
    assert deploy_trigger(PYTHON_DIFF) is None


def _diff(path: str, *lines: str) -> str:
    body = "\n".join(lines)
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n{body}\n"


# sprint-metrics#338's shape: the bind sprint-metrics#67 wrote, changed in plain code.
BIND_DIFF = _diff(
    "src/sprint_metrics/serve.py",
    '-    server = http.server.HTTPServer(("127.0.0.1", port), MetricsHandler)',
    '+    server = http.server.HTTPServer(("0.0.0.0", port), MetricsHandler)',
)


def test_a_bind_changed_in_code_starts_the_review():
    trigger = deploy_trigger(BIND_DIFF)
    assert trigger is not None and trigger.on == "code"
    assert trigger.matched == [
        "listens src/sprint_metrics/serve.py",
        "address src/sprint_metrics/serve.py",
    ]


def test_ports_permissions_and_environment_start_it_too():
    cases = {
        # sprint-metrics#452's shape.
        "environment": '+    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")',
        "port": '+        "--port",',
        "permissions": "+    path.chmod(0o600)",
    }
    for kind, line in cases.items():
        trigger = deploy_trigger(_diff("src/app/cli.py", line))
        assert trigger is not None and f"{kind} src/app/cli.py" in trigger.matched, kind
    assert deploy_trigger(_diff("web/server.ts", "-const port = process.env.PORT")) is not None


def test_a_removed_line_counts_as_much_as_an_added_one():
    assert deploy_trigger(_diff("src/a.py", '-    host = "127.0.0.1"')) is not None


def test_tests_docs_and_unchanged_lines_do_not():
    bind = '+    HTTPServer(("127.0.0.1", port), H)'
    for path in ["tests/test_serve.py", "src/a/tests/helpers.py", "README.md", "docs/run.py"]:
        assert deploy_trigger(_diff(path, bind)) is None, path
    context = _diff("src/a.py", '     HTTPServer(("127.0.0.1", port), H)', "-x", "+y")
    assert deploy_trigger(context) is None
    assert deploy_trigger(PYTHON_DIFF) is None


def test_a_path_it_cannot_read_is_skipped_not_counted_against_the_last():
    spaced = (
        "diff --git a/my app/serve.py b/my app/serve.py\n--- a/my app/serve.py\n"
        '+++ b/my app/serve.py\n@@ -1 +1 @@\n+    HTTPServer(("127.0.0.1", port), H)\n'
    )
    assert deploy_trigger(_diff("src/a.py", "+x = 1") + spaced) is None


def test_runnable_files_come_first():
    trigger = deploy_trigger(DOCKERFILE_DIFF + BIND_DIFF)
    assert trigger is not None
    assert (trigger.on, trigger.matched) == ("files", ["Dockerfile"])


def test_the_compose_file_the_workspace_names_is_runnable_whatever_its_name():
    stack = _diff("stack.yml", "+  image: postgres")
    assert deploy_trigger(stack) is None
    trigger = deploy_trigger(stack, named=["stack.yml"])
    assert trigger is not None and trigger.matched == ["stack.yml"]


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


def reviewed(monkeypatch, diff, deploy, code=APPROVAL, card=None):
    """One crew pull request through the review pass; what the deploy review was shown."""
    calls: list[dict] = []

    def fake_deploy(title, diff, **kw):
        calls.append(kw)
        if isinstance(deploy, Exception):
            raise deploy
        return deploy

    monkeypatch.setattr(review_flow, "review_deploy", fake_deploy)
    issues = DiffIssues([crew_pull()], diff)
    result, board = run_with_board(issues, code, monkeypatch, [card or waiting_card()])
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
        "version: 3\nintent:\n  scope:\n"
        "    serves: [{who: the Sponsor, needs: [how the crew performs]}]\n"
        "    purpose: metrics\n  release:\n    publishes: true\n"
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


def test_a_change_to_the_compose_file_the_workspace_names_gets_one(monkeypatch):
    named = {".devcontainer/devcontainer.json": '{"dockerComposeFile": "../stack.yml"}'}
    monkeypatch.setattr(FakeIssues, "file_at", lambda self, repo, path, ref: named.get(path))
    _, _, _, calls = reviewed(monkeypatch, _diff("stack.yml", "+  image: postgres"), APPROVAL)
    assert len(calls) == 1


def started(monkeypatch) -> list[CrewEvent]:
    """The DevOps Engineer's start events, as the run records them."""
    events: list[CrewEvent] = []
    monkeypatch.setattr(EventSink, "emit", lambda self, event: events.append(event))
    return events


def deploy_started(events: list[CrewEvent]) -> dict:
    [event] = [
        e for e in events if e.kind == EventKind.AGENT_STARTED and e.role == "DevOps Engineer"
    ]
    return event.detail


def test_a_run_records_that_code_started_it_and_what_matched(monkeypatch):
    events = started(monkeypatch)
    reviewed(monkeypatch, BIND_DIFF, APPROVAL)
    assert deploy_started(events) == {
        "pr": 14,
        "trigger": "code",
        "matched": ["listens src/sprint_metrics/serve.py", "address src/sprint_metrics/serve.py"],
        "devops_task": False,
    }


def test_devops_own_task_is_reviewed_and_recorded_as_its_own(monkeypatch):
    task = waiting_card().model_copy(
        update={"work_type": TASK, "labels": frozenset({role_tasks.DEVOPS.label})}
    )
    events = started(monkeypatch)
    _, _, _, calls = reviewed(monkeypatch, DOCKERFILE_DIFF, APPROVAL, card=task)
    assert len(calls) == 1
    detail = deploy_started(events)
    assert (detail["trigger"], detail["matched"], detail["devops_task"]) == (
        "files",
        ["Dockerfile"],
        True,
    )


def test_a_compose_lookup_that_fails_leaves_the_trigger_to_files_and_code(monkeypatch):
    def unreachable(self, repo, path, ref):
        if "devcontainer" in path:
            raise RuntimeError("HTTP 502")
        return None

    monkeypatch.setattr(FakeIssues, "file_at", unreachable)
    events = started(monkeypatch)
    issues, result, _, calls = reviewed(monkeypatch, BIND_DIFF, APPROVAL)
    assert len(calls) == 1 and result.failed == []
    assert deploy_started(events)["trigger"] == "code"
    assert len(issues.submitted) == 1
