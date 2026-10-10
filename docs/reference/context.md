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
| Decided for this epic | `design_notes.decided`, the latest Product Owner answer | Until step A3, which removes it: answers become rows of the record |
| Its stories | the epic's open sub-issues | What the note gives a direction to |
| The code as it stands | `repository_context` | Which module owns what, where files go |
| The last refusal | `_write_one` | Why the previous note was refused |

The questions the record leaves open are no longer part of this step: the Architect settles them before the split.
