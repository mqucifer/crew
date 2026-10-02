# 10. Telemetry never carries content

- **Date:** 2026-09-26
- **Status:** Accepted

## Context

The Sponsor wanted the crew's activity live in Grafana Cloud. Prompts and model
output are the crew's content and must not leave the machine. Grafana Cloud's
free plan keeps 7 days.

## Decision

- **No prompts or responses ever go to Grafana.**
- `var/telemetry/*.jsonl` is an allow-list projection of every event
  (`crew_org/telemetry.py`), with no summaries, errors, output, criteria or
  prompts. `var/events` stays the crew's full record.
- **The OTel Collector is the Sponsor's** (`deploy/telemetry`). Its
  `transform/no-content` step deletes content attributes before export.
- The export settings use their own names (`GRAFANA_OTLP_*`), never the
  standard `OTEL_EXPORTER_OTLP_*`, which any SDK would read and so bypass the
  collector's filter.
- **History stays local.** Grafana is the live window; long-term analysis reads
  local records.

## Consequences

- New telemetry fields are added to the allow-list on purpose. Nothing gets
  through by default.
- Visual views of the crew are built from local history, not from Grafana.
