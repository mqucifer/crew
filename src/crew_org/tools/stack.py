"""Is the stack the crew runs on healthy? (#335, DevOps phase 1)

The tick already refuses to start when the proxy or the model is down
(`llm.health`). Nothing looked at the rest: the telemetry exporter and the
OpenTelemetry Collector could be down for days, and the only sign was an empty
dashboard. This reads each, at the start of a tick and again for the standup.
The model is required to work; telemetry isn't, so a telemetry problem warns and
never stops a tick.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

EXPORTER = "http://127.0.0.1:9464/metrics"
COLLECTOR = "http://127.0.0.1:13133/"
TIMEOUT = 3.0


@dataclass(frozen=True)
class Check:
    component: str
    ok: bool
    detail: str
    required: bool = False


def _http(component: str, url: str, fix: str) -> Check:
    try:
        response = httpx.get(url, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        return Check(component, False, f"not answering at {url} ({type(exc).__name__}). {fix}")
    if response.status_code != 200:
        return Check(component, False, f"{url} answered {response.status_code}. {fix}")
    return Check(component, True, "up")


def stack_health() -> list[Check]:
    """The proxy and model (required), then the telemetry exporter and collector."""
    from crew_org.llm import health  # noqa: PLC0415

    ok, message = health()
    start = "Start it with `docker compose --env-file ../../.env up -d` in deploy/telemetry."
    return [
        Check("proxy and model", ok, message, required=True),
        _http("telemetry exporter", EXPORTER, start),
        _http("telemetry collector", COLLECTOR, start),
    ]


def problems(checks: list[Check]) -> list[str]:
    """Standup lines for what isn't healthy; none when everything is."""
    return [
        f"- **{c.component}**{' (required)' if c.required else ''}: {c.detail}"
        for c in checks
        if not c.ok
    ]
