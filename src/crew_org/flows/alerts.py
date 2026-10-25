"""A firing alert opens DevOps's Task in infra (crew#893, ADR 0034).

The alert rules are infra's (mqucifer/infra `grafana/alerts/`), applied to Grafana by
the Sponsor. Each pass reads what fires, with a read-only token, and never applies
anything: a dashboard or rule changes by an infra pull request the Sponsor applies
(mqucifer/infra#33). Only rules labelled `audience: crew` are the crew's; the Sponsor's
page them through Grafana's own routing and open no Task.

**One Task per firing.** An alert's Task is keyed by its fingerprint and when it began
firing. While one is open, the alert firing again is a comment on it, not a second
Task; once DevOps has closed it, a later firing is new work and opens its own.

**A resolved alert is noted on its Task,** once per firing. The Task stays open until
DevOps's review of it is done: an alert that stopped isn't a cause found.

**Grafana unread is a held line, never a stopped tick.** A missing token, an
unreachable Grafana or an answer that can't be read is said each pass, and nothing is
marked resolved from a list that wasn't read. Unread for a day straight, it becomes
DevOps's finding: a day without alerts is work, a pass's blip isn't.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from crew_org.columns import NEEDS_REFINEMENT, READY
from crew_org.config import load_env
from crew_org.events import EventKind, EventSink
from crew_org.flows import role_tasks as rt
from crew_org.llm import reraise_if_down
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card, ProjectClient

INFRA = "infra"
BY = rt.DEVOPS.name
ALERT_POINTS = 3
AUDIENCE = "crew"
ALERTS_PATH = "/api/alertmanager/grafana/api/v2/alerts"
TIMEOUT_S = 20.0
# When Grafana was first found unread, beside the events; gone once it's read again.
UNREAD_FILE = "alerts-unread.json"
UNREAD_FOR = timedelta(hours=24)
UNREAD = "alerts-unread"
# On an alert's Task, once per firing: it fired again, or it stopped.
FIRED = "<!-- crew:alert-fired starts={starts} -->"
RESOLVED = "<!-- crew:alert-resolved starts={starts} -->"
_FIRED = re.compile(r"<!-- crew:alert-fired starts=(\S+) -->")


def env_path() -> Path:
    """Where the read-only token is kept on the Mac (mqucifer/infra CONVENTIONS)."""
    return Path(os.environ.get("GRAFANA_ENV") or "~/.config/infra/grafana.env").expanduser()


class Unread(Exception):
    """Why the firing alerts couldn't be read. Never holds the token."""


@dataclass(frozen=True)
class Alert:
    fingerprint: str
    name: str
    starts: str
    labels: dict[str, str]
    annotations: dict[str, str]
    source: str = ""

    @property
    def key(self) -> str:
        return f"{self.fingerprint}@{stamp(self.starts)}"


@dataclass
class Alerts:
    opened: list[int] = field(default_factory=list)
    fired: list[int] = field(default_factory=list)
    resolved: list[int] = field(default_factory=list)
    finding: int | None = None
    failed: list[str] = field(default_factory=list)

    @property
    def moved(self) -> bool:
        return bool(self.opened or self.fired or self.resolved or self.finding)


def stamp(starts: str) -> str:
    """`startsAt` as a marker key can hold it: 2026-10-25T02:16:10.000Z → 20261025T021610Z."""
    return datetime.fromisoformat(starts.replace("Z", "+00:00")).strftime("%Y%m%dT%H%M%SZ")


def firing(env: dict[str, str], *, transport: httpx.BaseTransport | None = None) -> list[Alert]:
    """The crew's alerts firing now, not silenced or inhibited. Raises Unread."""
    url, token = env.get("GRAFANA_URL", "").rstrip("/"), env.get("GRAFANA_READ_TOKEN", "")
    if not url or not token:
        raise Unread(f"no GRAFANA_URL or GRAFANA_READ_TOKEN in {env_path()}")
    try:
        with httpx.Client(transport=transport, timeout=TIMEOUT_S) as client:
            answer = client.get(
                url + ALERTS_PATH,
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "active": "true",
                    "silenced": "false",
                    "inhibited": "false",
                    "filter": f'audience="{AUDIENCE}"',
                },
            )
    except httpx.HTTPError as exc:
        raise Unread(f"Grafana unreachable: {type(exc).__name__}") from None
    if answer.status_code != 200:
        raise Unread(f"Grafana answered {answer.status_code}")
    try:
        return [
            _alert(a)
            for a in answer.json()
            # The filter is Grafana's to honour; the crew's own check doesn't trust it.
            if a["labels"].get("audience") == AUDIENCE
            and (a.get("status") or {}).get("state", "active") == "active"
        ]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise Unread(f"Grafana's answer unreadable: {type(exc).__name__}") from None


