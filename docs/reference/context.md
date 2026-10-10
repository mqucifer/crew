# What each step is shown

The manifest of each step's context (crew#583, ADR 0023): its parts, in the order the step reads them, where each comes from, and why it is there. A step's context is changed here and in the code together. A test reads the tables and checks the code against them (step A7 of `docs/plans/context-record-build.md`); until then, a pull request that changes a step's context updates its table.

This is a drift check, not a gate (the Sponsor, 2026-10-10): no rule here may slow how a team changes software. A new fact goes into the epic's record and into a table here, never into a new reader.

Steps not yet listed are added as the plan's steps reach them.

## Architect, settling the design questions (before the split)

`settle_crew.describe_design`, called by `settle.settle_design_questions` (ADR 0018).

| Part | From | Why |
|---|---|---|
| The Goal | the Goal's issue, `panel.gather` | Each answer stays within it |
| The project's record | `.crew/project.yaml`, `panel.gather` | What the project is for and how it is built |
| The project's decision log | the project's `docs/decisions/`, `panel.gather` | Decisions that hold for every epic |
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
