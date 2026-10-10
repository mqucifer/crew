# Building the epic's record

**Bottom line:** make one record per epic the crew's memory, edited in place and read whole by every step; fetch code by a graph, with a read tool for the rest; make the crew's own model calls, so a refused answer is retried with its reason. Then run the context review again on a real epic. Success means none of the eight kinds of loss traced on sprint-metrics#468 can happen, because the mechanism that lost them is gone, not patched.

**Status:** 2026-10-10. D1, D2, D4, D5, D6 and D7 agreed in chat, D6 staged; D3 needs no decision. ADRs 0023 to 0025 record them. Q3 is decided (a Sponsor reply on an epic is recorded as a binding row, see the open questions); Q2 remains. Nothing built. **D6's scope confirmed later that day (Sponsor: "neither"):** no CrewAI layer is kept, not for routing, memory or calls; its unused features were weighed one by one, and CrewAI 1.15.27's executor is unchanged on the two faults; task guardrails are the fallback only if the module fails on its first site. ADR 0025 and ADR 0002 record it.

**The condition on all of it (Sponsor, 2026-10-10):** no rule put on the crew's context may be detrimental to how a development team develops and changes software. That is still the whole goal of the crew. Where a rule here would slow an ordinary change, the rule yields. The evidence is the Crew Context Review (page v6; raw runs in `~/infra-results/context-review/`; the tool is `scripts/context_review.py` in mqucifer/infra, runbook `runbooks/context-review.md`). Each decision below is asked in chat and becomes an ADR by PR when the Sponsor decides, before its part is built. Every file and function named here exists at crew commit 63e5936.

## Why this keeps coming back, and what is different now

The same conclusion has been decided before. Each time it was carried out as one more reader on one prompt, or not at all:

| When | Where | Decided | What happened |
|---|---|---|---|
| 2026-09-25 | crew#190 | Decisions are recorded where they reach | Open at P3, never built |
| 2026-09-26 | crew#231 | Roles ask for the context they need: a map first, then what they name | Built. The Developer's first answer still asked for files on 13 of 13 stories, each ask a full re-prompt |
| 2026-10-02 | crew#440, ADR 0016 | Conclusion rows in the epic body; stories cite rows; a project decision log | Built. The comment thread stayed the memory: the Product Owner's eight answers never became rows, and readers took the latest comment of a kind |
| 2026-10-02 | crew#457 | Every hand-off ends in a conclusion table | Open, not built |
| 2026-10-07 | ADR 0018 | The Architect settles design questions before the split | Accepted, not built. `flows/settle.py` has no Architect pass; `crews/design_note_crew.py` still settles them after the split |
| 2026-10-09 | crew#583 | One record per epic, binding vs open | Held for this plan |

There are now about 150 headings, each from a reader added for an incident with its own rule (latest, since the last split, first after a time, rows cited). Nobody owns the whole. That is the review's C8, and the factual half of its deferred question.

Three things are different in this plan:

1. **Each part replaces a mechanism and names what goes away.** No part adds a heading to a prompt. The ADR for each part lists the readers and rules it removes.
2. **One place builds every step's context,** with a manifest a test checks against the docs. A new fact goes into the record and the manifest, never into a new reader.
3. **The proof is the review tool run again** on a real epic, against numbers from epic 468. A part whose ADR is accepted and not built within the next sprint gets a status comment on crew#583 saying so.

## The decisions this needs

Asked in chat, one part at a time. "Replaces" is what the ADR removes.

