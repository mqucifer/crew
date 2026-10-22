"""The services a project declares for its tests, beside its workspace (crew#814, crew#860).

Each was run against Docker on 2026-10-22 before these pin it: Postgres 16 by digest
started as 999:999 with every capability dropped and a read-only root, its data on a
tmpfs it owns, on an `--internal` network; from its container 1.1.1.1 and
host.docker.internal:4000 were unreachable.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from crew_org.flows import gates
from crew_org.tools import container, devcontainer
from crew_org.tools.container import LABEL, Held, sweep
from crew_org.tools.sandbox import Sandbox
from crew_org.tools.services import Declared, Service, declared
from crew_org.tools.workspace import CheckResult, check

WORKSPACE = "ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:" + "e" * 64
POSTGRES = "postgres@sha256:" + "9" * 64
DEVCONTAINER = {
    "dockerComposeFile": "compose.yaml",
    "service": "workspace",
    "postCreateCommand": ["uv", "sync"],
    "containerEnv": {"SPRINT_METRICS_DB": "postgresql://postgres:postgres@postgres/sm"},
}
COMPOSE = f"""
services:
  workspace:
    image: {WORKSPACE}
  postgres:
    image: {POSTGRES}
    user: "999:999"
    environment:
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: sm
    tmpfs: [/var/lib/postgresql/data, /var/run/postgresql]
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "postgres"]
    ports: ["5432:5432"]
    volumes: ["/:/host"]
