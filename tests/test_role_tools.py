"""The tools a role's question needs (crew#846): what each reads, and who is given it.

`look` was run for real against crew-presentation's published site before the pull
request, in the sandbox, offline; here its browser is stood in for.
"""

from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest

from crew_org.crews.panel_crew import STEPS
from crew_org.session import role_tools
from crew_org.session.reading import ReadTools, RunsAtMain
from crew_org.tools import look as looking
from crew_org.tools.sandbox import Sandbox
from tests.test_session_git import sh

WORKFLOW = """\
name: tests
on:
  push:
    branches: [main]
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
    steps:
      - uses: actions/checkout@v4
      - name: Run the suite
        run: uv run pytest -q -m "not slow"
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "tests.yml").write_text(WORKFLOW)
    (root / ".devcontainer").mkdir()
    (root / ".devcontainer" / "devcontainer.json").write_text(
        json.dumps({"image": "python:3.12", "updateContentCommand": "uv sync", "name": "x"})
    )
    (root / "contracts" / "answers").mkdir(parents=True)
    (root / "contracts" / "answers" / "schema.json").write_text('{"v": 1}\n')
    (root / "contracts" / "answers" / "versions.json").write_text('{"1.0.0": "abc"}\n')
    (root / "site").mkdir()
    (root / "site" / "index.html").write_text("<h1>Hi</h1>")
    sh(root, "init", "-q", "-b", "main")
    sh(root, "add", "-A")
    sh(root, "commit", "-qm", "first")
    sh(root, "tag", "v1.0.0")
    return root


def context(root: Path, github=None) -> role_tools.Context:
    return role_tools.Context(root, "sprint-metrics", github)


# --- who is given what ----------------------------------------------------------------------


def test_each_panel_member_is_given_the_tools_its_question_needs():
    """crew#846's table: the reader's view, the contracts, proof, running it."""
    assert {role: step.tools for role, step in STEPS.items()} == {
        "ux_designer": ("look",),
        "architect": ("contracts",),
        "qa_engineer": ("run", "ci_steps"),
        "devops_engineer": ("ci_steps", "ci_log"),
    }
    named = {t for step in STEPS.values() for t in step.tools} - {"run"}
    assert named <= set(role_tools.TOOLS)


def test_a_step_is_offered_only_its_own_tools(repo):
    tools = ReadTools(repo, given=("ci_steps",), context=context(repo))
    assert tools.call("ci_steps", {}).kind == "ok"
    for other in ("ci_log", "run", "write_file"):
        reply = tools.call(other, {})
        assert (reply.kind, reply.rule) == ("refused", "session.arguments")
        assert "view, search, find, tests_for, git, ci_steps" in reply.text


def test_a_role_tool_that_fails_is_a_reply_not_a_crash(repo, monkeypatch):
    def broken(ctx, args):
        raise OSError("disk")

    monkeypatch.setitem(
        role_tools.TOOLS, "ci_steps", role_tools.RoleTool(role_tools.TOOLS["ci_steps"].spec, broken)
    )
    reply = ReadTools(repo, given=("ci_steps",), context=context(repo)).call("ci_steps", {})
    assert reply.kind == "error" and "OSError: disk" in reply.text


# --- ci_steps -------------------------------------------------------------------------------


def test_ci_steps_says_what_each_job_really_runs(repo):
    text = role_tools.ci_steps(context(repo), {})
    assert "## .github/workflows/tests.yml: tests" in text
    assert "On: push, pull_request" in text
    assert "### Job `test` on ubuntu-latest" in text
    assert "service `postgres`: postgres:16" in text
    assert 'uv run pytest -q -m "not slow"' in text and "uses actions/checkout@v4" in text
    assert '"updateContentCommand": "uv sync"' in text and '"name"' not in text


def test_ci_steps_for_one_workflow_or_none(repo, tmp_path):
    assert "tests.yml" in role_tools.ci_steps(context(repo), {"workflow": "tests.yml"})
    assert role_tools.ci_steps(context(repo), {"workflow": "deploy.yml"}).startswith(
        "No workflow found named deploy.yml"
    )
    assert role_tools.ci_steps(context(tmp_path), {}) == "No workflow found."


# --- contracts ------------------------------------------------------------------------------


