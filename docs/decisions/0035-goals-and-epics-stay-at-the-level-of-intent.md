# 35. Goals and epics stay at the level of intent

- **Date:** 2026-10-21
- **Status:** Accepted (built in M7: the Product Owner's choices, the intent check, "the Goal points elsewhere" and the epic overreach check by crew#846 and crew#815; the rest by crew#846, crew#847 and crew#858, which replaced crew#828's per-epic panel with the Goal's look, ADR 0036)

## Context

The Sponsor, 2026-10-20 and 21: "I wanted the PO and UX to say what was important.
Otherwise why do I have them?", "Nothing allows them to pull back and say 'we're going
in a different direction'", and "what's the use in building more things I don't want
or use just to generate more things to put on presentation."

The Sponsor reads an epic's title and summary to approve it, and that's fine. The Goal
is where the Sponsor's time counts. But every level of refinement pushed specifics
upward and copied them down:

- **The project record** that `crew onboard` writes calls a feature list the Sponsor's
  intent. sprint-metrics' says "in_scope: A sprint-metrics CLI… a table… or
  Prometheus", and panels treat it as binding. mqucifer/sprint-metrics#684's panel
  named the CLI 36 times, and nobody uses it (project-print ADR 0003).
- **The Goal** mqucifer/sprint-metrics#666 listed the measures, so the Product Owner
  copied them into mqucifer/sprint-metrics#684 to mqucifer/sprint-metrics#686,
  "middle" included.
- **The Product Owner** was told to "propose the epics that deliver" the Goal, and ADR
  0018's quotation check fenced its calls inside the Goal's words.
- **The panel's members** were all given the same task with a different background, so
  44% of their notes repeat each other across 41 panels (experiments/panel-overlap-828).

## Decision

**Each level says what only it can say** (the Sponsor, 2026-10-21):

| Level | Written by | It says | It doesn't say |
|---|---|---|---|
| Project record | The Sponsor, through `crew onboard` | who the project serves, what they need to see or decide, what's out | features, formats, endpoints, which go in the record's design and learned parts as history |
| Goal | The Sponsor, roughly; the operator may draft it into shape | who reads the result, what it should let them see or decide, what's out | the measures, fields or features |
| Epic | The Product Owner | which reader it serves and what they can now see or do, in a title and summary the Sponsor approves on | fields, formats, endpoints, files |
| Story | The Business Analyst | the specifics, with worked examples as its criteria (Example Mapping) | — |

- **An epic's reach is checked in code.** An epic that lists fields, formats, endpoints
  or files is sent back, as the quotation check sends back today (crew#815).
- **The Product Owner chooses what serves the reader,** and may leave out or reshape
  what the Goal mentions, saying why. "The Goal points elsewhere" is an answer it brings
  to the Sponsor (ADR 0018, ADR 0027, crew#846).
- **Each role asks its own question,** with its own goal and its own kind of answer.
  The Architect and the UX Designer ask theirs across a Goal's epics at once, in the
  Goal's look (ADR 0036); QA's and DevOps's open as their own tasks (ADR 0034), once
  their epic is split:

  | Role | Its goal | Its question | What it produces |
  |---|---|---|---|
  | UX Designer | the reader | Is this what the reader needs? What will they see or do, and does it matter to them? | the reader's view, what's missing for them, and criteria about what they see |
  | Architect | the system | What does this touch that's expensive to reverse? | the decisions, made before building, and the Goal's target diagram |
  | QA | proof | How will we know it worked for the reader? | the examples, which become the stories' criteria |
  | DevOps | running it | Can this be shipped, run and watched? | the runtime changes, or "none" |

- **Each role carries its field's background and the tools its question needs**
  (crew#846). For example, the UX Designer views the built site and the mock at the
  reader's widths, and the Product Owner reads the real data.
- **Onboarding records intent in the record's form above** (crew#847). sprint-metrics'
  record is rewritten to match project-print ADR 0003.

## Consequences

- A Goal can be short. The specifics arrive at the story, where the Business Analyst
  writes them as examples.
- Approval stays a title and a summary, and both now say the outcome for a reader.
- A small Goal gets short answers in its look (nothing expensive to reverse), which sizes
  the look without a separate step.
- The test is a crew-presentation Goal written as intent, filed once crew#815, crew#846,
  crew#847 and crew#858 are built.
- The draft standards PRs of 2026-10-20 asked the Sponsor for worked examples in the
  Goal. They're replaced by this ADR, and closed.

## Changelog

- **2026-10-21:** decided by the Sponsor in the overhaul's planning session, in the
  operator's chat (`briefs/2026-10-21-refinement-overhaul.md` in mqucifer/project-print).
- **2026-10-22:** the per-epic panel went (ADR 0036, crew#858). The Architect and the UX
  Designer ask their questions once for a Goal, in the Goal's look, each in its own form;
  QA's and DevOps's questions open as their own tasks (ADR 0034, M8). The Architect's and
  the UX Designer's background and tools are built; the Product Owner's `data` tool and
  QA's and DevOps's tools wait for their steps.