| ID | Question | Options | Recommended | Replaces |
|---|---|---|---|---|
| D1 | The unit of context (review Q1) | (a) per-step assembly from the thread, with readers for the known gaps; (b) one record per epic, in its body, edited in place and read whole by every refinement and design step; comments are the audit trail of each change | (b). **Agreed 2026-10-10.** | `decided()` and `story_problem_evidence` (latest of a kind), the design note's "latest since the last split" for decisions, the conclusion written once and never updated |
| D2 | What is binding, where it lives, who may change it (Q2; crew#583, crew#584) | Every row carries a status. **Binding:** set by the Sponsor, the Goal, the Product Owner within the Goal, or the Architect; later roles build and judge to it. **Open:** settled by the Architect before the split, or marked the implementer's choice, which no gate checks. A binding row changes only by a row that replaces it, naming its author and date. During delivery, a story's declared tests may be amended by one focused Business Analyst pass on the first failure of merged tests it didn't declare (crew#584 option A), written to the record | as stated. **Agreed 2026-10-10.** | the "same merged tests fail twice, then the whole epic re-splits" rule; crew#584's prose rule |
| D3 | The order of steps (Q5) | ADR 0018 already decides it: the Architect settles open design questions before the split. | Build it (step A2); no new decision | the design note's `settled` list; the criteria check's rule about criteria that settle an open question |
| D4 | How each step gets code and history (Q3) | (a) a deterministic graph: imports, importers, definitions, and which merged tests cover what; (b) retrieval; (c) a scout model; (d) a tool loop | (a) first; then a read tool inside the call, which needs D6. A small scout model is kept as a re-ranker option, the two-stage pattern from information retrieval, for when the graph's candidates exceed the ceiling; built only if C3's measurement shows that gap. No retrieval index: it adds a system with nothing to test it against. **Agreed 2026-10-10.** | text matching in `tools/pinning.py`; `need_files` as a full re-prompt |
| D5 | What a form does with a bad answer (Q4) | (a) the refusal's reason goes into the retry; (b) smaller answers in steps; (c) fewer rules | (a), plus the criteria check one story per call. (b) exists for the second attempt (crew#276). The rules stay: each one was an incident, and with (a) each refusal teaches. **Agreed 2026-10-10, with D6.** | CrewAI's silent resend of identical messages |
| D6 | CrewAI as the harness (Q6) | (a) keep it; (b) the crew's own call layer: messages, schema, retry with the reason, tools, the record of each call; CrewAI's Agent, Task and Crew leave the 22 call sites in `crews/`; (c) drop CrewAI | (b). (c) is a later mechanical step. The crew's `flows/` are plain Python already; CrewAI is used only at the call sites. **Agreed 2026-10-10, staged:** the module and one site first, the criteria check, proven on a real epic; the other sites move only after that proof | ADR 0002's "Crews sit on the inside" line, by a changelog entry |
| D7 | Who owns the whole (C8) | Each step's context is one function returning named parts; `docs/reference/context.md` lists each step's parts and why each is there; a test reads that table and checks the code against it; the event log records each part's size | as stated. **Agreed 2026-10-10, on the Sponsor's condition:** no rule put on the context may be detrimental to how a development team develops and changes software; that is still the whole goal of the crew. The manifest is a drift check, not a gate: it fails a PR that changes the code or the table without the other, and nothing else. A rule that slows an ordinary change yields | 150 readers, each with its own rule |

## The standards applied (D1 and D2)

Nothing here is invented. The record is a **configuration baseline** under **configuration management**, the discipline standardised in ISO 10007, IEEE 828 and CMMI's Configuration Management process area, applied to decisions rather than to files. Its four functions are the validation checklist. The row attributes and the links from rows to stories to tests follow **requirements engineering** (ISO/IEC/IEEE 29148: requirement attributes and bidirectional traceability). The two kinds of row and the readiness rule follow **Example Mapping** (rules and questions) and Scrum's Definition of Ready. The line between what is fixed and what is left to the builder follows **Shape Up** (a pitch fixes the problem, appetite, core solution, rabbit holes and no-gos, and leaves the design latitude) and **commander's intent** (fix the intent, free the method), which crew#583 already cites.

| Ours | The standard's term | Where |
|---|---|---|
| The record's binding rows at the split | The baseline: the agreed items the stories are written against, changed only through change control | CM: CMMI SG 1 "Establish baselines"; IEEE 828 identification |
| Row ID, Status, Set by, Date, Replaces, Source | Configuration identification; requirement attributes (identifier, source, rationale, priority, dependency) | IEEE 828; ISO/IEC/IEEE 29148 "Requirements attributes" |
| A binding row changes only by a replacing row, by its authority | Change control: a change request, approved by the change authority (the CCB), recorded | CM: CMMI SG 2 "Track and control changes" |
| The Status column and the change comments (who, when, for which card) | Configuration status accounting: the current status of every item and its change history | CM: CMMI SP 3.1 "Establish CM records" |
| Gates judge against binding rows; the review tool checks the record against what was decided | Configuration audit: functional (the product matches the baseline) and physical (the record is complete) | CM: CMMI SP 3.2 "Perform configuration audits" |
| Row → story ("Follows R1") → test (C rows, `criteria_tests`) | Bidirectional traceability; a requirements traceability matrix | ISO/IEC/IEEE 29148 |
| R rows and Q rows | Example Mapping's blue rule cards and red question cards; a table full of red cards means the story isn't understood yet | Example Mapping (Wynne, 2015) |
| No Architect question open at split time | Definition of Ready | Scrum |
| "Implementer's choice" rows | Shape Up's latitude for the people doing the work; commander's intent's "free the method" | Shape Up, "Write the pitch"; mission command |
| The tests a change breaks, from the coverage map (D4) | Regression test selection, also called test impact analysis | Rothermel and Harrold; pytest-testmon, Ekstazi |
| Imports, importers and definitions (D4) | A dependency graph from static analysis | the crew's `imported_files` and `importers_section` already |
| A file read inside the call (D4) | Tool use, also called function calling | the OpenAI-compatible API LiteLLM serves |
| A small model ordering the graph's candidates (D4, option) | Two-stage retrieval: a cheap first stage, then a re-ranker | information retrieval |
| A refused answer retried with its validation error (D5, D6) | Structured output with schema validation and re-ask on error | the Instructor library's pattern; OpenAI-style `response_format` with a JSON schema |

**The validation checklist.** Each line is checked by a test in the step named, and by the proof.

| CM function | The record must | Checked by |
|---|---|---|
| Identification | Give every row a unique ID and its attributes; refuse one without them | A1's schema and `tests/test_settle.py` |
| Baseline | Fix the set of binding rows at the split; every row reaches a story or is marked not for stories | the existing coverage check in `flows/conclusion.py` (`problems`), kept in A1 |
| Change control | Never delete a binding row; accept a replacement only from its authority (the Sponsor any row, the Product Owner its own calls, the Architect design rows, the Business Analyst test rows); post one change comment per edit | A1 and A5 unit tests on `flows/record.py` |
| Status accounting | Show every item's current status and history; every Product Owner answer and Sponsor reply is a row | A3's tests; E1: "answers that never became rows" is 0 |
| Audit | The gates judge a story against the binding rows it cites; the review tool finds no decision outside the record | the criteria check, review and QA (unchanged); E1 |
| Traceability | Every C row names the story that declares it; every story's cited rows exist | A4's tests; `tests/test_split_conclusion.py` |
| Readiness | No row open for the Architect remains at split time | A2's done-when |

What I could not verify: the exact attribute list in 29148's "Requirements attributes" clause, which is paywalled. The attributes named above (identifier, source, rationale, priority, dependency) are the ones its public summaries and templates agree on.

## What each step reads after Part A

The review's binding layer, as it will be. ● shown, – not shown.

| Step | The Goal | Project log | The record (all rows) | Rows a story cites | Code |
|---|---|---|---|---|---|
| Panel (4 roles) | ● | ● | writes nothing; its notes feed the settle | – | record summary |
| Product Owner, settling | ● | ● | writes it | – | – |
| Architect, settling open questions | ● | ● | ● | – | map + the files each question names |
| Product Owner, answering a story problem | ● | ● | ● (its own earlier answers included) | – | – |
| Split | – | ● | ● | – | map + the files the epic names + covering tests |
| Criteria check, per story | ● | ● | ● | – | the files the story names |
| Design note | – | ● | ● | – | map + the files the stories name |
| Developer, reviewer, QA | – | – | – | ● binding rows and the story's test rows | graph selection, read tool |
| Retro | – | – | the record's change comments | – | – |

The Sponsor's words reach the builders as binding rows whose source is the Sponsor, written by the settle and checked by the proof. ADR 0015's statement that the split sees the Sponsor's decisions becomes true by construction, and its text is edited to say so.

## The record

The epic body keeps the approved text untouched, then one section the code owns (today `## Refinement conclusion`, written by `flows/settle.py`). Its tables become the record:

| Table | Rows | Columns | Written by |
|---|---|---|---|
| Decisions | R*n* | ID, Status (binding), Context, Decision, Consequences, Set by, Date, Replaces | the settle; the Architect's settle; the Product Owner's answers; the Sponsor's replies, entered by the Product Owner step |
| Open | Q*n* | ID, Question, Impact, Settled by (Architect before the split, or implementer) | the settle. A Q settled becomes an R whose Replaces names it |
| Tests this epic changes | C*n* | ID, Test (`path::name`), What it asserts after, Declared by (story, split) | the split, from the coverage map (C2); a story problem; the delivery pass of D2 |
| For infra, Dismissed | I*n*, N*n* | as today | the settle |

Rules the code enforces, as `settle.py` enforces the conclusion's today: cells held short by schema; a binding row is never deleted, only replaced; every edit re-reads the body first and refuses if it changed; every edit posts one marked comment saying which rows changed, by whom, for which card. Old conclusions are parsed as they are; nothing is re-settled.

## Parts and how they depend

```
A  the record (D1, D2, D3, D7) ──► C  the graph (D4) ──► C4  the read tool
                               └─► D  the calls (D5, D6) ─┘
E  the proof: after A, C1–C3 and D1–D3
```

A comes first: everything else reads the record. C and D are independent of each other and can run in parallel. C4 needs D1. E is one real epic through the whole chain.

## Build steps

Each step is one PR from its own worktree, never stacked. Anything that calls a model or GitHub runs once for real, through LiteLLM, before its PR. Nothing is pulled while a tick runs. Each step's manifest entry goes into `docs/reference/context.md` with it (D7).

### Part A: the record

| Step | What | Files | Done when |
|---|---|---|---|
| A0 | **Replay test for D1**, before building: epic 468's 21:04 split and 21:25 design note again through LiteLLM, with one current-state block (every decision so far, as rows) in place of the latest answer | `experiments/record-468/`, from `~/infra-results/context-review/epic-468/calls.jsonl` | The field names survive into the split's criteria and the design note. If they don't, D1 is questioned before anything is built |
| A1 | **The record's form:** status, set-by, date and replaces columns; Q rows name who settles them; a `flows/record.py` that parses, renders and edits it with the re-read check; the old form still parsed | `flows/settle.py`, `crews/settle_crew.py`, `flows/conclusion.py` (its reading moves to `flows/record.py`), `flows/decision_log.py` (reads the new cells) | `tests/test_settle.py`, `tests/test_split_conclusion.py` and `tests/test_decision_log.py` pass on both forms; one real settle on the next approved epic |
| A2 | **The Architect settles before the split** (ADR 0018): a pass after the Product Owner's settle answers every Q marked for the Architect, each becoming a binding row that replaces it; the design note after the split no longer settles anything, and decides module ownership and file layout only | `flows/settle.py`, `crews/settle_crew.py` (the Architect's form), `flows/design_notes.py`, `crews/design_note_crew.py` (`settled` removed), `flows/board_flow.py` | No Q marked for the Architect remains at split time; one real run; ADR 0018's status updated |
| A3 | **Answers edit the record.** The Product Owner's answer to a story problem, and the Sponsor's reply, are written as rows (added or replacing), with one change comment. The split, the criteria check, the design note and the Product Owner itself read the record, not comments | `flows/board_flow.py` (`product_step`, `refine_epics`, `story_problem_evidence`), `flows/design_notes.py` (`decided` removed), `crews/criteria_crew.py` (`decided` parameter removed), `crews/refinement_crew.py` | `grep -rn "PRODUCT_ANSWER_MARKER" src/` finds only the writer; `tests/test_board_flow.py` and `tests/test_criteria_rows.py` cover the row writes; one real answer on a returned story |
| A4 | **Tests the epic changes are rows.** A split's Existing-tests lines and a story problem's pinned failures become C rows; a re-split is shown them and must carry each into a story or mark it dropped with why, as `check_accounted` does for superseded stories | `flows/story_problem.py`, `crews/refinement_crew.py`, `flows/record.py` | A re-split that drops a C row silently is refused; `tests/test_resplit_accounts.py` |
| A5 | **Who may change a story's contract during delivery** (D2): on the first failure of merged tests a story didn't declare, one focused Business Analyst call amends its Existing-tests line or returns it, writing a C row either way | `flows/delivery.py` (where `pinned_failures` is read), a small `crews/` form | sprint-metrics#529's case ends in one call, not six attempts and a re-split |
| A6 | **The retro and the standup read the record's change comments,** not answers paired by time | `flows/retro.py`, `crews/retro_crew.py`, `flows/standup.py` | The retro's prompt holds no Product Owner answer under a card it isn't about |
| A7 | **Housekeeping:** the status comments Claude posted under the Sponsor's login on crew#280 and crew#521 get a marker, and `decisions.py` skips marked comments; ADR 0015 edited; the manifest doc and its test | `flows/decisions.py`, `docs/decisions/0015-*.md`, `docs/reference/context.md`, `tests/test_context_manifest.py` | `crew decisions sprint-metrics#462` no longer returns Claude's progress tables; the manifest test passes for the steps A touched |

