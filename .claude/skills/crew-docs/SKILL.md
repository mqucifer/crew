---
name: crew-docs
description: Bring the crew's docs up to date with everything merged since docs/.docs-sync, and open a docs-only pull request. Run after a sprint or a batch of merged PRs.
disable-model-invocation: true
context: fork
agent: doc-keeper
argument-hint: "[optional: a commit to sync from, instead of docs/.docs-sync]"
---

# Keep the crew's docs in step with the code

The crew changes quickly: 37 pull requests merged between 2026-09-24 and 09-27,
and most added a line to one doc or none. This brings the docs back in step, as
one pull request that changes documentation and nothing else.

## 1. What has changed

- The last sync is the commit in `docs/.docs-sync` (or `$ARGUMENTS` if given).
- `git fetch` first, then work from `origin/main`, in your own worktree (step 5):
  never in the main checkout.
- List what merged since: `git log --no-merges --format='%h %s' <sync>..origin/main`.
  For each change, read its commit message (it says what and why) and its issue
  (`gh issue view <n>`), and note which docs it already touched
  (`git show --stat <hash>`). Read the code only when the message leaves the
  behaviour unclear.
- Skip what users and operators never see: pure refactors, test-only changes,
  and fixes to something that was never documented.

## 2. Where each change belongs

| Doc | Holds | Put a change here when it… |
|---|---|---|
| `docs/running-the-crew.md` | The operator's guide: commands, what a tick does, config, deploy, observability | adds or changes a command, a flag, a config key, a deploy step, or what the operator sees |
| `docs/ways-of-working.md` | The constitution: roles, authority, the gates, the flows, the rules agents are shown | changes what a role may do, how a gate judges, or how work flows (refinement, design, delivery, review, QA, loops, the retro) |
| `docs/inference-setup.md` | Models, aliases, sampling, effort | changes a model, an alias or how it's called |
| `docs/agent-auth.md` | Credentials, apps, permissions | changes an identity, a permission, or what `crew auth` checks |
| `docs/final-state.md` | The architecture overview | changes the shape of the system: a new role, phase, service or data flow |
| `README.md` | What the crew is, and where to read more | changes what a newcomer should know first |

A change can belong in more than one. A behaviour described in two docs is described the same way in both.

## 3. How to write it

- **Match the doc's voice.** Plain words, short sentences, the reason in a
  clause: the incident or card that caused it, cited as `crew#N` or
  `sprint-metrics#N`.
- **Add or amend. Don't rewrite** sections the changes don't touch.
- **A flow gets a table or a short numbered list**, not a paragraph. When
  several changes are one flow (for example breaking a loop: a story problem,
  the Product Owner's decision, the re-split, the new design note), describe
  the flow once, in one place, rather than a sentence per change.
- **Never renumber `ways-of-working.md`'s sections.** Code, issues and
  prompts cite them (§19). New material goes inside an existing section, or in
  a new one at the end.
- **Nothing secret, nothing personal:** no tokens, no hostnames beyond what the
  docs already name, no prompts.

## 4. Check it against the repository

Everything concrete you write is checked, not remembered:
- **Commands and flags:** `uv run crew --help` and `uv run crew <command> --help`.
- **Config keys and values:** read `src/crew_org/config/org.yaml` and
  `agents.yaml`. Numbers such as capacity or WIP limits come from there.
- **File paths, functions and event kinds:** grep for them.
- **Links between docs:** the target heading exists.

Anything you can't confirm, leave out and list as a question in the pull request.

## 5. Deliver it

- **Work in a worktree of your own, never the main checkout.** A tick may be
  running from the main checkout, and switching its branch changes the code
  under it: on 2026-09-30 this skill checked out its branch there mid-tick, and
  left the checkout on a branch deleted after merge.
  ```bash
  git worktree add ../crew-docs-<short head hash> -b docs/sync-<short head hash> origin/main
  ```
  Do every read, edit and commit there. When the pull request is open, remove
  it: `git worktree remove ../crew-docs-<short head hash>`.
- A pull request from that branch, never a push to `main`.
- Change only files under `docs/`, `README.md`, and `docs/.docs-sync`, which you
  set to the short hash of the `origin/main` you synced to.
- The pull request's body:
  - a table: change (with its PR or issue) → doc and section → what was added or amended
  - what you skipped, and why
  - **Questions**: anything you weren't sure of
- End the commit message and the pull request body with the attribution lines
  the session gives you.
