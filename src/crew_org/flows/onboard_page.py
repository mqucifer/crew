"""The onboarding interview in a local web page (#138).

The interview itself is unchanged (`flows.onboard.interview`): this is another
pair of hands for it. It asks through `Session.ask`, which waits until the page
posts a reply, and it tells and shows through methods that record what the
page polls for. A turn can ask ten questions, and a page gives each one its own
field, where a terminal gives all ten one line.

Standard library only. It serves one person on 127.0.0.1, and every request
carries a random token from the URL, so another page open in the same browser
cannot read the interview or answer for the Sponsor.
"""

from __future__ import annotations

import json
import secrets
import threading
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from crew_org.flows.onboard import ANSWER, CONFIRM
from crew_org.project import ProjectRecordError, gaps, render, validate


def compose(answers: list[dict[str, str]], text: str) -> str:
    """One reply from the page's fields, as the Product Owner reads it.

    Each answered question is quoted with its answer; a question left blank is
    left out rather than answered with nothing. Anything in the free field
    follows.
    """
    parts = [
        f"On “{a['question'].strip()}”: {a['text'].strip()}"
        for a in answers
        if a.get("text", "").strip()
    ]
    if text.strip():
        parts.append(text.strip())
    return "\n\n".join(parts)


class Session:
    """What the page shows, and the reply it is waiting for.

    Shared by the interview, which runs on the main thread, and the server's
    request threads. Waiting polls rather than blocking forever, so Ctrl-C in
    the terminal still reaches the interview.
    """

    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.token = secrets.token_urlsafe(16)
        self._lock = threading.Condition()
        self._messages: list[dict[str, str]] = []
        self._asking: list[str] = []
        self._raw: dict[str, Any] = {}
        self._proposed: dict[str, Any] = {}
        self._prompt: str | None = None
        self._reply: str | None = None
        self._thinking = False
        self._closed = False
        self._outcome: dict[str, str] | None = None
        self._seen_outcome = threading.Event()

    # --- the interview's side ---------------------------------------------------

    def tell(self, text: str) -> None:
        with self._lock:
            self._messages.append({"who": "Product Owner", "text": text})

    def show(
        self, *, raw: dict[str, Any], asking: list[str], proposed: dict[str, Any] | None = None
    ) -> None:
        with self._lock:
            self._raw, self._asking = raw, list(asking)
            self._proposed = proposed or {}

    def thinking(self, on: bool) -> None:
        with self._lock:
            self._thinking = on

    def ask(self, prompt: str) -> str | None:
        """Wait for the page's reply to `prompt`. None if the session is closed first."""
        with self._lock:
            self._prompt, self._reply = prompt, None
            try:
                while self._reply is None and not self._closed:
                    self._lock.wait(timeout=0.5)
            except KeyboardInterrupt:
                self._closed = True
            reply, self._prompt = self._reply, None
            return None if self._closed and reply is None else reply

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.notify_all()

    def finish(self, kind: str, text: str, link: str = "") -> None:
        """How it ended, for the page to say: `proposed`, `kept` or `stopped`."""
        with self._lock:
            self._outcome = {"kind": kind, "text": text, "link": link}
            self._prompt = None

    def wait_until_seen(self, timeout: float) -> bool:
        """Give the page a chance to show how it ended before the server stops."""
        return self._seen_outcome.wait(timeout)

    # --- the page's side --------------------------------------------------------

    def answer(self, reply: str) -> bool:
        """The Sponsor's reply. False if nothing is waiting for one."""
        with self._lock:
            if self._prompt is None or self._closed:
                return False
            self._messages.append({"who": "Sponsor", "text": reply})
            self._reply = reply
            self._lock.notify_all()
            return True

    def state(self) -> dict[str, Any]:
        with self._lock:
            try:
                record = render(validate(self._raw))
            except ProjectRecordError:
                record = _draft(self._raw)
            if self._outcome:
                self._seen_outcome.set()
            return {
                "repo": self.repo,
                "messages": list(self._messages),
                "asking": list(self._asking),
                "stage": {ANSWER: "answer", CONFIRM: "confirm"}.get(self._prompt or ""),
                "thinking": self._thinking,
                "record": record,
                "proposed": _draft(self._proposed),
                "missing": list(gaps(self._raw).values()),
                "outcome": self._outcome,
            }


def _draft(raw: dict[str, Any]) -> str:
    import yaml  # noqa: PLC0415

    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True) if raw else ""


# --- the server --------------------------------------------------------------------


def handler_for(session: Session) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:  # the terminal is the Sponsor's, not a log
            return

        def _authorised(self) -> bool:
            query = parse_qs(urlparse(self.path).query)
            given = (query.get("t") or [""])[0] or self.headers.get("X-Crew-Token", "")
            if secrets.compare_digest(given, session.token):
                return True
            self._send(HTTPStatus.FORBIDDEN, "text/plain", b"not this session")
            return False

        def _send(self, status: HTTPStatus, kind: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorised():
                return
            route = urlparse(self.path).path
            if route == "/":
                self._send(HTTPStatus.OK, "text/html", page(session.state()).encode())
            elif route == "/state":
                self._send(HTTPStatus.OK, "application/json", json.dumps(session.state()).encode())
            else:
                self._send(HTTPStatus.NOT_FOUND, "text/plain", b"not found")

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorised():
                return
            if urlparse(self.path).path != "/reply":
                self._send(HTTPStatus.NOT_FOUND, "text/plain", b"not found")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                action = body.get("action", "answer")
                reply = {"done": "done", "later": "later", "yes": "yes"}.get(action) or compose(
                    body.get("answers") or [], body.get("text") or ""
                )
            except (ValueError, TypeError, AttributeError):
                self._send(HTTPStatus.BAD_REQUEST, "text/plain", b"not a reply")
                return
            if not reply:
                self._send(HTTPStatus.BAD_REQUEST, "text/plain", b"nothing was answered")
                return
            if not session.answer(reply):
                self._send(HTTPStatus.CONFLICT, "text/plain", b"nothing is waiting for a reply")
                return
            self._send(HTTPStatus.OK, "application/json", b'{"ok": true}')

    return Handler


def serve(session: Session, *, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Start the page on 127.0.0.1 in the background. Returns the server and its URL."""
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(session))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, bound = server.server_address[:2]
    return server, f"http://{host}:{bound}/?t={session.token}"


def thinking_turn(session: Session, turn: Callable[..., Any]) -> Callable[..., Any]:
    """`turn`, with the page told while the Product Owner is thinking."""

    def wrapped(**context: Any) -> Any:
        session.thinking(True)
        try:
            return turn(**context)
        finally:
            session.thinking(False)

    return wrapped


# The page is a file of its own beside this one, read once at import.
PAGE = (Path(__file__).parent / "onboard_page.html").read_text(encoding="utf-8")


def page(state: dict[str, Any]) -> str:
    """The page, carrying `state` so it renders before its first poll.

    `</` is escaped so nothing the Sponsor or the model wrote can close the
    script it is embedded in.
    """
    embedded = json.dumps(state).replace("</", "<\\/")
    return PAGE.replace("/*STATE*/null", embedded, 1)