def _alert(a: dict[str, Any]) -> Alert:
    alert = Alert(
        fingerprint=str(a["fingerprint"]),
        name=str(a["labels"].get("alertname", "")),
        starts=str(a["startsAt"]),
        labels={str(k): str(v) for k, v in a["labels"].items()},
        annotations={str(k): str(v) for k, v in (a.get("annotations") or {}).items()},
        source=str(a.get("generatorURL") or ""),
    )
    # A firing whose start can't be read is an unreadable answer, not a Task.
    stamp(alert.starts)
    return alert


def task_body(alert: Alert) -> str:
    # Grafana's own `__…__` labels are its routing, not what the rule says.
    labels = "\n".join(
        f"- `{k}`: `{v}`" for k, v in sorted(alert.labels.items()) if not k.startswith("__")
    )
    said = "\n\n".join(
        f"**{k.capitalize()}:** {alert.annotations[k]}"
        for k in ("summary", "description")
        if alert.annotations.get(k)
    )
    runbook = alert.annotations.get("runbook_url", "")
    return rt.render(
        rt.DEVOPS,
        kind=rt.ALERT,
        key=alert.key,
        why=f"Grafana's alert **{alert.name}** has fired since {alert.starts}."
        + (f"\n\n{said}" if said else "")
        + f"\n\n**Labels:**\n{labels}"
        + (f"\n\n**Its rule in Grafana:** {alert.source}" if alert.source else ""),
        work=(f"Follow its runbook: {runbook}\n\n" if runbook else "It names no runbook.\n\n")
        + "Find the cause from the runbook and infra's files, and propose by pull request "
        "what would stop it or catch it sooner: the rule, a dashboard, the inventory, the "
        "runbook or a compose file. The Sponsor applies what merges; nothing here touches "
        "Grafana or a running service. If no change is needed, say why in `done`'s "
        "summary. When the alert stops, or fires again, it's said on this Task.",
        done_when=[
            "The cause, or what's still unknown, is written on this Task",
            "A change within infra's scope is proposed, or why none is needed is said",
            "The runbook says what this firing taught" if runbook else "A runbook is proposed",
        ],
    )


def _starts_on(issues: IssueClient, task: dict[str, Any], key: str) -> str:
    """The latest firing this Task has been told of: its own, or a later one's comment."""
    latest = key.split("@", 1)[1]
    for c in issues.comments(INFRA, int(task["number"])):
        body = c.get("body") or ""
        for starts in _FIRED.findall(body):
            latest = max(latest, starts)
    return latest


def _said(issues: IssueClient, number: int, mark: str) -> bool:
    return any(mark in (c.get("body") or "") for c in issues.comments(INFRA, number))


