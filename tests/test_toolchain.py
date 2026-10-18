"""A project's sandbox image and checks come from its design (#403)."""

from __future__ import annotations

from pathlib import Path

from crew_org.tools import workspace
from crew_org.tools.sandbox import IMAGE, Mode, Sandbox
from crew_org.tools.workspace import AUTOFIX, SETUP, VERIFY, CommandResult, toolchain

INTENT = """version: 2
intent:
  scope:
    purpose: A static site.
  release:
    deploys: false
  done:
    bar: Its acceptance criteria are met and CI is green.
"""

PLAYWRIGHT = "mcr.microsoft.com/playwright@sha256:" + "a" * 64


def project(tmp_path: Path, design: str = "") -> Path:
    (tmp_path / ".crew").mkdir()
    (tmp_path / ".crew/project.yaml").write_text(INTENT + (f"design:\n{design}" if design else ""))
    return tmp_path


def test_no_record_keeps_today_s_python_toolchain(tmp_path):
    tools = toolchain(tmp_path)
    assert tools.image is None and tools.setup == SETUP
    assert tools.autofix == AUTOFIX and tools.verify == VERIFY and tools.problem is None


def test_sprint_metrics_design_is_checked_exactly_as_before(tmp_path):
    """Its design names only its checks, which are today's commands."""
    tools = toolchain(
        project(tmp_path, "  checks:\n  - uv run ruff check .\n  - uv run pytest -q\n")
    )
    assert tools.image is None and tools.setup == SETUP and tools.autofix == AUTOFIX
    assert tools.verify == (["uv", "run", "ruff", "check", "."], ["uv", "run", "pytest", "-q"])


def test_a_design_with_its_own_image_runs_only_what_it_names(tmp_path):
    design = f"  sandbox_image: {PLAYWRIGHT}\n  setup: npm ci\n  checks:\n  - npx playwright test\n"
    tools = toolchain(project(tmp_path, design))
    assert tools.image == PLAYWRIGHT and tools.setup == ["npm", "ci"]
    assert tools.autofix == () and tools.verify == (["npx", "playwright", "test"],)
    assert tools.problem is None


def test_an_image_not_pinned_by_digest_is_refused(tmp_path):
    design = (
        "  sandbox_image: mcr.microsoft.com/playwright:latest\n  checks:\n  - npx playwright test\n"
    )
    assert "isn't pinned by digest" in toolchain(project(tmp_path, design)).problem


def test_an_image_with_nothing_to_check_is_refused(tmp_path):
    assert "no checks" in toolchain(project(tmp_path, f"  sandbox_image: {PLAYWRIGHT}\n")).problem


def test_check_runs_the_design_s_commands_in_the_design_s_image(tmp_path, monkeypatch):
    design = f"  sandbox_image: {PLAYWRIGHT}\n  setup: npm ci\n  checks:\n  - npx playwright test\n"
    ran = []

    def fake_run(worktree, command, *, sandbox, network, timeout, env=None):
        ran.append((command, sandbox.image, network))
        return CommandResult(command=" ".join(command), code=0, output="")

    monkeypatch.setattr(workspace, "run", fake_run)
    result = workspace.check(project(tmp_path, design), sandbox=Sandbox(mode=Mode.REQUIRED))
    assert ran == [
        (["npm", "ci"], PLAYWRIGHT, True),
        (["npx", "playwright", "test"], PLAYWRIGHT, False),
    ]
    assert result.ok


def test_a_refused_design_runs_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    design = "  sandbox_image: node:22\n  checks:\n  - npm test\n"
    result = workspace.check(project(tmp_path, design))
    assert not result.ok and "isn't pinned by digest" in result.results[0].output


def test_the_default_image_is_unchanged():
    assert Sandbox().image == IMAGE


# --- live, opt-in: CREW_SANDBOX_TESTS=1 (Docker, a network, a 2 GB image) -----------------

import os  # noqa: E402
import shutil  # noqa: E402

import pytest  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "static-site"


@pytest.mark.skipif(not os.environ.get("CREW_SANDBOX_TESTS"), reason="needs Docker and a network")
def test_a_static_site_is_built_and_checked_from_its_own_record(tmp_path):
    """#403's done-when: a browser test runs in the sandbox, from the design alone."""
    site = tmp_path / "site"
    shutil.copytree(FIXTURE, site)
    result = workspace.check(site, sandbox=Sandbox(mode=Mode.REQUIRED))
    assert result.ok, result.results[-1].output[-600:]
    assert [r.command for r in result.results] == ["npm ci", "npx playwright test"]


