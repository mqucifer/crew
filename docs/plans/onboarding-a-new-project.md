# Onboarding a new project

**Bottom line:** a project the Sponsor creates empty reaches its first landed story through the crew's own steps. Its first Goal is a walking skeleton, the same for every project: the crew builds what the design names until every check runs and passes, through its normal pipeline, and a fault in the design sends the design back to the Architect. A readiness check, run by the crew, says what is missing before a repository is worked. Success means crew-presentation is onboarded again through these steps and its first story lands, and a project that isn't Python is onboarded the same way.

**Status:** 2026-10-13. Every part agreed in chat; ADR 0030 records the decisions. Built: steps A to D and the design-fault path. The proof on crew-presentation is next. The issue is mqucifer/crew#690, in M4 on the Crew roadmap (Sponsor, 2026-10-13: it blocks crew-presentation starting, so it belongs in M4, not a new milestone). Each decision is asked in chat and becomes an ADR by pull request before its part is built.

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

*Changed 2026-10-14 by Part 7a: the repository is created from a template by a script the Sponsor runs, `board.yml` comes from the template, and the same script sets branch protection once the skeleton is green.*

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

**Five rules that would block it today, and what changes:**

| Rule | What it does to a skeleton | Change |
|---|---|---|
| A Goal decomposes into 2 to 5 epics | The Product Owner must invent a second epic, and the only other outcome is the product's: on mqucifer/crew-presentation#44 it proposed the site's pages (mqucifer/crew-presentation#48) | A Goal labelled `skeleton` is exactly one epic, and its text sets scope, not a count: only what makes the design's checks run and pass. Found in the proof (crew#690) |
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

*Changed 2026-10-14 by Part 7b: the lockfile is one the setup tool writes.*

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

## Part 7: what the first proof run found (2026-10-14)

The skeleton Goal mqucifer/crew-presentation#44 became one epic, mqucifer/crew-presentation#47, after the one-epic fix (mqucifer/crew#700). It was split into mqucifer/crew-presentation#49 to mqucifer/crew-presentation#52, and mqucifer/crew-presentation#49 was built as mqucifer/crew-presentation#53. The operator stopped the tick once its outcome was known. Four gaps, all with one cause: the plan was checked against sprint-metrics, whose hand-made scaffold quietly supplied what an empty repository lacks.

| Gap | What happened | What sprint-metrics had |
|---|---|---|
| 1. No CI on pull requests | No story writes a CI workflow. The design names only `deploy.yml`, and the skeleton's D3 refuses anything the structure doesn't name. CI never goes green, so level 2 can't be reached | A workflow written by hand |
| 2. The producer's contract unseen | The Architect's design note guessed the package's layout: a top-level `"major"` in `./.package/schema.json`. The real `delivery-history-2026-10-13` names its version in `manifest.json` (`schema_version: "1.5.1"`), carries three schemas, and nests them in a directory. mqucifer/crew-presentation#52's third criterion would fail every real package | It consumes nothing |
| 3. A lockfile no tool writes | The Architect chose pip and `requirements.txt` with hashes. The Developer can't run a resolver, so the hashes were invented (`a1b2c3d4…`). Its repair had setup strip them and install by version, so the lock checks nothing | `uv sync` writes `uv.lock` during setup, and the crew commits it |
| 4. No `.gitignore` | mqucifer/crew-presentation#53 commits `.DS_Store` and `__pycache__/*.pyc`. The crew commits whatever is in the worktree | A `.gitignore` |

None reverses a decision. Part 4's "no changes to the design step for a new project" was wrong in one respect: the lockfile (7b).

### 7a. A template repository (Sponsor, 2026-10-14: "These are pointing to be needing to use a template repo. Not one offs")

What is the same in every project comes from `mqucifer/project-template`, a GitHub template repository. What comes from the design stays in the skeleton Goal. The earlier objection to a template was a Python-shaped skeleton (Part 2's "Not a Python template"), and the template holds nothing that depends on the language.

| From the template | From the skeleton Goal |
|---|---|
| `.github/workflows/checks.yml`: reads the sandbox image, setup and checks from `.crew/project.yaml`, and runs them on every pull request. It passes when the design names no checks yet, so the record and design pull requests aren't blocked. CI always matches the design, and no story writes it | The files the design's structure names, as marked placeholders |
| `.github/workflows/board.yml`, moved out of the `crew onboard` pull request | Setup, and the lockfile its tool writes (7b) |
| `.gitignore`: OS files, common caches, `.crew-home/`, `.package/` | |

**How a project gets it (Sponsor, 2026-10-14: option A).** A new repository is created from the template. `crew onboard` also proposes any template file a repository is missing, so crew-presentation gets them now, and an older project catches up when the template changes; GitHub copies a template once.

**One script the Sponsor runs,** `scripts/new-project.sh <name>`, does Part 1's manual steps. It runs as the Sponsor because they need admin, which `quarters-claude` and the crew's Apps don't hold (crew#566). A template copies files and branches only, not settings.

| Part 1 step | In the script |
|---|---|
| 1. Create the repository | `gh repo create` from the template, public |
| Required settings | Issues, squash merging, delete branch on merge, Actions |
| 2. Access | Invites `quarters-claude` with Write (accepted with its own token); adds the repository to the Apps' installation unless they have every repository; checks that `BOARD_APP_PRIVATE_KEY` reaches it |
| 7. Deploy target | Pages with GitHub Actions as its source, when asked for |
| 6. Branch protection | `--protect`, once the skeleton has made CI green: pull requests, the approver's review, and the template's `checks` job required. Not at creation: the design's pull request names checks nothing can pass until the skeleton lands, and judging pull requests by the base branch's design instead would block a design revision (Part 2, option B). Corrected while building F |

What stays outside: `crew onboard` and `crew design`, which the Sponsor answers and merges, and `delivery.repos`, by pull request. The readiness check names `--protect` when level 2 waits for it.

### 7b. The lockfile is written by a tool (Sponsor, 2026-10-14: option A)

A Python project uses the crew's existing path: `pyproject.toml` and `uv.lock`, with setup running `uv sync` (installing uv first where the image lacks it, inside the setup script). The crew commits what setup writes, as it does in sprint-metrics. Every language follows the same rule with its own tool: `npm` writes `package-lock.json`, `cargo` writes `Cargo.lock`. The design check refuses a lockfile that no setup tool writes, such as a requirements file, so a model is never asked to write hashes. Part 4's lockfile row changes to this.

### 7c. The producer's usage notes are shown to the roles (Sponsor, 2026-10-14: option A)

ADR 0021 says records and Goals point to the source's usage notes and keep no copy, and crew-presentation's record does: "used as the crew's README says". The crew's README section "Data packages: how projects use them" answers mqucifer/crew-presentation#52 exactly: a consumer checks the MAJOR of the manifest's `schema_version`. Nothing follows the pointer.

- **A (agreed):** the record gets a `uses:` list (`repo`, `file`, `section`). The crew fetches each section from the producer's default branch and shows it to the Architect, the Business Analyst, the Developer and QA, as "How this project uses <repo>". The design system's pointer uses the same field. `crew onboard` asks for it.
- **B (not chosen):** the producer's tag pull request commits its notes and schemas into the consumer. Against ADR 0021: copies go stale.
- **C (not chosen):** the Developer reads `./.package/` after setup. Too late: the Architect and the Business Analyst decide before setup runs.

### 7d. The crew never commits OS or cache files

Every worktree gets an exclude (`.git/info/exclude`) for `.DS_Store`, `__pycache__/`, `*.pyc` and `.crew-home/`, in every project, whatever its `.gitignore` says.

### 7e. Two role behaviours, as evidence (parked, not planned here)

- The Architect's design note made setup a stub, against the skeleton Goal's outcome ("setup installs everything those checks use").
- The Developer's repair weakened the lock to pass its checks.

### 7f. The proof restarts

1. mqucifer/crew-presentation#53 and mqucifer/crew-presentation#49 to mqucifer/crew-presentation#52 are closed.
2. `crew onboard crew-presentation` proposes the template's files and the `uses:` pointers; the Sponsor merges.
3. `crew design crew-presentation --reason` names the lockfile rule; the Sponsor merges the design with `uv.lock`.
4. The Sponsor runs the script's settings part on crew-presentation, for branch protection.
5. The Sponsor labels mqucifer/crew-presentation#47 `needs:rework`, and the Business Analyst splits it again (the existing path, accounting for the closed stories). Then Part 6 continues from step 4.

## Build order

Each step is a commit, grouped into pull requests merged one at a time. Each change at a boundary ran once for real before its pull request.

| PR | Steps | What landed |
|---|---|---|
| mqucifer/crew#691 | The plan and ADR 0030 | This file; ADR 0030; ADR 0006 and ADR 0024 edited to match |
| mqucifer/crew#692 | A: the sandbox (Part 5) | A home inside each worktree, removed with it; the coverage map for every Python part, through the project's own setup and Python |
| mqucifer/crew#693 | B: the design step (Part 4); C: the readiness check (Part 3) | The lockfile field, shown in the design pull request; setup refused with shell operators; no changes from today before level 2. The two levels, `crew ready <repo>`, the standup's sections, other Goals held at level 1 |
| mqucifer/crew#694, mqucifer/crew#695, landed on `main` together | D: the skeleton Goal (Parts 1 and 2); the design-fault path (Part 2, option B) | The standard text; `crew onboard` adds `board.yml` to the record's pull request and files the Goal on the Sponsor's yes; marked placeholders excused by the removal guard. A skeleton story blocked on the design sends it back to the Architect, the Sponsor merges the revision, and the story goes back to Ready, once. Split from D because putting a blocked story back to work was a move the crew had never made |
| mqucifer/crew#700 | The skeleton Goal is one epic | Found in the proof: held to two epics, the Product Owner padded the skeleton with the site's pages |
| | F: the template (7a) | `mqucifer/project-template`'s files, by pull request there; `crew onboard` proposes missing template files; `board.yml` comes from the template; `scripts/new-project.sh` |
| | G: the lockfile and the exclude (7b, 7d) | The design check refuses a lockfile no setup tool writes; every worktree's exclude |
| | H: usage notes (7c) | The record's `uses:` field, asked by `crew onboard`, fetched and shown to the four roles |
| | E: the proof (Part 6, restarted by 7f) | crew-presentation, by the operator and the Sponsor |

## Waiting

- crew-presentation is ticked no further until this lands; ticks run with `--repo sprint-metrics`.
- crew-presentation is ticked no further until F, G and H land (2026-10-14); mqucifer/crew-presentation#53 stays open as evidence until 7f.
- mqucifer/crew-presentation#30 to mqucifer/crew-presentation#43, the technical epics and stories the first tick filed, are closed when the proof starts (Part 6).