def test_contracts_lists_what_is_published_and_what_changed_since_the_release(repo):
    (repo / "contracts" / "answers" / "schema.json").write_text('{"v": 2}\n')
    sh(repo, "commit", "-qam", "schema: v2")
    text = role_tools.contracts(context(repo), {})
    assert "- contracts/answers/schema.json" in text
    assert '{"1.0.0": "abc"}' in text
    assert "Since the last release, v1.0.0:" in text and "schema: v2" in text
    diff = role_tools.contracts(context(repo), {"path": "contracts/answers/schema.json"})
    assert '-{"v": 1}' in diff and '+{"v": 2}' in diff


def test_contracts_without_a_release_or_any_contract(repo, tmp_path):
    sh(repo, "tag", "-d", "v1.0.0")
    assert "No release tag yet" in role_tools.contracts(context(repo), {})
    (tmp_path / "empty").mkdir()
    assert role_tools.contracts(context(tmp_path / "empty"), {}).startswith("No published contract")
    assert "isn't one of the contracts" in role_tools.contracts(context(repo), {"path": "x.json"})


# --- ci_log ---------------------------------------------------------------------------------


class GitHub:
    def __init__(self, log="line\n" * 300 + "FAILED test_x"):
        self.log = log

    def latest_runs(self, repo, branch):
        assert branch == "main"
        return {
            ".github/workflows/tests.yml": {
                "id": 9,
                "conclusion": "failure",
                "updated_at": "2026-10-20T10:00:00Z",
                "head_sha": "abcdef123",
            }
        }

    def run_jobs(self, repo, run_id):
        return [{"id": 77, "name": "test", "conclusion": "failure"}]

    def job_log(self, repo, job):
        return self.log


