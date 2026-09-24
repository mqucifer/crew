"""The onboarding interview in a local page (#138).

Driven over real HTTP against a real server on 127.0.0.1, with the Product
Owner scripted: what is tested is what the page's replies do to the interview.
"""

from __future__ import annotations

import threading
import time

import httpx
import pytest

from crew_org.crews.onboarding_crew import (
    Answers,
    DoneAnswers,
    Question,
    ReleaseAnswers,
    ScopeAnswers,
    Turn,
)
from crew_org.flows.onboard import ANSWER, CONFIRM, interview
from crew_org.flows.onboard_page import Session, compose, serve, thinking_turn


class Script:
    def __init__(self, *turns: Turn) -> None:
        self.turns = list(turns)
        self.shown: list[dict] = []

    def __call__(self, **context) -> Turn:
        self.shown.append(context)
        return self.turns.pop(0)


@pytest.fixture
def page():
    session = Session("sprint-metrics")
    server, url = serve(session)
    base = url.split("/?")[0]
    yield session, base, url
    session.close()
    server.shutdown()


def post(base: str, session: Session, **body) -> httpx.Response:
    return httpx.post(f"{base}/reply", params={"t": session.token}, json=body)


def state(base: str, session: Session) -> dict:
    return httpx.get(f"{base}/state", params={"t": session.token}).json()


def wait_for(check, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.02)
    raise AssertionError("timed out")


def run(session: Session, raw: dict, po: Script) -> dict:
    """The interview on a thread, as the command runs it, answered through the page."""
    out: dict = {}

    def go() -> None:
        out["ended"] = interview(
            raw,
            repository="",
            turn=thinking_turn(session, po),
            ask=session.ask,
            tell=session.tell,
            show=session.show,
        )

    thread = threading.Thread(target=go, daemon=True)
    thread.start()
    out["thread"] = thread
    return out


# --- 1 and 5: served locally, to this session only ----------------------------------


def test_it_is_served_on_localhost_only(page):
    _, _, url = page
    assert url.startswith("http://127.0.0.1:")


def test_the_page_is_served_with_the_token(page):
    session, base, url = page
    response = httpx.get(url)
    assert response.status_code == 200 and "<title>Crew onboarding</title>" in response.text


@pytest.mark.parametrize("path", ["/", "/state"])
def test_a_request_without_the_token_is_refused(page, path):
    _, base, _ = page
    assert httpx.get(f"{base}{path}").status_code == 403
    assert httpx.get(f"{base}{path}", params={"t": "guess"}).status_code == 403


def test_a_reply_without_the_token_is_refused(page):
    session, base, _ = page
    response = httpx.post(f"{base}/reply", json={"action": "yes"})
    assert response.status_code == 403


# --- 2: a field for each question ------------------------------------------------


def test_each_answered_question_is_quoted_and_a_blank_one_is_left_out():
    reply = compose(
        [
            {"question": "Past sprints too?", "text": "Yes, the last six."},
            {"question": "Out of scope?", "text": "   "},
        ],
        "Also: no MCP server for now.",
    )
    assert reply == "On “Past sprints too?”: Yes, the last six.\n\nAlso: no MCP server for now."
    assert "Out of scope" not in reply


def test_a_reply_with_nothing_answered_is_refused(page):
    session, base, _ = page
    threading.Thread(target=session.ask, args=(ANSWER,), daemon=True).start()
    wait_for(lambda: state(base, session)["stage"] == "answer")
    response = post(base, session, action="answer", answers=[{"question": "q", "text": ""}])
    assert response.status_code == 400


def test_a_reply_when_nothing_is_waiting_is_refused(page):
    session, base, _ = page
    assert post(base, session, action="answer", text="hello").status_code == 409


# --- the interview, through the page -------------------------------------------------