"""


PROJECT = (
    "version: 3\nintent:\n  scope:\n"
    "    serves: [{who: the Sponsor, needs: [how the crew performs]}]\n"
    "    purpose: A site.\n  release:\n    deploys: false\n"
    "  done:\n    bar: CI is green.\ndesign:\n  checks:\n  - uv run pytest -q\n"
)


def files(devcontainer_json=DEVCONTAINER, compose=COMPOSE) -> dict[str, str]:
    return {
        ".devcontainer/devcontainer.json": json.dumps(devcontainer_json),
        ".devcontainer/compose.yaml": compose,
    }


def test_the_compose_files_other_services_are_declared_as_the_crew_starts_them():
    found = declared(files().get)
    (postgres,) = found.services
    assert postgres == Service(
        "postgres",
        POSTGRES,
        "999:999",
        {"POSTGRES_PASSWORD": "postgres", "POSTGRES_DB": "sm"},
        None,
        ["pg_isready", "-U", "postgres"],
        ["/var/lib/postgresql/data", "/var/run/postgresql"],
    )
    assert found.problems == {}
    assert set(found.not_honoured) == {"postgres.ports", "postgres.volumes"}


def test_a_project_with_no_compose_file_declares_none():
    assert not declared({".devcontainer.json": json.dumps({"image": WORKSPACE})}.get).any
    assert not declared({}.get).any


def test_run_services_chooses_which_start():
    chosen = declared(files({**DEVCONTAINER, "runServices": ["workspace"]}).get)
    assert chosen.services == [] and not chosen.any


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (("image: " + POSTGRES, "image: postgres:16"), "pinned by digest"),
        (('user: "999:999"', 'user: "0:0"'), "never runs as root"),
        (('user: "999:999"', ""), "never runs as root"),
        (('user: "999:999"', 'user: "00"'), "never runs as root"),
        (('user: "999:999"', 'user: "+0"'), "never runs as root"),
        (('user: "999:999"', 'user: "999:0"'), "never runs as root"),
        (('user: "999:999"', 'user: "root:999"'), "never runs as root"),
        (('user: "999:999"', 'user: "--privileged"'), "never runs as root"),
        (("image: " + POSTGRES, "image: --privileged@sha256:1"), "never an option"),
        (('["CMD", "pg_isready", "-U", "postgres"]', '["CMD-SHELL", "pg_isready"]'), "shell"),
        (("POSTGRES_DB: sm", "POSTGRES_DB: ${DB}"), "plain value"),
    ],
)
def test_a_service_that_cant_be_started_safely_is_a_problem_naming_it(change, said):
    found = declared(files(compose=COMPOSE.replace(*change)).get)
    assert found.services == [] and said in found.problems["postgres"]


def test_a_service_named_as_an_option_is_refused_before_any_argv():
    found = declared(files(compose=COMPOSE.replace("  postgres:", "  --rm:")).get)
    assert found.services == [] and "a service's name" in found.problems["--rm"]


def test_a_missing_compose_file_is_a_problem():
    found = declared({".devcontainer/devcontainer.json": json.dumps(DEVCONTAINER)}.get)
    assert "isn't on the default branch" in found.problems["compose"]


def test_the_workspace_image_is_its_compose_services(tmp_path):
    for path, text in files().items():
        (tmp_path / path).parent.mkdir(exist_ok=True)
        (tmp_path / path).write_text(text)
    found = devcontainer.read(tmp_path)
    assert found.image == WORKSPACE and found.problems == []
    assert "dockerComposeFile" not in found.not_honoured


def test_services_are_read_from_the_default_branch_not_the_story(tmp_path):
    """A story's own code mustn't choose what the host pulls and runs (crew#860)."""

    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    origin = tmp_path / "origin"
    git("init", "-q", "-b", "main", str(origin))
    for path, text in files().items():
        (origin / path).parent.mkdir(exist_ok=True)
        (origin / path).write_text(text)
    git("-C", str(origin), "add", ".")
    git("-C", str(origin), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
    work = tmp_path / "work"
    git("clone", "-q", str(origin), str(work))
    (work / ".devcontainer/compose.yaml").write_text(COMPOSE.replace(POSTGRES, "evil@sha256:1"))
    from crew_org.tools.services import at_main

    assert [s.image for s in at_main(work).services] == [POSTGRES]


# --- the held workspace ----------------------------------------------------------------


@pytest.fixture
def ran(monkeypatch) -> list[list[str]]:
    seen: list[list[str]] = []

    def run(argv, **kw):
        seen.append(argv)
        out = "true" if argv[1:3] == ["container", "inspect"] else ""
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(container.subprocess, "run", run)
    monkeypatch.setattr(Sandbox, "unavailable_reason", lambda self: None)
    monkeypatch.setattr(Sandbox, "search_path", lambda self: "/usr/local/bin:/usr/bin")
    return seen


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    for path, text in files().items():
        (tmp_path / path).parent.mkdir(exist_ok=True)
        (tmp_path / path).write_text(text)
    (tmp_path / ".crew").mkdir()
    (tmp_path / ".crew/project.yaml").write_text(PROJECT)
    return tmp_path


def postgres() -> Declared:
    return declared(files().get)


def runs(ran: list[list[str]]) -> list[list[str]]:
    return [a for a in ran if a[1] == "run"]


def test_a_project_with_no_services_starts_exactly_as_before(worktree, ran):
    Held(worktree, "x", declared=Declared()).start()
    (argv,) = runs(ran)
    assert argv[argv.index("--network") + 1] == "none"
    assert not any(a[1] == "network" for a in ran)


def test_its_services_share_an_internal_network_with_the_workspace_and_nothing_else(worktree, ran):
    held = Held(worktree, "x", declared=postgres())
    assert held.start().ok and held.down == {}
    (made,) = [a for a in ran if a[1:3] == ["network", "create"]]
    assert "--internal" in made and made[-1] == "crew-workspace-x-net"
    service, workspace = runs(ran)
    assert workspace[workspace.index("--network") + 1] == "crew-workspace-x-net"
    assert service[service.index("--network") + 1] == "crew-workspace-x-net"
    assert service[service.index("--network-alias") + 1] == "postgres"


def test_a_service_keeps_the_sandboxs_hardening_and_mounts_nothing(worktree, ran):
    Held(worktree, "x", declared=postgres()).start()
    service = runs(ran)[0]
    assert service[service.index("--user") + 1] == "999:999"
    assert "--read-only" in service and service[service.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in service and "--pids-limit" in service
    assert "-v" not in service and "--mount" not in service and "-p" not in service
    assert "/var/lib/postgresql/data:rw,nosuid,size=512m,uid=999,gid=999,mode=0700" in service
    assert f"{LABEL}=1" in service and service[-1] == POSTGRES


def test_its_image_is_pulled_on_the_host_by_digest_before_it_starts(worktree, ran, monkeypatch):
    def run(argv, **kw):
        ran.append(argv)
        missing = argv[1:3] == ["image", "inspect"] and argv[-1] == POSTGRES
        out = "true" if argv[1:3] == ["container", "inspect"] else ""
        return subprocess.CompletedProcess(argv, 1 if missing else 0, out, "")

    monkeypatch.setattr(container.subprocess, "run", run)
    Held(worktree, "x", declared=postgres()).start()
    pulled = [a for a in ran if a[1] == "pull"]
    assert pulled == [["docker", "pull", "-q", POSTGRES]]
    assert ran.index(pulled[0]) < ran.index(runs(ran)[0])


def test_a_service_that_wont_start_is_named_and_the_session_still_starts(
    worktree, ran, monkeypatch
):
    def run(argv, **kw):
        ran.append(argv)
        if argv[1] == "logs":
            return subprocess.CompletedProcess(argv, 0, "FATAL: data directory", "")
        stopped = argv[1:3] == ["container", "inspect"] and argv[-1].endswith("-postgres")
        out = "false" if stopped else "true" if argv[1:3] == ["container", "inspect"] else ""
        return subprocess.CompletedProcess(argv, 0, out, "")

    monkeypatch.setattr(container.subprocess, "run", run)
    held = Held(worktree, "x", declared=postgres())
    assert held.start().ok
    assert "it stopped: FATAL: data directory" in held.down["postgres"]


def test_a_declared_service_that_cant_be_started_safely_is_down_without_running(worktree, ran):
    unsafe = declared(files(compose=COMPOSE.replace(POSTGRES, "postgres:16")).get)
    held = Held(worktree, "x", declared=unsafe)
    assert held.start().ok
    assert "pinned by digest" in held.down["postgres"]
    assert not any(a[1] == "pull" for a in ran)


def test_stop_removes_the_services_and_their_network(worktree, ran):
    held = Held(worktree, "x", declared=postgres())
    held.start()
    held.stop()
    assert ran[-3:] == [
        ["docker", "rm", "-f", "crew-workspace-x"],
        ["docker", "rm", "-f", "crew-workspace-x-postgres"],
        ["docker", "network", "rm", "crew-workspace-x-net"],
    ]


def test_the_sweep_removes_a_gone_crews_networks_too(monkeypatch):
    removed: list[list[str]] = []

    def run(argv, **kw):
        if argv[1] in ("rm", "network") and argv[2] in ("-f", "rm"):
            removed.append(argv)
        listed = {
            "ps": "crew-workspace-dead-postgres\t999999\n",
            "network": f"crew-workspace-dead-net\t999999\ncrew-workspace-mine-net\t{os.getpid()}\n",
        }
        return subprocess.CompletedProcess(argv, 0, listed.get(argv[1], ""), "")

    monkeypatch.setattr(container.subprocess, "run", run)
    monkeypatch.setattr(container, "_alive", lambda pid: pid == str(os.getpid()))
    assert sweep() == ["crew-workspace-dead-postgres", "crew-workspace-dead-net"]
    assert removed[-1] == ["docker", "network", "rm", "crew-workspace-dead-net"]


# --- the gate at done ------------------------------------------------------------------


def test_a_service_thats_down_is_a_finding_at_done_naming_it():
    verdict = gates.Verdict(
        check=CheckResult(results=[], services_down={"postgres": "it stopped: FATAL"})
    )
    gates._checked(verdict)
    (finding,) = verdict.findings
    assert finding.rule == "check.gates.a_service_wont_start"
    assert "- `postgres`: it stopped: FATAL" in finding.says


def test_the_checks_carry_the_held_workspaces_down_services(worktree):
    class Down:
        down = {"postgres": "it stopped"}

        def resync(self):
            return None

        def exec(self, command, timeout=0):
            return container.Result(" ".join(command), 0, "")

    assert check(worktree, held=Down()).services_down == {"postgres": "it stopped"}
