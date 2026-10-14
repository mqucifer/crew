"""The sandbox is the thing standing between a generated test and the user's
machine, so its construction is pinned rather than trusted.

The live behaviour these encode was verified against Docker on 2026-09-18:
network blocked for test code but available for dependency resolution, a
read-only root filesystem, uid 501 rather than root, no host filesystem
visible, and a 4GB allocation killed at the 2g limit (exit 137).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from crew_org.config import load_org
from crew_org.tools.sandbox import HOME_DIR, Mode, Sandbox, prepare_home


@pytest.fixture
def box() -> Sandbox:
    return Sandbox(mode=Mode.REQUIRED, engine="docker")


def argv(box: Sandbox, *, network: bool = False) -> list[str]:
    return box.command(Path("/tmp/wt"), ["uv", "run", "pytest"], network=network)


# --- what the container is given ----------------------------------------


def test_test_code_gets_no_network(box):
    a = argv(box, network=False)
    assert a[a.index("--network") + 1] == "none"


def test_only_dependency_resolution_gets_a_network(box):
    a = argv(box, network=True)
    assert a[a.index("--network") + 1] == "bridge"


def test_it_does_not_run_as_root(box):
    assert "--user" in argv(box)
    assert "0:0" not in argv(box)


def test_the_root_filesystem_is_read_only(box):
    assert "--read-only" in argv(box)


def test_all_capabilities_are_dropped(box):
    a = argv(box)
    assert a[a.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in a[a.index("--security-opt") + 1]


def test_resources_are_bounded(box):
    a = argv(box)
    for flag in ("--memory", "--pids-limit", "--cpus"):
        assert flag in a, f"{flag} is not set"
    # Swap must match memory, or the limit is trivially escaped.
    assert a[a.index("--memory") + 1] == a[a.index("--memory-swap") + 1]


def test_only_the_worktree_and_cache_are_mounted(box):
    mounts = [argv(box)[i + 1] for i, x in enumerate(argv(box)) if x == "-v"]
    assert any(m.endswith(":/work") for m in mounts)
    assert any(m.endswith(":/cache") for m in mounts)
    assert len(mounts) == 2


def test_the_cache_is_a_host_directory_not_a_named_volume(box):
    """A named volume is created root-owned, and the container runs as the
    invoking user, so uv could not write into one."""
    mounts = [argv(box)[i + 1] for i, x in enumerate(argv(box)) if x == "-v"]
    cache = next(m for m in mounts if m.endswith(":/cache"))
    assert cache.startswith("/"), f"{cache} is a named volume, not a path"


def test_the_container_is_removed_after_the_run(box):
    assert "--rm" in argv(box)


# --- refusing to run unprotected ----------------------------------------


def test_a_missing_engine_blocks_rather_than_falling_back(monkeypatch, box):
    """A silent fallback to the host is worse than no sandbox: it looks
    protected while running arbitrary code as the user."""
    monkeypatch.setattr("crew_org.tools.sandbox.shutil.which", lambda _: None)
    reason = box.unavailable_reason()
    assert reason and "not on PATH" in reason
    assert "'off'" in reason


def test_a_dead_daemon_is_reported(monkeypatch, box):
    monkeypatch.setattr("crew_org.tools.sandbox.shutil.which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "no daemon")
    )
    assert "no daemon is answering" in (box.unavailable_reason() or "")


def test_a_hanging_daemon_does_not_hang_the_tick(monkeypatch, box):
    monkeypatch.setattr("crew_org.tools.sandbox.shutil.which", lambda _: "/usr/bin/docker")

    def hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=15)

    monkeypatch.setattr(subprocess, "run", hang)
    assert "did not respond" in (box.unavailable_reason() or "")


def test_mode_off_is_explicit_and_does_not_block():
    assert Sandbox(mode=Mode.OFF).unavailable_reason() is None


def test_the_shipped_config_requires_a_sandbox():
    assert Sandbox.from_config(load_org()).mode is Mode.REQUIRED


# --- what carries from one command to the next (crew#690, ADR 0030) -----


def test_home_is_inside_the_worktree(box):
    """Each command is its own container: a home under /tmp lost what setup
    installed before the checks ran."""
    a = argv(box)
    assert "HOME=/work/.crew-home" in a
    assert "HOME=/tmp" not in a


def test_what_setup_installed_under_the_home_is_found_first(box):
    a = box.command(Path("/tmp/wt"), ["ruff"], network=False, path="/usr/local/bin:/usr/bin")
    assert "PATH=/work/.crew-home/.local/bin:/usr/local/bin:/usr/bin" in a
    assert a.index("PATH=/work/.crew-home/.local/bin:/usr/local/bin:/usr/bin") < a.index(box.image)


def test_without_the_image_s_path_none_is_set(box):
    assert not [x for x in argv(box) if x.startswith("PATH=")]


def test_the_home_is_never_committed(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    prepare_home(tmp_path)
    (tmp_path / HOME_DIR / "lib.py").write_text("x = 1\n")
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert status == ""


def test_the_roles_that_write_commands_are_told_how_they_run():
    """crew-presentation's design called the image's own `python` while its setup
    installed into a uv environment, and the Developer saw only "No module named
    pytest": neither was told how the sandbox runs commands (crew#690)."""
    from crew_org.project import brief, parse
    from crew_org.tools.sandbox import HOME_DIR, HOW_IT_RUNS

    assert (
        f"HOME is `{HOME_DIR}`" in HOW_IT_RUNS and "only the checkout carries over" in HOW_IT_RUNS
    )
    intent = (
        "version: 2\nintent:\n  scope:\n    purpose: A site.\n  release:\n    deploys: false\n"
        "  done:\n    bar: CI is green.\n"
    )
    own = parse(intent + "design:\n  sandbox_image: x@sha256:abc\n  checks: [python -m pytest]\n")
    default = parse(intent + "design:\n  checks: [uv run pytest -q]\n")
    assert HOW_IT_RUNS in brief(own)
    assert HOW_IT_RUNS not in brief(default), "the default image's commands are the crew's own"


def test_the_architect_is_told_how_commands_run(monkeypatch):
    from crew_org.crews.design_crew import propose_design
    from crew_org.tools.sandbox import HOW_IT_RUNS
    from tests.asked import Stop, capture

    seen = capture(monkeypatch)
    with pytest.raises(Stop):
        propose_design(project="A site.", repository="")
    assert HOW_IT_RUNS in seen["description"]
