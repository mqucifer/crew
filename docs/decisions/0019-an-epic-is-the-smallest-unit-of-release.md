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
- Versions move once per epic. The service is 1.1.0; the next two epics under Goal
  174, trends and telemetry, are each a MINOR.
- A release story is technical work, so it holds the project's other epics until it
  lands, as technical work does. It's small, so the hold is short.
- Until crew#500 is built, a completed epic's release story is filed by hand.
  Epic 406's is the first.
