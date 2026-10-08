"""GitHub's rate limits, honoured (#293).

Both GitHub clients called `raise_for_status()` straight away, so a throttle
became an exception: a card or a phase failed for GitHub's reasons. The
primary limit (5,000 requests an hour for an installation) is far from a
tick's reads. The secondary limits are the risk: GitHub throttles *content
creation* (roughly 80 a minute, 500 an hour, and quick bursts), and a split
is a burst. Epic sprint-metrics#175 made six stories in twenty seconds, each
several writes. Bot and LLM traffic is the kind GitHub watches for.

So every GitHub request goes through `GitHubTransport`:

- **Writes are paced**, at least `WRITE_INTERVAL` apart (GitHub's guidance
  is about a second between content-creating requests). Reads aren't.
- **A throttle is waited out, not raised.** A 429, or a 403 that says it's
  a rate limit, waits for `Retry-After`, or until `x-ratelimit-reset` when
  the budget is spent, or a minute, doubling, when GitHub says neither (its
  docs ask for at least a minute). GraphQL's in-body `RATE_LIMITED` counts
  too. At most `ATTEMPTS` tries, and no single wait past `MAX_WAIT`.
- **Past that, `GitHubThrottled`**: the tick stops cleanly and the next one
  resumes. It's GitHub's limit, not a card's failure.
- **It's seen**: an observer is told of every wait, and the latest budget
  GitHub reported is kept for the telemetry.
- **A blip is retried too** (crew#515): a 5xx, an HTML error page where JSON
  belongs, or GraphQL's "Something went wrong". A few short waits, then
  `GitHubUnavailable`, which stops the tick as a throttle does. Only a request
  that's safe to repeat is retried: a read, a board query or mutation (they set
  or add a value), an edit, or adding labels. One that creates something (an
  issue, a comment, a pull request, a review) may have landed, so it's returned
  as it came and the caller fails as before.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

WRITE_INTERVAL = 1.0
MAX_WAIT = 120.0
ATTEMPTS = 3
FIRST_BACKOFF = 60.0
# A passing server error: a few short waits, doubling (crew#515).
TRANSIENT_ATTEMPTS = 4
TRANSIENT_BACKOFF = 5.0
WRITES = frozenset({"POST", "PATCH", "PUT", "DELETE"})


class GitHubThrottled(RuntimeError):
    """GitHub is throttling, for longer than a tick should wait. Nobody's failure."""

    def __init__(self, wait: float, detail: str) -> None:
        self.wait = wait
        super().__init__(f"GitHub is throttling ({detail}); it asked for {wait:.0f}s more")


class GitHubUnavailable(GitHubThrottled):
    """GitHub kept answering with a server error. Nobody's failure: the tick stops cleanly."""

    def __init__(self, wait: float, detail: str) -> None:
        self.wait = wait
        RuntimeError.__init__(
            self, f"GitHub isn't answering ({detail}) after {wait:.0f}s of retries"
        )


# Told of every throttle waited out: (seconds, detail). The tick points it at
# its event sink.
_OBSERVERS: list[Callable[[float, dict[str, Any]], None]] = []
# The budget GitHub last reported, by resource ("core", "graphql"…).
BUDGET: dict[str, dict[str, int]] = {}
_LOCK = threading.Lock()


def observe(fn: Callable[[float, dict[str, Any]], None]) -> None:
    if fn not in _OBSERVERS:
        _OBSERVERS.append(fn)


def _is_write(request: httpx.Request) -> bool:
    if request.method not in WRITES:
        return False
    if request.url.path.endswith("/graphql"):
        # A GraphQL query is a POST too; only a mutation creates anything.
        return b"mutation" in (request.content or b"")[:200]
    return True


def _record_budget(response: httpx.Response) -> None:
    headers = response.headers
    if "x-ratelimit-remaining" not in headers:
        return
    with _LOCK:
        BUDGET[headers.get("x-ratelimit-resource", "core")] = {
            "remaining": int(headers.get("x-ratelimit-remaining", "0")),
            "limit": int(headers.get("x-ratelimit-limit", "0")),
            "reset": int(headers.get("x-ratelimit-reset", "0")),
        }


