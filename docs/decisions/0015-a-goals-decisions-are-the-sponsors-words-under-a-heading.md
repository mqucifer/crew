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

**Who is shown the collection, and how it reaches the rest** (Sponsor,
2026-10-10). The panel and the Product Owner's settle are shown the collection.
The split and the gates are not: they read the epic's record (ADR 0023), where
the settle has written each Sponsor decision that bears on the epic as a
binding row whose source is the Sponsor. A comment carrying a crew or Claude
marker is skipped by the rule, whoever posted it.

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
- Whether every Sponsor decision that bears on an epic reached its record is
  part of the proof in `docs/plans/context-record-build.md`, not a rule the
  code can check alone.

## Changelog

- **2026-10-10:** the context review found that this ADR said the split is
  shown the collection and the code showed it only to the panel, and that 64%
  of the collection for Goal 462 was Claude's status comments posted under the
  Sponsor's login before crew#566. The split and the gates now read the epic's
  record instead (ADR 0023), and marked comments are skipped (Sponsor).
- **2026-10-10:** built (crew#583, step A7). The Claude marker is `<!-- claude -->`.
  Claude's seven status comments on crew#280 and crew#521 carry it. The two
  Sponsor decisions in crew#521's tables were moved into its body, under a
  heading that names the Sponsor, and crew#280's dependency on
  mqucifer/sprint-metrics#462, which only those comments stated, into its body.
  `crew decisions sprint-metrics#462` fell from 14,857 to 5,762 characters, with
  every decision kept.
