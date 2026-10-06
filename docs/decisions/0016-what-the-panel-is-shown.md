# 16. What the refinement panel is shown

- **Date:** 2026-10-05
- **Status:** Accepted (built by crew#440 and crew#468); its last bullet superseded by 0018

## Context

The panel test and its replay missed the same findings, and the cause was the
context, not the roles. Decisions that apply to a whole project (the counting
rule, the telemetry standard) name no Goal, so a Goal-based rule never found
them. Removing one source from the first test took a finding from three runs
of three to none. The Sponsor's reading, 2026-10-05: "It's all about giving the
right context to begin with."

## Decision

Sponsor, 2026-10-05.

- **The siblings shown:** a Goal's other epics, open ones and delivered ones. A
  delivered epic says what is already built, and the panel needs that. An
  epic closed as not planned was set aside and isn't shown. If the context
  grows too large, delivered epics become a changelog-style summary of what
  was completed; not before.
- **A project keeps a decision log** in its repository, in the form of these
  ADRs. The panel and the split are shown its Accepted entries with the Goal's
  decisions.
- **The Product Owner writes it**, from the Goal, the project's record and the
  Sponsor's earlier words. The entry lands like any crew change in the project,
  with no Sponsor step.
- **When the Product Owner can't answer** from those sources, the project's
  record is missing something. It asks the Sponsor one question, records the
  answer as an entry, and proposes the record's update. Agents don't change
  the record.

## Consequences

- The Sponsor is asked only where the written sources run out, and each such
  question is also a gap found in the record.
- A decision the Product Owner records from the Sponsor's words is the
  Sponsor's decision, entered without their review. If it is wrong, a later
  entry supersedes it, as with these ADRs.
- A project's log can grow past what fits. Its size is counted in the panel's
  event record so growth is seen first (crew#468).
- ADR 0015 still decides what counts as the Sponsor's words for a Goal. This
  adds the project-wide decisions that no Goal names.
