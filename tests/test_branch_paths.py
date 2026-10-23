"""The branch never picks a host file for the crew to read (crew#839, the review of
mqucifer/crew#877).

`dockerComposeFile` and the files a branch adds are the branch's to name. An absolute
path, one that climbs out of the repository, or a symbolic link would have had the crew
read a host file, `~/.claude/settings.json` with its token, and quote it back in a parse
error the session sees. Each is refused by name, and nothing of the file is read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crew_org.flows import role_tasks
from crew_org.tools import bounds, container, devcontainer, workspace

# A line standing for the token, on YAML that won't parse, so a parse error would quote it.
TOKEN = "NOT-A-REAL-SECRET-crew839"
SECRET = f"ok: 1\n: [GH_TOKEN={TOKEN} unclosed\n"


@pytest.fixture
def host(tmp_path: Path) -> Path:
    """A host file outside the repository, as the settings file is."""
    (tmp_path / "home").mkdir()
    secret = tmp_path / "home" / "settings.json"
    secret.write_text(SECRET)
    return secret


def repo(tmp_path: Path, compose: str) -> Path:
    root = tmp_path / "repo"
    (root / ".devcontainer").mkdir(parents=True)
    (root / ".devcontainer/devcontainer.json").write_text(
        json.dumps({"dockerComposeFile": compose, "service": "workspace"})
    )
    return root


def everything_said(root: Path) -> str:
    """Every message the crew writes about the workspace: the gate's and the session's."""
    defined = devcontainer.read(root)
    said = [
        *(defined.problems if defined else []),
        str(workspace.toolchain(root).problem),
        *container.own_services(root).problems.values(),
        *role_tasks.workspace_problems(root),
        *role_tasks.diagram_problems_in(root),
    ]
    return "\n".join(said)


def test_an_absolute_compose_path_is_refused_and_never_read(tmp_path, host):
    root = repo(tmp_path, str(host))
    said = everything_said(root)
    assert TOKEN not in said and "unclosed" not in said
    assert "is outside the repository: not read" in said


def test_a_compose_path_that_climbs_out_is_refused_and_never_read(tmp_path, host):
    root = repo(tmp_path, "../../home/settings.json")
    said = everything_said(root)
    assert TOKEN not in said and "unclosed" not in said
    assert "is outside the repository: not read" in said


def test_a_compose_file_that_links_out_is_refused_and_never_read(tmp_path, host):
    root = repo(tmp_path, "compose.yaml")
    (root / ".devcontainer/compose.yaml").symlink_to(host)
    said = everything_said(root)
    assert TOKEN not in said and "unclosed" not in said
    assert "goes through a symbolic link: not read" in said


def test_a_folder_that_links_out_is_refused_too(tmp_path, host):
    root = repo(tmp_path, "out/settings.json")
    (root / ".devcontainer/out").symlink_to(host.parent)
    said = everything_said(root)
    assert TOKEN not in said and "goes through a symbolic link: not read" in said


def test_a_workspace_file_that_is_a_link_is_refused(tmp_path, host):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".devcontainer.json").symlink_to(host)
    said = everything_said(root)
    assert TOKEN not in said and "goes through a symbolic link: not read" in said


def test_the_diagram_is_never_read_through_a_link(tmp_path, host):
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / role_tasks.DIAGRAM_PATH).symlink_to(host)
    said = "\n".join(role_tasks.diagram_problems_in(root))
    assert TOKEN not in said and "goes through a symbolic link: not read" in said


def test_the_gates_read_no_changed_file_through_a_link(tmp_path, host):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "Dockerfile").symlink_to(host)
    (root / "compose.yaml").symlink_to(host)
    outside: list[str] = []
    found = bounds._read(root, ["Dockerfile", "compose.yaml"], outside)
    assert found == {} and len(outside) == 2
    assert TOKEN not in "\n".join(outside)


def test_a_file_inside_still_reads(tmp_path):
    root = repo(tmp_path, "compose.yaml")
    (root / ".devcontainer/compose.yaml").write_text("services: {}\n")
    assert devcontainer.read_inside(root, ".devcontainer/compose.yaml") == "services: {}\n"
    assert devcontainer.read_inside(root, ".devcontainer/missing.yaml") is None