def test_a_whole_interview_through_the_page(page):
    session, base, _ = page
    po = Script(
        Turn(
            answers=Answers(scope=ScopeAnswers(purpose="Sprint metrics for the crew")),
            say="Two things.",
            questions=[
                Question(about="intent.release.deploys", question="Is the merge the release?"),
                Question(about="intent.done.checks", question="What must pass?"),
            ],
        ),
        Turn(
            answers=Answers(
                release=ReleaseAnswers(deploys=False), done=DoneAnswers(checks=["pytest"])
            ),
            say="Thanks.",
        ),
    )
    out = run(session, {}, po)

    # 3: the questions, the record so far and what it still lacks, all on the page.
    now = wait_for(lambda: (s := state(base, session))["stage"] == "answer" and s)
    assert now["asking"] == ["Is the merge the release?", "What must pass?"]
    assert "purpose: Sprint metrics for the crew" in now["record"]
    assert "the checks a change must pass to be done" in now["missing"]

    post(
        base,
        session,
        action="answer",
        answers=[
            {"question": "Is the merge the release?", "text": "Yes."},
            {"question": "What must pass?", "text": "pytest"},
        ],
    )
    confirming = wait_for(lambda: (s := state(base, session))["stage"] == "confirm" and s)
    assert confirming["missing"] == []
    assert "On “What must pass?”: pytest" in po.shown[1]["conversation"]

    post(base, session, action="yes")
    out["thread"].join(timeout=5)
    assert out["ended"].settled


def test_the_page_says_when_the_product_owner_is_thinking(page):
    session, base, _ = page
    release = threading.Event()

    def slow(**_context):
        release.wait(5)
        return Turn(answers=Answers(), say="Hello.")

    threading.Thread(target=thinking_turn(session, slow), kwargs={"x": 1}, daemon=True).start()
    assert wait_for(lambda: state(base, session)["thinking"])
    release.set()
    assert wait_for(lambda: not state(base, session)["thinking"])


# --- 4: done and later do what they do in the terminal ----------------------------------


@pytest.mark.parametrize("action, settled", [("done", True), ("later", False)])
def test_done_and_later_from_the_page(page, action, settled):
    session, base, _ = page
    partial = {"intent": {"release": {"deploys": False}, "done": {"checks": ["pytest"]}}}
    po = Script(
        Turn(
            answers=Answers(scope=ScopeAnswers(purpose="Metrics")),
            say="Anything out of scope?",
            questions=[Question(about="intent.scope.out_of_scope", question="Out of scope?")],
        )
    )
    out = run(session, partial, po)
    wait_for(lambda: state(base, session)["stage"] == "answer")

    post(base, session, action=action)
    if settled:
        wait_for(lambda: state(base, session)["stage"] == "confirm")
        post(base, session, action="yes")
    out["thread"].join(timeout=5)

    assert out["ended"].settled is settled
    assert f"**Sponsor:** {action}" in out["ended"].transcript


def test_closing_the_session_ends_the_interview_with_the_answers_kept(page):
    session, base, _ = page
    out = run(
        session, {"intent": {"scope": {"purpose": "p"}}}, Script(Turn(answers=Answers(), say="?"))
    )
    wait_for(lambda: state(base, session)["stage"] == "answer")

    session.close()
    out["thread"].join(timeout=5)

    assert not out["ended"].settled
    assert out["ended"].raw == {"intent": {"scope": {"purpose": "p"}}}


# --- 6: how it ended ------------------------------------------------------------------


def test_the_page_is_told_how_it_ended(page):
    session, base, _ = page
    session.finish("proposed", "The record is proposed:", "https://github.com/o/r/pull/9")

    ended = state(base, session)

    assert ended["outcome"] == {
        "kind": "proposed",
        "text": "The record is proposed:",
        "link": "https://github.com/o/r/pull/9",
    }
    assert ended["stage"] is None
    assert session.wait_until_seen(timeout=0)


def test_the_stage_follows_the_prompt():
    session = Session("r")
    for prompt, stage in ((ANSWER, "answer"), (CONFIRM, "confirm")):
        threading.Thread(target=session.ask, args=(prompt,), daemon=True).start()
        wait_for(lambda stage=stage: session.state()["stage"] == stage)
        session.answer("x")
        wait_for(lambda: session.state()["stage"] is None)
