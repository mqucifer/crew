# 14. Logging: one levelled stream, named events for what code reads

- **Date:** 2026-10-02
- **Status:** Accepted. Built by crew#449, planned for a crew iteration.

## Context

The Sponsor, 2026-10-01: "I can't get a sense of the logs we're sending.
There's no shared schema other than kind… there's not even a log level. Why
don't we have a shared logger library?"

The crew has no logging. There isn't one call to Python's `logging`.
Everything goes through the event sink, which has become four things at once:
the crew's record, the live view, the telemetry source and its diagnostics.

- **No levels**, so nothing can be filtered, raised or alerted on.
- **The event shape drifts.** Events have a five-field envelope and a free-form
  `detail`, with 66 keys and no definitions. `note` is a quarter of all events,
  and a catch-all.
- **Some names mislead.** `tick.started` is emitted for every pass, and a
  watcher raised a false alarm on it.
- **Code reads these events and acts on them:** the retro, the Architect's
  revisit, the standup, the sprint close, `crew export` and telemetry. A
  drifting field can silently change a crew decision.
- **Some failures leave no trace.** Twelve broad `except` blocks (eight in the
  flows) skip or carry on without a record. Six of them aren't even marked as
  deliberate.
- **A tick doesn't record which crew code ran it.** `tick.started` carries only
  `{"tick": 1}`. On 2026-10-01 a tick crashed from running mixed code versions,
  and the record couldn't show it.
- **Console output stands in as a record.** It's redirected into 53 loose
  `var/*.log` files, and Rich buffers it until exit.

## Decision

**One stream.** The crew logs through standard `logging`, with a logger per
module, through one shared module. As in OpenTelemetry's data model, an event
is a log record with an `event.name`, so events and logs are the same stream.

**The rule for what kind of record to write:** if code acts on it, it's a
**named event**; if a person diagnoses with it, it's an ordinary levelled
**log**.

- **Named events:**
  - their attributes have a schema defined in code, checked when written, and
    published as a generated reference;
  - a schema change is a versioned change (`schema_version`), because other
    projects read these events (crew#280).
- **Levels**, the same meaning everywhere:
  - `DEBUG`: detail for diagnosis;
  - `INFO`: normal progress, and decisions taken (a fallback chosen, a hold
    applied);
  - `WARNING`: work continued, degraded (a retry, a skip, a fallback after a
    failure);
  - `ERROR`: an operation failed;
  - `CRITICAL`: the tick stops.
- **A common context on every record,** from context variables: `tick` (an
  id unique across commands), `commit` and `dirty`, `pass`, `phase`, `run`,
  `repo`, `card`, `role`, `attempt`, `sprint` and `schema_version`. Call sites
  don't repeat it. **A run** is one role's work on one card in one phase, and
  every record and model call inside it carries its id. **The sink's events
  carry the same context**, stamped once when emitted, and the tick's commit
  and id go on the exported records' resource (`service.version`,
  `service.instance.id`). Records join by these ids, never by time or prose.
- **Failures are on the record.** A caught exception is re-raised, or
  recorded at `WARNING` or above with what was skipped and why. A silent skip
  needs a stated reason in its `noqa`. Enforced by lint: `BLE001`, `S110` and
  `S112` are turned on.
- **Each tick records its code:** the tick's first record carries the crew's
  commit and whether the working tree had uncommitted changes.
- **Content stays local.** Records in `var/` may hold content. Export goes
  through OpenTelemetry's log model, carrying level, context and `event.name`
  as attributes, and never content (ADR 0010).
- **Third-party loggers** (CrewAI, LiteLLM, httpx) are set to `WARNING` and
  above.
- **The console is for people.** The crew writes its own full record, so
  console output is never redirected to keep one, and nothing reads the
  console back. An ad-hoc capture goes to `var/logs/<command>-<date>.log`.

## Consequences

- It touches every place the crew writes a record. That's planned work for a
  crew iteration, never a live change during a sprint.
- Every reader of today's events reads the old `var/events` shape through an
  adapter. The retro and `crew export` must give the same results on Sprint
  12's history before the old writer goes.
- `note` disappears into levelled logs or named events. The misleading names
  are fixed: `tick.*` means the tick only, alongside `pass.*` and `phase.*`.
- Six existing silent catches need a reason or a record when the lint rules
  turn on.
- The tick-watcher and the retro can filter by level. Grafana gets levels and
  context as attributes.

## Changelog

- 2026-10-09: The common context gains `run`, a unique `tick` id, and the
  tick's `commit`, and the sink's events carry it too, so a record joins its
  run, its card and the code that ran it by field (crew#449 part 2, from
  discussions 552 and 553). The Sponsor asked for the telemetry context to come
  first, for many kinds of experiments.
