# 5. Escalation is rationed, not a crutch

- **Date:** 2026-09-18
- **Status:** Accepted

## Context

The Sponsor: "Escalation is important but not a replacement for proper task
designing, retry rules — i.e. we didn't set up the rules properly because
escalation was the easy path."

## Decision

Every local failure is classified before anything escalates. `SCHEMA` and
`SCOPE` never escalate. Only `VERIFY`, after bounded repairs, and a justified
`CAPABILITY` may escalate, within a fixed budget per sprint. The policy is
written up in `docs/ways-of-working.md`, "Escalation policy".

## Consequences

- The escalation rate is a health metric. The retro turns an overrun into
  process-defect issues, never into more budget.
- A failure that escalation would hide becomes a crew issue instead.
