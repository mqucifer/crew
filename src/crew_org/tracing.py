"""The crew's traces, with the OpenTelemetry SDK (#283).

A tick is a trace; its phases, the cards they work and the model calls made
for them are spans beneath it. The SDK does the shipping (batched, retried,
flushed at exit) to the OpenTelemetry Collector the Sponsor runs on this Mac,
which forwards to Grafana. The standard httpx instrumentation turns every
outbound call into a span and carries `traceparent` to LiteLLM, so its own
spans nest under the crew's once its tracing is on.

**Attributes, never content.** The crew's spans carry the same kind of fields
the telemetry log allows: card, repository, sprint, attempt, what it was for,
role, model alias, tokens, duration, finish reason. No prompt, response,
summary or error text is set on any span, and the collector's
`transform/no-content` drops content-named attributes anyway.

**Off unless configured.** The endpoint is `telemetry.otlp_endpoint` in
org.yaml, passed to the exporter directly: never the standard
`OTEL_EXPORTER_OTLP_*` variables, which any SDK reads, and which could send
straight to Grafana around the collector. Unset, nothing is exported and the
spans cost nothing. A collector that's down loses spans; it never slows a tick.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

_PROVIDER: Any = None


def start(org: dict, *, exporter: Any = None, resource: dict[str, Any] | None = None) -> bool:
    """Start exporting spans, if org.yaml names an endpoint. True if started.

    `exporter` replaces the OTLP one, for tests. `resource` is added to the process's
    resource: the crew's commit and the tick's id (crew#449), as `log.setup` takes it.
    """
    global _PROVIDER  # noqa: PLW0603
    if _PROVIDER is not None:
        return True
    endpoint = str((org.get("telemetry") or {}).get("otlp_endpoint") or "").rstrip("/")
    if not endpoint and exporter is None:
        return False
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import (  # noqa: PLC0415
        BatchSpanProcessor,
        SimpleSpanProcessor,
    )

    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": "crew", "service.namespace": "crew", **(resource or {})}
        )
    )
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,
        )

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", timeout=5))
        )
    trace.set_tracer_provider(provider)
    _PROVIDER = provider
    try:
        from opentelemetry.instrumentation.httpx import (  # noqa: PLC0415
            HTTPXClientInstrumentor,
        )

        HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    except Exception:  # noqa: BLE001, S110 - HTTP spans are a bonus, never a blocker
        pass
    return True


def stop() -> None:
    """Flush what's buffered. At the end of a command."""
    if _PROVIDER is not None:
        _PROVIDER.force_flush(timeout_millis=5000)


def tracer():
    return trace.get_tracer("crew")


def _clean(attributes: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in attributes.items()
        if v is not None and isinstance(v, str | int | float | bool)
    }


@contextmanager
def span(name: str, **attributes: Any):
    """A span around a block of the crew's work, with the given attributes."""
    with tracer().start_as_current_span(name, attributes=_clean(attributes)) as current:
        yield current


def model_call(
    *,
    start_ns: int,
    end_ns: int,
    failed: bool,
    attributes: dict[str, Any],
) -> None:
    """A finished model call, as a span under whatever the caller was doing."""
    call = tracer().start_span("gen_ai.chat", start_time=start_ns, attributes=_clean(attributes))
    if failed:
        from opentelemetry.trace import Status, StatusCode  # noqa: PLC0415

        call.set_status(Status(StatusCode.ERROR))
    call.end(end_time=end_ns)
