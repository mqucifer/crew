# 23. The epic's record is the crew's memory

- **Date:** 2026-10-10
- **Status:** Accepted (to be built by crew#583 as Part A of `docs/plans/context-record-build.md`)

## Context

The context review of 2026-10-10 (Sprints 17 to 20, with mqucifer/sprint-metrics#468
traced end to end) found that every loss traced was a fact missing from the prompt of
the role that lost it, and none was shown and then ignored. The cause was the crew's
memory. An epic's body is written once; after that everything is appended as comments
nobody edits, and each step rebuilds "what's decided" by picking one comment from that
thread by its own rule: the latest of a kind, the latest since the last split, the first
after a time. About 150 such readers exist, each added for an incident, and nobody owns
the whole. On epic 468 the Product Owner answered eight times, was never shown its own
answers, named the fields five ways, and the design note written after the split renamed
them. The conclusion was never updated, so a question stayed open after two answers had
settled it.

The same conclusion had been decided four times before (crew#190; crew#440 with
ADR 0016; ADR 0018; crew#583), and each time was carried out as one more reader, or
not built. The Sponsor's ask, 2026-10-10: this problem is not to be rediscovered.

## Decision

Sponsor, 2026-10-10 (D1, D2 and D7 of the plan).

- **One record per epic is the crew's memory.** It lives in the epic's body under its
  own heading, below the approved text, which is never touched. Every refinement and
  design step reads it whole. The Developer, the Code Reviewer and QA read the rows
  their story cites. Comments are the audit trail of each change, never what a step
  reads to learn a decision.
- **Every row has a status.** *Binding* rows are set by the Sponsor, the Goal's text,
  the Product Owner within the Goal (ADR 0018) or the Architect; later roles build and
  judge to them. *Open* rows name who settles them: the Architect before the split
  (ADR 0018), or the implementer, whose choice no gate checks. What the Goal is specific
  about is binding; what it leaves open is the crew's choice.
- **A binding row changes only by a row that replaces it,** naming its author and date,
  never by deletion. The Sponsor may replace any row, the Product Owner its own calls,
  the Architect design rows, and the Business Analyst the rows naming the merged tests a
  story changes. Each change posts one comment on the epic.
- **A Sponsor reply on an epic is recorded as a row** (Sponsor, 2026-10-10: "if I
  respond to an epic it should be recorded"). The Product Owner step enters it as a
  binding row whose source is the Sponsor and whose Decision cell quotes the Sponsor's
  words, so nothing is rewritten on the way in. A reply that never became a row is a
  loss in the proof.
- **The merged tests an epic changes are rows too,** declared by the split from the
  coverage map (ADR 0024) or by a story problem. During delivery, on the first failure
  of a merged test a story didn't declare, one focused Business Analyst pass amends the
  story's declaration or returns it, as a recorded change.
- **Every step's context is built in one place.** Each step has a manifest of its parts
  in `docs/reference/context.md`, a test checks the code against it, and each call's
  event records the parts and their sizes.
- **The condition on all of it:** no rule put on the crew's context may be detrimental
  to how a development team develops and changes software; that is the crew's whole
  goal. The manifest is a drift check, not a gate. A rule that slows an ordinary change
  yields.

**The standards.** The record is a configuration baseline under configuration
management (ISO 10007, IEEE 828, CMMI's Configuration Management process area), applied
to decisions: identification, change control, status accounting and audit are its
validation checklist, in the plan. Row attributes and the links from rows to stories to
tests follow requirements engineering (ISO/IEC/IEEE 29148: attributes and bidirectional
traceability). Binding and open rows follow Example Mapping's rules and questions and
Scrum's Definition of Ready. The line between fixed and free follows Shape Up and
commander's intent. The manifest is a bill of materials, checked as sprint-metrics checks
its docs against its code.

## Consequences

- These readers go: `decided()` and `story_problem_evidence` (the latest answer), the
  design note's "latest since the last split" for decisions, the conclusion written once,
  the retro's pairing of answers to cards by time, and the rule that the same merged
  tests must fail twice before the whole epic re-splits.
- The Product Owner sees its own earlier answers. The split, the criteria check and the
  design note see every decision so far, not the latest comment.
- The Sponsor's words reach the split and the gates as binding rows whose source is the
  Sponsor (ADR 0015, edited the same day).
- Open epics with the earlier conclusion form are read as they are; nothing is
  re-settled.
- crew#583 builds it. crew#584's finding becomes a row kind here and a selection rule in
  ADR 0024. crew#190 and crew#457 are closed or reshaped against it.
- The proof is the review tool run again on a real epic, against epic 468's numbers, in
  the plan's success test.

## Changelog

- **2026-10-10:** the plan's Q3 answered the same day: a Sponsor reply on an epic is
  recorded as a binding row by the Product Owner step, quoting the Sponsor's words
  (Sponsor).
