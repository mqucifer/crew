"""The handover check: what keeps the crew from taking over a repository it didn't build.

Claude builds a new project's alpha and the crew takes it over at level 2 with no
skeleton Goal, so this is the one point the alpha is checked against what the
crew's tools work with (crew#757, ADR 0030). The checklist is R1's. Checked live
on 2026-10-17: crew-presentation had four items missing (a tool its design
doesn't name, `sys.path` set at a test's top level, two skeleton placeholders
still in place, a `board.yml` behind the template's) and sprint-metrics two (no
`lockfile` in its design, its own CI rather than the template's).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from crew_org.flows import handover, onboard
from crew_org.flows.readiness import CREW, SPONSOR, Missing, Readiness
from crew_org.project import RECORD_PATH, Part, parse
from crew_org.tools.container import Result

RECORD = """version: 3
intent:
  scope:
    serves: [{{who: the Sponsor, needs: [how the crew performs]}}]
    purpose: Show how the crew works.
    {uses}
  release:
    deploys: false
  done:
    bar: Its acceptance criteria are met and CI is green.
design:
  checks: [uv run pytest -q]
  setup: {setup}
  lockfile: uv.lock
  structure: 'app/main.py runs the site; app/render.py renders it; build/ is its output.
    The play/pause toggle lives in render.py.'
"""
TEMPLATE = {
    ".github/workflows/checks.yml": "name: checks\n",
    ".github/crew/run_checks.py": "def main():\n    pass\n",
    ".gitignore": "build/\n",
}
FILES = {
    **TEMPLATE,
    "README.md": "# Site\n",
    "uv.lock": "version = 1\n",
    "app/__init__.py": "",
    "app/main.py": '"""Runs the site."""\n\nimport sys\n\n\ndef main():\n    return 0\n\n\n'
    'if __name__ == "__main__":\n    sys.exit(main())\n',
    "app/render.py": "WIDTH = 80\n\n\ndef render():\n    return WIDTH\n",
    "tests/test_render.py": "from app.render import render\n\n\ndef test_render():\n"
    "    assert render() == 80\n",
    "docs/decisions/0001-static.md": "# 1. Static\n",
}


class Issues:
    def __init__(self, goals: list[dict] | None = None) -> None:
        self.goals = [{"state": "closed"}] if goals is None else goals

    def labelled(self, repo, label):
        return self.goals if label == "goal" else []


def repository(
    tmp_path: Path, files: dict[str, str] | None = None, *, setup: str = "uv sync", uses: str = ""
) -> tuple[Path, object]:
    root = tmp_path / "site"
    record = RECORD.format(setup=setup, uses=uses)
    for path, text in {**FILES, RECORD_PATH: record, **(files or {})}.items():
        if text is None:
            continue
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run([*git, "add", "-A"], cwd=root, check=True)
    subprocess.run([*git, "commit", "-qm", "alpha"], cwd=root, check=True)
    return root, parse(record)


def check(tmp_path, files=None, *, alpha=True, issues=None, **kw):
    root, record = repository(tmp_path, files, **kw)
    return handover.check(issues or Issues(), "site", root, record, alpha=alpha, template=TEMPLATE)


def whats(found) -> str:
    return "\n".join(f"{m.what} ({m.who})" for m in found.missing)


def test_an_alpha_built_from_the_template_has_nothing_missing(tmp_path):
    found = check(tmp_path)
    assert found.missing == []
    assert any("--setup" in u for u in found.unchecked), "setup is named, not skipped"


# --- the record and design ------------------------------------------------------------


def test_the_design_rules_run_on_a_design_crew_design_didn_t_write(tmp_path):
    """A design written by hand met only the lockfile's presence before."""
    found = check(tmp_path, setup="'pip install -r requirements.txt && python fetch.py'")
    assert "without a shell" in whats(found)
    assert "`crew design site --reason` (the crew)" in whats(found)


def test_a_design_that_doesn_t_name_its_lockfile_is_the_architect_s(tmp_path):
    """sprint-metrics' design never named its `uv.lock` (ADR 0030, Consequences)."""
    root, record = repository(tmp_path)
    record.design.lockfile = None
    found = handover.design_rules("site", record)
    assert [m.what for m in found] == [
        "design: the design doesn't name its lockfile in `lockfile`; `crew design site --reason`"
    ]


