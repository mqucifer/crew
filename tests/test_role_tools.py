"""The tools a role's question needs (crew#846, crew#858): what each reads, and who is given it.

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

from crew_org.crews.goal_look_crew import STEPS
from crew_org.session import role_tools
from crew_org.session.reading import ReadTools
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


def test_each_role_in_the_goals_look_is_given_the_tools_its_question_needs():
    """crew#846's table, for the look's two roles: the reader's view, the contracts."""
    assert {role: step.tools for role, step in STEPS.items()} == {
        "ux_designer": ("look",),
        "architect": ("contracts",),
    }
    assert {t for step in STEPS.values() for t in step.tools} <= set(role_tools.TOOLS)


def test_a_step_is_offered_only_its_own_tools(repo):
    tools = ReadTools(repo, given=("contracts",), context=context(repo))
    assert tools.call("contracts", {}).kind == "ok"
    for other in ("look", "run", "write_file"):
        reply = tools.call(other, {})
        assert (reply.kind, reply.rule) == ("refused", "session.arguments")
        assert "view, search, find, tests_for, git, contracts" in reply.text


def test_a_role_tool_that_fails_is_a_reply_not_a_crash(repo, monkeypatch):
    def broken(ctx, args):
        raise OSError("disk")

    monkeypatch.setitem(
        role_tools.TOOLS,
        "contracts",
        role_tools.RoleTool(role_tools.TOOLS["contracts"].spec, broken),
    )
    reply = ReadTools(repo, given=("contracts",), context=context(repo)).call("contracts", {})
    assert reply.kind == "error" and "OSError: disk" in reply.text


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


def test_contracts_are_found_without_walking_dependencies(repo, monkeypatch):
    """A recursive glob walked node_modules and .venv before filtering them out."""
    import os  # noqa: PLC0415

    (repo / "openapi.yaml").write_text("openapi: 3.1\n")
    (repo / "api" / "v1").mkdir(parents=True)
    (repo / "api" / "v1" / "event.schema.json").write_text("{}")
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "x.schema.json").write_text("{}")
    entered = []
    real = os.walk

    def walk(top):
        for here, dirs, files in real(top):
            entered.append(Path(here).name)
            yield here, dirs, files

    monkeypatch.setattr(role_tools.os, "walk", walk)
    assert role_tools._contract_files(repo) == [
        "api/v1/event.schema.json",
        "contracts/answers/schema.json",
        "contracts/answers/versions.json",
        "openapi.yaml",
    ]
    assert "node_modules" not in entered and ".git" not in entered


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


PUBLIC = [(2, 1, 6, "", ("151.101.1.229", 443))]


@pytest.fixture
def public(monkeypatch):
    """Every host resolves to a public address, as the CDNs do."""
    monkeypatch.setattr(looking.socket, "getaddrinfo", lambda *a, **kw: PUBLIC)


def test_the_host_fetches_only_public_https_hosts_on_the_list(monkeypatch):
    """The page is generated code: what it names mustn't make the host reach localhost,
    the LAN or the Spark (CLAUDE.md's Never rules)."""
    monkeypatch.setattr(looking.socket, "getaddrinfo", lambda *a, **kw: PUBLIC)
    assert looking.allowed("https://cdn.jsdelivr.net/gh/mqucifer/design-system@v0.1.0/x.css")
    assert not looking.allowed("http://cdn.jsdelivr.net/x.css")
    assert not looking.allowed("https://localhost/x.css")
    assert not looking.allowed("https://127.0.0.1:4000/v1/models")
    assert not looking.allowed("https://192.168.1.20:30000/v1/models")
    assert not looking.allowed("https://evil.example/x.css")
    # A listed name that resolves to a private address is refused after DNS.
    for private in ("192.168.1.20", "10.0.0.5", "127.0.0.1", "169.254.169.254", "::1"):
        found = [(2, 1, 6, "", (private, 443))]
        monkeypatch.setattr(looking.socket, "getaddrinfo", lambda *a, found=found, **kw: found)
        assert not looking.allowed("https://cdn.jsdelivr.net/x.css"), private


