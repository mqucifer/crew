# 24. A step's code comes from a graph, then by asking

- **Date:** 2026-10-10
- **Status:** Accepted (to be built as Part C of `docs/plans/context-record-build.md`; crew#584 lands in it). For the Developer, a session's hints and lookups replace the read tool and the ceilings: decided and not built (ADR 0032).

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

The review of the Developer's first answers (crew#778, 2026-10-17) found the gap this
left. In 10 of 12 failed first answers, the file or fact the failure needed wasn't
shown. A test the coverage map found ranked 211th when the list keeps 15. A file over
about 20k characters couldn't be had whole through either `read_file` or `need_files`.

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
- **The map sees the tests the sandbox doesn't run** (Sponsor, 2026-10-10, after Sprint
  21's first run): a static half adds each test's reads and calls of the project's
  code, skipped tests included, and under the ceiling a test that spells out a named
  file's table is shown first. Coverage alone missed all three tests
  mqucifer/sprint-metrics#551's stories had to change (crew#612). Giving the sandbox
  a project's services, such as its database, is crew#614.
- **No retrieval index.** It adds a system with nothing to test it against.
- **For the Developer, the graph gives hints and the session looks up the rest** (the
  Sponsor, 2026-10-17, crew#778 decisions 2 and 4; ADR 0032). This is decided and not
  built.
  - A session starts with the graph's picks as paths, each with why it was picked, and
    no bodies.
  - The Developer finds the rest with `find` (a definition with its callers and
    importers, from the graph), `tests_for` (the coverage map) and `search`, and reads
    windows of files with `view`. `find` and `tests_for` are Python only to start.
  - The read tool, `need_files` and the reading ceilings stay for refinement and QA.
    They also stay for the Developer until its session is built, and its ceilings
    retire then.

**The standards.** Test impact analysis (Rothermel and Harrold's regression test
selection; pytest-testmon and Ekstazi in practice); a dependency graph from static
analysis; tool use, also called function calling; two-stage retrieval.

## Consequences

- Text matching leaves `tools/pinning.py`. The ways-of-working rule "the Developer sees
  the files its work names, and asks for the rest" gains the graph before the ask.
- The coverage map is per project and per base commit, under `var/coverage/`, built
  where the sandbox runs the tests. It runs for every Python part, in the crew's image
  or the project's own, through the project's own setup and Python (ADR 0030). Other
  languages when a project needs them.
- The replay of epic 468's Developer calls with the new selection, counting asks per
  story and prompt size, decides whether the scout is needed.
- crew#584 is built as the split's declaration from the map, with the delivery pass of
  ADR 0023.
- The Developer's session still starts with the tests its story declares, always
  shown (ADR 0032, decision 4).

## Changelog

- **2026-10-10:** the map gains a static half (crew#612; Sponsor). The services half
  was dropped the same night for crew#614.
- **2026-10-11:** the read tool is built for the Developer (crew#583, step C4): `read_file`
  reads a file or one definition by name inside the call. The Developer is shown up to
  120,000 characters and the call, reads included, is held to 175,000. The split's read
  tool follows with its move to the call layer.
- **2026-10-11:** the split reads inside the call too (crew#583, step C4), shown up to
  120,000 characters of the repository, with what it reads held under 175,000 for the
  whole call.
- **2026-10-11:** a repair's view follows the graph's rule too (crew#591): a large
  written file is shown as the definitions its answer changed, the code is sized to keep
  room to read, and the failure report is grouped by cause.
- **2026-10-11:** the map lists a test file far from the work in brief: how many tests
  it holds and its helpers, any test read by name; source files keep every signature
  (lean-prompts plan, L1). Test names were 80% of the map on
  mqucifer/sprint-metrics#567. The code shown before reading drops to 50,000 characters,
  the same code in 33k less.
- **2026-10-13:** the coverage map follows the language, not the image: a Python part in
  a project with its own sandbox image gets a map too (crew#690, ADR 0030; Sponsor).
- **2026-10-17:** for the Developer, the graph's picks become a session's hints, and
  `find`, `tests_for`, `search` and `view` replace the read tool, `need_files` and the
  ceilings (ADR 0032, decisions 2 and 4; the Sponsor, on crew#778). Decided, not built.