def test_structure_naming_a_file_that_isn_t_there_is_found(tmp_path):
    """crew-presentation's design declared changes to files that weren't there."""
    found = check(tmp_path, {"app/render.py": None})
    assert "`structure` names what isn't on the default branch: `app/render.py`" in whats(found)


def test_code_structure_doesn_t_name_is_found(tmp_path):
    """crew-presentation's `tools/token_check.py` was in no design."""
    found = check(tmp_path, {"tools/token_check.py": "def check():\n    pass\n"})
    assert "`structure` doesn't name `tools/token_check.py`" in whats(found)


def test_structure_s_ignored_output_and_prose_aren_t_paths_to_find(tmp_path):
    found = check(tmp_path)
    assert "build/" not in whats(found) and "play/pause" not in whats(found)


def test_the_docs_a_user_reads_must_be_there(tmp_path):
    found = check(tmp_path, {"README.md": None})
    assert "the docs the design names aren't there: `README.md` (Claude" in whats(found)


def test_a_pin_file_that_isn_t_there_is_the_builder_s(tmp_path):
    uses = (
        "uses:\n    - repo: mqucifer/crew\n      section: Data packages\n"
        "      pin: delivery-history.tag\n      contract: [schema.json]"
    )
    found = check(tmp_path, uses=uses)
    assert "`delivery-history.tag`, naming the mqucifer/crew release, isn't there" in whats(found)


def test_uses_the_roles_can_t_follow_are_named(tmp_path, monkeypatch):
    """Without the producer's notes, crew-presentation's roles guessed (crew#713)."""
    import crew_org.usage as usage

    monkeypatch.setattr(usage, "_file", lambda repo, file, ref="HEAD": "# Other\n")
    uses = "uses:\n    - repo: mqucifer/crew\n      section: Data packages"
    found = check(tmp_path, uses=uses)
    assert 'mqucifer/crew\'s `README.md` has no section "Data packages"' in whats(found)
    assert "it names no `pin`" in whats(found)
    assert f"({SPONSOR})" in whats(found)


def test_an_alpha_leaves_its_decisions_and_what_it_delivered(tmp_path):
    found = check(tmp_path, {"docs/decisions/0001-static.md": None}, issues=Issues(goals=[]))
    assert (
        f"no decisions in `docs/decisions/`: write the ones later work must keep as ADRs "
        f"({handover.CLAUDE})" in whats(found)
    )
    assert "no closed Goal" in whats(found)


def test_a_project_the_crew_works_keeps_its_decisions_in_its_epics(tmp_path):
    found = check(tmp_path, {"docs/decisions/0001-static.md": None}, alpha=False)
    assert found.missing == []


# --- layout ---------------------------------------------------------------------------


def test_python_that_runs_at_the_top_level_is_found(tmp_path):
    """crew-presentation's `tests/test_tokens.py` set `sys.path` before its imports."""
    test = "import sys\n\nsys.path.insert(0, '.')\nimport app\n\n\ndef test_x():\n    pass\n"
    found = check(tmp_path, {"tests/test_render.py": test})
    assert "`tests/test_render.py` runs code at its top level (line 3)" in whats(found)
    assert "(Claude, the alpha's builder)" in whats(found)


@pytest.mark.parametrize(
    "source",
    [
        '"""Doc."""\nfrom __future__ import annotations\nX: int = 1\n__all__ = ["X"]\n',
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import os\n",
        "try:\n    import tomllib\nexcept ImportError:\n    tomllib = None\n",
        'def main():\n    pass\n\n\nif __name__ == "__main__":\n    main()\n',
    ],
)
def test_imports_constants_definitions_and_a_main_block_are_definitions(source):
    assert handover.top_level_code(source) == []


@pytest.mark.parametrize(
    "source", ["print('hi')\n", "for x in []:\n    pass\n", "with open('x'):\n    pass\n"]
)
def test_anything_else_at_the_top_level_is_code(source):
    assert handover.top_level_code(source) == [1]


def test_code_that_isn_t_python_needs_a_part(tmp_path):
    found = check(tmp_path, {"site/js/replay.js": "export function play() {}\n"})
    assert "code that isn't Python sits in no part the design declares: `site/js/replay.js`" in (
        whats(found)
    )


def test_a_placeholder_left_in_place_is_found(tmp_path):
    """crew-presentation's replay player was still the skeleton's placeholder."""
    found = check(tmp_path, {"app/render.py": "# crew:placeholder\nWIDTH = 80\n"})
    assert "`crew:placeholder` still starts a line in `app/render.py`" in whats(found)


