"""GitHub's rate limits, honoured (#293)."""

from __future__ import annotations

import httpx
import pytest

from crew_org.llm import reraise_if_down
from crew_org.tools import github_http
from crew_org.tools.github_http import GitHubThrottled, GitHubTransport, throttled


class Clock:
    def __init__(self):
        self.now, self.slept = 1_000_000.0, []

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds

    def time(self):
        return self.now


def transport(responses, clock):
    replies = iter(responses)

    def handler(request):
        status, headers, body = next(replies)
        # No `request=`: a real transport's response doesn't carry it yet, and
        # reading it there raised (the crash this guards against).
        return httpx.Response(status, headers=headers, content=body)

    return GitHubTransport(
        httpx.MockTransport(handler), sleep=clock.sleep, clock=clock.time, monotonic=clock.time
    )


def client(responses, clock):
    return httpx.Client(transport=transport(responses, clock), base_url="https://api.github.com")


OK = (200, {"x-ratelimit-remaining": "4990", "x-ratelimit-limit": "5000"}, b"{}")


@pytest.fixture(autouse=True)
def no_observers():
    github_http._OBSERVERS.clear()
    yield
    github_http._OBSERVERS.clear()


def test_a_secondary_limit_is_waited_out_as_github_asks():
    clock = Clock()
    told = []
    github_http.observe(lambda wait, detail: told.append((wait, detail["status"])))
    limited = (
        403,
        {"retry-after": "30"},
        b'{"message": "You have exceeded a secondary rate limit"}',
    )
    response = client([limited, OK], clock).get("/repos/o/r/issues")
    assert response.status_code == 200
    assert clock.slept == [30.0] and told == [(30.0, 403)]


def test_a_spent_budget_waits_until_it_resets():
    clock = Clock()
    reset = str(int(clock.now) + 45)
    spent = (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset}, b"{}")
    assert client([spent, OK], clock).get("/x").status_code == 200
    assert clock.slept == [46.0]


def test_without_a_hint_it_waits_a_minute_as_github_s_docs_ask():
    clock = Clock()
    assert client([(429, {}, b""), OK], clock).get("/x").status_code == 200
    assert clock.slept == [60.0]


def test_graphql_s_in_body_rate_limit_counts():
    clock = Clock()
    limited = (200, {"retry-after": "5"}, b'{"errors": [{"type": "RATE_LIMITED"}]}')
    assert client([limited, OK], clock).post("/graphql", json={"query": "{x}"}).status_code == 200
    assert clock.slept == [5.0]


def test_longer_than_a_tick_waits_stops_it_rather_than_failing_a_card():
    clock = Clock()
    with pytest.raises(GitHubThrottled) as raised:
        client([(429, {"retry-after": "900"}, b"")], clock).get("/x")
    assert raised.value.wait == 900.0 and clock.slept == []


def test_a_throttle_that_keeps_coming_back_stops_after_three_tries():
    clock = Clock()
    again = (429, {"retry-after": "10"}, b"")
    with pytest.raises(GitHubThrottled):
        client([again, again, again], clock).get("/x")
    assert clock.slept == [10.0, 10.0]


def test_a_refusal_that_is_not_a_limit_is_left_alone():
    clock = Clock()
    forbidden = (403, {"x-ratelimit-remaining": "4000"}, b'{"message": "Resource not accessible"}')
    assert client([forbidden], clock).get("/x").status_code == 403
    assert clock.slept == []


def test_writes_are_paced_and_reads_are_not():
    clock = Clock()
    http = client([OK] * 5, clock)
    http.get("/a")
    http.get("/b")
    http.post("/repos/o/r/issues", json={})
    http.post("/repos/o/r/issues/1/comments", json={})
    http.post("/graphql", content=b'{"query": "mutation { addItem }"}')
    assert clock.slept == [1.0, 1.0], "two writes after the first, a second apart"


def test_the_budget_github_reports_is_kept():
    clock = Clock()
    headers = {
        "x-ratelimit-remaining": "4321",
        "x-ratelimit-limit": "5000",
        "x-ratelimit-resource": "core",
        "x-ratelimit-reset": "1",
    }
    client([(200, headers, b"{}")], clock).get("/x")
    assert github_http.BUDGET["core"]["remaining"] == 4321


def test_a_throttle_passes_through_a_card_s_catch_to_stop_the_tick():
    try:
        try:
            raise GitHubThrottled(900, "429 on /x")
        except GitHubThrottled as inner:
            raise RuntimeError("a card's work") from inner
    except RuntimeError as exc:
        with pytest.raises(GitHubThrottled):
            reraise_if_down(exc)


def test_only_throttles_count_as_throttles():
    request = httpx.Request("GET", "https://api.github.com/x")
    assert not throttled(request, httpx.Response(404))
    assert throttled(request, httpx.Response(429))


def test_a_graphql_answer_is_read_without_the_response_carrying_its_request():
    """The crash: the first tick on #294 raised on every GraphQL answer."""
    request = httpx.Request("POST", "https://api.github.com/graphql")
    assert not throttled(request, httpx.Response(200, content=b'{"data": {}}'))


# --- a passing server error is retried, a create is never repeated (crew#515) -------------

WENT_WRONG = (
    200,
    {"content-type": "application/json"},
    b'{"errors": [{"message": "Something went wrong while executing your query. '
    b'Please include `CF84:1584F2` when reporting this issue."}]}',
)


def test_a_server_error_on_a_read_is_retried_after_a_short_wait():
    clock = Clock()
    assert client([(502, {}, b""), OK], clock).get("/repos/o/r/issues").status_code == 200
    assert clock.slept == [5.0]


def test_graphql_s_something_went_wrong_is_retried():
    clock = Clock()
    board = client([WENT_WRONG, OK], clock)
    assert board.post("/graphql", json={"query": "mutation { x }"}).json() == {}
    assert 5.0 in clock.slept


def test_an_html_error_page_where_json_belongs_is_retried():
    clock = Clock()
    page = (200, {"content-type": "text/html; charset=utf-8"}, b"<html>Unicorn!</html>")
    assert client([page, OK], clock).get("/repos/o/r/issues/1/comments").status_code == 200


def test_a_create_that_may_have_landed_is_not_repeated():
    clock = Clock()
    response = client([(502, {}, b""), OK], clock).post("/repos/o/r/issues/1/comments", json={})
    assert response.status_code == 502, "returned as it came; the caller fails as before"
    assert clock.slept == [], "no retry wait"


def test_adding_labels_is_safe_to_repeat():
    clock = Clock()
    labels = client([(503, {}, b""), OK], clock)
    assert labels.post("/repos/o/r/issues/1/labels", json={"labels": ["x"]}).status_code == 200


def test_github_still_failing_stops_the_tick_like_a_throttle():
    clock = Clock()
    down = (502, {}, b"")
    with pytest.raises(github_http.GitHubUnavailable, match="isn't answering") as stopped:
        client([down, down, down, down], clock).get("/x")
    assert isinstance(stopped.value, GitHubThrottled), "the loop stops cleanly on it"
    assert clock.slept == [5.0, 10.0, 20.0]
    with pytest.raises(github_http.GitHubUnavailable):
        reraise_if_down(stopped.value)  # a card's catch passes it through


def test_a_graphql_refusal_is_not_a_blip():
    clock = Clock()
    refused = (200, {}, b'{"errors": [{"message": "Could not resolve to a node"}]}')
    assert client([refused], clock).post("/graphql", json={"query": "q"}).json()["errors"]
    assert clock.slept == []
