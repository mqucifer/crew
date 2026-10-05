# 15. A Goal's decisions are the Sponsor's comments and headed sections

- **Date:** 2026-10-05
- **Status:** Accepted

## Context

The refinement panel and the split must be shown what the Sponsor has decided
for a Goal (crew#440). The rule that finds those decisions came out of the
panel test: the Sponsor's comments and decision log on the Goal's own cards,
plus anything of theirs elsewhere that names the Goal.

Run for real (`crew decisions`, 2026-10-05), it had a flaw the test's cutoff
date had hidden. The Sponsor's login also opens the crew's defect reports, and
those cite a Goal as evidence ("found in the Sprint 8 tick, decomposing
sprint-metrics#171"). Counted whole, they were about 15 KB of the 36 KB for
Goal 174, and four of five other Goals pulled in the same kind. It grows with
every Goal the crew works.

## Decision

Sponsor, 2026-10-05. For an issue outside the Goal's own cards, the rule counts
the Sponsor's comments and the body sections whose heading names the Sponsor
(`(Sponsor, 2026-09-29)`, or a "Sponsor decisions" log). It doesn't count the
rest of the body.

The Sponsor's comments on a Goal's superseded epics and stories stay in. That a
card was superseded, and why, is context the panel should have.

## Consequences

- A decision written in an issue body, outside a heading that names the
  Sponsor, isn't collected. It goes in a comment or under such a heading.
- crew#280 keeps its decisions: its sections are headed `(Sponsor, date)`.
- Goal 174's collection fell from 36 KB to 17 KB, about 4.5 KB of it the
  "Superseded:" notes. If those get in the way when the panel runs on
  sprint-metrics#406, skipping cards closed as not planned is the next step.
  It would also drop a decision made on a card before it was superseded, so it
  needs the Sponsor.
- The rule is `src/crew_org/flows/decisions.py`, and `crew decisions <repo>#<n>`
  prints what it finds.
