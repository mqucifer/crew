# 34. Roles open their own tasks

- **Date:** 2026-10-20
- **Status:** Accepted (to be built in M8, crew#839)

## Context

**Only epics and bugs bring work to the crew.** An epic comes from a Goal; a bug
comes from a collaborator (ADR 0027). A role that sees other work can only note it:
- On mqucifer/sprint-metrics#667, DevOps found that the `requires_db` tests never
  run in CI. As a reviewer it could only note this, and set it aside as pre-existing.
  QA then accepted CI proof that the job's log shows was skipped (crew#817).
- **Work that follows an event has no way in.** A release's deploy on the Mac is a
  step by hand. A UX review of what a release built has no place in the flow (crew#830).

M8's done-when is "a completed epic is released and deployed, and the Sponsor only
approves". That needs DevOps and the UX Designer to do work no epic plans.

## Decision

**A task is work owned by a role, not by an epic** (Sponsor, 2026-10-20). It follows
SAFe's enabler stories, with event-triggered runbooks for the events.
- **A task belongs to a role:** DevOps, the UX Designer or QA. That role's session
  does it, with its tools and its scope.
- **An event or a role's finding opens it:**
  - an event, such as a release merging (a DevOps deploy task) or a release going
    live (a UX Designer review task);
  - a finding outside the story a role is working on, filed in the project's
    repository.
- **It flows like a bug** (ADR 0027): triaged, sized, admitted to a sprint, done and
  reviewed. **It needs no approval from the Sponsor to start.** The Sponsor approves
  what it produces, such as a release PR or a proposed Goal.
- **A task doesn't set direction.** New behaviour for a project's users is still a
  Goal, so a task's finding that needs one goes to the Product Owner (ADR 0027).
- **The first deploy task runs in the tick, on the Mac** (Sponsor, 2026-10-20). It
  bumps the pinned release, pulls, restarts, runs a health check, and rolls back to
  the old release if the check fails. No always-on service is added.

## Consequences

- DevOps's session (crew#817) is the first to pick up tasks. The UX Designer's
  release review (crew#830) is the second.
- QA's triggers are shaped in M10's planning session.
- A role's finding outside its story is filed, not set aside, so "pre-existing" is no
  longer a reason to drop it.
- A release's merge is the Sponsor's only approval for a release and its deploy
  (crew#829).

## Changelog

- **2026-10-20:** decided by the Sponsor in M8's planning session, in the operator's
  chat (crew#839).
