# 19. Each completed epic releases itself

- **Date:** 2026-10-07
- **Status:** Accepted (to be built in M8, crew#816; the release planned at the split as DevOps's Task, crew#839)

## Context

sprint-metrics epic 406, the stateful service, completed on 2026-10-07 and nothing
was released. Its outcome begins "A user runs the released container as a service",
so the epic's work was merged but its outcome wasn't met. The project's decisions
said what a release is: "a release is a PR: version bump, dated changelog", and on
merge a tag, a GitHub Release and an image (Goal 174, D5). They didn't say when one
happens, or who opens it.

A first answer filed a release story under each epic once it completed, and held
the epic open until it merged. On 2026-10-08 that meant reopening two completed
epics, and the Sponsor took the call instead: a release only when they asked.

On 2026-10-19, mqucifer/sprint-metrics#667's split made a release story,
mqucifer/sprint-metrics#671, and the Product Owner wrote "Epic is a PATCH release"
into the epic's record. The Sponsor wanted that: an epic deciding its own release.
Two problems showed up with it (crew#816):
- **Several epics are in flight at once,** so `main` holds part of another epic when
  one completes, and its release ships those stories too.
- **The changelog was behind.** Stories added no `[Unreleased]` entry, and the
  release story wrote a fixed list and required `[Unreleased]` to end empty, so
  another epic's entries would be dropped or the release would fail.

## Decision

- **Every completed epic releases itself** (Sponsor, 2026-10-20). Its release is
  DevOps's Task, opened when the epic is split, which waits for every story of the
  epic, so a completed epic is never reopened (ADR 0034, crew#839). The Product Owner
  sets the SemVer bump from what the epic changes. The Sponsor approves the release
  PR: the Task's pull request merges only after their approving review (crew#839).
- **A release ships what `[Unreleased]` holds** (Sponsor, 2026-10-20), including
  another epic's merged stories. This is trunk-based development: every merged story
  has passed review, QA and CI. The version is the one the most significant entry
  needs (Keep a Changelog, SemVer), not the one planned at the split if more has
  landed since.
- **A story that changes what a user sees adds its `[Unreleased]` changelog entry,**
  as it adds its documentation criterion, so a release assembles what's there.
- **A release story is dated by the crew,** with the real day. The model never
  supplies a date or a version on its own.

## Consequences

- No branch per epic: crew#500 stays closed. When a project has outside users, or an
  epic could leave things worse half-done, this is revisited with feature flags.
- A release can name entries from an epic that hasn't completed. That epic's own
  release later ships whatever it adds after.
- Versions follow each project's own versioning rule.
- The release is DevOps's Task, opened when the epic is split (ADR 0034, crew#839),
  not a story: the split refuses a story that cuts it. The Sponsor approves the
  Task's pull request before it merges.

## Changelog

- 2026-10-07: Accepted as "An epic is the smallest unit of release": each completed
  epic files a release story under itself and stays open until it merges (crew#501).
- 2026-10-08: Rewritten. The Sponsor decides when a release is cut until crew#500
  settles it; releases are never attached to epics; what triggers one, an epic or a
  bug, is left open. The file keeps its name so existing links still work (crew#500).
- 2026-10-20: Rewritten as "Each completed epic releases itself". Every completed
  epic's last story is its release, planned at the split; a release ships what
  `[Unreleased]` holds, another epic's merged stories included. Decided by the
  Sponsor in the operator's chat (crew#816). The file keeps its name.
- 2026-10-20: the crew releases until M8 builds the Sponsor's approval of a release
  PR. The Sponsor, after the operator held mqucifer/sprint-metrics#701 for their
  merge: the decision stands, and the missing step was ours, not a reason to hold.
- 2026-10-22: the release is DevOps's Task, opened by the split, in place of the
  release story, and it waits for the Sponsor's approving review (crew#839, ADR 0034).