def throttled(request: httpx.Request, response: httpx.Response) -> bool:
    """Is this GitHub saying slow down, rather than no?

    The request is passed in, not read off the response: inside a transport
    the client hasn't attached it yet, and `response.request` raises. That
    crashed the first tick on this code; tests with a mock transport that set
    it themselves hadn't seen it.
    """
    if response.status_code == 429:
        return True
    if response.status_code == 403:
        if response.headers.get("x-ratelimit-remaining") == "0":
            return True
        if "retry-after" in response.headers:
            return True
        text = response.text.lower()
        return "rate limit" in text or "abuse" in text
    if response.status_code == 200 and request.url.path.endswith("/graphql"):
        try:
            errors = response.json().get("errors") or []
        except (ValueError, json.JSONDecodeError):
            return False
        return any(e.get("type") == "RATE_LIMITED" for e in errors if isinstance(e, dict))
    return False


def transient(request: httpx.Request, response: httpx.Response) -> bool:
    """Is this GitHub failing for a moment, rather than refusing the request?

    On 2026-10-07 the board's GraphQL answered "Something went wrong while
    executing your query" and the standup got a body that wasn't JSON, while
    githubstatus.com showed everything operational. A phase failed for each.
    """
    if response.status_code in (500, 502, 503, 504):
        return True
    if response.headers.get("content-type", "").startswith("text/html"):
        return True  # an error page where the API's JSON belongs
    if response.status_code == 200 and request.url.path.endswith("/graphql"):
        try:
            errors = response.json().get("errors") or []
        except (ValueError, json.JSONDecodeError):
            return True
        return any(
            "something went wrong" in str(e.get("message", "")).lower()
            for e in errors
            if isinstance(e, dict)
        )
    return False


def repeatable(request: httpx.Request) -> bool:
    """Safe to send again if the first may have landed."""
    if request.method in ("GET", "HEAD", "PATCH", "PUT", "DELETE"):
        return True
    path = request.url.path
    # The board's GraphQL: queries, and mutations that set or add a value.
    # Adding labels, or creating one that already exists, changes nothing twice.
    return path.endswith("/graphql") or path.endswith("/labels")


def wait_for(response: httpx.Response, attempt: int, now: float) -> float:
    """How long GitHub asked to wait, or the backoff when it didn't say."""
    headers = response.headers
    if headers.get("retry-after", "").isdigit():
        return float(headers["retry-after"])
    if headers.get("x-ratelimit-remaining") == "0" and headers.get("x-ratelimit-reset"):
        return max(0.0, float(headers["x-ratelimit-reset"]) - now) + 1.0
    return FIRST_BACKOFF * (2**attempt)


class GitHubTransport(httpx.BaseTransport):
    """An httpx transport that paces GitHub writes and waits out its throttles."""

    def __init__(
        self,
        inner: httpx.BaseTransport | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner or httpx.HTTPTransport()
        self._sleep = sleep
        self._clock = clock
        self._monotonic = monotonic
        self._last_write = float("-inf")

    def _pace(self, request: httpx.Request) -> None:
        if not _is_write(request):
            return
        with _LOCK:
            gap = WRITE_INTERVAL - (self._monotonic() - self._last_write)
            if gap > 0:
                self._sleep(gap)
            self._last_write = self._monotonic()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        blips, waited = 0, 0.0
        attempt = 0
        while attempt < ATTEMPTS:
            self._pace(request)
            response = self._inner.handle_request(request)
            response.read()
            _record_budget(response)
            if transient(request, response) and repeatable(request):
                blips += 1
                what = f"{response.status_code} on {request.url.path}"
                if blips >= TRANSIENT_ATTEMPTS:
                    raise GitHubUnavailable(waited, what)
                wait = TRANSIENT_BACKOFF * (2 ** (blips - 1))
                for fn in list(_OBSERVERS):
                    with contextlib.suppress(Exception):  # a view never stops the work
                        fn(
                            wait,
                            {
                                "status": response.status_code,
                                "path": request.url.path,
                                "resource": "transient",
                                "attempt": blips,
                            },
                        )
                self._sleep(wait)
                waited += wait
                continue
            if not throttled(request, response):
                return response
            wait = wait_for(response, attempt, self._clock())
            detail = {
                "status": response.status_code,
                "path": request.url.path,
                "resource": response.headers.get("x-ratelimit-resource", ""),
                "attempt": attempt + 1,
            }
            if wait > MAX_WAIT or attempt == ATTEMPTS - 1:
                raise GitHubThrottled(wait, f"{response.status_code} on {request.url.path}")
            for fn in list(_OBSERVERS):
                with contextlib.suppress(Exception):  # a view never stops the work
                    fn(wait, detail)
            self._sleep(wait)
            attempt += 1
        return response  # unreachable: the last attempt returns or raises

    def close(self) -> None:
        self._inner.close()