def alert_tasks(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    cards: list[Card],
    events_dir: Path | None,
    env: dict[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
    now: datetime | None = None,
) -> Alerts:
    """Open a Task for each of the crew's alerts that fires, note those that stopped."""
    result = Alerts()
    now = now or datetime.now(UTC)
    try:
        if env is None:
            try:
                env = load_env(env_path())
            except (OSError, UnicodeDecodeError) as exc:
                raise Unread(f"{env_path()} unreadable: {type(exc).__name__}") from None
        alerts = firing(env, transport=transport)
    except Unread as exc:
        why = str(exc)
        result.failed.append(f"{INFRA} — alerts unread: {why}")
        sink.note(
            EventKind.NOTE, f"alerts unread: {why}"[:160], repo=INFRA, rule="decision.alerts.unread"
        )
        _unread(
            issues, board, sink, cards=cards, events_dir=events_dir, why=why, now=now, result=result
        )
        return result
    if events_dir is not None:
        (events_dir / UNREAD_FILE).unlink(missing_ok=True)
    try:
        tasks = [
            (t, m)
            for t in issues.labelled(INFRA, rt.DEVOPS.label)
            if (m := rt.marked(t.get("body"))) is not None and m.kind == rt.ALERT
        ]
    except Exception as exc:  # noqa: BLE001
        reraise_if_down(exc)
        result.failed.append(f"{INFRA} — alert Tasks unread: {exc}"[:200])
        return result
    for alert in alerts:
        try:
            _one(issues, board, sink, alert, tasks=tasks, cards=cards, result=result)
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            result.failed.append(
                f"{INFRA} — "
                + rt.not_opened(sink, repo=INFRA, role=rt.DEVOPS, kind=rt.ALERT, card=None, why=exc)
            )
    now_firing = {a.fingerprint for a in alerts}
    for task, m in tasks:
        if task.get("state") != "open" or m.key.split("@", 1)[0] in now_firing:
            continue
        try:
            _stopped(issues, sink, task, m.key, now=now, result=result)
        except Exception as exc:  # noqa: BLE001
            reraise_if_down(exc)
            result.failed.append(
                f"{INFRA} — #{task['number']}'s resolved alert not noted: {exc}"[:200]
            )
    return result


def _stopped(
    issues: IssueClient,
    sink: EventSink,
    task: dict[str, Any],
    key: str,
    *,
    now: datetime,
    result: Alerts,
) -> None:
    """Tell an open Task its alert stopped, once per firing."""
    number = int(task["number"])
    mark = RESOLVED.format(starts=_starts_on(issues, task, key))
    if not _said(issues, number, mark):
        issues.comment(
            INFRA,
            number,
            f"{mark}\nThe alert stopped firing: not firing at {now:%Y-%m-%d %H:%M} UTC. "
            "This Task stays open until its review is done.",
        )
        result.resolved.append(number)
        sink.note(
            EventKind.NOTE,
            f"infra #{number}: its alert stopped firing",
            card=number,
            repo=INFRA,
            rule="decision.alerts.resolved",
        )


def _one(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    alert: Alert,
    *,
    tasks: list[tuple[dict[str, Any], rt.Marked]],
    cards: list[Card],
    result: Alerts,
) -> None:
    mine = [(t, m) for t, m in tasks if m.key.split("@", 1)[0] == alert.fingerprint]
    still = next(((t, m) for t, m in mine if t.get("state") == "open"), None)
    if still is not None and still[1].key != alert.key:
        # Firing again while its Task is open: told on that Task, once.
        number = int(still[0]["number"])
        mark = FIRED.format(starts=stamp(alert.starts))
        if _said(issues, number, mark):
            return
        issues.comment(
            INFRA, number, f"{mark}\nThe alert fired again at {alert.starts}, after it stopped."
        )
        result.fired.append(number)
        sink.note(
            EventKind.NOTE,
            f"infra #{number}: its alert fired again",
            card=number,
            repo=INFRA,
            rule="decision.alerts.fired",
        )
        return
    if still is None and any(m.key == alert.key for _, m in mine):
        # This firing's Task was closed while it still fires: DevOps's review is done.
        return
    opened = rt.open_task(
        issues,
        board,
        sink,
        repo=INFRA,
        role=rt.DEVOPS,
        kind=rt.ALERT,
        key=alert.key,
        title=f"Alert: {alert.name}"[:120],
        body=task_body(alert),
        points=ALERT_POINTS,
        cards=cards,
        column=READY,
        by=BY,
        event="alert.firing",
        rule="decision.alerts.alert_tasks",
        detail={"fingerprint": alert.fingerprint, "service": alert.labels.get("service", "")},
    )
    if opened.new:
        result.opened.append(opened.number)


def _unread(
    issues: IssueClient,
    board: ProjectClient,
    sink: EventSink,
    *,
    cards: list[Card],
    events_dir: Path | None,
    why: str,
    now: datetime,
    result: Alerts,
) -> None:
    """Keep when Grafana was first unread; a day of it is DevOps's finding, once."""
    if events_dir is None:
        return
    path = events_dir / UNREAD_FILE
    try:
        since = datetime.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["since"])
    except (OSError, ValueError, KeyError, TypeError):
        since = now
        try:
            path.write_text(json.dumps({"since": now.isoformat()}), encoding="utf-8")
        except OSError as exc:
            result.failed.append(f"{INFRA} — alerts unread not recorded: {exc}"[:200])
    if now - since < UNREAD_FOR:
        return
    key = f"{UNREAD}-{since:%Y-%m-%d}"
    try:
        opened = rt.open_task(
            issues,
            board,
            sink,
            repo=INFRA,
            role=rt.DEVOPS,
            kind=rt.FINDING,
            key=key,
            title="The crew hasn't read Grafana's alerts for a day",
            body=rt.render(
                rt.DEVOPS,
                kind=rt.FINDING,
                key=key,
                why=f"Every pass since {since:%Y-%m-%d %H:%M} UTC failed to read the firing "
                f"alerts from Grafana. The last pass said: {why}. No alert has opened a Task "
                "since, whatever fired.",
                work="Find why the crew can't read Grafana: the read-only token's file on the "
                "Mac, the token's expiry or role, Grafana's URL, or Grafana itself. A token or "
                "its file is the Sponsor's to change: say what's needed. Propose what would "
                "catch it sooner.",
                done_when=["Triaged: the Business Analyst writes what done means."],
            ),
            points=None,
            cards=cards,
            column=NEEDS_REFINEMENT,
            by=BY,
            event="alerts.unread",
            rule="decision.alerts.unread",
            detail={"since": since.isoformat()},
        )
    except Exception as exc:  # noqa: BLE001
        reraise_if_down(exc)
        result.failed.append(
            f"{INFRA} — "
            + rt.not_opened(sink, repo=INFRA, role=rt.DEVOPS, kind=rt.FINDING, card=None, why=exc)
        )
        return
    if opened.new:
        result.finding = opened.number