def test_ci_log_shows_the_default_branchs_runs_and_a_jobs_log_end(repo):
    sh(repo, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    sh(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    sh(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    text = role_tools.ci_log(context(repo, GitHub()), {})
    assert "## .github/workflows/tests.yml: failure" in text and "(abcdef1)" in text
    assert "- job 77 `test`: failure" in text
    tail = role_tools.ci_log(context(repo, GitHub()), {"job": 77})
    assert tail.endswith("FAILED test_x") and f"last {role_tools.LOG_LINES} lines" in tail


def test_ci_log_says_why_it_cant_read(repo):
    assert "no GitHub client" in role_tools.ci_log(context(repo), {})
    assert "can't be read" in role_tools.ci_log(context(repo, GitHub(log="")), {"job": 1})


# --- data -----------------------------------------------------------------------------------


def package(packages: Path, name: str, **body) -> None:
    (packages / name).mkdir(parents=True)
    (packages / name / "delivery-history.json").write_text(json.dumps(body))


def test_data_reads_the_newest_package_and_a_part_by_its_path(tmp_path, repo):
    packages = tmp_path / "packages"
    package(packages, "delivery-history-2026-10-19", schema_version="1.7.0")
    package(packages, "delivery-history-2026-10-20", schema_version="1.8.0")
    package(
        packages,
        "delivery-history-2026-10-20-2",
        schema_version="1.8.0",
        analysis={"sprints": [{"sprint": "S1"}]},
        sprint_metrics={"answers": {"S1": {"first_try": 0.5}}},
    )
    (packages / "delivery-history-2026-10-21.tar.gz").write_text("")
    assert role_tools.newest_package(packages).name == "delivery-history-2026-10-20-2"
    parts = role_tools.data(context(repo), {}, packages)
    assert parts.startswith("delivery-history-2026-10-20-2, schema 1.8.0")
    assert "- `analysis`: 1 keys: sprints" in parts
    assert '"sprint": "S1"' in role_tools.data(
        context(repo), {"part": "analysis.sprints.0"}, packages
    )
    assert "0.5" in role_tools.data(context(repo), {"part": "sprint_metrics.answers.S1"}, packages)
    assert "`nope` isn't there" in role_tools.data(context(repo), {"part": "nope"}, packages)


def test_data_says_when_nothing_is_built(tmp_path, repo):
    assert role_tools.data(context(repo), {}, tmp_path).startswith("No delivery-history package")


# --- look -----------------------------------------------------------------------------------


def looked(**at_375) -> looking.Looked:
    seen = {
        "title": "Sprint 30",
        "scroll_width": 375,
        "viewport": 375,
        "headings": ["h1 Sprint 30"],
        "text": "Sprint 30\n7 stories delivered",
        "too_wide": [],
        "no_alt": [],
        "missing": [],
        "errors": [],
        "status": 200,
    }
    return looking.Looked("index.html", {375: {**seen, **at_375}})


def test_look_says_what_a_reader_meets_at_each_width():
    text = looked().text()
    assert "## At 375px: Sprint 30" in text and "Fits the width: no sideways scroll." in text
    assert "Headings: h1 Sprint 30" in text and "7 stories delivered" in text
    wide = looked(
        scroll_width=520,
        too_wide=[{"element": "table.runs", "right": 520, "text": "Sprint"}],
        no_alt=["chart.png"],
        missing=["https://cdn.example/x.css (net::ERR_FAILED)"],
    ).text()
    assert "Scrolls sideways: 520px of content in 375px." in wide
    assert "`table.runs` reaches 520px: 'Sprint'" in wide
    assert "Images without alt text: chart.png" in wide
    assert "Didn't load: https://cdn.example/x.css" in wide


def test_the_published_site_is_the_newest_pages_artifact(tmp_path):
    inner = io.BytesIO()
    with tarfile.open(fileobj=inner, mode="w") as tar:
        page = b"<h1>Sprint 30</h1>"
        info = tarfile.TarInfo("./index.html")
        info.size = len(page)
        tar.addfile(info, io.BytesIO(page))
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as zipped:
        zipped.writestr("artifact.tar", inner.getvalue())

    class Pages:
        def pages_artifact(self, repo):
            return {"id": 1}, outer.getvalue()

    files = looking.published(Pages(), "crew-presentation", tmp_path)
    assert (files / "index.html").read_bytes() == page

    class NoPages:
        def pages_artifact(self, repo):
            return None

    with pytest.raises(looking.NotLooked, match="no published site"):
        looking.published(NoPages(), "crew-presentation", tmp_path)


def test_the_published_site_is_the_default_branchs_successful_deploy():
    """Any branch's run uploads a github-pages artifact, and so does a failed deploy."""
    from crew_org.tools.github_issues import IssueClient, Trust  # noqa: PLC0415

    def artifact(n, branch, expired=False):
        return {
            "id": n,
            "expired": expired,
            "workflow_run": {"id": n, "head_branch": branch},
            "archive_download_url": f"https://api.github.com/download/{n}",
        }

    runs = {
        1: {"conclusion": "success", "event": "push"},
        2: {"conclusion": "failure", "event": "push"},
        3: {"conclusion": "success", "event": "push"},
        4: {"conclusion": "success", "event": "push"},
    }
    listed = [artifact(1, "feature"), artifact(2, "main"), artifact(3, "main", expired=True)]
    listed.append(artifact(4, "main"))

    def answer(request):
        path = request.url.path
        if path == "/repos/mqucifer/crew-presentation":
            return httpx.Response(200, json={"default_branch": "main"})
        if path.endswith("/actions/artifacts"):
            return httpx.Response(200, json={"artifacts": listed})
        if "/actions/runs/" in path:
            return httpx.Response(200, json=runs[int(path.rsplit("/", 1)[1])])
        return httpx.Response(200, content=f"zip {path.rsplit('/', 1)[1]}".encode())

    trust = Trust(sponsor="mquarters", crew=frozenset(), tools=frozenset())
    client = httpx.Client(transport=httpx.MockTransport(answer))
    github = IssueClient("t", "mqucifer", client=client, trust=trust)
    found = github.pages_artifact("crew-presentation")
    assert found is not None and found[0]["id"] == 4 and found[1] == b"zip 4"


def test_a_pages_other_hosts_are_fetched_with_their_types(tmp_path, monkeypatch):
    """The design system and fonts come from CDNs; the container is offline."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "a.html").write_text(
        '<link rel="stylesheet" href="https://cdn.example/styles.css">'
        '<script src="https://cdn.example/app.js"></script><img src="local.png">'
    )
    served = {
        "https://cdn.example/styles.css": (
            "text/css",
            "@font-face{src:url(https://f.example/a.woff2)}",
        ),
        "https://cdn.example/app.js": ("text/javascript", "1"),
        "https://f.example/a.woff2": ("font/woff2", "font"),
    }

    asked = []

    def answer(request):
        asked.append(str(request.url))
        kind, body = served[str(request.url)]
        return httpx.Response(200, headers={"content-type": kind}, text=body)

    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(answer), **kw)
    )
    into = tmp_path / "into"
    into.mkdir()
    found = looking.externals(site, "a.html", into)
    assert {url: kind for url, (_, kind) in found.items()} == {
        url: kind for url, (kind, _) in served.items()
    }
    assert (
        json.loads((into / "map.json").read_text())["https://f.example/a.woff2"][1] == "font/woff2"
    )


def test_the_bound_on_other_hosts_is_spent_on_the_requested_page_first(tmp_path, monkeypatch):
    """Gathered from every page, the page's own CSS and fonts could miss the bound."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "a.html").write_text(
        "".join(f'<script src="https://cdn.example/{n}.js"></script>' for n in range(60))
    )
    (site / "b.html").write_text(
        '<link rel="stylesheet" href="https://cdn.example/b.css"><script src="https://cdn.example/0.js">'
    )

    def answer(request):
        kind = "text/css" if request.url.path.endswith(".css") else "text/javascript"
        body = (
            "@font-face{src:url(https://f.example/b.woff2)}" if "b.css" in str(request.url) else ""
        )
        return httpx.Response(200, headers={"content-type": kind}, text=body)

    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(answer), **kw)
    )
    into = tmp_path / "into"
    into.mkdir()
    found = looking.externals(site, "b.html", into)
    assert set(found) == {
        "https://cdn.example/b.css",
        "https://cdn.example/0.js",
        "https://f.example/b.woff2",
    }