### Part C: the graph

| Step | What | Files | Done when |
|---|---|---|---|
| C1 | **The coverage map:** after the sandbox runs a project's tests on the base branch, `coverage` with dynamic test contexts records which tests execute which files and definitions; stored per base commit under `var/coverage/<repo>/` | new `tools/coverage_map.py`; `tools/workspace.py`, where the sandbox runs the tests; `tools/regression.py` (`signatures_for_context` for definition bounds) | On sprint-metrics 1.2.0 the map names the three tests crew#584 found by hand for epic 468's keys (`test_schema_gen`, `test_schema`, `test_docs::test_drift_check`); a fixture-repo test |
| C2 | **The split's pinning tests come from the map:** the tests covering the files and definitions the epic and its C rows name, not text matching; each becomes a C row the stories declare as kept or changed | `tools/pinning.py` (text matching removed), `flows/board_flow.py` (`RepoContext.pinning_for`), `crews/refinement_crew.py` | For sprint-metrics#507 and #529, all broken tests are named at split time (today 0 of 5 and 1 of 3); crew#584 closed as built here |
| C3 | **Selection from the graph** for the Developer and the reviewer: the files the work names, their importers, and the tests covering the changed definitions, ranked under the ceiling; everything else by name | `tools/repo_context.py` (`select_files`, `focused_context`), `tools/review_evidence.py` (`importers_section` reused) | A replay of epic 468's Developer first attempts with the new selection: asks per story and prompt size, against 13 of 13 and the sizes in the review |
| C4 | **A read tool** (after D1): the Developer and the split read a file inside the call, under the same ceiling, instead of returning `need_files` and being re-prompted; `need_files` stays as the last resort | `crew_org/calls.py`, `crews/delivery_crew.py`, `crews/refinement_crew.py`, `tools/repo_context.py` | A real call through LiteLLM reads a file and returns the structured answer; first answers that only ask fall to zero on the proof epic |

