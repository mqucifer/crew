# 16. What the refinement panel is shown

- **Date:** 2026-10-05
- **Status:** Accepted (built by crew#440 and crew#468)

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
- **When no source answers a note,** the Product Owner may decide it within
  the Goal, recorded as its call, and the Architect settles design questions;
  ADR 0018 says how. The Sponsor is asked only when the Product Owner can't
  tell which way the Goal points. Agents don't change the project's record.

## Consequences

- The Sponsor is asked only when the Goal itself is unclear, and each such
  question is also a gap found in the record.
- A decision the Product Owner records from the Sponsor's words is the
  Sponsor's decision, entered without their review. If it is wrong, a later
  entry replaces it.
- A project's log can grow past what fits. Its size is counted in the panel's
  event record so growth is seen first (crew#468).
- ADR 0015 still decides what counts as the Sponsor's words for a Goal. This
  adds the project-wide decisions that no Goal names.

## Changelog

- **2026-10-05:** "When the Product Owner can't answer" changed from asking the
  Sponsor whenever the written sources ran out to the Product Owner's leeway in
  ADR 0018 (Sponsor).
- **2026-10-07:** that change folded into this text, now that ADRs are edited in
  place (Sponsor).
