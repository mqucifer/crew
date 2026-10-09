# The crew's record

What the crew records about its own work, where it goes, and how its parts join. Built under
crew#449 (ADR 0014). The generated meanings of every key, kind, rule and event name are in
[reference/records.md](reference/records.md).

**What it's for:** many kinds of experiments and improvements from one record: the retro, a
diagnosis, Grafana, replays like the Laya experiments (mqucifer/crew discussions 552 and 553), and
whatever comes next. It isn't shaped for one consumer. A package such as `delivery-history`
takes from it only what its own contract allows (ADRs 0020 and 0021).

## One record, three outputs

| Output | Holds | Leaves this machine? |
|---|---|---|
| `var/events/*.jsonl` | The event log: what code reads and acts on, one line per event | No |
| `var/logs/crew.jsonl` | Every log record, levelled, message included; events mirrored as `events.<kind>` | No |
| `var/telemetry/*.jsonl`, tailed by the Sponsor's collector into Grafana | Every event, content-free: names, ids and numbers only | Yes, without content (ADR 0010) |
| OTLP to the collector, then Grafana | The log's own records (not the mirrored events) and the traces, content-free | Yes, without content |

Events reach Grafana once, through `var/telemetry`. The OTLP handler leaves the mirrored
`events.<kind>` records out, so they aren't sent twice.

## How records join

Every record carries the same context, and records join by its ids, never by time or by prose.

```
tick (id, commit, dirty) ─┬─ pass ─ phase
                          └─ run: one role, one card, one phase
                               ├─ card (repo, number), sprint, attempt
                               ├─ model calls (call_id, prompt_hash, output_schema)
                               ├─ files.shown / files.asked (+ why)
                               ├─ decisions (rule, failure_kind)
                               ├─ what the gates found (findings, unproven_numbers, failing_tests)
                               └─ how it ended (agent.finished)
issue.cites ─► cards (repo#n)        a fix's merge commit ─► the first tick that ran it
```

- **Tick:** `tick` is unique across commands. `commit` and `dirty` say which code ran it. On
  exported records they're the resource's `service.instance.id` and `service.version`.
- **Run:** `log.run(role, card=…, repo=…)` opens one. Every record, model call and span inside
  carries its `run`. A model call made outside any run is a run of its own.
- **Where it's stamped:** the sink stamps each event's `ctx` when it's written, so call sites
  don't repeat it. A log record gets the same context from `log.scope()`.
- **Trace:** a log line, an event and a span written in the same span share `trace_id` and
  `span_id`.
- **Reviews:** a review is recorded under the card it closes, with its pull request in `pr`.
- **Issues:** each pass records `issue.cites` for the crew issues changed since the last one: the
  qualified cards their body names, whoever filed them. A bare `#N` isn't guessed at.
- **Fixes:** a fix is dated by the first tick whose commit contains it, not by its merge.

## Asking it questions

| Question | Ask |
|---|---|
| What is a tick doing now? | `crew log --follow` (`--level`, `--card`, `--phase`, `--event` filter it) |
| What happened to a card, run by run? | `crew runs --card sprint-metrics#475` |
| Every event of one run | `jq -c 'select(.ctx.run == "RUN")' var/events/*.jsonl` |
| What blocked cards this sprint, by rule | `jq -r 'select(.kind == "card.blocked" and .ctx.sprint == "Sprint 20") \| .detail.rule' var/events/*.jsonl \| sort \| uniq -c` |
| Which issues cite a card | `jq -c 'select(.kind == "issue.cites" and (.detail.cites \| index("sprint-metrics#475")))' var/events/*.jsonl` |
| In Grafana, one run's events | `{service_name="crew"} \| run="RUN"` |
| In Grafana, one rule's events | `{service_name="crew"} \| rule="escalation.budget"` |
| In Grafana, a run's events by kind and rule (instant query) | `sum by (failure_kind, rule) (count_over_time({service_name="crew"} \| run="RUN" [6h]))` |
| In Grafana, what one commit's ticks did | `{service_name="crew"} \| commit="COMMIT"` |
| In Grafana, a tick's own log lines | `{service_name="crew"} \| crew_tick="TICK"` |

**The names differ by path.** An event, read from `var/telemetry`, carries the context's keys as
they are: `run`, `card`, `rule`, `failure_kind`, `commit`. A log line sent over OTLP carries them
as `crew_<key>` (`crew_run`, `crew_tick`), with the commit as `service_version` and the tick as
`service.instance.id`. Both carry `trace_id`, so an event and a log line of one run join on it.

## What leaves, and what stays

- **Leaves:** names, ids, hashes and numbers: the context, `rule`, `failure_kind`, `pr`,
  `prompt_hash`, `output_schema`, token counts and durations. The allow-lists are
  `telemetry.DETAIL` and `log._CONTEXT_OUT`, and the collector drops content-named keys again.
- **Stays in `var/`:** messages and summaries, prompts and answers, review findings' statements,
  failing tests' assertion lines, what the file selection read (`selection_text`), and the reason
  given for each file asked for (`why`).
- A key added to a record stays local until it's put on an allow-list on purpose.

## Adding to it

- **A new kind of record:** name it in `EventKind`, or log it as a named event with
  `log.event(logger, "name", …)` if a person diagnoses with it rather than code acting on it. The
  rule is ADR 0014's: if code acts on it, it's a named event; if a person diagnoses with it, it's a
  log.
- **A new decision:** add its rule to `rules.Rule`, with a docstring saying what it means, and put
  `rule=` on the event that records it. A new kind of failure goes in `rules.Kind` the same way.
- **A new context key:** set it with `log.scoped(...)`, explain it in `reference.CONTEXT`, and add
  it to `log._CONTEXT_OUT` only if it may leave.
- **Then run `crew reference`.** A test fails while `docs/reference/records.md` is stale.

## History and what's still open

- **Records from before 2026-10-09 have no `ctx`.** Readers fall back to what those events carry
  (`detail.repo`, and a review keyed by its pull request), and `crew runs` finds no runs in them.
- **Still open in crew#449:** a schema for each named event's attributes, checked when written;
  the readers moved over to those schemas; and the silent `except` blocks put on the record.
