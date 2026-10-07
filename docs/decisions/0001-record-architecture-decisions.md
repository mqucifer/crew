# 1. Record architecture decisions

- **Date:** 2026-10-02
- **Status:** Accepted. How decisions are recorded is now kept in this directory's README, as living documentation; the process bullets below were its first version.

## Context

The crew repo's design decisions were scattered. Reasons were woven into
`docs/ways-of-working.md`, spread over issues and PRs, and about a dozen of the
Sponsor's decisions lived only in Claude's private memory, where the Sponsor
couldn't review them and a session could miss one. That had already gone wrong
once: an early CLAUDE.md draft contradicted a decision that existed only in a
memory body.

## Decision

Decisions about the crew's own design are recorded as ADRs in this
directory, in Michael Nygard's format: Status, Context, Decision and
Consequences.

- **What gets one** (Sponsor, 2026-10-02): every decision the Sponsor makes or
  approves about the crew, and any technical choice of Claude's that would be
  costly to reverse. Routine implementation choices stay in PR text.
- **How:** Claude writes it by PR in the session the decision is made, and the
  Sponsor's merge is the approval.
- **Changing one:** the ADR is edited in place, so it says what's true now,
  and a dated line in its Changelog records what changed.

These records are about the crew itself. They aren't the crew's ADRs for the
products it builds (crew#190), and the crew agents aren't shown them.

## Consequences

- A decision is reviewable and findable by number. Memory keeps only working
  state and preferences.
- Each ADR adds a small PR for the Sponsor to merge.
- Backfilled ADRs (0002 to 0013) give the date the decision was first made, and
  say where it was recorded before.

## Changelog

- **2026-10-07:** an ADR is changed in place and keeps a changelog, instead of
  being superseded by a new one, so each ADR reads as the decision now (Sponsor,
  as their team does at work).
