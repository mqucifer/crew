# 33. Refinement works in a session

- **Date:** 2026-10-18
- **Status:** Accepted (built by crew#807; the narrow checks wait for the Sponsor's OK)

## Context

**Every Developer struggle in the first session runs traced back to the story** (crew#807, `var/notes/refinement-path-analysis.md`). There were criteria that contradicted the code, a wrong Existing tests line, a declared test that didn't exist, and a criterion the data can't produce.

**Refinement stated facts about code it didn't look up:**
- The panel and the Product Owner's settle saw no code. On mqucifer/sprint-metrics#660, QA repeated a claim it couldn't check ("per sm#661"), and the settle made it a binding row about `tests/test_store.py` assertions that don't exist.
- The Architect's design settle decided R2 ("remove `maximum: 100`") against a `maximum` no schema has.
- The split on mqucifer/sprint-metrics#660 was 283,836 characters, over the reading ceiling, so it had no read tool. Its delivered list alone was 205k.
- The decomposition was shown the whole repository (572k for mqucifer/sprint-metrics#650), and the UX Designer's note was too (376k on #660). Two of the analysis' four story defects came from the UX step, and nothing checked them.

ADR 0032's session, one conversation of tool calls that ends with `done`, had just proved itself for the Developer.

## Decision

The Sponsor, on crew#807 (2026-10-18), with the operator's calls inside those decisions.

**1. Each step that states facts about code works in a session:**
- the panel: Architect, UX Designer, QA and DevOps, one at a time (ADR 0032's P1);
- the Product Owner's decomposition and settle;
- the Architect's design settle;
- the Business Analyst's split, and its repair of criteria the check refuses;
- QA's criteria check, one story per session;
- the UX Designer's note (the UX criteria).

**2. Each step ends with its own answer form as `done`.**
- The form's rules are told in its fields' descriptions, as in a one-call form (ADR 0031).
- A form refused for its shape is a reply in the same session, not a discarded answer.
- So are the checks a one-call step was refused by after its answer: the settle's coverage, grounding and product-call checks, the design settle's, the split's (delivered, accounted, provided, and the record's rows and tests), and the UX note's coverage of the stories.
- There's no `blocked` tool, since each form has its own way to say it can't answer. A stop fails the step, as a call refused every time did.

**3. The tools read, and nothing runs:**
- `view`, `search` (the start's notes too), `find`, `tests_for`, and the git tool from mqucifer/crew#808 with `restore` refused;
- no edit tools, no `run` and no container;
- each step reads one checkout of the default branch, made once a tick per repository (`Workspace.at_main`).

**4. The start holds what the step decides from:**
- its own section first: what it decides, its scope, and whose the neighbouring work is;
- the Goal, the epic, its siblings with their records, the Sponsor's decisions, the decision log and the record, as the step needs them;
- the project's record, said to be the whole project's (how it's released and what CI runs stay in the file);
- the map, the hints, and the rules, each told once.

It doesn't preload the repository, the focused code, the pinning tests or the delivered list. The last is a note the session searches. `docs/reference/context.md` lists each step's parts. On sm#665 and #667 the starts measured 14.5k to 21k characters, against 17.5k to 565k for the one-call prompts (`var/notes/807/prompts/` in the main checkout).

**5. Message 0 is the role's text, with no constitution,** as the Developer's is.
- The Architect's refinement steps get their own entry (`architect.refinement`), and so do QA's panel (`qa_engineer.panel`) and the Product Owner's settle (`product_owner.settle`).
- The Business Analyst's Spike and "say so in a comment" lines are cut.
- The UX Designer's constitution goes, as neither of its steps sends it.

**6. The rules' words** (the Sponsor's review of the rendered prompts):
- "What the Goal, an epic, a note or a row says the code *does today* can be out of date or wrong: check it in the code. What they *decide* is binding."
- A note that states a fact about the code cites it: a path and definition first, with the line as at main's commit, a test, or a commit.
- The split: "Look up what the code does today, then write each criterion as the behaviour the epic asks for; where that differs from today, the story says so." And "A story whose criteria the code already meets asks for nothing: list it in `already_delivered`."

**7. The forms change where the evidence asked:**
- the UX note gets `already_holds`, a criterion the code already meets at main, with where it was seen;
- `already_delivered` takes `by` or, when no story delivered it, `seen_at`;
- the split's `need_files` goes, since a session views a file itself.

**8. The stops are high, with no nudges, and every signal is recorded:**
- 100 calls (a reading step never edits or runs, so the Developer's "calls with no edit and no run" counts every one);
- 50 views of one file;
- 2 hours of model time per step.

The events are the Developer's, each naming its step. A step's answer goes on the log by shape only. delivery-history leaves a step's session out, and counts it as left out.

**9. Held for the Sponsor's OK, the narrow checks from crew#807**, each as a reply at `done`:
- the UX criteria through the criteria check;
- a quote from code checked against the named file;
- a criterion's value checked against the package field's type;
- declared test names resolved at the split and again at delivery's start;
- the contract ruling shown each named test's source.

## Consequences

- **Gone with the one-call steps:**
  - the split's `need_files` and its ask loop;
  - the focused code the design settle, the split and the criteria check were shown;
  - the whole repository the decomposition and the UX note were shown;
  - the pinning tests (`tools/pinning.py`);
  - the settle's and the UX note's retries.
- **Still one call:** bug triage, the Product Owner's answer to a story problem, its placing of the Sponsor's words, the backlog order and the design note. None is in crew#807's list.
- **What it costs:** each step's model time goes up by its tool calls, and the Spark serves one session at a time.
- **What this doesn't decide:** the narrow checks (9), and whether the stops and the hints suit refinement, which the records will show.
- **The ADRs it changes, each with a changelog line:** 0016 (the panel reads the code), 0018 (the design settle reads it), 0024 (no `need_files` for the split), 0025 (these steps' calls are sessions) and 0032 (the session serves refinement).

## Changelog

- **2026-10-18:** decided and built (crew#807). The Sponsor decided the sessions and the git tool, reviewed each step's opening messages before the build, and chose the role text, the constitution's absence, the stops and one panel member at a time. They also set the criteria check, its repair and `already_holds` into the build, and the wording in decision 6. The operator's calls on crew#807: the versioning pointer, and `already_delivered`'s `seen_at`.
