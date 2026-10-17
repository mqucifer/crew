# 30. A new project starts from an alpha Claude builds

- **Date:** 2026-10-13
- **Status:** Accepted (the skeleton path built by crew#690, whose plan is `docs/plans/onboarding-a-new-project.md`; the alpha's handover check built by crew#757)

## Context

crew-presentation was the first project the crew took from an empty repository. sprint-metrics had started from a scaffold written by hand. After crew-presentation was unpaused, no story could land:

- there were no files for its checks to run;
- its setup couldn't run without a shell;
- what setup installed was gone before the checks ran;
- its dependencies left out the test runner.

Its design took four runs, and the Code Reviewer passed designs with no lockfile and no CI on pull requests. Every one of these faults fails at once when a design's setup and checks are run for real.

The Sponsor, 2026-10-13: the crew onboards projects often enough to plan this for success, and a new repository's skeleton is never its final state. On who builds it: "An architect determines the needs, devs and DevOps implement their pieces."

The skeleton path worked, at a cost. crew-presentation's onboarding took three days (2026-10-13 to 2026-10-15) and about a dozen crew fixes (crew#700 to crew#711, crew#718) before its first real story landed. Most of the faults were layout, the sandbox and missing facts, not code. The Sponsor, 2026-10-15: local-model time is the bottleneck, and onboarding isn't where it should go. The M6 planning session (crew#755, 2026-10-16 and 2026-10-17) researched what a takeover needs (`var/notes/m6-research/`, R1 and R2) and decided the rest.

## Decision

- **Who builds what** (the Sponsor, 2026-10-17):
  - **Claude builds the crew's own code, its roles included. The crew does not build itself** (ADR 0008, ADR 0013).
  - **Claude builds a new project's alpha, and the crew takes it over** at a date set for each project, not at the alpha's end.
  - **Claude makes the first run of anything that measures or changes the crew's own setup,** such as the first model experiment. The crew may run later iterations.
  - **The crew does the ongoing work in the projects it runs.** Its share grows by taking projects over and by gaining roles.
- **Onboarding is eight steps in a fixed order:**
  1. the Sponsor creates the repository from `mqucifer/project-template`;
  2. the Sponsor gives the crew's Apps access;
  3. Claude builds the alpha from the template, to an intent agreed with the Sponsor while building: the Sponsor's purpose, what a release is, and the bar for done. Claude writes it into the record's `intent` (`.crew/project.yaml`) in the alpha's pull requests, which the Sponsor merges;
  4. `crew design` runs once on the finished alpha, so the Architect owns the design from the start. Claude reviews the design PR against the alpha, and the Sponsor merges it;
  5. the Sponsor sets branch protection requiring the template's `checks` job;
  6. the Sponsor sets up the deploy target, when a release is a deployment;
  7. the handover check passes: `crew ready` reaches level 2 with nothing missing. This is what Claude must pass;
  8. `crew onboard` runs as the takeover, the Sponsor confirming the record, and the repository is added to `delivery.repos`. It refuses a repository below level 2 and names what's missing.
  
  The Sponsor keeps creating repositories and their rules, because the crew's App has no admin (crew#566). Every crew-built project shares the one board. Steps 1, 2 and 6 and the required settings are one script the Sponsor runs, `scripts/new-project.sh`, at creation, and step 5 is the same script's `--protect` once `checks` is green: a template copies files, not settings.
- **The alpha is handed over at readiness level 2,** so the crew gets no skeleton Goal and its first Goal on the project is real work.
- **The handover check is the one Definition of Done for Claude and the crew** (Scrum Guide: teams on one product share one). It is `crew ready` running the handover checklist (R1: 28 items in 7 groups) against a repository the crew didn't build: the record and design, a layout the crew's view and edit tools handle, CI that runs as the sandbox does, tests that pin every behaviour the alpha promises, the repository's settings, and the docs and usage notes.
- **Size and shape limits are reported at handover, never refused.** A repository grows whatever size it starts at, and the crew's focused views already handle growth (crew#231, crew#583, crew#591). The map lists files that aren't Python by name only, so a large part that isn't Python is the one place growth is weakly handled.
- **What the alpha leaves the crew:**
  - **the decisions later work must keep,** as ADRs in the project's `docs/decisions/`. The panel, the settle step and the split read them for every epic (`flows/project_log.py`). An epic's record rows alone reach only that Goal's epics;
  - **what it delivered, filed as a closed Goal** with its epic and stories Done and their criteria. Later splits see that work as delivered (`flows/delivered.py`), and bugs have accepted criteria to be judged against (ADR 0027).
- **No separate context file for agents (such as AGENTS.md).** The crew's roles see only what crew code puts in their prompts, so a file helps only if it's added to every prompt. The one controlled study found that generated context files lowered success in 5 of 8 settings and cost about 20% more (Gloaguen et al., 2026). The facts go in the record, the design and the decision log, which the roles already read. What the roles are told and what is gated is made visible instead (crew#756).
- **What is the same in every project comes from a template repository,** `mqucifer/project-template`: the CI workflow that runs the design's setup and checks on every pull request, read from the record, `board.yml`, and a `.gitignore`. It holds nothing that depends on the language. `crew onboard` proposes any template file a repository is missing.
- **A repository that reaches the crew below level 2 gets a walking skeleton as its first Goal** (Cockburn; Freeman and Pryce). That was every new project before the alpha, and it stays the path for one the crew must start itself. The Goal's text is the same for every project, kept in the crew repository. `crew onboard <repo> --skeleton` files it on the Sponsor's yes, which is their authorship: the flag says the crew starts the project itself, so onboarding comes first and isn't refused below level 2.
  - **Its outcome:** every check the design names runs and passes, in the sandbox and in CI on every pull request. It doesn't deploy.
  - **Who builds it:** the normal pipeline, with the Developer building what the Architect's design names.
  - **One epic:** the skeleton has one outcome, so the Product Owner proposes exactly one epic for it, where every other Goal gets 2 to 5. The project's pages, features and behaviour belong to its own Goals.
  - **Its split:** one story first and alone, making every check run and pass.
  - **Placeholders:** each is marked, starting a line in a comment. The story that replaces one rewrites it whole and drops the marker, and needs no declared contract change. The marker is bookkeeping, so no test checks for it.
  - **A design fault in it** (setup failing, or a check's own tool missing) sends the design back to the Architect with the output, and the Sponsor merges the revision.
- **`board.yml` comes from the template.** Records protect it from stories.
- **A readiness check replaces the usable-record precondition** (crew#132). It runs at every tick and as `crew ready <repo>`, and names what's missing and who fixes it. It has two levels:
  - **Level 1, enough for the skeleton:** repository and access, the required settings, `board.yml`, record, design, and the deploy target.
  - **Level 2, enough for any Goal:** CI green on the default branch, protection requiring its checks, and the design's lockfile present.
  
  Below level 2, only the skeleton Goal is worked. A Claude-built alpha reaches level 2 before the crew takes it over, and `crew onboard` files no skeleton Goal without `--skeleton`.
- **The required settings are the ones the crew's code relies on:** issues on, squash merging allowed, delete branch on merge, Actions on. The repository is public, or on a plan where branch protection is enforced.
- **Rules a machine can check are checked by machine, not left to the Code Reviewer:**
  - the design names its lockfile in its own field, whatever the language, and the design check refuses a design that names none, or one no setup tool writes (Python: `uv.lock` from `uv sync`);
  - setup is one command, and shell operators are refused (§19.4);
  - for a new repository, the design declares no changes from today.
- **The crew never commits OS or cache files:** every worktree excludes them, whatever the project's `.gitignore` says.
- **Each worktree has its own home directory inside it,** ignored by git and removed with the worktree, so what setup installs is there for the checks in any language. Downloads stay shared through the sandbox's one cache.
- **Tools follow the language, not the image.** The coverage map runs for every Python part, through the project's own setup and Python.

## Consequences

- **Local-model time goes to real Goals, not to onboarding.** Claude's time goes into the alpha instead.
- **The handover check is built** (crew#757): `crew ready` runs the checklist after the levels, and `--setup` runs the design's setup once in the sandbox, on a copy. What only a person can judge, such as whether every promised behaviour has a test, it names as not checked. A tick runs the levels only.
- **A design fault is found before the takeover:** the Architect designs against code that exists, and the alpha's CI has already run the design's checks.
- **The Sponsor's steps are named, and scripted:** creating the repository, access, branch protection and the deploy target. A missed one shows up in the standup, not at a failed deploy.
- **crew-presentation was the skeleton path's proof** (M4). Which alpha first proves the handover is set when the plan reaches it.
- **The non-Python proof comes with the print project,** a fresh repository from the template that mines the prototype (M13 on the Crew roadmap). Its takeover is the crew working on a running codebase it didn't build, as every takeover is.
- Projects onboarded before this are checked against level 2 at their next tick. sprint-metrics' design gains the lockfile field, naming its `uv.lock`.

## Changelog

- 2026-10-13: Accepted. The Sponsor decided each part in chat while planning crew#690.
- 2026-10-14: The skeleton Goal is one epic. Held to the usual minimum of two, the Product Owner padded the first one with the site's pages (Sponsor, in chat, after the crew-presentation proof).
- 2026-10-14: Template repository, setup script, tool-written lockfile and worktree exclude, from the first proof run's four gaps (plan Part 7). The Sponsor chose each in chat.
- 2026-10-15: A placeholder is rewritten whole, and its replacement drops the marker. Held to editing by name, crew-presentation's stories reached for a whole rewrite twice, `build.py` (story 73) and `tests/test_tokens.py` (story 84), and the second was refused. A test quoting the marker made a real file look like a placeholder (Sponsor, in chat, after the second).
- 2026-10-17: A new project starts from an alpha Claude builds, handed over at readiness level 2 with no skeleton Goal, in place of the skeleton for every new project. Added: who builds what, `crew design` run on the finished alpha, the handover check as the one Definition of Done, size limits reported and never refused, where the alpha's decisions and delivered work live, and no AGENTS.md. Onboarding had taken three days of local-model time on crew-presentation. The Sponsor decided each part in the M6 planning session (crew#755).
- 2026-10-17: Onboarding moves after the handover check. Claude writes the alpha's record to the intent agreed with the Sponsor while building; `crew onboard` is the takeover, run once `crew ready` reaches level 2 with nothing missing, and refuses a repository below level 2. The skeleton Goal is filed only with `crew onboard --skeleton`, for a project the crew starts itself. Onboarding was step 3, before the alpha, so it would have filed a skeleton Goal for every alpha. The Sponsor decided it in chat, recorded on crew#757.
