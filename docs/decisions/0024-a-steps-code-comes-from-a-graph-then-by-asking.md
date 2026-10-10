# 24. A step's code comes from a graph, then by asking

- **Date:** 2026-10-10
- **Status:** Accepted (to be built as Part C of `docs/plans/context-record-build.md`; crew#584 lands in it)

## Context

The context review measured what each role is shown and what its answers used. Code
and tests in full were 48 to 84 percent of most prompts; the Developer changed files
holding 19 percent of the file text it was shown; the Code Reviewer named one or two of
the seven to ten files it was shown. The selection still missed what was needed: the
Developer's first answer asked for files on 13 of 13 stories, each ask a full re-prompt
(crew#231's `need_files`), and on 7 of 8 it asked instead of working. Text matching
couldn't find the merged tests a shape change breaks, because those tests assert a whole
shape without naming the new keys: 1 of 3 for mqucifer/sprint-metrics#529 and 0 of 5 for
mqucifer/sprint-metrics#507 (crew#584). The four empty answers came at 95k to 181k
prompt tokens.

## Decision

Sponsor, 2026-10-10 (D4 of the plan).

- **A deterministic graph selects first:** imports, importers, where a name is defined,
  and which merged tests execute which code. The last is a coverage map the crew builds
  in its sandbox for each base commit: test impact analysis, also called regression test
  selection.
- **The split's pinning tests come from the map,** not from text matching: the tests
  covering the files and definitions the epic and its rows name, each declared by a
  story as kept or changed (ADR 0023).
- **What the graph misses is read by a tool inside the call** (ADR 0025): the Developer
  and the split read a file and continue, instead of returning `need_files` and being
  re-prompted. `need_files` stays as the last resort.
- **A small scout model is kept as a re-ranker option,** the two-stage pattern from
  information retrieval, for when the graph's candidates exceed the ceiling. It is built
  only if the replay after the graph shows that gap. It need not be the crew's main
  model.
- **No retrieval index.** It adds a system with nothing to test it against.

**The standards.** Test impact analysis (Rothermel and Harrold's regression test
selection; pytest-testmon and Ekstazi in practice); a dependency graph from static
analysis; tool use, also called function calling; two-stage retrieval.

## Consequences

- Text matching leaves `tools/pinning.py`. The ways-of-working rule "the Developer sees
  the files its work names, and asks for the rest" gains the graph before the ask.
- The coverage map is per project and per base commit, under `var/coverage/`, built
  where the sandbox runs the tests. Python first; other toolchains when crew#376's
  profiles reach them.
- The replay of epic 468's Developer calls with the new selection, counting asks per
  story and prompt size, decides whether the scout is needed.
- crew#584 is built as the split's declaration from the map, with the delivery pass of
  ADR 0023.
