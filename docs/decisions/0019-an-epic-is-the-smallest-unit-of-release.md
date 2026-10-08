# 19. The Sponsor decides when a release is cut

- **Date:** 2026-10-07
- **Status:** Accepted (until crew#500 settles how releases happen)

## Context

sprint-metrics epic 406, the stateful service, completed on 2026-10-07 and nothing
was released. Its outcome begins "A user runs the released container as a service",
so the epic's work was merged but its outcome wasn't met.

The project's decisions said what a release is: "a release is a PR: version bump,
dated changelog", and on merge a tag, a GitHub Release and an image (Goal 174, D5),
with new API as MINOR (D4). They didn't say when a release happens, or who opens
that pull request. The 1.0.0 and 1.0.2 releases happened only because an epic
planned a release story.

Checking this also found the changelog behind:
- none of epic 406's stories had added an `[Unreleased]` entry;
- three entries from Sprint 12 had never been released;
- 1.0.0 to 1.0.2 were dated July 2025, although all three were tagged on 2026-09-30.

The first version of this decision made each completed epic file a release story
under itself, and held the epic open until it merged. Applied on 2026-10-08 to
sprint-metrics' epics 408 (telemetry) and 407 (trends), it meant reopening two
completed epics to attach releases of behaviour already being replaced
(mqucifer/sprint-metrics#462). It also left out the harder question: several epics
are worked at once, so `main` almost always holds part of another epic when one
completes, and a release cut then would ship that incomplete work. The Sponsor,
2026-10-08: "I don't like that we're re-opening completed epics just to tack on
releases", and on bugs: "I'm not willing to exclude that at the moment."

## Decision

- **Until crew#500 settles how releases happen, the Sponsor decides when a release
  is cut.** The crew files a release story when the Sponsor asks for one, and not
  otherwise.
- **A release is never attached to an epic.** A completed epic stays closed. A
  release story stands on its own, with no parent epic.
- **What may trigger a release is open.** An epic completing, a bug users need fixed
  now, or the Sponsor's own call are all possible. crew#500 decides, once releases
  can cope with several epics in flight.
- **A release story is dated by the crew,** with the real day. The model never
  supplies a date or a version on its own.
- **A story that changes what a user sees adds its `[Unreleased]` changelog entry,**
  as it adds its documentation criterion, so a release assembles what's there.

## Consequences

- Work merges to `main` and waits there, unreleased, until the Sponsor cuts a
  release. sprint-metrics' epics 407 and 408 wait this way, and their stories added
  no changelog entries, so the first release names them.
- A release story with no epic is admitted after the approved epics' stories, as any
  story with no epic is.
- Versions follow each project's own versioning rule.
- crew#500 is on hold for a branching strategy. The Sponsor's view on 2026-10-08:
  probably "a large complex effort for little gain".

## Changelog

- 2026-10-07: Accepted as "An epic is the smallest unit of release": each completed
  epic files a release story under itself and stays open until it merges (crew#501).
- 2026-10-08: Rewritten. The Sponsor decides when a release is cut until crew#500
  settles it; releases are never attached to epics; what triggers one, an epic or a
  bug, is left open. The file keeps its name so existing links still work (crew#500).
