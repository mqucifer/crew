# 8. The crew repo is off the board

- **Date:** 2026-09-23
- **Status:** Accepted

## Context

The crew's own refinement had decomposed a crew Goal onto the board. Crew cards
worked by hand polluted the capability measures, and two backlogs in one place
caused repeated confusion. The Sponsor called the mix-up "too much
confusion".

## Decision

- The board holds delivery work only. All crew-repo items were removed, and
  auto-add for the crew repo was turned off.
- Every phase is bounded by `delivery.repos` (crew#105).
- The crew's backlog is its issues, ordered by P-label.

## Consequences

- Crew work is done by hand through PRs, not by ticks.
- The removed cards' field values are snapshotted in
  `var/board-snapshots/crew-cards-2026-09-23.json`.
