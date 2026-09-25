"""A long run outlives its first app token (#182).

On 2026-09-25 a tick started at 19:13 and failed on every phase from 20:15 with
a 401: an installation token lasts an hour, and the clients kept the string.
"""

from __future__ import annotations

import httpx
import pytest

from crew_org.git_ops import BotIdentity, Workspace
from crew_org.tokens import BearerAuth, current
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import PermissionDenied, ProjectClient


class Minter:
    """An app provider stand-in: a new token each time it's asked after expiry."""

    def __init__(self) -> None:
        self.minted = 0

    def __call__(self) -> str:
        return f"token-{self.minted}"

    def expire(self) -> None:
        self.minted += 1


def recording_transport(seen: list[str], status: int = 200, body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization", ""))
        return httpx.Response(status, json=body if body is not None else {})

    return httpx.MockTransport(handler)


def test_each_request_carries_the_token_as_it_is_then():
    minter, seen = Minter(), []
    client = httpx.Client(auth=BearerAuth(minter), transport=recording_transport(seen))
    client.get("https://api.github.com/a")
    minter.expire()  # the hour passes, the provider mints a new one
    client.get("https://api.github.com/b")
    assert seen == ["Bearer token-0", "Bearer token-1"]


def test_the_issue_client_uses_a_fresh_token_after_the_first_expires():
    minter, seen = Minter(), []
    issues = IssueClient(minter, "mqucifer")
    issues._client = httpx.Client(
        auth=BearerAuth(minter),
        transport=recording_transport(seen, body={"number": 1}),
        base_url="https://api.github.com",
    )
    issues.get("crew", 1)
    minter.expire()
    issues.get("crew", 1)
    assert seen == ["Bearer token-0", "Bearer token-1"]


def test_a_personal_access_token_is_used_as_it_is():
    seen = []
    client = httpx.Client(auth=BearerAuth("ghp_fixed"), transport=recording_transport(seen))
    client.get("https://api.github.com/a")
    client.get("https://api.github.com/b")
    assert seen == ["Bearer ghp_fixed", "Bearer ghp_fixed"]
    assert current("ghp_fixed") == "ghp_fixed"


def test_git_operations_resolve_the_token_each_time():
    minter = Minter()
    ws = Workspace("mqucifer", "sprint-metrics", minter, BotIdentity("crew[bot]", 1))
    assert ws.token == "token-0"
    minter.expire()
    assert ws.token == "token-1"
    other = ws.for_repo("crew")
    minter.expire()
    assert other.token == "token-2", "another repo's workspace keeps the source, not a string"


def test_a_token_still_refused_after_refreshing_fails_with_the_existing_message():
    """Revoked, not expired: a refresh can't help, and it isn't looped on."""
    board = ProjectClient(Minter(), "mqucifer", 1)
    board._client = httpx.Client(
        auth=BearerAuth(Minter()),
        transport=recording_transport([], status=401, body={"message": "Bad credentials"}),
    )
    with pytest.raises(PermissionDenied, match="Run `crew auth`"):
        board._call("query { viewer { login } }")