def test_quoting_the_marker_mid_line_isn_t_carrying_it(tmp_path):
    test = 'def test_marker():\n    assert "x  crew:placeholder" != ""\n'
    found = check(tmp_path, {"tests/test_render.py": test})
    assert "placeholder" not in whats(found)


# --- CI -------------------------------------------------------------------------------


def test_the_template_s_files_changed_or_missing_are_found(tmp_path):
    """sprint-metrics runs its own CI; crew-presentation's `board.yml` is behind."""
    found = check(tmp_path, {".github/workflows/checks.yml": "name: mine\n", ".gitignore": None})
    assert (
        "`.github/workflows/checks.yml` differs, `.gitignore` is missing; copy them from "
        "`mqucifer/project-template`" in whats(found)
    )


def test_a_workflow_the_crew_could_never_edit_is_found(tmp_path):
    """Its CI must stay within what the crew may write (§19.2; crew#727)."""
    deploy = "on: pull_request_target\njobs:\n  d:\n    permissions: {actions: write}\n"
    found = check(tmp_path, {".github/workflows/deploy.yml": deploy})
    assert "runs on `pull_request_target`" in whats(found)
    assert "asks to write actions" in whats(found)
    assert "Otherwise the crew can't edit it" in whats(found)


# --- tests ----------------------------------------------------------------------------


def test_a_part_whose_tests_aren_t_there_is_found(tmp_path):
    _root, record = repository(tmp_path)
    record.design.parts = [
        Part(path="site/", language="javascript", tests=["site/tests/*.spec.js"]),
        Part(path="", language="python", tests=[]),
    ]
    found = handover.tested(record, ["app/main.py"], CREW)
    assert [m.what for m in found] == [
        "no file matches the part `site/`'s tests `site/tests/*.spec.js`",
        "the part `(all)` names no tests",
    ]


# --- sizes ----------------------------------------------------------------------------


def test_sizes_are_reported_never_missing(tmp_path):
    """A repository grows whatever size it starts at (the Sponsor, ADR 0030)."""
    big = "def f():\n    return 1\n" + "# padding line for size\n" * 3_000
    found = check(tmp_path, {"app/render.py": big + "WIDTH = 80\n\n\ndef render():\n    pass\n"})
    assert found.missing == []
    assert any("`app/render.py`" in r and "shown by definition" in r for r in found.reported)


def test_a_test_file_past_where_qa_sees_all_of_it_is_named(tmp_path):
    big = "def test_x():\n    pass\n" + "# padding\n" * 7_000
    found = check(tmp_path, {"tests/test_render.py": big})
    assert any("split by concern `tests/test_render.py`" in r for r in found.reported)


# --- the workspace's devcontainer.json (crew#778) -----------------------------------

PINNED = "mcr.microsoft.com/playwright/python:v1.52.0-noble@sha256:" + "a" * 64


@pytest.fixture
def registry(monkeypatch):
    """Every pinned image is found in its registry, without asking it."""
    from crew_org.tools import base_images

    monkeypatch.setattr(base_images, "lookup", lambda host, name, digest: object())


def with_file(tmp_path, text, **kw):
    return check(tmp_path, {".devcontainer/devcontainer.json": text}, **kw)


def test_without_the_file_the_design_is_the_workspace_and_nothing_is_flagged(tmp_path):
    assert "devcontainer" not in whats(check(tmp_path))


def test_the_file_and_the_design_naming_the_setup_is_two_sources(tmp_path, registry):
    """The Sponsor, 2026-10-18: devcontainer.json is the one source."""
    found = with_file(tmp_path, f'{{"image": "{PINNED}", "postCreateCommand": ["uv", "sync"]}}')
    assert (
        "design: it names its setup, and `.devcontainer/devcontainer.json` is their one "
        "source; `crew design site --reason` (the crew)"
    ) in whats(found)


def test_a_file_that_is_the_one_source_is_clean(tmp_path, registry):
    found = with_file(
        tmp_path, f'{{"image": "{PINNED}", "postCreateCommand": ["uv", "sync"]}}', setup="null"
    )
    assert "devcontainer" not in whats(found)


