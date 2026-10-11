# What each step is shown

The manifest of each step's context (crew#583, ADR 0023): its parts, in the order the step reads them, where each comes from, and why it is there. A step's context is changed here and in the code together. `tests/test_context_manifest.py` reads the tables and checks that every function, class and method they name exists in the code, so a pull request that renames or removes one updates its table too.

This is a drift check, not a gate (the Sponsor, 2026-10-10): no rule here may slow how a team changes software. A new fact goes into the epic's record and into a table here, never into a new reader.

Every step the plan's table "What each step reads after Part A" names is listed. A step added later gets its table in the same pull request.

## Architect, settling the design questions (before the split)

`settle_crew.describe_design`, called by `settle.settle_design_questions` (ADR 0018).

| Part | From | Why |
|---|---|---|
| The Goal | the Goal's issue, `panel.gather` | Each answer stays within it |
| The project's record | `.crew/project.yaml`, `panel.gather` | What the project is for and how it is built |
| The project's decision log | the project's `docs/decisions/`, `panel.gather` | Decisions that hold for every epic |
| The other epics under this Goal, with their records | `panel.siblings_of` | A question another epic already settled is answered the same way, citing its row (crew#611) |
| The epic | its approved text | What the questions are about |
| The epic's record, whole | `record.split` | The binding rows each answer must not contradict |
| The code as it stands | `RepoContext.focused_for`, on the questions' text | The map, and the files the questions name |
| The questions left for the Architect | `record.architect_questions` | What to settle, by ID |
| The last refusal | `propose_design` | Why the previous answer was refused |

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

`refinement_crew.split_epic`, called by `board_flow.refine_epics` (#14, crew#231, crew#440).

| Part | From | Why |
|---|---|---|
| The repository | `RepoContext.focused_for`: the map, and the files the epic names | Criteria written against what the code has |
| Tests that pin behaviour this epic touches | `RepoContext.pinning_for`, from the coverage map | The merged tests that run, or by their own code read or call, what the epic and its record name and check a whole shape, and any the record names (C2, C5); a test that spells out a named file's table first |
| The stories this split replaces | the epic's superseded sub-issues | Each is accounted for (#248) |
| What the project has delivered | `Delivered.render` | Not written again |
| Stories other epics plan | `planned_elsewhere` | Not written again, or built on |
| The project's decision log | the project's `docs/decisions/` | Decisions that hold for every epic |
| The other epics under this Goal, with their records | `panel.siblings_of` | A story doesn't contradict a binding row of theirs (crew#611) |
| The epic's record, whole | `record.split` | Rows each story follows, open questions no story settles, and the merged tests (C rows) each story carries or drops |
| Why it was sent back | `story_problem_evidence` and the Sponsor's notes | The problem to answer; its answer is in the record |
| The epic | its approved text | What to split |

## Business Analyst, ruling on a story's contract in delivery

`contract_crew.rule_on_contract`, called by `delivery.rule_on_contract` (crew#584, step A5).

| Part | From | Why |
|---|---|---|
| The story | its issue | Its criteria and Existing tests line |
| The rows it follows | `story_rows` | The decisions that may require the change |
| The merged tests it broke and doesn't declare | `story_problem.pinned_failures`, less what it declares | What to rule on |

## The refinement panel (four members)

`panel_crew.describe`, called by `panel_crew.run_panel` (crew#440).

| Part | From | Why |
|---|---|---|
| The Goal | the Goal's issue, `panel.gather` | What the epic is for |
| The project's record | `.crew/project.yaml` | What the project is for and how it is built |
| The Sponsor's decisions for the Goal | `decisions.collect_decisions` | What the Sponsor has already decided (ADR 0015) |
| The project's decision log | `project_log.read_log` | Decisions that hold for every epic |
| The other epics under this Goal, with their records | `panel.siblings_of` | Where this epic meets its siblings, and what they already decided; a superseded one only by the binding rows it carries (crew#611) |
| The epic | its approved text | What each member reads, from its own role |

## Product Owner, settling the panel's notes

`settle_crew.describe`, called by `settle.propose` (crew#440).

| Part | From | Why |
|---|---|---|
| The Goal, the project's record, the Sponsor's decisions, the decision log, the other epics | `panel.gather` | What settles a note, or what a call must stay within (ADR 0018) |
| The epic | its approved text | What the notes are about |
| The panel's notes | `settle_crew.numbered` | What to settle, by number |
| The Sponsor's answer | `settle._reply` | When the Product Owner asked |
| The last refusal | `settle.propose` | Why the previous conclusion was refused |

## QA, the criteria check

`criteria_crew.check_criteria`, called one story per call by `criteria_check.check_each` (#428, crew#583 D3).

| Part | From | Why |
|---|---|---|
| The project's code | `RepoContext.focused_for`, on the story's own text | What the code defines and merged tests pin |
| The split's other stories | `criteria_check.render_stories` | Criteria that can't both pass |
| Stories other epics plan | `criteria_check.planned_criteria` | Criteria that can't both pass |
| The Goal and the project's decision log | the Goal's issue, `project_log.read_log` | A criterion restating a decision isn't deciding a question |
| The epic's record | `record.split` | Rows a criterion must not contradict, and questions it must not settle |
| The story | `criteria_check.render_stories` | What is checked |

## Developer, delivering a story

`delivery_crew.implement_story`, called by `delivery.deliver_story`.

| Part | From | Why |
|---|---|---|
| The story | its issue | What to build, and its Existing tests line |
| The rows it follows | `conclusion.story_rows` | The binding decisions it builds to, and any row that replaced one |
| The epic's notes | `presentation_notes.story_notes` | The Architect's design note and the UX Designer's presentation note |
| What the gates said before | `delivery.prior_context` | A rework answers the verdict it is about |
| The repository | `repo_context.focused_context`, with `delivery.coverage_for` | The map, the files the work names and their imports, and by name what imports them and the merged tests that run them (C3) |
| The last failure | `deliver_story`'s feedback | What to repair, and why the previous answer was refused |

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
| What the board says, the standups, the escalation ledger | the board, the event log | What happened in the sprint |
| Why work didn't land first time | the event log | The causes, by fingerprint |
| Stories that went back, and what was decided for each | `loops.from_comments` | Each answer paired with its own story's return, from the record (step A6) |
| Known and closed crew issues | the crew repository | What's already filed |

