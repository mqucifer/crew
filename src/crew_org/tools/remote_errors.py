"""Telling a service's failure from the card's, and saying where either happened (#390).

sprint-metrics#358 was blocked for a person on `JSONDecodeError: Expecting value:
line 1 column 1 (char 0)`, the error from reading a GitHub reply that wasn't JSON,
at 12:17 on 2026-09-30, while GitHub was misbehaving for the Sponsor too. The
Developer's answer was right, and nothing about the story needed a person. The
block kept only the message, so the call that failed couldn't be named.
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path

import httpx

# A reply GitHub, or a registry, sends when it's struggling: a server error.
_SERVER_ERROR = 500


def _chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _service(host: str) -> str:
    if "github" in host:
        return "GitHub"
    if host in ("ghcr.io", "registry-1.docker.io", "auth.docker.io"):
        return f"the registry ({host})"
    return host or "a remote service"


def _frames(exc: BaseException) -> list[traceback.FrameSummary]:
    return traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []


def transient_remote(exc: BaseException) -> str | None:
    """The service a passing failure came from, or None if it's the card's own.

    Passing: a timeout or a dropped connection, a server error (5xx), or a reply
    that couldn't be read as JSON. A 4xx is an answer, not a stumble, and stays
    the card's to deal with.
    """
    for current in _chain(exc):
        if isinstance(current, httpx.TransportError):
            request = getattr(current, "_request", None)
            return _service(request.url.host if request is not None else "")
        if isinstance(current, httpx.HTTPStatusError):
            if current.response.status_code >= _SERVER_ERROR:
                return _service(current.request.url.host)
            return None
        if type(current).__name__ == "IssueError" and " -> 5" in str(current):
            return "GitHub"
        if isinstance(current, json.JSONDecodeError):
            # Only a reply's body: a model's own output fails validation instead.
            files = [f.filename for f in _frames(current)]
            if any("httpx" in f for f in files):
                crew = [f for f in files if "crew_org" in f]
                if any("github" in f for f in crew):
                    return "GitHub"
                if any("registry" in f or "base_images" in f for f in crew):
                    return "the registry"
                return "a remote service"
    return None


def where(exc: BaseException, frames: int = 3) -> str:
    """The last few of the crew's own frames: file, line, function (#390)."""
    ours = [f for f in _frames(exc) if "crew_org" in f.filename]
    shown = (ours or _frames(exc))[-frames:]
    lines = []
    for f in shown:
        parts = Path(f.filename).parts
        rel = "/".join(parts[parts.index("crew_org") :]) if "crew_org" in parts else f.filename
        lines.append(f"{rel}:{f.lineno} in {f.name}")
    return "; ".join(lines)
