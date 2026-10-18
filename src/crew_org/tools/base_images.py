"""The base images a Dockerfile builds from, as their registries hold them now (#364).

The Developer works in a container with no network (§14), so nothing it can do
tells it what a registry holds. sprint-metrics#310, "pin the base image by
digest", could only be answered by inventing a digest, and was, twice. So the
crew's own process reads the registries, anonymously: a digest the branch pins
that the registry doesn't have sends it back from the gates at `done`, naming
the digest the registry holds now.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from crew_org.tools.repo_context import is_text

TIMEOUT = 15.0
_FROM = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", re.I | re.M)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class Ref:
    """An image reference: `python:3.12-slim@sha256:…` or `ghcr.io/o/r:1`."""

    host: str
    name: str
    tag: str
    digest: str | None

    @property
    def shown(self) -> str:
        repo = self.name.removeprefix("library/") if self.host == "docker.io" else self.name
        prefix = "" if self.host == "docker.io" else f"{self.host}/"
        return f"{prefix}{repo}:{self.tag}"


def parse(reference: str) -> Ref | None:
    """The parts of an image reference, or None for one this can't resolve (a variable)."""
    if "$" in reference:
        return None
    ref, _, digest = reference.partition("@")
    first, _, rest = ref.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        host, path = first, rest
    else:
        host, path = "docker.io", ref
    name, _, tag = path.rpartition(":") if ":" in path.rsplit("/", 1)[-1] else (path, "", "")
    if host == "docker.io" and "/" not in name:
        name = f"library/{name}"
    return Ref(host=host, name=name, tag=tag or "latest", digest=digest or None)


def base_images(dockerfile: str) -> list[Ref]:
    """The images a Dockerfile's stages build from: not `scratch`, not an earlier stage."""
    stages: set[str] = set()
    found: list[Ref] = []
    for match in _FROM.finditer(dockerfile):
        reference, alias = match.group(1), match.group(2)
        if reference.lower() != "scratch" and reference.lower() not in stages:
            ref = parse(reference)
            if ref is not None and ref not in found:
                found.append(ref)
        if alias:
            stages.add(alias.lower())
    return found


def is_dockerfile(path: str) -> bool:
    name = Path(path).name
    return is_text(Path(path)) and ("dockerfile" in name.lower() or "containerfile" in name.lower())


# --- reading a registry, anonymously ---------------------------------------------------------

_AUTH = {
    "docker.io": (
        "https://auth.docker.io/token",
        "registry.docker.io",
        "https://registry-1.docker.io",
    ),
    "ghcr.io": ("https://ghcr.io/token", "ghcr.io", "https://ghcr.io"),
    # Microsoft's registry serves Playwright's browser images, and needs no token (#403).
    "mcr.microsoft.com": (None, None, "https://mcr.microsoft.com"),
}
_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


class Unreadable(Exception):
    """The registry couldn't be asked, or wouldn't answer anonymously."""


def manifest_digest(host: str, name: str, reference: str) -> str | None:
    """The digest a tag or digest resolves to, or None if the registry has no such thing."""
    if host not in _AUTH:
        raise Unreadable(f"{host} isn't a registry the crew reads")
    auth, service, registry = _AUTH[host]
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            headers = {"Accept": _ACCEPT}
            if auth is not None:
                token = client.get(
                    auth, params={"service": service, "scope": f"repository:{name}:pull"}
                )
                token.raise_for_status()
                headers["Authorization"] = f"Bearer {token.json().get('token', '')}"
            response = client.head(f"{registry}/v2/{name}/manifests/{reference}", headers=headers)
    except httpx.HTTPError as exc:
        raise Unreadable(f"{host} couldn't be reached: {exc}") from exc
    if response.status_code == 404:
        return None
    if response.status_code in (401, 403):
        raise Unreadable(f"{host}/{name} can't be read anonymously")
    if response.status_code >= 400:
        raise Unreadable(f"{host} answered {response.status_code}")
    return response.headers.get("Docker-Content-Digest")


# Replaced in tests: every lookup goes through here.
LOOKUP: Callable[[str, str, str], str | None] = manifest_digest

# What's been read, for the life of the process: a tick. A tag moves every week
# or two, not between one card and the next, and a digest exists or doesn't.
# A registry that couldn't be read isn't remembered, so it's asked again.
_READ: dict[tuple[str, str, str], str | None] = {}


def lookup(host: str, name: str, reference: str) -> str | None:
    key = (host, name, reference)
    if key not in _READ:
        _READ[key] = LOOKUP(host, name, reference)
    return _READ[key]


# --- what's refused ------------------------------------------------------------------------


def invented(path: str, dockerfile: str) -> list[str]:
    """A reason for each digest this Dockerfile pins that its registry doesn't have."""
    reasons: list[str] = []
    for ref in base_images(dockerfile):
        if ref.digest is None:
            continue
        if not _DIGEST.match(ref.digest):
            reasons.append(f"{path} pins `{ref.shown}@{ref.digest}`, which isn't a sha256 digest")
            continue
        try:
            found = lookup(ref.host, ref.name, ref.digest)
            current = lookup(ref.host, ref.name, ref.tag)
        except Unreadable as exc:
            reasons.append(
                f"{path}: the digest pinned for `{ref.shown}` couldn't be checked ({exc})"
            )
            continue
        if found is None:
            reasons.append(
                f"{path} pins `{ref.shown}@{ref.digest}`, which its registry doesn't have: it "
                f"was worked out, not read. `{ref.shown}` is `{current}` now; pin that."
            )
    return reasons