# --- the Architect proposes them, and the image is real ---------------------------------

from crew_org.crews.design_crew import Choice, DesignProposal  # noqa: E402
from crew_org.flows import design as design_flow  # noqa: E402
from crew_org.tools import base_images  # noqa: E402

REAL = "mcr.microsoft.com/playwright:v1.63.0-noble@sha256:" + "e" * 64


def proposal(**kw):
    return DesignProposal(
        checks=[Choice(value="npx playwright test", basis="package.json")],
        summary="A static site tested in a browser.",
        **kw,
    )


def test_the_architect_s_toolchain_reaches_the_record():
    design = design_flow.to_design(
        proposal(
            sandbox_image=Choice(value=REAL, basis="Playwright's own image"),
            setup=Choice(value="npm ci", basis="package-lock.json"),
            autofix=[Choice(value="npx prettier --write .", basis="package.json")],
        )
    )
    assert design.sandbox_image == REAL and design.setup == "npm ci"
    assert design.autofix == ["npx prettier --write ."]


def test_a_python_design_names_none_of_them():
    design = design_flow.to_design(proposal())
    assert design.sandbox_image is None and design.setup is None and design.autofix == []


def test_a_real_pinned_image_holds(monkeypatch):
    monkeypatch.setattr(base_images, "lookup", lambda host, name, ref: ref)
    assert design_flow.image_problems(Choice(value=REAL, basis="x")) == []


def test_an_invented_digest_is_refused(monkeypatch):
    monkeypatch.setattr(base_images, "lookup", lambda host, name, ref: None)
    assert "doesn't exist" in design_flow.image_problems(Choice(value=REAL, basis="x"))[0]


def test_an_unpinned_image_is_refused():
    problems = design_flow.image_problems(Choice(value="node:22", basis="x"))
    assert "isn't pinned by digest" in problems[0]


def test_microsoft_s_registry_is_read_without_a_token():
    assert base_images._AUTH["mcr.microsoft.com"][0] is None


def test_a_named_tag_is_pinned_to_the_digest_the_registry_reads(monkeypatch):
    read = {}
    digest = "sha256:" + "a" * 64

    def lookup(host, name, ref):
        read["asked"] = (host, name, ref)
        return digest

    monkeypatch.setattr(base_images, "lookup", lookup)
    pinned, problems = design_flow.pin_image(
        Choice(value="mcr.microsoft.com/playwright:v1.63.0-noble (kept current)", basis="x")
    )
    assert problems == []
    assert read["asked"] == ("mcr.microsoft.com", "playwright", "v1.63.0-noble")
    assert pinned.value == f"mcr.microsoft.com/playwright:v1.63.0-noble@{digest}"


def test_a_tag_the_registry_doesn_t_have_is_refused(monkeypatch):
    monkeypatch.setattr(base_images, "lookup", lambda host, name, ref: None)
    _, problems = design_flow.pin_image(Choice(value="node:99-invented", basis="x"))
    assert "no such tag" in problems[0]


def test_a_pinned_image_keeps_its_digest_and_loses_the_commentary(monkeypatch):
    monkeypatch.setattr(base_images, "lookup", lambda *a: pytest.fail("no lookup to pin"))
    pinned, problems = design_flow.pin_image(Choice(value=f"{REAL} (pinned)", basis="x"))
    assert problems == [] and pinned.value == REAL


def test_a_project_whose_parts_name_no_python_has_no_python(tmp_path):
    design = (
        f"  sandbox_image: {PLAYWRIGHT}\n  checks:\n  - npx playwright test\n"
        "  parts:\n  - path: ''\n    language: javascript\n"
    )
    assert toolchain(project(tmp_path, design)).python is False


def test_a_python_part_in_its_own_image_is_python(tmp_path):
    """The coverage map follows the language, not the image (crew#690, ADR 0030)."""
    design = (
        f"  sandbox_image: {PLAYWRIGHT}\n  checks:\n  - python -m pytest\n"
        "  parts:\n  - path: ''\n    language: python\n"
        "  - path: site/js/\n    language: javascript\n"
    )
    assert toolchain(project(tmp_path, design)).python is True


