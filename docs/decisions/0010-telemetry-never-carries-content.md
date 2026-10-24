# 10. Telemetry never carries content

- **Date:** 2026-09-26
- **Status:** Accepted

## Context

The Sponsor wanted the crew's activity live in Grafana Cloud. Prompts and model
output are the crew's content and must not leave the machine. Grafana Cloud's
free plan keeps 7 days.

## Decision

- **No prompts or responses ever go to Grafana.**
- **Prompts and answers never leave the Mac unencrypted.** The one way they
  leave is the backup of the crew's history, encrypted on the Mac before it's
  written to iCloud Drive, with the key kept off iCloud and out of every
  repository (ADR 0005 in mqucifer/project-print; `runbooks/crew-backup.md` in
  mqucifer/infra).
- `var/telemetry/*.jsonl` is an allow-list projection of every event
  (`crew_org/telemetry.py`), with no summaries, errors, output, criteria or
  prompts. `var/events` stays the crew's full record.
- **The OTel Collector is the Sponsor's** (`deploy/telemetry`). Its
  `transform/no-content` step deletes content attributes before export.
- The export settings use their own names (`GRAFANA_OTLP_*`), never the
  standard `OTEL_EXPORTER_OTLP_*`, which any SDK would read and so bypass the
  collector's filter.
- **History stays local.** Grafana is the live window; long-term analysis reads
  local records. Their only copy off the Mac is that encrypted backup.

## Consequences

- New telemetry fields are added to the allow-list on purpose. Nothing gets
  through by default.
- Visual views of the crew are built from local history, not from Grafana.

## Changelog

- **2026-10-24:** The rule that content never leaves the machine became
  "prompts and answers never leave the Mac unencrypted", so the crew's history can be backed up, encrypted, to
  iCloud. Decided by the Sponsor in M9's planning session: ADR 0005 in
  mqucifer/project-print, built in mqucifer/infra#36.
