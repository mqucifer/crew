# 18. The Product Owner decides within the Goal, and the Architect settles design before the split

- **Date:** 2026-10-05
- **Status:** Accepted (built by crew#440, and the Architect's settle before the split by step A2 of `docs/plans/context-record-build.md`, crew#583; the leeway widened to the Goal's intent on 2026-10-21 and built by crew#846; the Architect decides in the Goal's look since crew#858, ADR 0036)

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

- **The Product Owner chooses what serves the Goal's reader** (the Sponsor,
  2026-10-21, ADR 0035). It may decide a note itself when no source answers it, as a
  team's owner does, and may leave out or reshape what the Goal mentions, saying why.
  A call stays within the Goal's intent: its reader, what they should see or decide,
  and what's out. It doesn't contradict a decision of the Sponsor's.
- **It is recorded as the Product Owner's call.** The row says so and gives the
  reason. The code checks the call against the Goal's intent and sends the conclusion
  back if it reaches outside it: the call quotes the Goal's reader, or what they
  should see or decide, and not what the Goal rules out.
- **"The Goal points elsewhere" is an answer.** When serving the reader needs
  something the Goal's intent doesn't cover, the Product Owner brings it to the
  Sponsor as a question or a proposed Goal (ADR 0027).
  The conclusion's bottom line counts the calls.
- **The leeway is for what the product does.** A design question (a path, a response
  shape, a parameter) is the Architect's.
- **The Architect decides the design before the split,** once for the Goal, in the
  Goal's look (ADR 0036): each decision across the Goal's epics that would be expensive
  to reverse, made, with the options it weighed, and recorded as a row of the Goal's
  record set by the Architect, naming the epics it binds. The split writes criteria
  against those rows. A question any reasonable choice answers is left to the
  implementer. A call the Architect can't make within the Goal goes to the Sponsor as
  the Product Owner's one question.
- **No design note follows the split.** What's left to design (which module owns
  what, the file layout) is the Developer's, within the rows and the Goal's target
  diagram.
- **The Sponsor is asked only when the Product Owner can't tell which way the Goal
  points**, so that any call it made might contradict it.

This replaces the last bullet of ADR 0016, which had the Product Owner ask the
Sponsor whenever its written sources ran out.

## Consequences

- The Sponsor is asked less, and can see every call: a row marked as the Product
  Owner's call, with the Goal wording behind it. A wrong one is replaced by a later
  row or decision, as with any record here.
- The intent check proves the call names the Goal's reader or what they should see or
  decide, and not what it rules out. It doesn't prove the call serves them: that stays
  the Product Owner's judgement, and the criteria check and the Sponsor's review of the
  conclusion are what catch the rest.
- The first run with the leeway had the Product Owner deciding API paths, response
  shapes and parameters. The Sponsor drew the line there (2026-10-05): design goes
  to the Architect.
- Refinement costs one Architect session per Goal, in the look (ADR 0036). It cost one
  more call for each epic with open design questions,
  and saves the story problems and re-splits they caused.
- A call can narrow an approved epic, as the 406 row did, so long as it stays within
  the Goal and is recorded. If narrowing epics proves to be a problem, that is a
  further limit for the Sponsor to set.
- Once projects keep decision logs (crew#468), the calls go there too.

## Changelog

- **2026-10-07:** the Architect settles open design questions before the split,
  not in the design note after it, so the split's criteria build on settled
  contracts (Sponsor, from the evidence on sprint-metrics epics 406 and 407).
- **2026-10-10:** built (crew#583, step A2). Each answer is a binding row of the
  epic's record, set by the Architect, that replaces its question (ADR 0023). A
  question the settle leaves to the implementer isn't the Architect's, and no
  gate checks it. The split refuses an epic with a question still open for the
  Architect.
- **2026-10-18:** the Product Owner's settle and the Architect's design settle each work in a session over the code (crew#807, ADR 0033): a fact about the code is checked in it, and what's decided is binding. Their checks refuse at `done`, in the session.
- **2026-10-21:** the Product Owner chooses what serves the Goal's reader, within its intent rather than its words; the quotation check becomes a check against the intent; "the Goal points elsewhere" is an answer. Decided by the Sponsor in the overhaul's planning session (ADR 0035, crew#846).
- **2026-10-22:** the Architect's design settle per epic, and the design note after the
  split, go: the Architect decides once for the Goal, in the Goal's look, into the Goal's
  record, and the Product Owner settles the rest of the look there (ADR 0036, crew#858).
  The leeway and the intent check are unchanged.