def test_a_sessions_looks_fetch_the_site_and_other_hosts_once(tmp_path, monkeypatch):
    """Each look re-downloaded the artifact and every external before."""
    inner = io.BytesIO()
    with tarfile.open(fileobj=inner, mode="w") as tar:
        page = b'<link rel="stylesheet" href="https://cdn.example/s.css"><h1>Sprint 30</h1>'
        info = tarfile.TarInfo("./index.html")
        info.size = len(page)
        tar.addfile(info, io.BytesIO(page))
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as zipped:
        zipped.writestr("artifact.tar", inner.getvalue())
    downloads = []

    class Pages:
        def pages_artifact(self, repo):
            downloads.append(repo)
            return {"id": 1}, outer.getvalue()

        def pages(self, repo):
            return {"html_url": f"https://mqucifer.github.io/{repo}/"}

    asked = []

    def answer(request):
        asked.append(str(request.url))
        if request.url.path.endswith(".css"):
            css = "@font-face{src:url(https://f.example/a.woff2)}"
            return httpx.Response(200, headers={"content-type": "text/css"}, text=css)
        return httpx.Response(200, headers={"content-type": "font/woff2"}, text="font")

    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(answer), **kw)
    )
    kept = looking.Fetched()
    for _ in range(2):
        files, base = kept.site(Pages(), "crew-presentation")
        found = looking.externals(files, "index.html", kept.externals(), kept.found)
    assert downloads == ["crew-presentation"] and base == "/crew-presentation/"
    assert asked == ["https://cdn.example/s.css", "https://f.example/a.woff2"]
    # The font a cached stylesheet names is still in this page's map.
    assert set(json.loads((kept.externals() / "map.json").read_text())) == set(found)
    assert len(found) == 2
    where = files.parents[1]
    kept.close()
    assert not where.exists()


def test_what_look_fetched_goes_when_the_session_ends(repo):
    tools = ReadTools(repo, given=("look",), context=context(repo))
    held = tools.context.looked.externals()
    tools.stop()
    assert not held.exists()


def test_the_site_is_mounted_read_only_and_the_container_has_no_network(tmp_path):
    argv = Sandbox().command(tmp_path, ["true"], network=False, readonly={tmp_path: "/site"})
    assert argv[argv.index("--network") + 1] == "none"
    assert f"type=bind,src={tmp_path.resolve()},dst=/site,readonly" in argv


def test_the_look_tool_serves_a_directory_of_the_checkout_or_the_published_site(repo, monkeypatch):
    seen = []
    monkeypatch.setattr(
        looking,
        "look",
        lambda files, page, widths, base, fetched: (
            seen.append((files, page, widths, base)) or looked()
        ),
    )
    text = role_tools.look(context(repo), {"source": "site/", "widths": [320]})
    assert "Sprint 30" in text and seen == [((repo / "site").resolve(), "index.html", (320,), "/")]
    assert "isn't in `site`. Its pages: index.html" in role_tools.look(
        context(repo), {"source": "site", "page": "replay.html"}
    )
    assert "isn't a directory in the repository" in role_tools.look(
        context(repo), {"source": "../"}
    )
    assert "no GitHub client" in role_tools.look(context(repo), {})


