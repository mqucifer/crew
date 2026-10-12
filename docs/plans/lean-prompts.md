# Lean prompts

**Bottom line:** most of what a Developer call holds now is standing material, the same in every call, not the code the story needs. Cut it the way 2026-10-11's fixes cut the rest: show what the work needs, and let the model fetch the rest when it needs it. Then take code out of escaped JSON (crew#626), which is both the biggest answer-side cost and the day's one recurring failure. The goal: a Developer's first attempt under about 35k prompt tokens and a repair under about 40k, with no rise in refusals.

**Status:** 2026-10-11. **Part of the context-record plan's E1** (`docs/plans/context-record-build.md`, "E1 so far"): these are E1's findings on Goal mqucifer/sprint-metrics#558, fixed while E1 runs, not a separate effort. Proposed by Claude after the Sponsor asked, of the model family's limits, whether context bloat can be solved at all. Nothing built. L1 to L4 are inside the decisions already made (ADR 0024, ADR 0025). **L5 is decided (Sponsor, 2026-10-11): option A, recorded as ADR 0026.** **Order (Sponsor):** L1 to L3 in one PR, then L5, then L4.

**The condition on all of it** is the context-record plan's: no rule on the crew's context may be detrimental to how a development team develops and changes software. A cut that leaves a role guessing at something it used to be shown is a loss, and goes back.

## Why

Qwen3.8-27B gets unreliable past about 60k prompt tokens: answers come back empty (`runbooks/empty-model-answers.md`: 0% under 50k, 6% from 50k to 90k, 26% above), and long structured answers slip. That ceiling can't be moved. Every model degrades as context grows, frontier models later, so the answer is context engineering, not a larger window: the smallest set of tokens that carry what the step needs, the rest fetched just in time (the practice Anthropic and others describe as context engineering; Chroma's "context rot" measurements).

2026-10-11 showed most of the bloat was self-inflicted, and fixable:

| Call | Before | After | What changed |
|---|---|---|---|
| mqucifer/sprint-metrics#567, first attempt | 82k tokens, 10 empty answers | ~51k, answers | read tool, code sized to leave room (mqucifer/crew#620, mqucifer/crew#628) |
| Its VERIFY repair | 142,865 tokens, asked for files | 47,776, fixed the bug | report grouped by cause, written files as changed definitions (mqucifer/crew#630) |

Where the tokens go now, in that first attempt (199k characters, about 51k tokens):

| Part | Characters | Share | Kind |
|---|---|---|---|
| Map of every file, definitions and signatures | 47.1k | 24% | standing |
| Project record 8.7k, epic's design note 16.5k, presentation note 13.0k | 38.3k | 19% | standing, whole epic |
| Files shown in full | 35.8k | 18% | the work |
| Files it read | 34.9k | 18% | the work, chosen by the model |
| Story with its acceptance criteria | 15.6k | 8% | the work |
| Answer schema, as raw JSON Schema | 13.5k | 7% | standing |
| Role and rules (system) | 10.0k | 5% | standing |

The code it worked from is about a third. The answer side is as large: that attempt thought 16k tokens and wrote 28k, its code escaped inside JSON strings.

## Steps

Each step is one PR from its own worktree, measured the way 2026-10-11's were: replay the logged request from the proxy's log with and without the change, and compare tokens, refusals and whether the answer is right. Each step records its part sizes (`llm.started` already carries them).

| Step | What | Files | Done when |
|---|---|---|---|
| L1 | **The map shrinks.** With `read_file`, the map no longer has to show every signature. It lists every file by name, and in full only the files near the work (the graph's neighbourhood, ADR 0024): what the work names, their imports and importers. Everything else is one line per file with its top-level names | `tools/repo_context.py` (`repository_context`, `focused_context`) | sm#567's replay: the map under 15k characters; the answer is no worse and asks no more |
| L2 | **The notes are cut to the story.** The design note and the presentation note are shown as their shared part (interfaces, data shapes, the epic's approach) and this story's own section, not every story's. The rows are already cut this way (crew#440) | `flows/presentation_notes.py` (`story_notes`), `flows/design_notes.py` | The two notes under 12k together for sm#567; the Code Reviewer and QA keep the same cut |
| L3 | **The schema is written once, plainly.** The server keeps the full JSON Schema to hold the answer to. The prompt's copy becomes a field list, each field's name, type and description, without JSON Schema's wrapping | `crew_org/calls.py` (`task_text`) | The schema's prompt copy under 7k for the Developer; the refusal rate on the replays doesn't rise |
| L4 | **QA's prompt** gets the same: QA's acceptance check ran at 74.7k tokens on mqucifer/sprint-metrics#564 on 2026-10-11, its context still given whole | `crews/qa_crew.py`, `flows/acceptance.py` | QA under 50k on that story's replay |
| L5 | **Code leaves the JSON (crew#626, P1).** The answer keeps its structure in JSON (paths, operations, targets, which test proves which criterion), and each body of code goes in a plain delimited block the JSON names, the edit-block approach tools such as Aider use. No quote is escaped, so no string can close early | `crews/delivery_crew.py` (the forms), `crew_org/calls.py` (the answer's two parts), `tools/workspace.py` | The decision is taken (below); sm#567's replays: no quote slips in 10 runs where 3 in 6 slipped, and fewer tokens written |

**Order (Sponsor, 2026-10-11):** L1, L2 and L3 as one PR, a commit each; they are independent, mechanical and inside decisions already made. Then L5 (ADR 0026), then L4 for QA.

**L4, measured on mqucifer/sprint-metrics#569 rather than #564 (2026-10-12).** Sprint 22's QA calls ran above the target: 69,088 prompt tokens on #569 and 85,827 on mqucifer/sprint-metrics#585, because `tests/test_service.py` (200,000 characters) was shown whole. QA now sees a large test file in part (its imports, the tests the change touches or the work names, and their fixtures) and reads the rest by name. #569's logged call, rebuilt this way, was 20,227 tokens, and 24,222 after three reads. It proved all eight criteria, including the one it had held unproven for a test it wasn't shown (crew#645).

## The decision on L5 (ADR 0026)

**This is a new choice, not a reversal.** On 2026-09-18 the Sponsor asked for research on how the Developer's edits address code, and edits by name were chosen over whole files, search/replace and unified diff (ways-of-working, "edit by name"; zero format failures against 11 and 31). That stays exactly as decided. What was never weighed is where the code itself sits in the answer: JSON came with CrewAI's typed answer (`output_pydantic`), and the substrate gate measured it on small answers only (5 of 5 valid). Since ADR 0025 the crew makes its own calls, so a mixed answer is an ordinary change. Prior art the 2026-09-18 research missed: Aider measured in 2024 that models write worse code when it is wrapped in JSON. Markdown-delimited code beat JSON for every model tried, for example 60.5% against 54.1% (Claude 3.5 Sonnet) and 60.6% against 51.1% (DeepSeek-Coder V2) on 133 problems ([aider.chat, 2024-08-14](https://aider.chat/2024/08/14/code-in-json.html)).

**What structured JSON is for, and what it costs.** The structure earns its place:
- every answer has a known shape;
- about 40 checks, each from an incident, refuse a wrong field with a reason the re-ask can act on;
- edits by name let the guards see that nothing unnamed changed;
- QA and review read which test proves which criterion, and what was retired and why.

What fails is code inside it. Every quote in code is escaped, twice in a test that checks JSON output, and under the server's grammar a slip runs on as whitespace: sm#567 slipped on 3 of its last 6 answers.

| Option | What it is | For | Against |
|---|---|---|---|
| **A. Structure in JSON, code in blocks** (recommended) | JSON for the structure; each code body in a delimited block the JSON names, joined by the crew before the forms' checks run | Keeps every check and guard; no escaping; shorter answers. The server can still hold the shape: SGLang takes a custom grammar (EBNF) as well as a JSON schema | The answer has two parts, so the call layer splits and joins them; the forms' checks move to the joined answer |
| B. JSON without the server's grammar | The same form, checked after the answer instead of enforced while it's written | A slip can't run away; nothing else changes | Malformed JSON comes back instead, and is re-asked; escaping stays |
| C. As now | The runaway guard and the reason in the re-ask (mqucifer/crew#627) | Nothing to build | A slip still costs a whole answer, minutes each |

**Decided: A** (Sponsor, 2026-10-11; ADR 0026). It removes the cause, not the cost of the cause, and it is the shape crew-presentation's web code (HTML, CSS, JavaScript, quote-heavy all) needs most: files that aren't Python change by whole-file and quoted edits today, all inside JSON strings.

## Not in this plan

- **A memory store** (Oracle AI Agent Memory, Mem0 and the like). They recall model-written notes by similarity: they add context rather than remove it, and the losses traced on epic 468 were exact facts a summary drops. The record stays the memory of truth (ADR 0023). Search over the crew's history is a later, separate need, if a loss is traced to a fact that existed and wasn't found; pgvector on the shared Postgres would serve it.
- **Carrying a reading round's thinking** into the answer: measured on 2026-10-11 (crew#583), the saving was within run-to-run variation and the prompt grew by the thinking carried.
- **A larger model or window.** Outside the crew's control, and every model degrades with length.

## The success test

| Check | 2026-10-11 | Success |
|---|---|---|
| Developer first attempt, prompt tokens (sm#567) | ~51k | under 35k |
| Developer repair, prompt tokens (sm#567) | 47.8k | under 40k |
| Standing material's share of a Developer prompt | 55% | under 30% |
| QA acceptance, prompt tokens (sm#564) | 74.7k | under 50k |
| Answers that slip on a quote (sm#567's replays) | 3 of 6 | 0 of 10, after L5 |
| Refusals per answer on the replays | measured before each step | no rise |
