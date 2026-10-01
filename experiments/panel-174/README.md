# The refinement panel test (sprint-metrics Goal #174)

**Status:** designed 2026-10-01, not yet run. Part 5 of the plan agreed with the Sponsor that day: Architect, UX Designer, QA and DevOps each take a focused pass on an epic before it's split. This test checks that panel before it's built into the crew.

**The question:** would that panel have caught, before the split, what went wrong with Goal #174's first epics? Or would the members miss the same things together, as identical panels did in the earlier panel test (Discussion #359: "correlated blind spots need critique, rules or checks")?

## The case

Goal #174's first epics as they stood on the morning of 2026-10-01, before the Goal was revised:

- **The Goal:** #174's body before its revision (from its edit history).
- **The epics:** #184 (accept events, retained history), #185 (versioned intake format), #187 (logs and traces), with #186 (the container, delivered) as a sibling.
- **Nothing is written to GitHub.** The epics are closed and superseded. Notes go to files here, as in `panel-359`.

## What each member is shown

This is what's really under test. Every member gets the same context, **collected mechanically, the way the crew would collect it in production**:

1. the Goal
2. the project record (`.crew/project.yaml` as it was that morning, before #409 and #412)
3. the epic, and its sibling epics under the Goal
4. **the Sponsor's recorded decisions:** comments by the Sponsor on the Goal's cards, and on crew issues that name the Goal or the project (crew#280, crew#283, the #186 decisions, the counting rule from 2026-09-30)

If item 4 is hand-picked rather than found by a rule, the test flatters the panel. The collection rule is written down and run before the panel, and its output is kept (`inputs/`).

## The arms

- **Panel, serial.** Four focused calls per epic, one per role. Each answers with a note, or "nothing to add" (a first-class answer).
- **Panel, parallel.** The same four calls at once, to measure wall time and per-request time from LiteLLM's per-request records under load (not from idle gauges). It runs only after the serial arm's notes are judged correct.
- **Context without item 4.** The serial panel again, without the Sponsor's recorded decisions. This shows how much of any catch comes from the context rather than the roles.

Three runs per arm. Small sample, so it gives direction.

## The score sheet

These are the epic-level findings from the review (https://claude.ai/artifact/TXQstfAr8xrYqXFEzkjva6). A finding counts if any member names it, with its source.

| # | Finding | Kind | Expected from |
|---|---|---|---|
| 1 | History kept in memory only; the Sponsor decided Postgres (crew#280) | Sponsor decision | DevOps, Architect |
| 2 | No `unblocked` event; crew#280 pushes one | Sponsor decision | QA, Architect |
| 3 | A card bound to its creation sprint; "a sprint counts a story where it merges" | Sponsor decision | QA |
| 4 | Traces as custom JSON on stdout, not OTLP (crew#283, and #187's own promise) | Sponsor decision | DevOps |
| 5 | A new event version called MAJOR; the #186 decisions say MINOR | Sponsor decision | Architect |
| 6 | Trends asked for twice, planned nowhere | Coverage | QA, Architect |
| 7 | #184 and #185 define different event formats | Between epics | Architect, QA |
| 8 | Intake versioned differently from the answers (`"1"` vs `1`) | Between epics | Architect |
| 9 | #187 instruments only the old file mode, not the service | Between epics | DevOps, QA |
| 10 | No settings per environment, no test bed, nothing for production | Environments | DevOps |

The story-level contradictions (#393, #394, #395, #402) aren't scored here. The panel runs before the split, and the criteria check (crew#428) covers those after it. It was proven live on them.

**Also counted:** notes that aren't needed (to check warning 1, that a model asked for input will produce some), and the cost per epic in calls, tokens and wall time.

## What would change the plan

- The panel catches most of 1–10 only with item 4: the collection of decisions is the work, more than the roles.
- Members miss the same findings together: add a critic step, as #359 found for the Architect.
- Many unneeded notes: tighten "nothing to add" before building.
