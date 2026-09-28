"""Reading a published container image from GHCR, anonymously (#335).

A public package is readable with an anonymous pull token, so checking what a
release published needs no credential and no new permission. A package that
isn't public reads as unreadable, and says so: making it public is the
Sponsor's step after the first push.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

REGISTRY = "https://ghcr.io"
ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
INDEX_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)


class ImageUnreadable(Exception):
    """The registry refused an anonymous read."""


@dataclass
class Image:
    digest: str
    platforms: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)


class Registry:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=30, follow_redirects=True)
        self._tokens: dict[str, str] = {}

    def _token(self, name: str) -> str:
        if name not in self._tokens:
            response = self._client.get(
                f"{REGISTRY}/token", params={"scope": f"repository:{name}:pull"}
            )
            if response.status_code in (401, 403):
                raise ImageUnreadable(
                    f"ghcr.io/{name} can't be read anonymously: the package doesn't exist "
                    "yet, or isn't public"
                )
            response.raise_for_status()
            self._tokens[name] = response.json().get("token", "")
        return self._tokens[name]

    def _get(self, name: str, path: str, accept: str = ACCEPT) -> httpx.Response:
        return self._client.get(
            f"{REGISTRY}/v2/{name}/{path}",
            headers={"Authorization": f"Bearer {self._token(name)}", "Accept": accept},
        )

    def digest(self, name: str, tag: str) -> str | None:
        """The digest `tag` points at, or None if there's no such tag."""
        response = self._get(name, f"manifests/{tag}")
        if response.status_code == 404:
            return None
        if response.status_code in (401, 403):
            raise ImageUnreadable(f"ghcr.io/{name}:{tag} can't be read anonymously")
        response.raise_for_status()
        return response.headers.get("Docker-Content-Digest")

    def image(self, name: str, tag: str) -> Image | None:
        """The image `tag` names: its digest, platforms and labels. None if no such tag."""
        response = self._get(name, f"manifests/{tag}")
        if response.status_code == 404:
            return None
        if response.status_code in (401, 403):
            raise ImageUnreadable(f"ghcr.io/{name}:{tag} can't be read anonymously")
        response.raise_for_status()
        manifest: dict[str, Any] = response.json()
        image = Image(digest=response.headers.get("Docker-Content-Digest", ""))
        media = manifest.get("mediaType") or response.headers.get("Content-Type", "")
        if media in INDEX_TYPES or "manifests" in manifest:
            # buildx adds attestation manifests as platform unknown/unknown.
            entries = [
                m
                for m in manifest.get("manifests") or []
                if (m.get("platform") or {}).get("os") not in (None, "unknown")
            ]
            image.platforms = [
                f"{m['platform']['os']}/{m['platform']['architecture']}" for m in entries
            ]
            if entries:
                one = self._get(name, f"manifests/{entries[0]['digest']}")
                one.raise_for_status()
                image.labels = self._labels(name, one.json())
        else:
            image.labels = self._labels(name, manifest)
            config = self._config(name, manifest)
            if config:
                image.platforms = [f"{config.get('os')}/{config.get('architecture')}"]
        return image

    def _config(self, name: str, manifest: dict[str, Any]) -> dict[str, Any]:
        digest = (manifest.get("config") or {}).get("digest")
        if not digest:
            return {}
        blob = self._get(name, f"blobs/{digest}", accept="*/*")
        blob.raise_for_status()
        return blob.json()

    def _labels(self, name: str, manifest: dict[str, Any]) -> dict[str, str]:
        return (self._config(name, manifest).get("config") or {}).get("Labels") or {}
