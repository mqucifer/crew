"""A firing alert opens DevOps's Task in infra (crew#893)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx

from crew_org.columns import NEEDS_REFINEMENT, READY
from crew_org.flows import alerts
from crew_org.flows.role_tasks import ALERT, DEVOPS, FINDING, marked
from tests.role_task_fakes import Board, Issues, events, sink

TOKEN = "glsa_not-a-real-token-0123456789"
ENV = {"GRAFANA_URL": "https://grafana.example/", "GRAFANA_READ_TOKEN": TOKEN}
NOW = datetime(2026, 10, 25, 9, 0, tzinfo=UTC)
STARTS = "2026-10-25T02:16:10.000Z"


def firing(fingerprint="fp1", starts=STARTS, audience="crew", **labels):
    return {
        "fingerprint": fingerprint,
        "startsAt": starts,
        "status": {"state": "active"},
        "labels": {
            "alertname": "Requests are waiting at the Spark",
            "team": "crew",
            "owner": "devops",
            "source": "infra-38",
            "service": "sglang",
            "audience": audience,
            "__grafana_receiver__": "Incident response",
            **labels,
        },
        "annotations": {
            "summary": "Requests queue at SGLang",
            "runbook_url": "https://github.com/mqucifer/infra/blob/main/runbooks/sglang.md",
        },
        "generatorURL": "https://grafana.example/alerting/grafana/abc/view",
    }


def grafana(*answer, status=200, seen=None):
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=list(answer))

    return httpx.MockTransport(handle)


def run(issues, board, s, tmp_path, transport, *, env=ENV, now=NOW):
    return alerts.alert_tasks(
        issues, board, s, cards=[], events_dir=tmp_path, env=env, transport=transport, now=now
    )


def test_a_firing_crew_alert_opens_one_devops_task_in_infra(tmp_path):
    issues, board, s, seen = Issues(), Board(), sink(tmp_path), []
    result = run(issues, board, s, tmp_path, grafana(firing(), seen=seen))
    assert result.opened == [901]
    task = issues.issues[901]
    found = marked(task["body"])
    assert (found.role, found.kind, found.key) == (DEVOPS, ALERT, "fp1@20261025T021610Z")
    assert task["title"] == "Alert: Requests are waiting at the Spark"
    body = task["body"]
    assert "runbooks/sglang.md" in body and STARTS in body and "`service`: `sglang`" in body
    assert "Requests queue at SGLang" in body and "__grafana_receiver__" not in body
    assert ("status", "item-N901", READY) in board.done
    # Read-only, with the token in a header and only the crew's alerts asked for.
    asked = seen[0]
    assert asked.method == "GET" and asked.url.path == alerts.ALERTS_PATH
    assert asked.headers["Authorization"] == f"Bearer {TOKEN}"
    assert asked.url.params["filter"] == 'audience="crew"'
    assert asked.url.params["silenced"] == "false"

    again = run(issues, board, s, tmp_path, grafana(firing()))
    assert again.opened == [] and again.fired == [] and len(issues.issues) == 1


def test_the_sponsors_alerts_open_no_task(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    legacy = firing("fp2")
    del legacy["labels"]["audience"]
    result = run(issues, board, s, tmp_path, grafana(firing(audience="sponsor"), legacy))
    assert result.opened == [] and issues.issues == {}


def test_a_stopped_alert_is_noted_on_its_task_once_and_it_stays_open(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    run(issues, board, s, tmp_path, grafana(firing()))
    stopped = run(issues, board, s, tmp_path, grafana())
    assert stopped.resolved == [901]
    assert "stopped firing" in issues.comments_on[901][0]["body"]
    assert issues.issues[901]["state"] == "open"
    assert run(issues, board, s, tmp_path, grafana()).resolved == []
    assert len(issues.comments_on[901]) == 1


def test_firing_again_while_its_task_is_open_is_a_comment_not_a_task(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    run(issues, board, s, tmp_path, grafana(firing()))
    run(issues, board, s, tmp_path, grafana())
    later = "2026-10-25T06:00:00.000Z"
    refired = run(issues, board, s, tmp_path, grafana(firing(starts=later)))
    assert refired.fired == [901] and refired.opened == [] and len(issues.issues) == 1
    assert "fired again at " + later in issues.comments_on[901][-1]["body"]
    assert run(issues, board, s, tmp_path, grafana(firing(starts=later))).fired == []
    # It stops again: that firing's stop is said too.
    assert run(issues, board, s, tmp_path, grafana()).resolved == [901]
    assert len(issues.comments_on[901]) == 3


def test_firing_after_its_task_closed_opens_a_new_one(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    run(issues, board, s, tmp_path, grafana(firing()))
    issues.close("infra", 901)
    # The same firing, still going after DevOps closed its Task: the review was done.
    assert run(issues, board, s, tmp_path, grafana(firing())).opened == []
    later = run(issues, board, s, tmp_path, grafana(firing(starts="2026-10-26T01:00:00Z")))
    assert later.opened == [902]
    assert marked(issues.issues[902]["body"]).key == "fp1@20261026T010000Z"


def test_grafana_unread_is_a_held_line_and_nothing_is_resolved(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    run(issues, board, s, tmp_path, grafana(firing()))

    def down(request):
        raise httpx.ConnectError("no route")

    for transport, said in [
        (httpx.MockTransport(down), "Grafana unreachable: ConnectError"),
        (grafana(status=401), "Grafana answered 401"),
        (httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")), "unreadable"),
        (grafana({"fingerprint": "x", "labels": {"audience": "crew"}}), "unreadable"),
    ]:
        result = run(issues, board, s, tmp_path, transport)
        assert result.resolved == [] and said in result.failed[0], result.failed
    missing = run(issues, board, s, tmp_path, grafana(), env={})
    assert "no GRAFANA_URL or GRAFANA_READ_TOKEN" in missing.failed[0]
    assert 901 not in issues.comments_on
    noted = [
        e for e in events(tmp_path) if (e["detail"] or {}).get("rule") == "decision.alerts.unread"
    ]
    assert len(noted) == 5
    assert TOKEN not in (tmp_path / "events.jsonl").read_text()


def test_a_day_unread_is_one_devops_finding_and_reading_again_clears_it(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    unread = grafana(status=503)
    assert run(issues, board, s, tmp_path, unread).finding is None
    since = json.loads((tmp_path / alerts.UNREAD_FILE).read_text())["since"]
    assert since == NOW.isoformat()
    assert run(issues, board, s, tmp_path, unread, now=NOW + timedelta(hours=23)).finding is None
    day = run(issues, board, s, tmp_path, unread, now=NOW + timedelta(hours=24))
    assert day.finding == 901
    found = marked(issues.issues[901]["body"])
    assert (found.kind, found.key) == (FINDING, "alerts-unread-2026-10-25")
    assert ("status", "item-N901", NEEDS_REFINEMENT) in board.done
    assert "Grafana answered 503" in issues.issues[901]["body"]
    assert run(issues, board, s, tmp_path, unread, now=NOW + timedelta(hours=30)).finding is None
    assert len(issues.issues) == 1

    run(issues, board, s, tmp_path, grafana(), now=NOW + timedelta(hours=31))
    assert not (tmp_path / alerts.UNREAD_FILE).exists()


def test_a_task_github_refuses_is_held_and_tried_next_pass(tmp_path):
    issues, board, s = Issues(), Board(), sink(tmp_path)
    issues.fail = "create"
    held = run(issues, board, s, tmp_path, grafana(firing()))
    assert held.opened == [] and "alert task not opened" in held.failed[0]
    issues.fail = None
    assert run(issues, board, s, tmp_path, grafana(firing())).opened == [901]


def test_the_token_is_read_from_its_file(tmp_path, monkeypatch):
    path = tmp_path / "grafana.env"
    path.write_text(f"GRAFANA_URL=https://grafana.example\nGRAFANA_READ_TOKEN={TOKEN}\n")
    monkeypatch.setenv("GRAFANA_ENV", str(path))
    seen = []
    result = alerts.alert_tasks(
        Issues(),
        Board(),
        sink(tmp_path),
        cards=[],
        events_dir=tmp_path,
        transport=grafana(seen=seen),
        now=NOW,
    )
    assert result.failed == [] and seen[0].headers["Authorization"] == f"Bearer {TOKEN}"
