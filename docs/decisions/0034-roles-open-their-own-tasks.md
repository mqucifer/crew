# 34. Roles open their own tasks

- **Date:** 2026-10-20
- **Status:** Accepted (built in M7 by crew#839: the Task, the Architect's and DevOps's events, findings, and their sessions; the deploy and the UX review are M8's, QA's Tasks M10's)

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
    live (a UX Designer review task), or an alert firing (a DevOps review task, in
    mqucifer/infra; Sponsor, 2026-10-20);
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

## What's built (M7, crew#839)

The Sponsor, 2026-10-22: Task is the board's Work Type option, and M7 builds the
Architect's and DevOps's share. The operator reviewed the design before it was built.

- **A Task on the board** is the Work Type field's `Task`. Its role is a `role:` label
  (`role:architect`, `role:devops`, `role:ux`, `role:qa`), and a marker in its body names
  its role, its kind and what it's for, so one event never opens two.
- **The events:**
  - **A Goal's epics are all done:** the Architect's Task, a child of the Goal, makes
    the Goal's target diagram (ADR 0036) the repository's, `docs/architecture.md`,
    corrected to what was built, and records where the build differs. The Goal closes
    once that Task is done too.
  - **A repository has no diagram:** the Architect's first Task draws it. Not the
    crew's own repository, whose architecture has no owner.
  - **A repository has no workspace file:** DevOps's first Task writes
    `devcontainer.json` and the services its tests need, and makes CI run the same
    (crew#860). Not while it waits on its walking skeleton.
  - **A Goal's look names a new service or toolchain:** DevOps's Task for it, a child
    of the Goal, named at the level of intent (crew#860).
  - **The gate at `done` finds the workspace isn't what CI runs:** a merged test
    skipped for a reason the file doesn't declare CI-only, or a service that won't
    start. DevOps's finding, one per cause, however many stories meet it (crew#860).
  - **An epic is split:** DevOps's Task for its CI and release, a child of the epic, one
    per epic. It replaces the release story (ADR 0019): the split refuses a story that
    cuts the release. It waits for every story of the epic, so the epic doesn't close
    before its release lands.
  - An event whose Task GitHub refused is recorded, and the next pass opens it.
- **A finding outside the story:** the Code Reviewer, DevOps's deploy review and QA name
  what they saw outside the story in their verdict. Each is filed for its owner as a
  Task, which the Business Analyst triages as a bug, or as a bug. One per owner for each
  story and round, every filing recorded, to be tuned from that evidence.
- **The role's session** works its Task through the Developer's path, with its role in
  the Developer's place and the Developer's tools. It may change only its role's area,
  checked at `done`; the Architect's diagram must render. The Code Reviewer reviews it,
  and QA has no story criteria to prove. Its Task isn't split, so a blocked one goes
  to a person, not back to refinement.
- **What the Sponsor approves:** the diagram and the release, by their approving review
  before the merge. The release is ADR 0019's approval; the diagram is the Sponsor's
  view of the system, once a Goal. A Task from a finding is bug-like work and merges
  after the Code Reviewer (ADR 0027).
- **A role with no session yet** (the UX Designer's comes in M8, QA's in M10): its Tasks
  are filed and held, the hold naming the milestone, and recorded each pass.

**The standards.** SAFe's enabler stories: work that supports delivery, owned and sized
like a story, admitted to a sprint beside it. Event-triggered runbooks for the events:
each has a trigger, a named owner, one bounded piece of work and a record that it ran.
Checklist: every Task names its role and the event or finding that opened it; each
event opens at most one; a Task flows through triage, admission, the session, review
and merge like a bug; and nothing a role finds is dropped as pre-existing.

**Left for M8:** the deploy Task on a release's merge (crew#829) and the UX Designer's
review when a release goes live (crew#830). QA's Tasks wait for M10.

## Consequences

- DevOps's session (crew#817) is the first to pick up tasks. The UX Designer's
  release review (crew#830) is the second.
- QA's triggers are shaped in M10's planning session.
- A refinement note marked `infra` (ADR 0017) is DevOps's finding outside its
  story, so it's filed in mqucifer/infra as a task, not left in the epic's conclusion.
- A role's finding outside its story is filed, not set aside, so "pre-existing" is no
  longer a reason to drop it.
- A release's merge is the Sponsor's only approval for a release and its deploy
  (crew#829).

## Changelog

- **2026-10-20:** decided by the Sponsor in M8's planning session, in the operator's
  chat (crew#839).
- **2026-10-20:** an alert opens a task for DevOps to review, decided by the Sponsor
  in M9's planning session (mqucifer/infra#33).
- **2026-10-22:** a task is the board's `Task` work type, and M7 builds the Architect's
  and DevOps's share, decided by the Sponsor on crew#839. Built by crew#839, with the
  operator's answers on what the Sponsor approves and the finding cap.
- **2026-10-22:** DevOps keeps each project's workspace file true (the Sponsor, on
  crew#860): its first Task where there's none, one for each need a Goal's look names,
  and a finding when the gate at `done` finds the workspace isn't what CI runs.