def test_the_published_site_is_served_under_the_path_github_serves_it_under():
    """A project site's pages name their assets under /<repo>/: at the root they 404."""

    class Pages:
        def __init__(self, url):
            self.url = url

        def pages(self, repo):
            return {"html_url": self.url} if self.url else None

    project = Pages("https://mqucifer.github.io/crew-presentation/")
    assert looking.base_path(project, "crew-presentation") == "/crew-presentation/"
    assert looking.base_path(Pages("https://mqucifer.github.io/x"), "x") == "/x/"
    assert looking.base_path(Pages("https://crew.example.org"), "x") == "/"
    assert looking.base_path(Pages(None), "x") == "/"


def test_the_container_serves_the_site_under_its_base_and_all_of_its_origin(tmp_path):
    import functools  # noqa: PLC0415
    import http.server  # noqa: PLC0415
    import threading  # noqa: PLC0415

    from crew_org.tools import look_page  # noqa: PLC0415

    assert look_page.in_site("/crew-presentation/css/a.css", "/crew-presentation/") == "/css/a.css"
    assert look_page.in_site("/css/a.css", "/crew-presentation/") is None
    assert look_page.in_site("/css/a.css", "/") == "/css/a.css"
    # A nested page's root-relative and sibling assets are the site's too.
    origin = "http://127.0.0.1:8123"
    assert look_page.is_local(f"{origin}/crew-presentation/fonts/a.woff2", origin)
    assert look_page.is_local(f"{origin}/favicon.ico", origin)
    assert not look_page.is_local("http://127.0.0.1:81234/x", origin)
    assert not look_page.is_local("https://cdn.example/x.css", origin)

    (tmp_path / "css").mkdir()
    (tmp_path / "css" / "a.css").write_text("h1{}")
    served = type("Served", (look_page.Site,), {"base": "/crew-presentation/"})
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(served, directory=str(tmp_path))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        at = f"http://127.0.0.1:{server.server_address[1]}"
        assert httpx.get(f"{at}/crew-presentation/css/a.css").text == "h1{}"
        assert httpx.get(f"{at}/css/a.css").status_code == 404
    finally:
        server.shutdown()


# --- QA's run -------------------------------------------------------------------------------


def test_qa_runs_in_the_developers_container_over_a_clone_started_on_its_first_run(
    repo, monkeypatch
):
    from crew_org.tools import container

    made = []

    class Held:
        def __init__(self, worktree, name):
            made.append(self)
            self.worktree, self.name, self.stopped = worktree, name, False

        def start(self):
            return container.Result("start", 0, "")

        def setup(self):
            return None

        def resync(self):
            return None

        def exec(self, command, timeout=0, stdin=None):
            return container.Result(" ".join(command), 0, "1 passed")

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(container, "Held", Held)
    runs = RunsAtMain(repo, "sprint-metrics-panel-qa")
    tools = ReadTools(repo, given=("ci_steps",), context=context(repo), runs=runs)
    assert "run" in tools.offered and made == []
    done = runs.exec(["pytest", "-q"])
    assert done.ok and made[0].worktree != repo and (made[0].worktree / ".git").exists()
    tools.stop()
    assert made[0].stopped and not runs.copy.exists()


def test_a_container_that_doesnt_start_says_why_on_every_run(repo, monkeypatch):
    from crew_org.tools import container

    class Down:
        def __init__(self, worktree, name):
            pass

        def start(self):
            return container.Result("start", 126, "docker is not running")

    monkeypatch.setattr(container, "Held", Down)
    runs = RunsAtMain(repo, "x")
    assert runs.exec(["pytest"]).output == "docker is not running"
    assert runs.exec(["pytest"]).code == 126
    runs.stop()


def test_the_product_owner_reads_the_real_data_where_it_chooses():
    """Deciding the epics and settling the panel (crew#846's table)."""
    from crew_org.crews import settle_crew

    assert settle_crew.SETTLE.tools == ("data",)
    source = Path(role_tools.__file__).parents[1] / "crews" / "refinement_crew.py"
    decompose = source.read_text().split('"decompose",')[1].split("steps.run(")[0]
    assert 'tools=("data",)' in decompose
