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
import yaml  # type: ignore[import-untyped]

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


def _headers(client: httpx.Client, host: str, name: str) -> tuple[str, dict[str, str]]:
    """The registry's address, and the headers that read `name` there anonymously."""
    if host not in _AUTH:
        raise Unreadable(f"{host} isn't a registry the crew reads")
    auth, service, registry = _AUTH[host]
    headers = {"Accept": _ACCEPT}
    if auth is not None:
        token = client.get(auth, params={"service": service, "scope": f"repository:{name}:pull"})
        token.raise_for_status()
        headers["Authorization"] = f"Bearer {token.json().get('token', '')}"
    return registry, headers


def _refused(response: httpx.Response, host: str, name: str) -> None:
    if response.status_code in (401, 403):
        raise Unreadable(f"{host}/{name} can't be read anonymously")
    if response.status_code >= 400:
        raise Unreadable(f"{host} answered {response.status_code}")


def manifest_digest(host: str, name: str, reference: str) -> str | None:
    """The digest a tag or digest resolves to, or None if the registry has no such thing."""
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            registry, headers = _headers(client, host, name)
            response = client.head(f"{registry}/v2/{name}/manifests/{reference}", headers=headers)
    except httpx.HTTPError as exc:
        raise Unreadable(f"{host} couldn't be reached: {exc}") from exc
    if response.status_code == 404:
        return None
    _refused(response, host, name)
    return response.headers.get("Docker-Content-Digest")


# A page of tags, and how many pages are read: postgres has thousands, in the
# registry's lexical order, so a prefix is matched across all of them.
TAG_PAGE = 1000
TAG_PAGES = 10


def tag_list(host: str, name: str) -> tuple[list[str], bool]:
    """The tags the registry holds for `name`, and whether there were more than were read."""
    found: list[str] = []
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            registry, headers = _headers(client, host, name)
            url: str | None = f"{registry}/v2/{name}/tags/list?n={TAG_PAGE}"
            for _ in range(TAG_PAGES):
                if url is None:
                    break
                response = client.get(url, headers=headers)
                if response.status_code == 404:
                    return [], False
                _refused(response, host, name)
                found += [t for t in response.json().get("tags") or [] if isinstance(t, str)]
                following = response.links.get("next", {}).get("url")
                url = str(httpx.URL(registry).join(following)) if following else None
    except httpx.HTTPError as exc:
        raise Unreadable(f"{host} couldn't be reached: {exc}") from exc
    return found, url is not None


# Replaced in tests: every lookup goes through here.
LOOKUP: Callable[[str, str, str], str | None] = manifest_digest
TAGS: Callable[[str, str], tuple[list[str], bool]] = tag_list

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
        if ref.digest is not None:
            reasons += unread(path, ref, ref.digest)
    return reasons


def compose_images(compose: str) -> dict[str, str | None]:
    """Each compose service's image, by service; None for one that names none."""
    try:
        spec = yaml.safe_load(compose)
    except yaml.YAMLError:
        return {}  # `services.declared` says it doesn't parse
    given = spec.get("services") if isinstance(spec, dict) else None
    if not isinstance(given, dict):
        return {}
    return {
        str(name): image if isinstance(image := (raw or {}).get("image"), str) else None
        for name, raw in given.items()
        if isinstance(raw, dict | None)
    }


def unpinned(path: str, compose: str) -> list[str]:
    """A reason for each image a compose file names that isn't pinned to a digest its
    registry has (crew#839).

    The host pulls these, so a tag that can move is refused as well as a digest that
    was worked out: either way the reason names the digest the registry holds now,
    since the session can't read a registry itself.
    """
    reasons: list[str] = []
    for service, image in compose_images(compose).items():
        ref = parse(image) if image else None
        if ref is None:
            reasons.append(f"{path}'s `{service}` names no image the crew can read a registry for")
        elif ref.digest is None:
            try:
                current = lookup(ref.host, ref.name, ref.tag)
            except Unreadable as exc:
                reasons.append(f"{path} pins `{ref.shown}` by tag, and it couldn't be read ({exc})")
                continue
            held = (
                f"`{ref.shown}` is `{current}` now; pin `{ref.shown}@{current}`"
                if current
                else (f"its registry has no `{ref.shown}`")
            )
            reasons.append(f"{path} pins `{ref.shown}` by tag, which can move: {held}.")
        else:
            reasons += unread(path, ref, ref.digest)
    return reasons


def unread(path: str, ref: Ref, digest: str) -> list[str]:
    """Why the digest `path` pins for `ref` wasn't read from its registry, or nothing."""
    if not _DIGEST.match(digest):
        return [f"{path} pins `{ref.shown}@{digest}`, which isn't a sha256 digest"]
    try:
        found = lookup(ref.host, ref.name, digest)
        current = lookup(ref.host, ref.name, ref.tag)
    except Unreadable as exc:
        return [f"{path}: the digest pinned for `{ref.shown}` couldn't be checked ({exc})"]
    if found is None:
        return [
            f"{path} pins `{ref.shown}@{digest}`, which its registry doesn't have: it "
            f"was worked out, not read. `{ref.shown}` is `{current}` now; pin that."
        ]
    return []