def test_each_field_the_crew_doesn_t_honour_is_named(tmp_path, registry):
    found = with_file(
        tmp_path,
        f'{{"image": "{PINNED}", "runArgs": ["--privileged"], "initializeCommand": ["x"]}}',
        setup="null",
    )
    assert (
        "`.devcontainer/devcontainer.json`: `runArgs` isn't honoured (the crew sets the "
        "container's flags); remove it (Claude, the alpha's builder)"
    ) in whats(found)
    assert "`initializeCommand` isn't honoured (it runs on the host)" in whats(found)


def test_a_field_no_specification_defines_is_named(tmp_path, registry):
    """crew#839: a setup the crew reads as nothing is never handed over quietly."""
    found = with_file(tmp_path, f'{{"image": "{PINNED}", "setupCommands": [["x"]]}}', setup="null")
    assert "`.devcontainer/devcontainer.json`: `setupCommands` isn't a devcontainer field" in whats(
        found
    )


def test_the_file_s_image_follows_the_image_rule(tmp_path):
    found = with_file(tmp_path, '{"image": "node:22"}', setup="null")
    assert "`.devcontainer/devcontainer.json`: the sandbox image `node:22` isn't pinned" in whats(
        found
    )


def test_the_file_s_image_stands_for_the_design_s_in_its_rules(tmp_path, registry):
    """A design with a JavaScript part needs its own image: the file names it."""
    root, record = repository(
        tmp_path, {".devcontainer/devcontainer.json": f'{{"image": "{PINNED}"}}'}, setup="null"
    )
    record.design.parts = [Part(path="site/js/", language="javascript", tests=[])]
    assert handover.design_rules("site", record) != []
    assert handover.design_rules("site", record, image=PINNED) == []


# --- setup, in the sandbox -----------------------------------------------------------


class Workspace:
    """A session's workspace, its setup replaced by `does` on the copy."""

    does = staticmethod(lambda copy: None)
    started = 0
    stopped = 0
    ran: list[list[str]] = []
    fails = ""

    def __init__(self, worktree, name, sandbox=None):
        self.worktree = worktree

    def start(self):
        Workspace.started += 1
        return Result("start", 1 if self.fails == "start" else 0, "no such image")

    def setup(self):
        Workspace.does(self.worktree)
        return Result("uv sync", 1 if self.fails == "setup" else 0, "no network")

    def exec(self, command):
        Workspace.ran.append(command)
        return Result(" ".join(command), 0, "")

    def stop(self):
        Workspace.stopped += 1


def setup_found(tmp_path, monkeypatch, does, *, fails=""):
    """The handover's setup run, its workspace replaced by `Workspace`."""
    import crew_org.tools.container as container

    monkeypatch.setattr(Workspace, "does", staticmethod(does))
    monkeypatch.setattr(Workspace, "fails", fails)
    monkeypatch.setattr(Workspace, "started", 0)
    monkeypatch.setattr(Workspace, "stopped", 0)
    monkeypatch.setattr(Workspace, "ran", [])
    monkeypatch.setattr(container, "Held", Workspace)
    root, record = repository(tmp_path)
    return handover.setup_leaves(root, record, object(), handover.CLAUDE)


def test_setup_that_re_locks_and_leaves_the_tree_clean_passes(tmp_path, monkeypatch):
    def relocks(copy):
        (copy / "uv.lock").write_text("version = 1\n")

    assert setup_found(tmp_path, monkeypatch, relocks) == []
    # As a session's workspace: started, set up, a command run in it, and removed.
    assert Workspace.started == 1 and Workspace.ran == [["true"]] and Workspace.stopped == 1


def test_a_workspace_that_doesn_t_start_is_missing(tmp_path, monkeypatch):
    found = setup_found(tmp_path, monkeypatch, lambda copy: None, fails="start")
    assert [m.what for m in found] == ["the workspace doesn't start: no such image"]
    assert Workspace.stopped == 1


def test_setup_that_fails_in_the_workspace_is_missing(tmp_path, monkeypatch):
    found = setup_found(tmp_path, monkeypatch, lambda copy: None, fails="setup")
    assert [m.what for m in found] == ["setup fails: no network"]


def test_setup_that_doesn_t_write_the_lockfile_can_t_re_lock(tmp_path, monkeypatch):
    """crew-presentation#66 blocked twice: a story can't write a lock (crew#712)."""
    found = setup_found(tmp_path, monkeypatch, lambda copy: None)
    assert [m.what for m in found] == [
        "setup doesn't write `uv.lock`, so a story that changes a dependency can't "
        "re-lock: setup should resolve before it installs"
    ]