### Part D: the calls

| Step | What | Files | Done when |
|---|---|---|---|
| D1a | **The crew's own call layer, on one site first:** one module builds the messages from `agents.yaml`'s role and the step's parts, calls LiteLLM with the schema, validates, and on a refusal re-asks with the error appended; an empty answer is a refusal with that reason; every attempt is recorded with its reason and each part's size; the record's model-call events come from it. The criteria check is the first site | new `crew_org/calls.py`; `crews/criteria_crew.py`; `events.py` (`llm.refused`) | The criteria check runs for real on the next epic through the module: its answers compare with the sprint before, and each refusal's reason is in the event log; ADR 0002 changelog entry. If the module itself fails here, the fallback is CrewAI's task guardrails carrying the reason (ADR 0025), not a redesign |
| D1b | **The other 21 sites move,** a few per PR, with their prompts unchanged except the CrewAI framing; CrewAI leaves `pyproject.toml` with the last one | `agents.py`; `llm.py` (`_recording` removed); each `crews/*.py` | Only after D1a's proof. The same prompt and schema through LiteLLM once for real per site |
| D2 | **Tools in the call layer:** a read tool, called in rounds, then the final structured answer with the schema | `crew_org/calls.py` | After D1a's proof. One real call with a tool round and a structured answer, through LiteLLM with the stack's tool-call parser |
| D3 | **The criteria check one story per call,** shown the record, the story, the planned criteria and the files the story names | `flows/board_flow.py` (`refine_epics`), `crews/criteria_crew.py`, `flows/criteria_check.py` | Its thinking stays under the 16k edge on the proof epic; no empty answers |
| D4 | **Standing rules per role:** the escalation policy out of the Developer's and the Architect's, branch conventions out of the Developer's; the `text_edits` description made true | `src/crew_org/config/agents.yaml`, `crews/delivery_crew.py` | A test that each role's system text holds only its sections |

