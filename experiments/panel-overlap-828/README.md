# Why the refinement panel's members repeat each other (mqucifer/crew#828)

**Since mqucifer/crew#858:** the per-epic panel this describes was replaced by the Goal's look, and `crew_org.crews.panel_crew` is gone, so the scripts here no longer run. They stay as the evidence crew#858 and crew#828 cite.

**Status:** analysis, 2026-10-20, from 41 panels' notes in mqucifer/sprint-metrics and mqucifer/crew-presentation since 2026-10-01. No model was run. `analyse.py` reproduces every number. Written for mqucifer/crew#828, part 2 (a panel sized to the epic), while the evidence was fresh.

## The result

**The four members do the same job with a different background, so they find the same problems.** The Sponsor said so on 2026-10-20, and the code and the notes agree:
- **Overlap:** 44% of all notes repeat another member's note on the same epic.
- **The trend:** it was 28% in the pre-build test, 42% while the panel was one call per member, and 60% since the members became sessions over the code.
- **What it costs:** the repeats are words the Product Owner has to read and settle. sm#684's settle took 26 minutes, after an 18-minute panel.

**Recommendation:** give each member a different thing to produce, not a different area to own. That's the Three Amigos practice as Example Mapping runs it, where each role brings a different kind of card. Measure it by replaying recorded panels before building it into the crew.

## The evidence

| | Panels | Notes a panel | Overlapping | Words a panel |
|---|---|---|---|---|
| Pre-build test, panel-174 (2026-10-02) | 108 calls, 3 arms | — | 75 of 269 (28%) | — |
| One call per member (to 2026-10-18) | 38 | 3.9 | 64 of 150 (42%) | 734 |
| Sessions over the code (2026-10-19 on) | 3 | 6.7 | 12 of 20 (60%) | 1,458 |

- **Settled by:** the Architect 100 notes (59%), the Product Owner 70, the Sponsor and infra none. The pre-build test had the same split (about 70/25).
- **"Nothing to add":** DevOps 37 of 41 panels, QA 11, UX 13, the Architect 14. DevOps is mostly silent, and when it speaks on a non-runtime epic it repeats the others. On sm#684 its one note was the event name, which three other members had already raised.
- **What a repeat looks like** (sampled pairs, read by hand):
  - **sm#684:** "name the 8th event type and bump the version". All four members raised it: the Architect, UX, QA and DevOps.
  - **sm#467:** "a sixth event type contradicts decision 5". UX and QA both raised it.
  - **sm#600:** "Prometheus has no null". The Architect and UX both raised it.
  - **sm#469:** "'unknown' isn't defined observably". UX and QA both raised it.

## Why it happens

The cause is the panel's structure, in `src/crew_org/crews/panel_crew.py`:
1. **One task, word for word, for all four.** `opening()` (lines 171–183) asks each member to "name each problem in your area that should be settled before the split: something the epic gets wrong, leaves out or contradicts".
2. **The same context and the same tools.** `PanelContext` is "the same for all four". Since mqucifer/crew#807 each member is a session that can read the whole codebase.
3. **The same output form.** `PanelNote` is problem, source, settle and settled_by for every role.
4. **Areas defined by outcome, and they overlap.** `FOCUS` (lines 39–68) gives each member one to three sentences of "you own…":
   - the Architect owns "data shapes, interfaces";
   - UX owns "what a program sends and gets back";
   - QA owns "whether it can be proven";
   - DevOps owns "proven in CI".

   An unnamed event type, or an unclassified version change, falls in all four areas at once.
5. **"The other roles cover theirs" can't work in parallel.** The members don't see each other's notes, by design (they run at once). Each one reports the biggest gap it finds, and the biggest gap is the same for all of them.

Sessions made it worse. With the whole codebase readable, every member finds the same code-level gap, so the notes roughly doubled in length and the overlap went up.

**Already decided, and why it no longer holds:** the pre-build test measured 28% overlap and kept it. It recommended no step to merge notes and no separate critic, only scope lines and per-role checks in the task. At 28%, and with one call per member, that was cheap. At 60%, and with 45 minutes per epic, it isn't.

## Why this conclusion keeps coming back

On 2026-10-10 the Sponsor noted that my "change the structure, not the prompts" answer keeps returning with the problems. This case shows why, and it supports the guess written down then: **the structure gets adopted as prompt lines, never as the shape of the work.**
- **The record took Example Mapping's structure,** as R and Q rows (ADR 0023, `docs/plans/context-record-build.md`).
- **The panel that feeds it didn't.** Its members were given a lens ("you own…") plus checks written as prompt lines (ADR 0011, the pre-build test's change 2). They weren't given different things to produce.

So each fix narrowed what a member looks at, but left everyone producing the same kind of thing. The overlap comes back because the shape that causes it was never changed.

## Options

| | What changes | Overlap | Time | What it feeds |
|---|---|---|---|---|
| **A. A different output per role** (recommended) | QA writes examples, including the edge cases (Example Mapping's green cards). The Architect writes the decisions that are expensive to reverse, as candidate R and Q rows. UX writes sample outputs: what the reader actually sees. DevOps fills in the runtime contract as a form (settings, startup, health, CI proof), or says "no runtime change". | Can't repeat by construction: an example isn't a design decision | The same or less: smaller, typed answers, and DevOps's form is short | QA's examples become story criteria and acceptance tests (M10). DevOps's contract feeds the deploy (M8). UX's samples feed the docs. |
| B. A step that merges the notes after the panel | Nothing for the members; one more call before the settle | Hidden, not removed | +1 call | The same as today |
| C. Members one after another, each seeing the earlier notes | The order | Lower | About 2.6× slower (panel-174) | The same as today |
| D. A panel sized to the epic (crew#828, part 2) | Fewer members on small epics | Lower on small epics only | Faster | The same as today |

A and D combine: with typed outputs, sizing becomes "which outputs does this epic need", such as no DevOps form for a docs-only epic.

## How to prove it before building

Replay recorded panels, the way `experiments/panel-174/replay.py` did, and compare A against today's panel:
- on sm#684 to sm#692 (Sprint 31), and the panel-174 inputs;
- **overlap,** from this script;
- **findings caught,** scored against panel-174's ten findings;
- **the settle's time.**

This is an experiment the Sponsor schedules; no model run was made here.

## Caveats

- **Overlap here is a word measure.** Pairs were sampled and read, not every note, so it may count a related-but-different pair as a repeat. panel-174's 28% was scored by reading every note, so the two numbers aren't strictly comparable.
- **Only 3 panels are from the session era.** 60% is a direction, not a rate.
- **Some repeats may be worth having.** Two roles independently naming the same gap is a signal of how serious it is. Option A keeps that signal: the gap shows up as both an example that fails and a decision that's missing, without four copies of the same note.
