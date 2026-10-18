# 5. Nothing escalates: a story that can't be done goes back

- **Date:** 2026-09-18
- **Status:** Accepted. Nothing escalates (ADR 0032, decision 9); the rationing this
  decision first set was deleted with escalation (crew#778, Stage E).

## Context

The Sponsor: "Escalation is important but not a replacement for proper task
designing, retry rules — i.e. we didn't set up the rules properly because
escalation was the easy path."

On 2026-10-17 the Sponsor went further: escalation "has been hiding problems", and
the escalated Claude sessions gave little to learn from (crew#778, decision 9).

## Decision

**Nothing escalates.** A stuck session (`blocked`, no progress, a ceiling or the
backstop) goes back to the Business Analyst who wrote the story, with its evidence.
An undecided choice goes to the Sponsor. A story with no epic to split it again is
blocked for a person. The constitution's §9 says where each failure goes.

## Consequences

- The rate of stories returned to the Business Analyst is the health measure
  escalation's rate was. The retro and `crew export` count it, and the retro turns a
  high rate into process-defect issues. It reaches sprint-metrics through a Goal from
  the Sponsor.
- A failure that escalation would hide becomes a crew issue instead.

## Changelog

- **2026-10-17:** nothing escalates once the Developer's session is built, and the
  measure becomes the rate of stories returned to the Business Analyst (ADR 0032,
  decision 9; the Sponsor, on crew#778). Decided, not built.
- **2026-10-18:** built (crew#778, Stage E). The failure classes, the escalation
  policy, the per-sprint budget and the ledger are deleted, and the retro counts the
  stories returned to the Business Analyst. The title was "Escalation is rationed, not
  a crutch".