### Part E: the proof

| Step | What | Done when |
|---|---|---|
| E1 | The next approved epic with open questions runs through the whole chain in ticks | The success test below, read with `scripts/context_review.py` on that epic |
| E2 | crew#583 closed as built, crew#584 closed by C2, crew#190 and crew#457 closed as superseded or reshaped, with a status comment each | Each issue says which step did what |

## The success test (E1)

| Check | Epic 468 | Success |
|---|---|---|
| Losses traced that were missing from the prompt that needed them | 8 | 0 |
| Product Owner answers that never became rows | 8 of 8 | 0 |
| Open questions still open at split time, not marked the implementer's choice | Q1, after two answers settled it | 0 |
| First answers that only asked for files | 13 of 13 stories | 0 re-prompts; reads inside the call |
| Refused answers whose retry saw the reason | 0 of 97 | all |
| Empty answers | 4, at 95–181k prompt tokens | 0; no prompt over 60k tokens except by the ceiling, and that named |
| Merged tests broken that the split didn't declare | 5 (sprint-metrics#507), 3 (sprint-metrics#529) | 0 |
| Readers of "the latest comment of a kind" outside `flows/record.py` | `decided`, `story_problem_evidence`, `note_for`, `_reply`, the retro's pairing | 0 |

## What to reuse

