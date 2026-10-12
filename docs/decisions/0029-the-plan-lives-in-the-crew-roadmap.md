# 29. The plan lives in the Crew roadmap project

- **Date:** 2026-10-12
- **Status:** Accepted

## Context

Sprints are one day long, and new ideas arrive faster than they can be built. By 2026-10-12, 57 crew issues were open, and only about five of them were on the path to what we said we were delivering (crew-presentation building against `delivery-history`). Issues had become a dumping ground. Claude spent that day building useful work that wasn't on the path.

A plan kept as one pinned issue still had to be read and updated by hand. The Sponsor didn't want to track a plan by looking into one issue, or in a tool other than GitHub that would need keeping in step.

## Decision

**The plan lives in the Crew roadmap project** (https://github.com/orgs/mqucifer/projects/2). It's separate from the crew's delivery board, which stays the crew's (ADR 0002). It holds what we're delivering and when.

- **A milestone is an iteration of its Plan field,** with a name and its dates. The Roadmap view lays them out on a timeline.
- **A milestone's items are the issues that make it up,** from any repository, plus a draft item for each step that is the Sponsor's (a release cut, an unpause). An issue can be in several projects, so the crew's board and its epic and story nesting are untouched.
- **It updates itself.** An item goes to Done when its issue closes, through the project's built-in workflow. The only edits are re-planning: a milestone's dates, or an item moved between milestones.
- **Changing the plan is the Sponsor's call.** Claude says a milestone is going to slip before it slips.

**Work outside the plan is parked, grouped.** An idea, or a finding off the path, is one part of a themed Discussion in the Ideas category (ADR 0009). It isn't a separate issue. A theme comes back onto the plan as one issue with its parts.

## Consequences

- The crew repo's open issues are the plan's work, the current retro and the current standup. On 2026-10-12, 57 became 8. 50 were parked as parts of nine Discussions (crew#658 to crew#666).
- Claude works only on the current milestones' items. A new idea gets one line ("parked: doesn't move M-n") and goes to a Discussion.
- Views can't be made through the API, so the Sponsor sets a view's layout in the project.

## Changelog

- **2026-10-12:** decided by the Sponsor in chat, when the crew repo's issues were cleaned up.