def test_a_page_naming_private_addresses_makes_the_host_fetch_nothing_of_them(
    tmp_path, monkeypatch, public
):
    site = tmp_path / "site"
    site.mkdir()
    (site / "a.html").write_text(
        '<link rel="stylesheet" href="http://localhost:4000/v1/models">'
        '<script src="https://192.168.1.20:30000/generate"></script>'
        '<link rel="stylesheet" href="https://cdn.jsdelivr.net/moved.css">'
    )
    asked = []

    def answer(request):
        asked.append(str(request.url))
        # A listed host redirecting off the list is not followed.
        return httpx.Response(302, headers={"location": "http://192.168.1.20/x.css"})

    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(answer), **kw)
    )
    into = tmp_path / "into"
    into.mkdir()
    assert looking.externals(site, "a.html", into) == {}
    assert asked == ["https://cdn.jsdelivr.net/moved.css"]


def test_a_pages_other_hosts_are_fetched_with_their_types(tmp_path, monkeypatch, public):
    """The design system and fonts come from CDNs; the container is offline."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "a.html").write_text(
        '<link rel="stylesheet" href="https://cdn.jsdelivr.net/styles.css">'
        '<script src="https://cdn.jsdelivr.net/app.js"></script><img src="local.png">'
    )
    served = {
        "https://cdn.jsdelivr.net/styles.css": (
            "text/css",
            "@font-face{src:url(https://fonts.gstatic.com/a.woff2)}",
        ),
        "https://cdn.jsdelivr.net/app.js": ("text/javascript", "1"),
        "https://fonts.gstatic.com/a.woff2": ("font/woff2", "font"),
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
        json.loads((into / "map.json").read_text())["https://fonts.gstatic.com/a.woff2"][1]
        == "font/woff2"
    )


def test_the_bound_on_other_hosts_is_spent_on_the_requested_page_first(
    tmp_path, monkeypatch, public
):
    """Gathered from every page, the page's own CSS and fonts could miss the bound."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "a.html").write_text(
        "".join(f'<script src="https://cdn.jsdelivr.net/{n}.js"></script>' for n in range(60))
    )
    (site / "b.html").write_text(
        '<link rel="stylesheet" href="https://cdn.jsdelivr.net/b.css"><script src="https://cdn.jsdelivr.net/0.js">'
    )

    def answer(request):
        kind = "text/css" if request.url.path.endswith(".css") else "text/javascript"
        body = (
            "@font-face{src:url(https://fonts.gstatic.com/b.woff2)}"
            if "b.css" in str(request.url)
            else ""
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
        "https://cdn.jsdelivr.net/b.css",
        "https://cdn.jsdelivr.net/0.js",
        "https://fonts.gstatic.com/b.woff2",
    }


def test_a_sessions_looks_fetch_the_site_and_other_hosts_once(tmp_path, monkeypatch, public):
    """Each look re-downloaded the artifact and every external before."""
    inner = io.BytesIO()
    with tarfile.open(fileobj=inner, mode="w") as tar:
        page = b'<link rel="stylesheet" href="https://cdn.jsdelivr.net/s.css"><h1>Sprint 30</h1>'
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
            css = "@font-face{src:url(https://fonts.gstatic.com/a.woff2)}"
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
    assert asked == ["https://cdn.jsdelivr.net/s.css", "https://fonts.gstatic.com/a.woff2"]
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


def test_the_page_looked_at_is_the_sites_never_a_host_file(repo, monkeypatch):
    """`externals` reads the page on the host, for its links."""
    monkeypatch.setattr(looking, "look", lambda *a, **kw: pytest.fail("looked outside the site"))
    (repo / "secret.json").write_text('{"token": "x"}')
    (repo / "site" / "link.html").symlink_to(repo / "secret.json")
    for page in ("../secret.json", "../../../../etc/hosts", "link.html"):
        text = role_tools.look(context(repo), {"source": "site", "page": page})
        assert "isn't a page of `site`" in text, page
