# 17. The product builds to its spec; infra owns where it runs

- **Date:** 2026-10-05
- **Status:** Accepted (built by crew#440; infra is onboarded later)

## Context

The refinement panel replay mixed two things. DevOps's notes on a sprint-metrics
epic ran from how CI proves the service (fine) to a volume path and what the
Sponsor must provide in production (not the epic's to settle), and the check
line I gave it asked a product epic to settle production. Parts 1, 2 and 4 of
`docs/plans/goal-174-service.md` (environments, settings and secrets, production
on the shared Postgres) are infra's content written into a product's plan.

The Sponsor, 2026-10-05: sprint-metrics should only care about building to the
spec, and infra (DevOps) should care about environments and the deployed runtime.
DevOps is brought in early, like a real team, and then takes projects to run in
production.

## Decision

Sponsor, 2026-10-05.

- **A product builds to its spec and proves it in CI.** The spec includes its
  runtime contract: the settings it reads (names and defaults), what it needs at
  startup, how it is started, health-checked and stopped. CI proves it with
  throwaway services and no real secrets. For sprint-metrics that is
  `SPRINT_METRICS_DB`, a schema the service creates at startup, and a throwaway
  Postgres in CI.
- **Infra owns the deployed runtime:** which server, real addresses and secrets,
  provisioning, backups, scaling, dashboards. The production Postgres is infra's.
- **DevOps is always on the refinement panel**, scoped to the runtime contract
  and the CI proof. A note about the deployed runtime is marked `infra`, listed
  apart in the epic's conclusion under "For infra", and no story waits on it.
- **The shared Postgres** (Sponsor, 2026-10-01): one Postgres for LiteLLM, the
  crew and sprint-metrics, each service with its own schema and user. It replaces
  the "its own Postgres" in crew#280, already recorded as superseded in the Goal's
  decision log.

## Consequences

- Until infra is onboarded, after the presentation pilot (the Sponsor's order),
  the "For infra" items stay in the conclusions. They become infra issues, and
  input to infra's first Goal, when it is onboarded. That Goal is redrafted then. The carry-in list is crew#475.
- Parts 1, 2 and 4 of the Goal 174 plan stay as the record until infra's Goal
  takes them over.
- What a product declares as its runtime contract is the handoff infra reads to
  run it in production. Where that lives in the product, such as its docs, is
  decided with infra's onboarding.
- DevOps on a product epic is cheap when it has nothing to add, and the contract
  is where the replay found real gaps (a health check the epic never named).
- ADRs 0015 and 0016 are unaffected.
