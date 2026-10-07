# 18. The Product Owner decides within the Goal, and the Architect settles design before the split

- **Date:** 2026-10-05
- **Status:** Accepted (built by crew#440; the Architect's settle before the split is still to be built there)

## Context

A real run of the settle step on sprint-metrics epic 406 gave a row that narrowed
what the epic promised: "first delivery proves restarts only", where the epic's
outcome says it survives restarts and upgrades. None of the sources it cited said
upgrades were deferred. The settle task told the Product Owner never to decide
what its sources don't hold, so I called it overreach.

The Sponsor, 2026-10-05: the Product Owner needs leeway in order to hand off
ownership, so long as the call is recorded and doesn't contradict the overall
Goal. It sounds like a real team.

The panel's proof (sprint-metrics epics 406 and 407, Sprints 17 and 18) then showed
that leaving design questions to the design note was too late. The design note is
written after the split, so the split wrote criteria on contracts nobody had
settled yet: the intake schema, the health check, what "the last N sprints" means.
All five story problems raised in refinement came from that, each costing a
Product Owner answer and a re-split. The panel was built because design was
missing too much (the Sponsor, 2026-10-07); it found the right questions, and then
handed them on past the point where they were needed.

## Decision

Sponsor, 2026-10-05.

- **The Product Owner may decide a note itself** when no source answers it, as a
  team's owner does. The decision doesn't contradict the Goal or a decision of the
  Sponsor's.
- **It is recorded as the Product Owner's call.** The row says so, gives the
  reason, and quotes the Goal's own words that the decision stays within. The code
  checks the quotation is in the Goal, and sends the conclusion back if it isn't.
  The conclusion's bottom line counts the calls.
- **The leeway is for what the product does.** A note a member marked for the
  Architect is a design question (a path, a response shape, a parameter): it stays
  an open question for the design note unless a source settles it. The code refuses
  an own call on such a note and sends the conclusion back.
- **The Architect settles every open question before the split,** in the settle
  step, right after the Product Owner's conclusion. Each answer is its own call
  within the Goal and the decided rows, and becomes a decided row the split writes
  criteria against. It names each question by its ID; one left out is asked for
  again, and one still left out after the retries is for a person.
- **The design note, after the split, follows those rows.** It decides what's left
  to design: which module owns what, and the file layout. A question that can only
  be answered with the code read stays open, and the split writes no criterion on
  it until the design note settles it.
- **The Sponsor is asked only when the Product Owner can't tell which way the Goal
  points**, so that any call it made might contradict it.

This replaces the last bullet of ADR 0016, which had the Product Owner ask the
Sponsor whenever its written sources ran out.

## Consequences

- The Sponsor is asked less, and can see every call: a row marked as the Product
  Owner's call, with the Goal wording behind it. A wrong one is replaced by a later
  row or decision, as with any record here.
- The quotation check proves the wording is the Goal's. It doesn't prove the call is
  consistent with it: that stays the Product Owner's judgement, and the criteria
  check and the Sponsor's review of the conclusion are what catch the rest.
- The first run with the leeway had the Product Owner deciding API paths, response
  shapes and parameters. The Sponsor drew the line there (2026-10-05): design goes
  to the Architect.
- Refinement costs one more Architect call for each epic with open design questions,
  and saves the story problems and re-splits they caused.
- A call can narrow an approved epic, as the 406 row did, so long as it stays within
  the Goal and is recorded. If narrowing epics proves to be a problem, that is a
  further limit for the Sponsor to set.
- Once projects keep decision logs (crew#468), the calls go there too.

## Changelog

- **2026-10-07:** the Architect settles open design questions before the split,
  not in the design note after it, so the split's criteria build on settled
  contracts (Sponsor, from the evidence on sprint-metrics epics 406 and 407).
