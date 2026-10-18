"""An invented digest is sent back, naming what the registry holds (#364).

sprint-metrics#310, "pin the base image by digest", pinned
sha256:0e03e071…6a7b8c9d0e twice. The sandbox has no network, so the digest
could only be made up. Docker Hub's was sha256:f77ac9e4…
"""

from __future__ import annotations

import pytest

from crew_org.tools import base_images, bounds
from crew_org.tools.base_images import base_images as froms
from crew_org.tools.base_images import invented, parse

REAL = "sha256:" + "f7" * 32
FAKE = "sha256:" + "0e" * 32


@pytest.fixture
def registry(monkeypatch):
    """python:3.12-slim is REAL; every other digest is unknown."""
    asked = []

    def lookup(host, name, reference):
        asked.append((host, name, reference))
        if name == "library/python" and reference in ("3.12-slim", REAL):
            return REAL
        return None

    monkeypatch.setattr(base_images, "LOOKUP", lookup)
    return asked


def test_references_are_read_as_registries_name_them():
    assert parse("python:3.12-slim") == base_images.Ref(
        "docker.io", "library/python", "3.12-slim", None
    )
    assert parse(f"python:3.12-slim@{REAL}").digest == REAL
    assert parse("ghcr.io/mqucifer/sprint-metrics:1.0.0").host == "ghcr.io"
    assert parse("ubuntu").tag == "latest"
    assert parse("$BASE") is None


def test_an_earlier_stage_and_scratch_are_not_base_images():
    dockerfile = "FROM python:3.12-slim AS build\nFROM build AS test\nFROM scratch\n"
    assert [r.shown for r in froms(dockerfile)] == ["python:3.12-slim"]


def test_an_invented_digest_is_refused_and_the_real_one_named(registry):
    [why] = invented("Dockerfile", f"FROM python:3.12-slim@{FAKE}\n")
    assert "doesn't have" in why and REAL in why


def test_a_real_digest_passes(registry):
    assert invented("Dockerfile", f"FROM python:3.12-slim@{REAL}\n") == []


def test_one_that_cant_be_checked_is_refused_not_passed():
    [why] = invented("Dockerfile", f"FROM python:3.12-slim@{FAKE}\n")
    assert "couldn't be checked" in why


def gates(dockerfile: str) -> list[str]:
    """What the gates at `done` find in a branch that changes the Dockerfile (crew#778)."""
    return bounds.judged(
        ["Dockerfile"], None, workflows=({}, {}), dockerfiles={"Dockerfile": dockerfile}
    )


def test_the_gates_send_back_a_branch_pinning_an_invented_digest(registry):
    assert any("doesn't have" in r for r in gates(f"FROM python:3.12-slim@{FAKE}\n"))


def test_a_dockerfile_with_no_digest_costs_no_lookup(registry):
    assert gates("FROM python:3.12-slim\n") == [] and registry == []


# --- once a tick ----------------------------------------------------------------------------------


def test_a_tag_is_read_once_a_tick(registry):
    invented("Dockerfile", f"FROM python:3.12-slim@{REAL}\n")
    invented("Dockerfile", f"FROM python:3.12-slim@{REAL}\n")
    assert registry.count(("docker.io", "library/python", "3.12-slim")) == 1


def test_a_registry_that_couldnt_be_read_is_asked_again():
    [why] = invented("Dockerfile", f"FROM python:3.12-slim@{FAKE}\n")
    assert "couldn't be checked" in why
    assert ("docker.io", "library/python", "3.12-slim") not in base_images._READ