def test_no_parts_named_means_the_whole_project_is_python(tmp_path):
    assert toolchain(tmp_path).python is True


# --- commands run as their words, never through a shell (crew#690) --------


def test_shell_operators_are_found_spaced_or_not():
    from crew_org.tools.workspace import shell_operators

    assert shell_operators("pip install -r requirements.txt && python x.py") == ["&&"]
    assert shell_operators("pip install -r r.txt&&python x.py") == ["&&"]
    assert shell_operators("ruff check . | tee out") == ["|"]
    assert shell_operators("python build.py; true") == [";"]


def test_quoted_text_is_an_argument_not_an_operator():
    from crew_org.tools.workspace import shell_operators

    assert shell_operators('python -m pytest -k "a or b"') == []
    assert shell_operators('python -c "print(1 > 0)"') == []
    assert shell_operators("uv sync --extra dev --quiet") == []


def test_a_merged_design_whose_setup_needs_a_shell_is_a_problem(tmp_path):
    """Refused before anything runs, and the card says why: crew-presentation's
    merged setup could never have run."""
    design = (
        f"  sandbox_image: {PLAYWRIGHT}\n"
        "  setup: pip install -r requirements.txt && python tools/fetch_package.py\n"
        "  checks:\n  - python -m pytest\n"
    )
    problem = toolchain(project(tmp_path, design)).problem
    assert "uses `&&`" in problem and "without a shell" in problem


# --- devcontainer.json, the workspace's one source (crew#778) -----------------


def with_file(tmp_path: Path, text: str, design: str = "  checks:\n  - npx playwright test\n"):
    root = project(tmp_path, design)
    (root / ".devcontainer").mkdir()
    (root / ".devcontainer/devcontainer.json").write_text(text)
    return root


def test_the_file_gives_the_image_setup_and_environment(tmp_path):
    tools = toolchain(
        with_file(
            tmp_path,
            f'{{"image": "{PLAYWRIGHT}", "postCreateCommand": ["npm", "ci"],'
            ' "containerEnv": {"CI": "1"}}',
        )
    )
    assert tools.image == PLAYWRIGHT and tools.setup == ["npm", "ci"]
    assert tools.env == {"CI": "1"} and tools.problem is None
    # Its own image, so only what the design names runs in it.
    assert tools.autofix == () and tools.verify == (["npx", "playwright", "test"],)


def test_the_file_is_the_one_source_over_the_design(tmp_path):
    design = "  sandbox_image: other@sha256:abc\n  setup: uv sync\n  checks:\n  - uv run pytest\n"
    tools = toolchain(with_file(tmp_path, f'{{"image": "{PLAYWRIGHT}"}}', design))
    assert tools.image == PLAYWRIGHT and tools.setup is None


def test_a_file_that_can_t_be_used_is_refused_before_anything_runs(tmp_path, monkeypatch):
    root = with_file(tmp_path, f'{{"image": "{PLAYWRIGHT}", "postCreateCommand": "npm ci"}}')
    assert "array of words" in toolchain(root).problem
    monkeypatch.setattr(workspace, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    result = workspace.check(root, sandbox=Sandbox(mode=Mode.REQUIRED))
    assert not result.ok and "array of words" in result.results[0].output


def test_the_file_s_image_is_pinned_like_the_design_s(tmp_path):
    problem = toolchain(with_file(tmp_path, '{"image": "node:22"}')).problem
    assert "`.devcontainer/devcontainer.json`" in problem and "isn't pinned by digest" in problem


def test_check_gives_each_command_the_file_s_environment(tmp_path, monkeypatch):
    seen = []

    def fake_run(worktree, command, *, sandbox, network, timeout, env=None):
        seen.append(env)
        return CommandResult(command=" ".join(command), code=0, output="")

    monkeypatch.setattr(workspace, "run", fake_run)
    root = with_file(
        tmp_path,
        f'{{"image": "{PLAYWRIGHT}", "postCreateCommand": ["npm", "ci"],'
        ' "containerEnv": {"CI": "1"}}',
    )
    assert workspace.check(root, sandbox=Sandbox(mode=Mode.REQUIRED)).ok
    assert seen == [{"CI": "1"}, {"CI": "1"}]