| Piece | Where | Use |
|---|---|---|
| The review tool and its joins | mqucifer/infra `scripts/context_review.py` | A0, C3 and E1 |
| The optimistic edit of the epic body | `flows/settle.py`, `settle_epic` | Every record edit (A1) |
| Imports and importers | `tools/repo_context.py` `imported_files`; `tools/review_evidence.py` `importers_section` | The graph (C1, C3) |
| Definition bounds | `tools/regression.py` `signatures_for_context` | Mapping covered lines to definitions (C1) |
| The retry loop with the reason recorded | `flows/settle.py` `propose` | The pattern for `calls.py` (D1) |
| The stepped second attempt | `crews/stepped.py` | Unchanged; it moves to `calls.py` with the rest |

## Open questions

| ID | Question | Settled by |
|---|---|---|
| Q1 | D1 to D7 above, each a chat decision and an ADR | Sponsor |
| Q2 | Sequence against the Goal chain (crew#280, crew#521, crew-presentation): Part A and D1 before Sprint 21 and the rest after it, or all of it first | Sponsor |
| Q3 | Whether a Sponsor reply on an epic is entered as a row by the Product Owner step (recommended) or quoted as it is. **Decided 2026-10-10 (Sponsor): "if I respond to an epic it should be recorded."** The Product Owner step enters the reply as a binding row whose source is the Sponsor and whose Decision cell quotes the Sponsor's words; step A3 builds it, and E1 counts a reply that never became a row as a loss | Decided |
| Q4 | The coverage map for projects that aren't Python (crew#376 profiles) | Deferred until one exists |
