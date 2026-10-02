# 3. The Sponsor's gates

- **Date:** 2026-09-18
- **Status:** Accepted

## Context

The Sponsor is a pure manager: they decide what the project is and where it
focuses, and they don't review individual work items. "I'd like to review at
the end of a sprint, not at the swimlanes." "I want to be able to control when
the process runs, not the individual hops between."

## Decision

- **Three human touchpoints:**
  - the `goal` label, applied by hand, marks a drafted Goal as ready; the crew
    doesn't touch a Goal before it (crew#62, 2026-09-22);
  - epic approval and prioritisation;
  - sprint-end review of the increment.
- **Everything else is automated.** Merges are gated by branch protection,
  required checks and the reviewer agent.
- **The Sponsor starts each run** with a manual `crew tick`, which runs until
  no card can move. There are no prompts per transition.

## Consequences

- Nothing may apply the `goal` label automatically, whether an issue template
  or an Action: that would remove the gate. The template sets `needs:human`
  only.
- The Sponsor wants a real-time view of agent activity while a tick runs.
