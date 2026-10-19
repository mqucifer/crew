# What each step is shown

The manifest of each step's context (crew#583, ADR 0023): its parts, in the order the step reads them, where each comes from, and why it is there. A step's context is changed here and in the code together. `tests/test_context_manifest.py` reads the tables and checks that every function, class and method they name exists in the code, so a pull request that renames or removes one updates its table too.

This is a drift check, not a gate (the Sponsor, 2026-10-10): no rule here may slow how a team changes software. A new fact goes into the epic's record and into a table here, never into a new reader.

Every step the plan's table "What each step reads after Part A" names is listed. A step added later gets its table in the same pull request.

## Architect, settling the design questions (before the split)

`settle_crew.design_opening`, the start of the session `settle_crew.settle_design` runs, called by `settle.settle_design_questions` (ADR 0018, crew#807).

| Part | From | Why |
|---|---|---|
| Its step | `settle_crew.design_opening` | What it settles, and that the stories are the Business Analyst's |
| The Goal | the Goal's issue, `panel.gather` | Each answer stays within it |
| The project's decision log | the project's `docs/decisions/`, `panel.gather` | Decisions that hold for every epic |
| The other epics under this Goal, with their records | `panel.siblings_of` | A question another epic already settled is answered the same way, citing its row (crew#611) |
| The epic | its approved text | What the questions are about |
| The epic's record, whole | `record.split` | The binding rows each answer must not contradict |
| The questions left for the Architect | `record.architect_questions` | What to settle, by ID |

Then the four parts every refinement session's start ends with (below). The code it was shown, focused on the questions, it reads with the tools. `done` refuses a cut cell or a question left out, in the session.

## Architect, the design note (after the split)

`design_note_crew.write_note`, called by `design_notes.write_notes` (crew#155).

| Part | From | Why |
|---|---|---|
| The project's record | `.crew/project.yaml` | What the project is for and how it is built |
| The epic, with its record | the epic's body | The settled rows the note builds on, never re-decides |
| Its stories | the epic's open sub-issues | What the note gives a direction to |
| The code as it stands | `repository_context` | Which module owns what, where files go |
| The last refusal | `_write_one` | Why the previous note was refused |

The questions the record leaves open are no longer part of this step: the Architect settles them before the split. Nor is the latest Product Owner answer, read from the comments: every answer is a row of the record (step A3).

## Product Owner, answering a story problem

`refinement_crew.answer_story_problem`, called by `board_flow.product_step` (#189).

| Part | From | Why |
|---|---|---|
| The project's record | `.crew/project.yaml` | What the project is for |
| What the project has delivered | `Delivered.render` | A choice that contradicts delivered work is caught |
| The Goal | the Goal's issue | The answer stays within it |
| The epic, with its record | the epic's body | Every decision so far, its own earlier answers included (ADR 0023) |
| Why it came back | `story_problem_evidence`, the latest story problem | The question to answer |
| The last refusal | `product_step` | Why the record refused the previous rows |

## Product Owner, placing the Sponsor's words

`refinement_crew.place_sponsor_words`, called by `board_flow.record_sponsor_words` (ADR 0023).

| Part | From | Why |
|---|---|---|
| The epic, with its record | the epic's body | Which row or question the words change |
| The Sponsor's words | the Sponsor's comment on the epic | Quoted as the row's decision, unchanged |
| The last refusal | `record_sponsor_words` | Why the record refused the previous placing |

## Business Analyst, the split

`refinement_crew.split_opening`, the start of the session `refinement_crew.split_epic` runs, called by `board_flow.refine_epics` (#14, crew#440, crew#807).

| Part | From | Why |
|---|---|---|
| Its step | `refinement_crew.split_opening` | How stories are written here, and that the epic's scope is the Product Owner's |
| The epic | its approved text | What to split |
| The epic's record, whole | `record.split` | Rows each story follows, open questions no story settles, and the merged tests (C rows) each story carries or drops |
| The other epics under this Goal, with their records | `panel.siblings_of` | A story doesn't contradict a binding row of theirs (crew#611) |
| The project's decision log | the project's `docs/decisions/` | Decisions that hold for every epic |
| Stories other epics plan | `planned_elsewhere` | Not written again, or built on |
| The stories this split replaces | the epic's superseded sub-issues | Each is accounted for (#248) |
| Why it was sent back | `story_problem_evidence` and the Sponsor's notes | The problem to answer; its answer is in the record |
| What the project has delivered, as a note | `steps.delivered_section`, from `Delivered.render` | Searched or viewed, not shown: 205k characters for sprint-metrics |

Then the four parts every refinement session's start ends with (below). The repository and the merged tests that pin what the epic names, which it was shown, it reads with `view`, `search` and `tests_for`. `done` refuses a split that names a story the project hasn't delivered or planned, drops a superseded one, shows a field its package doesn't carry, or leaves a row unfollowed (`conclusion.problems`), and the session answers it.

## Business Analyst, ruling on a story's contract in delivery

`contract_crew.rule_on_contract`, called by `delivery.rule_on_contract` from the gates' route (`session_end.route`; crew#584, ADR 0032 decision 6).

| Part | From | Why |
|---|---|---|
| The story | its issue | Its criteria and Existing tests line |
| The rows it follows | `story_rows` | The decisions that may require the change |
| The merged tests `done` changed where the story says they stay, with `done`'s why | `gates.changed_tests` | What to rule on |

## The refinement panel (four members)

`panel_crew.opening`, the start of each member's session, which `panel_crew.review` runs; `panel_crew.run_panel` runs the four at once, and records each one's model time while they run (crew#440, crew#807).

| Part | From | Why |
|---|---|---|
| Its step | `panel_crew.opening`, with the member's `FOCUS` | What it owns, its scope, and that "nothing to add" is an answer |
| The Goal | the Goal's issue, `panel.gather` | What the epic is for |
| The Sponsor's decisions for the Goal | `decisions.collect_decisions` | What the Sponsor has already decided (ADR 0015); left out when there are none |
| The project's decision log | `project_log.read_log` | Decisions that hold for every epic |
| The other epics under this Goal, with their records | `panel.siblings_of` | Where this epic meets its siblings, and what they already decided; a superseded one only by the binding rows it carries (crew#611) |
| The epic | its approved text | What each member reads, from its own role |

Then the four parts every refinement session's start ends with (below).

## Product Owner, settling the panel's notes

`settle_crew.opening`, the start of the session `settle_crew.settle` runs, called by `settle.propose` (crew#440, crew#807).

| Part | From | Why |
|---|---|---|
| Its step | `settle_crew.opening` | The four ways a note is settled, and when to ask the Sponsor |
| The Goal, the Sponsor's decisions, the decision log, the other epics | `panel.gather` | What settles a note, or what a call must stay within (ADR 0018) |
| The epic | its approved text | What the notes are about |
| The panel's notes | `settle_crew.numbered` | What to settle, by number |
| The Sponsor's answer | `settle._reply` | When the Product Owner asked |

Then the four parts every refinement session's start ends with (below). `done` refuses a cut cell, an own call the Goal's words don't hold, a design question decided as a product call, or a note left unanswered, in the session.

## QA, the criteria check

`criteria_crew.opening`, the start of the session `criteria_crew.check_criteria` runs, one session per story, all at once, called by `criteria_check.check_each` (#428, crew#583 D3, crew#807).

| Part | From | Why |
|---|---|---|
| Its step | `criteria_crew.opening` | What counts as a conflict, and what doesn't |
| The Goal and the project's decision log | the Goal's issue, `project_log.read_log` | A criterion restating a decision isn't deciding a question |
| The epic's record | `record.split` | Rows a criterion must not contradict, and questions it must not settle |
| The split's other stories | `criteria_check.render_stories` | Criteria that can't both pass |
| Stories other epics plan | `criteria_check.planned_criteria` | Criteria that can't both pass |
| The story | `criteria_check.render_stories` | What is checked |

Then the four parts every refinement session's start ends with (below). The code it was shown, focused on the story, it reads with the tools.

## Business Analyst, repairing criteria the check refused

`refinement_crew.repair_opening`, the start of the session `refinement_crew.repair_criteria` runs, called by `board_flow.refine_epics` (crew#440, crew#807).

| Part | From | Why |
|---|---|---|
| Its step | `refinement_crew.repair_opening` | Change only what the conflicts need |
| The epic | its approved text | What the stories are for |
| The epic's record | `record.split` | Rows a repaired criterion must not contradict |
| The project's decision log | `project_log.read_log` | Decisions that hold for every epic |
| The split's other stories | `criteria_check.render_stories` | They stay as they are |
| The stories to repair | `criteria_check.render_stories` | What is rewritten |
| Why their criteria can't all pass | `criteria_check.described` | What the repair answers |

Then the four parts every refinement session's start ends with (below).

## Product Owner, decomposing a Goal

`refinement_crew.decompose_opening`, the start of the session `refinement_crew.propose_epics` or `refinement_crew.propose_missing_epics` runs, called by `board_flow.tick` (crew#807).

| Part | From | Why |
|---|---|---|
| Its step | `refinement_crew.decompose_opening` | How epics are drawn, and that stories and design are others' |
| The Goal | the Goal's issue | What the epics deliver |
| What the Goal still lacks | `board_flow.lacking_notes` | Only for a Goal under way that lacks an epic (crew#429) |
| Why it was sent back | the Sponsor's notes, `board_flow.rework_gate` | On a rework |
| What the project has delivered, as a note | `steps.delivered_section`, from `Delivered.render` | Searched or viewed, not shown |

Then the four parts every refinement session's start ends with (below). The repository, which it was shown whole (572k characters for sprint-metrics#650), it reads with the tools.

## UX Designer, the presentation note

`presentation_note_crew.opening`, the start of the session `presentation_note_crew.write_note` runs, called by `presentation_notes.write_notes` (#377, crew#807).

| Part | From | Why |
|---|---|---|
| Its step | `presentation_note_crew.opening` | What the note holds, and that a criterion already met goes in `already_holds` |
| The Goal | the Goal's issue | Who the output is for |
| The other epics under this Goal, with their records | `panel.siblings_of` | crew-presentation#124's "0.5 days" came seconds after its sibling's note asked for hours and minutes |
| The epic, with its record | `panel._with_record` | What changes for the reader |
| Its stories | the epic's open sub-issues | What the criteria are added to |

Then the four parts every refinement session's start ends with (below). `done` refuses a note that leaves out a story or names one the epic doesn't have.

## What every refinement session's start ends with

`steps.start`, after the step's own parts (crew#807).

| Part | From | Why |
|---|---|---|
| The project | `steps.project_section`, from the project's record | The whole project's record, said not to be the step's scope; how it's released and what its CI runs stay in the file |
| The map | `start.project_map` | Every file, its size, and a Python file's top-level names |
| Hints | `start.hints`, from the graph and the coverage map, on the step's own text | Paths only, each with why it was picked |
| How the session works | `steps.rules` | Its read tools, that facts about the code are checked and decisions are binding, `done`, and the stops, each told once |

Each step reads one checkout of the default branch, `Workspace.at_main`, through `ReadTools`: `view`, `search` (the notes too), `find`, `tests_for` and `git`, which reads. Its `done` is its answer form (`steps.done_spec`), and each turn is one call of the conversation (`calls.converse`, ADR 0025).

## Developer, delivering a story

`start.start`, the start of a session `session_delivery.deliver_by_session` runs (ADR 0032, decision 4). The rest the session looks up with its tools.

| Part | From | Why |
|---|---|---|
| The story | its issue, with `session_delivery.story_section` | What to build, and its Existing tests line |
| The rows it follows | `conclusion.story_rows` | The binding decisions it builds to, and any row that replaced one |
| The epic's notes | `presentation_notes.story_notes`, cut by `for_story` | The Architect's design note and the UX Designer's presentation note: the shared parts whole, this story's direction whole, the others' first sentence (L2) |
| The tests the story declares | `start.declared_tests` | Always shown, as merged at the base commit |
| How the project works | `start.how_it_works`, from the project's record | Its checks, its parts and its conventions |
| The map | `start.project_map` | Every file, its size, and a Python file's top-level names |
| Hints | `start.hints`, from the graph and `delivery.coverage_for` | Paths only, each with why it was picked |
| Instead of the hints, on a rework or a resume | `session_delivery.returned_start`, `session_delivery.resumed_start`, `session_end.rework_start` | Review's or the gates' findings and the last `done`, or the last checkpoint (P2, P3) |
| The rules for its tools and its `done` | `start.RULES` | Each told once |

Each turn is one call of the conversation (`calls.converse`, ADR 0025). A tool's reply stays in the history whole, until one compaction near 125k tokens (ADR 0032).

## Code Reviewer, judging a diff

`review_crew.review_diff`, called by the review phase.

| Part | From | Why |
|---|---|---|
| The story and the rows it follows | its issue, `conclusion.story_rows` | What the change is judged against |
| The design note | `presentation_notes.story_notes` | The direction the change follows |
| The code the diff imports, and who imports what it changes | `review_evidence.importers_section` | What the change can break |
| The diff | the pull request | What is judged |

## QA, accepting a story

`qa_crew.verify_story`, called by the acceptance phase.

| Part | From | Why |
|---|---|---|
| The story and the rows it follows | its issue, `conclusion.story_rows` | Each criterion is proven or not |
| The tests that were written | the pull request | Which test proves which criterion |
| What running the suite produced | the sandbox | Proof, not a claim |
| The docs the change edits | the pull request | A doc criterion is judged by reading the doc |

## Scrum Master, the retro

`retro_crew.write_retro`, called by the sprint close.

| Part | From | Why |
|---|---|---|
| What the board says, and the standups | the board, the event log | What happened in the sprint |
| Why work didn't land first time | the event log | The causes, by fingerprint |
| Stories that went back, and what was decided for each | `loops.from_comments` | Each answer paired with its own story's return, from the record (step A6) |
| Known and closed crew issues | the crew repository | What's already filed |

