# Onboarding a new project

**Bottom line:** a project the Sponsor creates empty reaches its first landed story through the crew's own steps. Its first Goal is a walking skeleton, the same for every project: the crew builds what the design names until every check runs and passes, through its normal pipeline, and a fault in the design sends the design back to the Architect. A readiness check, run by the crew, says what is missing before a repository is worked. Success means crew-presentation is onboarded again through these steps and its first story lands, and a project that isn't Python is onboarded the same way.

**Status:** 2026-10-13. Every part agreed in chat; ADR 0030 records the decisions. Nothing built. The issue is mqucifer/crew#690, in M4 on the Crew roadmap (Sponsor, 2026-10-13: it blocks crew-presentation starting, so it belongs in M4, not a new milestone). Each decision is asked in chat and becomes an ADR by pull request before its part is built.

**The condition on all of it (Sponsor, 2026-10-13):** the crew onboards projects often enough to plan this for success, and a new repository's skeleton is never its final state.

## Why now

crew-presentation is the first project the crew took from an empty repository. sprint-metrics started from a scaffold written by hand on 2026-09-18 ("Enough structure that CI is green and the crew has something to branch from"), so the crew had never met an empty one. After crew-presentation was unpaused (mqucifer/crew#689), the first tick filed technical epics and nine stories, and no story could land:

| What broke | Where it should have been caught |
|---|---|
| The design was proposed against a record ADR 0021 had said to rewrite (one rerun) | Onboarding: the record |
| The design's checks fetched data inside a sandbox with no network (two reruns, found by the operator, not the crew) | A real run of the design |
| Setup `pip install -r requirements.txt && python tools/fetch_package.py` reaches pip with `&&` as a package name: the crew runs commands without a shell | A real run of the design |
| The design's dependencies leave out pytest, which every check uses | A real run of the design |
| What setup installs is gone before the checks run: each sandbox command is a fresh container, and pip installs under `HOME`, outside the worktree (verified 2026-10-13) | The sandbox (Part 5) |
| No `requirements.txt`, `build.py` or tests exist, and each story must pass all four checks: mqucifer/crew-presentation#35 was blocked on "No module named pytest" | A skeleton |
| Changes declared against files that don't exist became stories (mqucifer/crew-presentation#37, mqucifer/crew-presentation#39) | The design, for a new repository |
| GitHub Pages was never enabled; the first deploy would have failed | A readiness check |

Five of the eight fail at once when the design's setup and checks are run for real on a skeleton. That is the rule this repository holds Claude to: boundary code makes one real call before it lands.

## The standards it follows

- **Walking skeleton** (Alistair Cockburn; Freeman and Pryce, *Growing Object-Oriented Software, Guided by Tests*): the thinnest implementation that builds, tests and deploys end to end, made first, and replaced as the real work arrives.
- **Operational readiness review:** a checklist a service passes before it is worked on or run, each item checkable.

## Part 1: the steps, in order (agreed 2026-10-13)

The Sponsor creates the repository; the crew checks it (Sponsor, 2026-10-13). Creating repositories and changing branch protection need admin, which the crew's App doesn't hold, by the Sponsor's decision (crew#566). Every crew-built project shares the one board (organization project 1), so a new project needs no board of its own.

| # | Step | Who | Needs | How the crew checks it |
|---|---|---|---|---|
| 1 | Create the repository in mqucifer, with a first commit (a README), so the onboarding pull request has a branch | Sponsor | | The repository and its default branch exist |
| 2 | Give the crew's two Apps (crew, approver) access, add `quarters-claude` with Write, and add the repository to the organization secret `BOARD_APP_PRIVATE_KEY`'s access if it is limited to selected repositories | Sponsor | 1 | The crew can read and comment on it |
| 3 | `crew onboard`: the record's intent | The crew; the Sponsor merges | 1, 2 | The record exists with every required answer |
| 4 | `crew design`: the design | The crew; the Sponsor merges | 3 | The design exists, and the Code Reviewer passed it |
| 5 | The skeleton Goal (Part 2): every check runs and passes, in the sandbox and in CI | `crew onboard` files it on the Sponsor's yes; the crew delivers it | 4 | CI is green on the default branch |
| 6 | Branch protection: pull requests required, the approver's review, and the CI checks from step 5 as required checks | Sponsor | 5 | Protection names those checks |
| 7 | The deploy target, when a release is a deployment: Pages with GitHub Actions as its source, or a registry | Sponsor | 1 | The target exists |
| 8 | Add the repository to `delivery.repos` | A pull request; the Sponsor merges | 3 to 7 | The readiness check passes. A repository that fails isn't worked, and the standup names what is missing |

What changes from how crew-presentation was set up:

- `board.yml` moves into the `crew onboard` pull request. It is the crew's own automation, the same in every project, and was copied by hand.
- Required checks come after the skeleton. Branch protection can only require a check that has run once.
- **Required settings,** checked by the readiness check (Sponsor, 2026-10-13: crew-presentation's differing setting was an oversight). Each is one the crew's code relies on:

  | Setting | Why |
  |---|---|
  | Issues on | The board and every role work on issues |
  | Squash merging allowed | The crew merges every pull request by squash (`flows/merge.py`) |
  | Delete branch on merge on | The crew never deletes its own branches; crew-presentation already keeps them |
  | Actions on | CI and `board.yml` |
  | Public, or a plan where branch protection is enforced | §19.7: on a private repository of a free organization GitHub enforces nothing |

## Part 2: the skeleton is a project's first Goal (agreed 2026-10-13)

**Not an Architect's pull request (Sponsor, 2026-10-13):** "It more reads like an extension and a first golden goal. An architect determines the needs, devs and DevOps implement their pieces." The Architect has already said what is needed, in the design. A standard first Goal, the same for every new project, has the Developer build it, through the normal pipeline: the Product Owner proposes the epic, the Sponsor approves it, the Business Analyst splits it, QA checks the criteria, the Developer delivers, the Code Reviewer and the DevOps Engineer's deploy review judge it, and QA verifies it. That pipeline is then proven on a new repository too. Today the DevOps Engineer reviews and the Developer writes CI. A DevOps role that implements is M7's ("DevOps releases"), and this Goal is natural first work for it, but doesn't wait for it.

**Its outcome:** every check the design names runs and passes, in the crew's sandbox and in CI on every pull request. It doesn't deploy (Sponsor, 2026-10-13): the readiness check covers the deploy target (Part 1, step 7), and the first real epic makes the first deploy.

**Four rules that would block it today, and what changes:**

| Rule | What it does to a skeleton | Change |
|---|---|---|
| Every story passes every check | Only a story that makes all of them run can land first | The split puts one skeleton story first and alone: the smallest files that make every check run and pass. The rest wait, as stories in one epic already do |
| `board.yml` is never-touch in every record | No story may write it | It moves into the `crew onboard` pull request, the crew's own onboarding change |
| The removal guard refuses removing a merged test unless a contract change declares it (crew#316) | The stories that replace the placeholders are refused | Skeleton placeholders carry a marker, and replacing a marked placeholder needs no declaration |
| The record is protected | A design fault, such as a missing dependency or a setup that can't run, blocks the skeleton story, and the Developer can't fix it | When setup fails, or a check's own tool is missing, the crew reruns the design with that output as the reason, and the Sponsor merges the revision (Sponsor, 2026-10-13: option B) |

**Who files it (Sponsor, 2026-10-13):** the standard text lives in the crew repository, and `crew onboard` files it when it finishes. Only the Sponsor authors Goals, and their yes at the end of onboarding is the authorship, as their yes opens the record's pull request.

**Concerns it must answer:**

- **Not a Python template.** The Goal's text says what must be true, not which files. What the skeleton holds comes from the project's design (its image, setup, checks and parts). Proven on a project that isn't Python.
- **The skeleton doesn't become the architecture.** It holds only what the design's structure names, every placeholder is marked, and the first stories replace them, as sprint-metrics' `tests/test_scaffold.py` was replaced by its first story.

## Part 3: the readiness check (agreed 2026-10-13)

It replaces today's one precondition, a usable record (crew#132). It runs at the start of every tick for each repository in `delivery.repos`, and on demand as `crew ready <repo>`, so the Sponsor can check a repository right after creating it. What it checks is Part 1's last column. What is missing goes in the standup's **Not worked** section, each item with who fixes it: the Sponsor or the crew.

**Two levels (Sponsor, 2026-10-13).** Some items can't pass before the skeleton exists: CI can't be green with no code, and branch protection can't require a check that has never run.

| Level | Holds | The crew works |
|---|---|---|
| 1: ready for the skeleton | The repository and the Apps' access, the required settings, `board.yml`, a complete record, a merged design, and the deploy target when a release is a deployment | The skeleton Goal only |
| 2: ready for any Goal | Level 1, and the skeleton landed: CI green on the default branch, branch protection requiring its checks, and the lockfile the design names (Part 4) | Every Goal |

Other Goals wait at level 1, as product epics already wait for technical ones. A Goal filed before the design exists, as the skeleton Goal is when `crew onboard` finishes, waits for level 1.

## Part 4: the design step (agreed 2026-10-13)

The Code Reviewer's judgement passed crew-presentation's design without a lockfile, without CI on pull requests, and with a setup that can't run. Each rule a machine can check gets a mechanical check, in the step where it can fail:

| Gap | Handled by |
|---|---|
| Changes declared against files that don't exist (mqucifer/crew-presentation#37, mqucifer/crew-presentation#39) | For a new repository, the design declares no changes from today. The skeleton Goal carries the work |
| Setup with `&&`, which the crew runs without a shell | Setup stays one command. The design check refuses shell operators (`&&`, `\|\|`, `\|`, `;`, redirects), so nothing reaches a shell (§19.4); the Architect writes a setup script instead |
| No CI running the checks on every pull request (§19.7) | Readiness level 2 (Part 3): CI green, and branch protection requiring its checks |
| A check that can't get its inputs inside the sandbox (crew Discussion 661) | The skeleton Goal: such a check fails the skeleton story, and the design goes back to the Architect (Part 2, option B) |
| No lockfile (§19.3, §19.7) | A new design field names the project's lockfile, whatever its language (`uv.lock`, `package-lock.json`, a requirements file with hashes, `Cargo.lock`). The design check refuses a design that names none, and readiness level 2 confirms the file exists (Sponsor, 2026-10-13) |

## Part 5: the crew's sandbox and tools work for any project (agreed 2026-10-13)

**What setup installs is gone before the checks run.** Each sandbox command is its own container: read-only, with `HOME=/tmp` on a fresh tmpfs, and only the worktree carried from one command to the next. In crew-presentation's image, run with the crew's sandbox flags (2026-10-13), `pip install -r requirements.txt` exited 0, and the next command failed with `ModuleNotFoundError` and "No module named pytest". pip can't write the image's site-packages, so it installs under `HOME`, which the next container doesn't have. The default Python projects work only because uv puts `.venv` inside the worktree. A project with its own image works only if its setup happens to install into the worktree, which nothing tells the Architect. crew-presentation's design could not have worked even with pytest listed and no `&&`.

**A home inside each worktree (Sponsor, 2026-10-13).** Every story's worktree gets its own home directory inside it, ignored by git, so whatever setup installs (pip's user installs, any tool's files under `HOME`) is still there for the checks, in any language. It lasts as long as the worktree and is removed with it: the crew removes a story's worktree when the story is done (`git_ops.py`, `close`). A shared install was rejected: one story could pass on another's packages (§19.7, a build from a clean checkout and its lockfile), and stories running side by side would overwrite each other. Downloads stay shared through the sandbox's one cache mount, where pip's downloads already land, so a second worktree installs from the cache, not the network. Measured 2026-10-13: a sprint-metrics `.venv` is about 34 MB, the shared cache 160 MB; crew-presentation's would be about 100 MB a worktree, freed with it. Pruning the shared cache waits until it is big.

**The coverage map for Python parts in any image (Sponsor, 2026-10-13).** The map is the only tool chosen by image, not language: it raises "has its own sandbox image; the map is Python only" (`tools/coverage_map.py`), and it installs and measures with uv. It instead runs for every Python part, installing pytest-cov through the project's own setup and measuring with the project's own Python. The context-record plan deferred this "until one exists" (its Q4); crew-presentation is that one.

## Part 6: the proof (agreed 2026-10-13)

**M4's proof is crew-presentation alone.** M4 is done when the first story from Goal mqucifer/crew-presentation#10's epics lands:

1. Close mqucifer/crew-presentation#30 to mqucifer/crew-presentation#43, the technical epics and stories filed against the old steps, and delete the crew's leftover `feat/*` branches there.
2. The Sponsor turns on delete-branch-on-merge, and enables Pages with GitHub Actions as its source.
3. `crew onboard crew-presentation` adds `board.yml` (already there by hand) and, on the Sponsor's yes, files the skeleton Goal. `crew design` runs again: setup without shell operators, a lockfile named, no changes from today.
4. Level 1 passes; the crew delivers the skeleton Goal. A design fault goes back to the Architect on its own (Part 2).
5. The Sponsor sets branch protection to require the skeleton's CI checks; level 2 passes.
6. The Product Owner proposes Goal mqucifer/crew-presentation#10's epics. The operator checks that they carry failing the pull request that changes `delivery-history.tag` on a major version the site can't display (the design has no CI job for it before merge), and the Sponsor approves them.

**The proof on a project that isn't Python waits for the first real one (Sponsor, 2026-10-13),** most likely the print prototype on the roadmap's ladder: it is that milestone's entry check, not M4's. Waiting is a small risk: the skeleton Goal's text names no language, the home inside each worktree works for any tool, and nothing built here assumes Python.

## Build order

Each step is a commit; steps are grouped into pull requests, merged one at a time in this order, each body saying where it sits. Each change at a boundary runs once for real before its pull request: the sandbox in crew-presentation's image and in the default one.

| PR | Steps | What lands |
|---|---|---|
| 1 | The plan and ADR 0030 | This file; ADR 0030; ADR 0006 and ADR 0024 edited to match |
| 2 | A: the sandbox (Part 5) | A home inside each worktree, removed with it; the coverage map for every Python part, through the project's own setup and Python |
| 3 | B: the design step (Part 4); C: the readiness check (Part 3) | The lockfile field; setup refused with shell operators; no changes from today before level 2. The two levels, `crew ready <repo>`, the standup's section, other Goals held at level 1 |
| 4 | D: the skeleton Goal (Parts 1 and 2) | The standard text; `crew onboard` adds `board.yml` to the record's pull request and files the Goal on the Sponsor's yes; marked placeholders excused by the removal guard; the skeleton story split first and alone; a design fault on the skeleton story sends the design back with its output |
| | E: the proof (Part 6) | crew-presentation, by the operator and the Sponsor |

## Waiting

- crew-presentation is ticked no further until this lands; ticks run with `--repo sprint-metrics`.
- mqucifer/crew-presentation#30 to mqucifer/crew-presentation#43, the technical epics and stories the first tick filed, are closed when the proof starts (Part 6).
