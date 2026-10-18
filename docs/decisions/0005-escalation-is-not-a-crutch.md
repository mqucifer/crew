# 5. Escalation is rationed, not a crutch

- **Date:** 2026-09-18
- **Status:** Accepted. When the Developer's session is built, nothing escalates
  (ADR 0032, decision 9); until then the rationing below stands.

## Context

The Sponsor: "Escalation is important but not a replacement for proper task
designing, retry rules — i.e. we didn't set up the rules properly because
escalation was the easy path."

On 2026-10-17 the Sponsor went further: escalation "has been hiding problems", and
the escalated Claude sessions gave little to learn from (crew#778, decision 9).

## Decision

Every local failure is classified before anything escalates. `SCHEMA` and
`SCOPE` never escalate. Only `VERIFY`, after bounded repairs, and a justified
`CAPABILITY` may escalate, within a fixed budget per sprint. The policy is
written up in `docs/ways-of-working.md`, "Escalation policy".

**When the Developer's session is built, nothing escalates** (ADR 0032; decided,
not built). A stuck session (`blocked`, no progress, a ceiling or the backstop)
goes back to the Business Analyst who wrote the story, with its evidence. An
undecided choice goes to the Sponsor.

## Consequences

- The escalation rate is a health metric. The retro turns an overrun into
  process-defect issues, never into more budget. When the session is built, the
  measure becomes the rate of stories returned to the Business Analyst. It reaches
  sprint-metrics through a Goal from the Sponsor.
- A failure that escalation would hide becomes a crew issue instead.

## Changelog

- **2026-10-17:** nothing escalates once the Developer's session is built, and the
  measure becomes the rate of stories returned to the Business Analyst (ADR 0032,
  decision 9; the Sponsor, on crew#778). Decided, not built.
