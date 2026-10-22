# 37. A re-split replaces only what the problem reaches, and the rounds are counted

- **Date:** 2026-10-21
- **Status:** Accepted (built by crew#859)

## Context

The Sponsor's review of the flow on 2026-10-21 (`docs/process-as-is.md` in
mqucifer/project-print, step 7) found two things wrong with a story problem's re-split:

- **It discarded every unstarted story.** When the Product Owner answered a story
  problem, the rework gate closed all of the epic's unstarted stories and the epic was
  split again from scratch. Most of Sprint 31's discarded stories went that way.
- **Nothing counted the rounds.** Only "work already started" stopped an epic going
  problem, answer, split again, so sprint-metrics#685 went round three times in a day.

Nothing records which earlier story of its epic a story needs. Only the epic's order
does: delivery holds a story until every earlier sibling lands (crew#756), so every
later story is taken to build on it. The Sponsor, 2026-10-22, on crew#859: "I don't want
to have a bunch of logic dealing with this dependency hell."

## Decision

The Sponsor, 2026-10-21 and 22 (crew#859).

- **A re-split replaces only the affected stories,** by one rule: the epic's open stories
  that the problem's comment names, and every later story of the epic. A problem that
  names none of them (the Sponsor's own `needs:rework`, say) replaces every unstarted
  story. The rest stay as they are, started or not. The replacements are numbered after
  every kept story, so the epic's order stays true.
- **Started work is never discarded.** A re-split that would replace a started story
  waits, naming it; a started story it keeps doesn't hold it.
- **The split is shown what it keeps and what it replaces,** each whole, and writes stories
  only for the replaced ones, accounting for each. The rows and tests the kept stories
  follow count for the record's checks, and the criteria check reads the kept stories
  beside the new ones.
- **Nothing is closed until the replacements exist.** The replaced stories close as not
  planned, and `needs:rework` comes off, only after the new stories are created. A split
  that fails leaves the epic as the next pass finds it, with its count unchanged.
- **The rounds are counted per epic, on GitHub.** Each re-split after a story problem
  marks its split comment with its round. The Sponsor's own rework isn't a round.
- **The stop starts at 3** (`refinement.resplit_stop` in `org.yaml`), and is lowered from
  evidence. Once an epic has had that many rounds, its next problem isn't the Product
  Owner's: the Sponsor gets one question on the epic, two lines first (what keeps failing,
  and the question), with every round folded beneath. Their reply becomes a binding row,
  and the round goes ahead. Every round is an `epic.resplit` event, with what it replaced,
  kept and named, and where each named story stood; every stop is an
  `epic.resplit_stopped`.
- **The answer goes into the Goal's record,** as a row binding that epic (ADR 0036). An
  epic with no Goal keeps it in its own record.
- **DevOps's release Task stays under the epic** through a re-split (ADR 0034): it isn't a
  story, so it's never replaced.
- **What goes:** closing every unstarted story and splitting the epic again from scratch.
  A Goal sent back with `needs:rework` still supersedes its unstarted epics; that isn't
  this decision.
- **Across epics, nothing new.** A story in another epic whose `Builds on` line names a
  replaced story is left as it is: a closed story counts as landed, so it no longer waits
  for it, as before.

**The standard.** Impact analysis by traceability: a change's dependants are found from
the links already recorded, not inferred. Checklist: every replaced story is reached from
the problem by a recorded link (named in its comment, or later in the epic's order);
nothing is replaced that no link reaches; a link nothing records isn't invented; and every
round is recorded, so the stop is set from evidence.

## Consequences

- **The gain is the count and the stop, not what a round keeps.** In the 17 re-splits
  after a story problem the event log holds, this rule would have kept 3 of 45 discarded
  stories: the named story was the epic's first open story in 15 of them. Explicit links
  between an epic's stories would keep more, and are parked in mqucifer/crew Discussion
  795 with that evidence.
- A returned story sends back only its later siblings; the earlier ones stay in their
  sprint.
- The stop is lowered from the `epic.resplit` events, which carry each round's number.

## Changelog

- **2026-10-21:** decided in the Sponsor's review of the flow (crew#859).
- **2026-10-22:** one rule for what's affected, and nothing new across epics (the Sponsor,
  on crew#859). Built by crew#859.
