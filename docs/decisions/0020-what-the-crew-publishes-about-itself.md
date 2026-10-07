# 20. What the crew publishes about itself

- **Date:** 2026-10-07
- **Status:** Accepted (to be built by crew#521)

## Context

crew-presentation is a public site about how the crew works. It shows the crew's
`delivery-history` package: a replay of the crew's work, and sprint-metrics' answers
about each sprint (ADR 0021). The crew releases that package on its own public
repository, so once released, it's published.

What the crew may publish had only been called "content-free". That word was never
defined, and it had been copied into a Goal's decisions and the project's record.
It was read two ways: the replay mock shows a refusal's reason, which a strict
reading of "never the crew's reasoning" would ban.

ADR 0010 already keeps prompts and responses out of telemetry, which goes to a
private destination, Grafana. A public site needs its own rule, written once.

The Sponsor, 2026-10-07: "The presentation project should only be working based on
the contract data the crew is defining so it shouldn't care whether it's free of
something or not."

## Decision

**What the crew may publish about itself:**

| | What | Example |
|---|---|---|
| Measures | Times, counts, attempts, tokens, cost | "attempt 5", "4,880 reasoning tokens" |
| Names and states | Roles, steps, card states, file names, issue and PR numbers | "Developer · #127 · Building" |
| Text already public | Issue and PR titles, which are on GitHub already | "Stable JSON API" |
| Reasons the crew's code writes | The fixed messages of the crew's own checks, with names filled in | "no definition named DEFAULT_THRESHOLDS" |

**Never:** a prompt; anything a model wrote that isn't already on GitHub, such as an
answer, review text or its reasoning; a secret.

- **The crew enforces it where the data leaves.** The export's model is an
  allow-list, as telemetry's is (ADR 0010): a field is published only when it's
  listed in that model. A field added to an event later stays local until someone
  decides it may leave.
- **A reason the export can't attribute to the crew's own code is left out.** The
  replay then shows the step that refused and its failure class, without the reason.
- **The consumer doesn't restate this rule.** It takes what its contract carries
  (ADR 0021), and the contract carries only what this allows.

## Consequences

- The replay can say why a step refused when one of the crew's checks wrote the
  reason. A refusal whose reason a model wrote shows as refused, with its failure
  class.
- Telemetry keeps its own, narrower allow-list for Grafana (ADR 0010). It carries no
  titles.
- A Goal or a project record says nothing about what the crew may publish. Each
  points to its contract.

## Changelog

- 2026-10-07: Accepted. The Sponsor agreed the definition while planning
  crew-presentation's Goals (crew#518).
- 2026-10-07: The context names the `delivery-history` package, released on the crew's
  repository, in place of snapshot pull requests (crew#521).
