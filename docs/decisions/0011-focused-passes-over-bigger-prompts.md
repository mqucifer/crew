# 11. Focused passes over bigger prompts

- **Date:** 2026-09-29
- **Status:** Accepted

## Context

Asked to add four questions to the Architect's prompt, the Sponsor said no:
"we basically did this with the lens test and it isn't better than detailed
second passes. And if we give it too much to answer at once we've seen those
long times and risks missing details." The evidence:
- lens lines in the Architect's prompt caught 0 of 3 (crew#359);
- both real catches came from second passes, the critic and the deploy review;
- answer size drives long thinking and empty answers (crew#312).

## Decision

When a role misses a class of things, the fix is a separate, focused pass: a
role with its own duty, shown evidence chosen mechanically. More duties don't
go into the role's prompt. Agent prompts set scope and delivery, not design;
before adding a rule, check what the role was shown.

## Consequences

- Each prompt keeps a narrow job. More passes cost more calls, which is
  accepted because correctness comes before speed.
- Applied in the refinement panel (`docs/plans/refinement-panel-build.md`):
  four focused calls, one per role.
