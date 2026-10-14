# 30. A new project starts from a walking skeleton

- **Date:** 2026-10-13
- **Status:** Accepted (to be built by crew#690; the plan is `docs/plans/onboarding-a-new-project.md`)

## Context

crew-presentation was the first project the crew took from an empty repository. sprint-metrics had started from a scaffold written by hand. After crew-presentation was unpaused, no story could land:

- there were no files for its checks to run;
- its setup couldn't run without a shell;
- what setup installed was gone before the checks ran;
- its dependencies left out the test runner.

Its design took four runs, and the Code Reviewer passed designs with no lockfile and no CI on pull requests. Every one of these faults fails at once when a design's setup and checks are run for real.

The Sponsor, 2026-10-13: the crew onboards projects often enough to plan this for success, and a new repository's skeleton is never its final state. On who builds it: "An architect determines the needs, devs and DevOps implement their pieces."

## Decision

- **Onboarding is eight steps in a fixed order:**
  1. the Sponsor creates the repository with a first commit;
  2. the Sponsor gives the crew's Apps access;
  3. `crew onboard`;
  4. `crew design`;
  5. the skeleton Goal;
  6. the Sponsor sets branch protection requiring the skeleton's CI checks;
  7. the Sponsor sets up the deploy target, when a release is a deployment;
  8. the repository is added to `delivery.repos`.
  
  The Sponsor keeps creating repositories and their rules, because the crew's App has no admin (crew#566). Every crew-built project shares the one board.
- **A project's first Goal is a walking skeleton** (Cockburn; Freeman and Pryce). The Goal's text is the same for every project, kept in the crew repository. `crew onboard` files it on the Sponsor's yes, which is their authorship.
  - **Its outcome:** every check the design names runs and passes, in the sandbox and in CI on every pull request. It doesn't deploy.
  - **Who builds it:** the normal pipeline, with the Developer building what the Architect's design names.
  - **Its split:** one story first and alone, making every check run and pass.
  - **Placeholders:** each is marked, and replacing a marked one needs no declared contract change.
  - **A design fault in it** (setup failing, or a check's own tool missing) sends the design back to the Architect with the output, and the Sponsor merges the revision.
- **`board.yml` goes in through the `crew onboard` pull request.** Records protect it from stories.
- **A readiness check replaces the usable-record precondition** (crew#132). It runs at every tick and as `crew ready <repo>`, and names what's missing and who fixes it. It has two levels:
  - **Level 1, enough for the skeleton:** repository and access, the required settings, `board.yml`, record, design, and the deploy target.
  - **Level 2, enough for any Goal:** CI green on the default branch, protection requiring its checks, and the design's lockfile present.
  
  Below level 2, only the skeleton Goal is worked.
- **The required settings are the ones the crew's code relies on:** issues on, squash merging allowed, delete branch on merge, Actions on. The repository is public, or on a plan where branch protection is enforced.
- **Rules a machine can check are checked by machine, not left to the Code Reviewer:**
  - the design names its lockfile in its own field, whatever the language, and the design check refuses a design that names none;
  - setup is one command, and shell operators are refused (§19.4);
  - for a new repository, the design declares no changes from today.
- **Each worktree has its own home directory inside it,** ignored by git and removed with the worktree, so what setup installs is there for the checks in any language. Downloads stay shared through the sandbox's one cache.
- **Tools follow the language, not the image.** The coverage map runs for every Python part, through the project's own setup and Python.

## Consequences

- A design fault surfaces in the skeleton's first story, before any feature work, and goes back to the Architect without a person.
- **The Sponsor's steps are named:** creating the repository, access, branch protection after the skeleton, and the deploy target. A missed one shows up in the standup, not at a failed deploy.
- **crew-presentation is onboarded again through these steps** as M4's proof.
- **The non-Python proof waits for the first real non-Python project,** most likely the print prototype.
- Projects onboarded before this are checked against level 2 at their next tick. sprint-metrics' design gains the lockfile field, naming its `uv.lock`.

## Changelog

- 2026-10-13: Accepted. The Sponsor decided each part in chat while planning crew#690.
