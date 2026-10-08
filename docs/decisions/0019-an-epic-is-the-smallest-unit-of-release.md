# 19. An epic is the smallest unit of release

- **Date:** 2026-10-07
- **Status:** Accepted (to be built by crew#500)

## Context

sprint-metrics epic 406, the stateful service, completed on 2026-10-07 and nothing
was released. Its outcome begins "A user runs the released container as a service",
so the epic's work was merged but its outcome wasn't met.

The project's decisions said what a release is: "a release is a PR: version bump,
dated changelog", and on merge a tag, a GitHub Release and an image (Goal 174, D5),
with new API as MINOR (D4). They didn't say when a release happens, or who opens
that pull request. The 1.0.0 and 1.0.2 releases happened only because an epic
planned a release story. The ways of working give releases to the DevOps Engineer
(crew#335), but no step of its proposes one.

Checking this also found the changelog behind:
- none of epic 406's stories had added an `[Unreleased]` entry;
- three entries from Sprint 12 had never been released;
- 1.0.0 to 1.0.2 were dated July 2025, although all three were tagged on 2026-09-30.

The Sponsor, 2026-10-07: "Epics should be the smallest unit of a release. Not at
story level."

## Decision

- **An epic is the smallest unit of release.** A story never triggers a release on
  its own.
- **When a product epic completes,** in a project whose release is a published
  version, the DevOps Engineer files a release story under it:
  - bump the version by the project's versioning rule;
  - move the epic's `[Unreleased]` entries under that version, dated with the real
    day. The crew gives the story the date; the model never supplies one.
- **The epic is complete when that release story merges** and the release check has
  verified what was published.
- **At sprint close, a check catches a completed epic that was never released,**
  and files its release story. It never releases loose stories.
- **A story that changes what a user sees adds its `[Unreleased]` changelog entry,**
  as it adds its documentation criterion, so the release story assembles what's
  there.

## Consequences

- An epic's outcome that names a released artifact is met when it can be used,
  not when its code merges.
- Versions move once per epic.
- A release story is technical work, so it holds the project's other epics until it
  lands, as technical work does. It's small, so the hold is short.
- **Until crew#500 is built, no release story is filed by hand.** A completed epic
  waits unreleased on `main`, and crew#500's close check files its release when it's
  built. Epic 406's 1.1.0 was the only one filed by hand. sprint-metrics' epics 408
  (telemetry) and 407 (trends) completed on 2026-10-07 and 2026-10-08 and wait this
  way. The Sponsor, 2026-10-08: "I don't quite get the desire to make two releases
  for something we currently know is not what we want and we have backlog work that
  will do this for us automatically. I'd rather have less real epics to wade
  through." Their sprints are about to change (mqucifer/sprint-metrics#462).
- **Left open, to rethink later.** The DevOps Engineer still reviews stories, and
  later bugs, through the deploy review. One day it may mark a particular story or
  bug as needing its own release, such as a fix users need now. That would be an
  exception it flags, never every story. The Sponsor, 2026-10-07: "That may need to
  be rethought at a later time if it can tag them earlier as needs release or
  something. Not every story."

## Changelog

- 2026-10-07: Accepted (crew#501).
- 2026-10-08: No release story is filed by hand until crew#500 is built; completed
  epics wait unreleased. The Sponsor withdrew the hand-filed releases of epics 407
  and 408 (crew#500).
