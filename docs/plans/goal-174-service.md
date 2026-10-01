# Running the service (sprint-metrics Goal #174)

How the history-keeping service runs, gets tested and is looked after, and where DevOps and QA come into the work. Planned with the Sponsor on 2026-10-01.

**Status:** parts 1–5 agreed. Parts 6 (sequencing) and 7 (the Goals that follow) are still to plan. Nothing here is built yet, except where noted.

## 1. Environments

| Environment | What it is | Postgres |
|---|---|---|
| **Local checks** | The crew's sandbox: no network, runs lint and unit tests. | None. The store's tests skip. |
| **CI** | GitHub Actions, including a short-lived compose stack: the service and a throwaway Postgres, seeded, tested, torn down. Runners are amd64. | Throwaway, per run |
| **Production** | Compose on the Mac today. Later, dedicated Intel or AMD hardware, moved as-is. | The shared one |

- No persistent test bed. Short-lived stacks are cheap, and sprint-metrics is public, so Actions minutes are free.
- Nothing in production may depend on the Mac. Anything machine-specific, like the Spark's `.local` address, comes from settings.

## 2. Settings and secrets

| Setting | Local checks | CI | Production |
|---|---|---|---|
| `SPRINT_METRICS_DB` | Unset | The throwaway database's address, in the workflow. Not a secret. | **Secret**, in the host's `.env` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Unset (telemetry off) | Unset, or a collector in the stack | The crew's collector |
| `OTEL_SERVICE_NAME` | Default | Default | `sprint-metrics` |

- Production secrets stay in the host's `.env` for now.
- DevOps proposes settings and secrets by name, and may list which exist. It never reads a value.

## 3. Test bed and QA suite

- **CI only, for now.** DevOps makes sure CI is right for what the tests need.
- **QA creates the test data,** and owns acceptance tests that run against the short-lived stack. This is the first concrete step of the QA-owned suite (Discussion #261).
- **The suite is its own epic in sprint-metrics,** worked in the normal cycle. It doesn't hold features back: QA starts from what's built, then grows the suite as each feature arrives.

## 4. Production on the shared Postgres

- **Every service is a good tenant:** its own schema and its own user, in one database, with access to nothing else. The Sponsor maintains the users.
- **LiteLLM is dropped and recreated** into its own schema with its own user. It's a transient gateway with one user. Anything truly needed is extracted first, as an experiment or runbook.
- **Each service migrates its own schema** at startup.
- **Setup SQL** (users, schemas, permissions, the database's name) is proposed by DevOps, applied by the Sponsor, and rehearsed by CI on every run. The admin password becomes a real secret.
- **Backups, later:** a one-time setup of an encrypted dump to the Sponsor's iCloud backup, with short retention.
- For DevOps to check: that LiteLLM can use a non-default schema through its connection string.

## 5. DevOps and QA in the process

Modelled on how working teams refine: everyone with a stake is in the room, and people speak only when it matters to them.

- **A refinement panel, before each epic is split.** The Architect, UX Designer, QA and DevOps each take their own focused pass on the epic. "Nothing to add" is a first-class answer. It replaces labels, so no role is skipped because an epic wasn't labelled.
- **Every member sees the same context:** the Goal, the project record, the sibling epics, and the Sponsor's recorded decisions, collected by rule. The misses on 2026-10-01 came from that context being missing, not from a missing role.
- **The Architect's design note stays after the split,** on the stories. The panel is the conversation first; the design comes once the work is sliced.
- **Each note is checked before it's acted on.** A model asked for input tends to produce some, and an unneeded note shouldn't become work, as sprint-metrics#419 did.
- **Serial first, then parallel.** Prove the notes are right, then make it crew#299's first parallel case. The calls are independent, read-only, and share a long prefix.
- Refinement is the slowest step and carries the most risk. The extra calls are accepted for that reason.

**The test before it's built:** [`experiments/panel-174/README.md`](../../experiments/panel-174/README.md) (crew PR #441).

**Still open in this part:** who writes the QA suite's stories, and how its growth is triggered with each feature. To be answered from how working teams do it. A starting point: QA writes a suite story inside each feature epic, and a Developer builds it in the same cycle.

## 6. Sequencing Goal #174 (to plan)

What exists today:
- #414 (dependencies) is open.
- #415 and #416 are held in Inbox for DevOps.
- #406, #407 and #408 are approved and held.
- The QA suite epic doesn't exist yet.

## 7. The Goals that follow (to plan)

Written last, from parts 1–4.

## Crew work this implies

| Issue | What | Change from this plan |
|---|---|---|
| crew#440 | DevOps' environment note | Becomes DevOps' seat on the panel instead of a label trigger |
| crew#299 | Parallel work | The panel is its first case, after the serial run |
| crew#430 | Design risks read against the Goal | Covered by the panel seeing the Goal |
| crew#439 | Which design changes become technical epics | Unchanged |
| crew#431 | Stale-Goal forecast | Unchanged; it would read the panel's notes |
| Discussion #261 | QA-owned suite | The first step is part 3. QA can't write to a repo yet |
