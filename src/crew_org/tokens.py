"""A GitHub token that stays valid for as long as a command runs (#182).

An app installation token lasts an hour. The crew's clients used to be handed
the token string and keep it, so a tick that passed the hour failed on every
phase with a 401: on 2026-09-25 one started at 19:13 and stopped at 20:15.
A `Token` is either that string (a personal access token, which doesn't
expire) or a callable returning the current one (the app provider, which
mints a fresh token shortly before the old one lapses). The clients ask for
it on every request.
"""

from __future__ import annotations

from collections.abc import Callable, Generator

import httpx

Token = str | Callable[[], str]


def current(token: Token) -> str:
    """The token to use right now."""
    return token() if callable(token) else token


class BearerAuth(httpx.Auth):
    """`Authorization: Bearer …` with the token as it is at each request."""

    def __init__(self, token: Token) -> None:
        self._token = token

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {current(self._token)}"
        yield request
