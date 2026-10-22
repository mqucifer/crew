# 36. One look across a Goal's epics, recorded on the Goal

- **Date:** 2026-10-21
- **Status:** Accepted (built by crew#858)

## Context

The Sponsor reviewed the crew's flow step by step on 2026-10-21 (`docs/process-as-is.md`
in mqucifer/project-print, "Agreed while reviewing"). Refinement ran per epic: four
panel members, the Product Owner's settle and the Architect's design settle before each
split, then a design note and a presentation note after it. The review found:

- **Nobody owned the seams.** A panel member was told a problem inside another epic
  "belongs to that epic's review". mqucifer/sprint-metrics#717's cross-epic conflict
  came through that way.
- **It repeated, and it cost.** 44% of panel notes overlapped (60% since sessions), a
  4-epic Goal cost 16 panel sessions, and refinement was about 91% of Sprint 31's model
  time.
- **The design came last.** The design note was written after the stories existed, so
  it could only fit around them, and the Code Reviewer "validated" a design nobody had
  submitted. The UX Designer's reader criteria were appended to stories already written.

On 2026-10-22 the Sponsor added that a project refines one Goal at a time: "I've been
doing it manually because something felt off." Rework followed the Goal, not the
overlap (mqucifer/sprint-metrics#462 had all 11 re-splits of its period), and every
pass refined every approved epic before it admitted or delivered anything, so one
Goal's refinement held the other's delivery (crew#858, the comment of 2026-10-22).

## Decision

The Sponsor, 2026-10-21 and 22 (crew#858), with the order of Goals' turns set by the operator reviewing the build.

- **One look per Goal, before any epic is split.** Once none of a Goal's epics waits
  for the Sponsor's approval (an epic parked back in Inbox waits too, and the hold names
  it), the Architect and the UX Designer read all of its approved epics once, each in
  its own session, at the same time, each in its own form:

  | Role | Its question (ADR 0035) | What it produces |
  |---|---|---|
  | Architect | What does this touch that's expensive to reverse? | what the code supports; each decision across the epics, made, with the options it weighed; the risks and seams; the Goal's target C4 diagram; any call beyond the Goal |
  | UX Designer | Is this what the reader needs? | the reader; what they meet per epic; what's missing; criteria about what they see |

  The Architect decides; it doesn't propose. A call it can't make within the Goal's
  intent goes to the Sponsor as the Product Owner's one question.
- **The record moves to the Goal.** The Product Owner settles the look once into the
  Goal's record, a marked comment on the Goal (its body is the Sponsor's). The
  Architect's decisions and the reader's criteria go in as they set them; the Product
  Owner settles the rest into rows, questions for the implementer, items for infra or
  dismissals. Every row names the epics it binds: `all`, or their refs. An epic reads the
  rows that bind it, and its split's record checks (every binding row followed, every
  declared test carried) work against those. A row a step adds for one epic (the
  Sponsor's reply on it, an answer to a story problem, a test a story changes) binds that
  epic; a Sponsor's reply on the Goal binds every epic.
- **The target diagram is C4 in Mermaid,** its own comment on the Goal: the repository's
  system with the Goal's new parts and connections, and a sequence diagram only when the
  Goal adds an interaction. A change is a new version of the comment, the one before
  kept and marked replaced, and each epic's split comment links to it. The diagram is
  checked in code when the Architect answers: a C4 diagram type, known macros with their
  arguments, braces and quotes that close, and aliases defined once and related only when
  defined. Nothing below the Goal carries a diagram.
- **The readers.** The split reads the epic, the Goal's rows that bind it, the target
  diagram, and the stories other epics already plan. The Developer, the Code Reviewer and
  QA are shown the rows the story follows and the target diagram.
- **An epic added later** (`needs:epic`) gets the look for that epic only, against the
  record and diagram the Goal has. Its settle adds rows and never changes one; an item
  that contradicts a row is the Sponsor's question.
- **One Goal refines at a time in a project.** While one Goal of a repository has an
  approved epic that isn't split, the other Goals' epics wait to be refined, and the hold
  names the Goal they wait on. One that has begun keeps its turn until its epics are all
  split, even if a higher-priority Goal is approved meanwhile; otherwise the first in
  admission's order (Priority, the Product Owner's Rank, number) takes it. The next
  Goal can be decomposed and approved meanwhile. A technical epic has no Goal, is never
  held by this, and still goes first.
- **What goes, outright:** the per-epic panel, its settle and the design settle; the
  design note, its review and the design threshold (`needs:design`, `no:design`); the
  presentation note and `needs:ux`; admission's holds for the notes; the "builds on
  unsplit" hold; the Goal's index of its epics' rows; `crew panel` and `crew settle`
  (`crew look` prints a Goal's look). An epic split before its Goal had a record keeps
  its own record, and its notes are still shown to the Developer and the Code Reviewer,
  until it closes: a migration with an end, not a second path.

**The standards.** The look is the Three Amigos' conversation before the work is split:
the business side is the Product Owner's epics and its settle, the development side the
Architect, and the reader's side the UX Designer (QA's side waits for its own test bed,
M10). Its checklist: every approved epic read before any is split, each role answering
its own question, and the answers settled once into what the split reads. The epics it
starts from are Shape Up pitches, naming what they build on, what's new and the risks,
so the look spends itself on the high-value unknowns. The diagram is the C4 model
(Simon Brown): context and containers, components where useful. The record stays a
configuration baseline (ADR 0023), one level up: identification (every row its ID,
author, date and the epics it binds), change control (a row replaced, never edited),
status accounting (one change comment per edit), and audit (the gates judge a story
against the rows it cites).

## Consequences

- A Goal costs two look sessions and one settle, not four panel sessions, a settle and
  a design settle per epic.
- The seams between a Goal's epics have an owner: the Architect reads them all at once.
- A Goal's epics wait for the last of them to be approved; an epic the Sponsor parks
  holds its Goal until it is approved or closed.
- With one Goal refining at a time, delivery isn't held behind another Goal's
  refinement, and the split and the criteria check see fewer other planned stories.
- QA's and DevOps's work leaves refinement. It opens as their own tasks (ADR 0034, M8),
  and QA's proof through its own tests waits for M10.
- Keeping the diagram true after delivery is the Architect's task when a Goal's epics
  are done (ADR 0034), not part of this.
- The proof is a replay on a real Goal, scheduled with the Sponsor after this merges.

## Changelog

- **2026-10-21:** decided in the Sponsor's review of the flow (crew#858).
- **2026-10-22:** one Goal refines at a time per project (the Sponsor, on crew#858); in
  admission's order, with a Goal that has begun keeping its turn, so two Goals approved
  at once don't hold each other (the operator, reviewing the build).
