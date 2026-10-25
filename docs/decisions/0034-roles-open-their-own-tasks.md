# 34. Roles open their own tasks

- **Date:** 2026-10-20
- **Status:** Accepted (built in M7 by crew#839: the Task, the Architect's and DevOps's events, findings, and their sessions; in M8 the deploy by crew#829 and the UX review by crew#830; QA's Tasks M10's)

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
  checked at `done`; the Architect's diagram must render. It also has its gate's own
  checks as a tool, to run before `done`: the Architect's `diagram_check`, DevOps's
  `workspace_check`. Each calls the gate's code, read-only. The Code Reviewer reviews it,
  and QA has no story criteria to prove. Its Task isn't split, so a blocked one goes
  to a person, not back to refinement.
- **What the Sponsor approves:** the diagram and the release, by their approving review
  before the merge. The release is ADR 0019's approval; the diagram is the Sponsor's
  view of the system, once a Goal. A Task from a finding is bug-like work and merges
  after the Code Reviewer (ADR 0027).
- **A role with no session yet** (QA's comes in M10): its Tasks are filed and held, the
  hold naming the milestone, and recorded each pass.

**The standards.** SAFe's enabler stories: work that supports delivery, owned and sized
like a story, admitted to a sprint beside it. Event-triggered runbooks for the events:
each has a trigger, a named owner, one bounded piece of work and a record that it ran.
Checklist: every Task names its role and the event or finding that opened it; each
event opens at most one; a Task flows through triage, admission, the session, review
and merge like a bug; and nothing a role finds is dropped as pre-existing.

## The deploy (M8, crew#829)

- **A verified release opens DevOps's deploy Task,** once per repository and version,
  when infra's inventory declares a service its repository deploys and the version
  is newer than the tag infra pins. What the service is and how its health is told
  are declared there, in its `deploy:` block, not in the crew's code (mqucifer/project-print
  ADR 0002).
- **The tick runs it, with no model.** It bumps the tag in infra's compose file,
  pulls, restarts and checks the health. A failure rolls back to the release that was
  running, read from the container, and files DevOps's finding with the log, every
  settings value taken out. The Task never waits in a column a session claims from: it
  ends in `Done`, or in `Blocked` with `needs:human` when the rollback failed too.
- **A MAJOR ahead of a reader waits.** Each reader the block names pins a release; a
  release at another MAJOR opens no Task, and is recorded and said on the standup until
  the reader moves on purpose.
- **The new tag reaches infra by pull request,** which the crew merges itself once
  infra's guard checks pass: the release's merge was the approval.
- **Each run is recorded:** `deploy.started`, `deploy.healthy`, `deploy.rolled_back`,
  `deploy.rollback_failed` and `deploy.held`.

## What's built (M8, crew#830): the UX Designer's release review

- **A release goes live:** its tag has a GitHub Release, and a GitHub Pages deployment
  succeeded after it was published. crew-presentation publishes to Pages and has no
  release workflow, so the release check doesn't see it. Each pass opens the UX
  Designer's review Task, one per repository and version, in Ready. Only for a project
  GitHub has a Pages site for (where the pages are is GitHub's fact) and whose record
  names a reader (who reads them is the record's intent; the operator, 2026-10-24).
- **Its session** is a read-only step session, not the Developer's path: a review has no
  branch and no pull request, and the UX Designer changes no file. It reads the code
  and `look`s at the published pages at the readers' widths. It judges them by two
  established standards, and each correction names the one it breaks: Nielsen's 10
  usability heuristics, and the WCAG 2.2 success criteria `look` can see (non-text
  content, info and relationships, meaningful sequence, reflow, page titled, headings and
  labels). `look` doesn't measure contrast, so the review doesn't claim it.
- **Its corrections:** each names its page, width, what it saw, what the reader should
  meet instead, its standard, and its owner. `done` refuses one on a page and width it
  never looked at on the published site, so each is filed with its screenshot, uploaded
  to the reviewed Release. Each is a bug, or a Task for the role it belongs to, through
  the same filing as a finding outside a story, up to ten per owner (one for a story's
  reviewers, as before). Then the Task is closed and Done. It never blocks a merge.
- **A finding another role files for the UX Designer** is worked by the same session,
  on the version readers meet now.
- **One that can't be worked** (no live release, its session stopped) is blocked for a
  person, as a Task is.

**Left:** QA's Tasks wait for M10.

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
- **2026-10-23:** a role's Task session can run its gate's checks itself, before
  `done` (the Sponsor, on crew#839). On mqucifer/sprint-metrics#738 the Architect
  found out at the gate that its diagram didn't render, and three rounds went on it.
- **2026-10-23:** a project's first workspace is three steps (the Sponsor, on
  crew#839; ADR 0032): DevOps's workspace file and services, the Developer's bug for
  the tests they turn on, then DevOps's Task for CI. A Task in Merging with changes
  requested on its head goes back to its role for rework.
- **2026-10-24:** the deploy Task is built (crew#829), with the operator's answers: a
  MAJOR ahead of the release a reader pins waits, and the crew merges the infra pull
  request recording a healthy deploy's tag itself.

- **2026-10-24:** the UX Designer's release review built (crew#830): a release going live
  is read from GitHub Pages, its Task is worked by a read-only session, and its
  corrections are filed with screenshots, up to ten per owner. The UX hold is lifted;
  no `role:ux` Task was held. The operator's answers on where the pages are and where
  screenshots live.