def test_what_setup_leaves_outside_ignored_paths_is_found(tmp_path, monkeypatch):
    """What setup fetched reached every view, and a 1 MB view broke a call (crew#711)."""

    def fetches(copy):
        (copy / "uv.lock").write_text("version = 1\n")
        (copy / ".package").mkdir()
        (copy / ".package" / "data.json").write_text("{}")

    found = setup_found(tmp_path, monkeypatch, fetches)
    assert found == [
        Missing(
            "setup leaves 1 files git would commit (`.package/data.json`); "
            "ignore where it installs and fetches",
            handover.CLAUDE,
        )
    ]


# --- onboarding is the takeover -------------------------------------------------------


def test_onboarding_refuses_a_repository_below_level_2_and_names_what_it_lacks():
    """The Sponsor, 2026-10-17: `crew onboard` runs after the handover (crew#757)."""
    lacking = Missing("CI hasn't run green on `main`", CREW)
    assert onboard.refused(Readiness("site", 1, [lacking]), skeleton=False) == [lacking]


def test_projects_already_onboarded_are_at_level_2_and_not_refused():
    """crew-presentation and sprint-metrics were both at level 2 on 2026-10-17."""
    assert onboard.refused(Readiness("sprint-metrics", 2), skeleton=False) == []


def test_a_project_the_crew_starts_itself_is_onboarded_with_skeleton():
    assert (
        onboard.refused(Readiness("new", 0, [Missing("no record", SPONSOR)]), skeleton=True) == []
    )


@pytest.fixture
def takeover(monkeypatch):
    """`crew onboard` up to its readiness check, with GitHub and the model replaced."""
    import crew_org.auth
    import crew_org.config
    import crew_org.git_ops
    import crew_org.llm
    import crew_org.tools.github_issues
    from crew_org import cli
    from crew_org.flows import readiness

    asked = []
    monkeypatch.setattr(crew_org.config, "load_env", lambda: {"GITHUB_OWNER": "o"})
    monkeypatch.setattr(crew_org.auth, "resolve_credentials", lambda env: ("t", "crew"))
    monkeypatch.setattr(crew_org.tools.github_issues, "IssueClient", lambda *a: object())
    monkeypatch.setattr(crew_org.git_ops, "Workspace", lambda *a: object())
    monkeypatch.setattr(cli, "_bot_identity", lambda *a: None)
    monkeypatch.setattr(crew_org.llm, "health", lambda: asked.append("model") or (False, "down"))

    def at(level, missing=()):
        monkeypatch.setattr(readiness, "check", lambda *a: Readiness("site", level, list(missing)))

    return at, asked


def onboard_cli(*args):
    from typer.testing import CliRunner

    from crew_org.cli import app

    return CliRunner().invoke(app, ["onboard", "site", *args])


def test_crew_onboard_refuses_below_level_2_before_the_interview(takeover):
    at, asked = takeover
    at(1, [Missing("CI hasn't run green on `main`", CREW)])
    result = onboard_cli()
    assert result.exit_code == 1
    assert "readiness level 1" in result.output and "CI hasn't run green" in result.output
    assert "--skeleton" in result.output
    assert asked == [], "refused before the model is asked anything"


def test_crew_onboard_goes_on_at_level_2_and_with_skeleton(takeover):
    at, asked = takeover
    at(2)
    assert onboard_cli().exit_code == 1 and asked == ["model"]
    at(0, [Missing("no record", SPONSOR)])
    assert onboard_cli("--skeleton").exit_code == 1 and asked == ["model", "model"]


def test_the_usual_compose_workspace_service_passes_the_handover(tmp_path, registry):
    """The review of mqucifer/crew#880: `command: sleep infinity` and `volumes` are the
    specification's own compose shape, ignored by the crew, and never "remove it"."""
    compose = (
        f"services:\n  workspace:\n    image: {PINNED}\n    command: sleep infinity\n"
        "    volumes: [..:/workspaces:cached]\n"
    )
    found = check(
        tmp_path,
        {
            ".devcontainer/devcontainer.json": '{"dockerComposeFile": "compose.yaml", '
            '"service": "workspace"}',
            ".devcontainer/compose.yaml": compose,
        },
        setup="null",
    )
    assert "workspace.command" not in whats(found) and "workspace.volumes" not in whats(found)
