# Claude's standards for the crew repo

How Claude builds, proves and lands changes here. These are Claude's rules,
not the crew's: the crew agents have their own, and the two change
independently. The crew's escalations run `claude -p --safe-mode`, so this
file never reaches them. Change it by pull request, like any other file.

## Starting work
- **Work comes from crew issues, in P-label order** (P0 first). The Issues
  view decides; the board's field does not.
- **One worktree per issue:** `git worktree add ../crew-<n> -b <type>/<n>-<summary> origin/main`.
  Types: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `exp`.
  The main checkout is where ticks run, so new work never happens there.
- **Never pull while a tick runs** (`pgrep -f "crew tick"`). A tick imports
  phase modules lazily, so a pull mid-tick mixes versions and crashes it.
  *Guarded by the hook.*

## Changing code
- **Match the code around you:** its naming, comment density and idiom. Comments say why, not what.
- **Big files: one edit, then check it**, by parsing it or running its tests,
  before the next. Batched multi-site edits have corrupted a 1,100-line file.
- **Fix the generator, never its output.** Weak crew output is evidence for
  the role that should have caught it. It isn't something to patch by hand.
- **Agent prompts set scope and delivery, not design.** Before adding a rule,
  ask what the agent was shown. A focused extra pass beats a longer prompt.
- **Explain module moves plainly,** with a before/after import example.

## Done means
- Tests cover the change, and `uv run pytest -q` passes.
- `uv run ruff check .` and `uv run ruff format --check .` pass.
- mypy errors don't rise above the baseline (crew#459).
- **Boundary code has made one real call.** Code at a boundary (a transport,
  a client, an exporter, a CLI flag) has run once for real before its PR,
  through LiteLLM on :4000 for model traffic. Mocks once let a crash reach the
  first tick.
- Docs that describe the change are updated in the same PR.
- **Correctness over speed.** Judge by whether it's right, not by latency.

## Landing it
- **Always a pull request; nothing is pushed to `main`.** "Push" means open a
  PR, even when the credential could bypass protection. *Guarded by the hook.*
- **Commit subject:** `<type>: <what is now true>`. The body says why and
  names the issue as `crew#<n>`.
- **PR body:** `Closes mqucifer/crew#<n>`, then **Why**, **Change** and
  **Verification**. Verification says how it actually ran, not how it could.
- **References are explicit:** `owner/repo#12` or a full URL, which link and
  close; `crew#12` is plain text. Never a bare `#12`: GitHub links it in
  whichever repo the text lands in.
  *Guarded by the hook for `gh` issue and PR bodies.*
- **Crew PRs are merged by the Sponsor; Claude doesn't approve them.** The
  approver App has no access to this repo, by the Sponsor's decision, so
  `crew review --repo crew` only gets a 403. Don't run it, and don't propose
  giving the App access. In a delivery repo, `crew review --repo <name>`
  approves Claude's PRs, which the Sponsor authors and can't approve.
- **When blocked on the Sponsor** (a merge, an approval, a decision), send a
  push notification. They step away.
- **After merge:** close the issue, then remove the worktree and branch
  (`git worktree remove ../crew-<n>`, `git branch -D <branch>`).
- **An issue left open gets a status comment.** When a PR lands part of an
  issue, or work on it stops partway, comment on the issue: each criterion done
  (and by which PR), what's left, and what it waits on. The Sponsor follows the
  work from the issue, not from PR bodies or chat.

## Decisions
- **A decision that changes how the crew works becomes an ADR** in the session
  it's made: across roles, steps or projects, or costly to reverse. A choice inside
  one issue goes in that issue and its PR. The process is kept in
  `docs/decisions/README.md`; read it there. Not into memory.
- **The Sponsor's decisions are asked in chat,** with the options and a
  recommendation, never left in a PR body for them to find.
- **Read the ADRs before changing what they cover.** An ADR says what's true
  now: to change a decision, edit its ADR by PR and add a dated line to its
  Changelog.

## Never
- **An Anthropic API key, anywhere.** Escalation runs on the subscription
  through `claude -p`. A key bills separately. *Blocked by `scripts/pre-commit`.*
- **Model traffic around the proxy.** Everything goes via LiteLLM :4000, and
  nothing probes SGLang on the Spark directly.
- **Unvetted generated code run outside the sandbox.** Released crew-built
  software is ordinary software, and is still deployed in a container.
- **A check weakened to make something pass.** A test deleted to go green counts.

## Commands
```
uv sync --extra dev                                   # once per worktree
uv run pytest -q
uv run ruff check . && uv run ruff format .
ln -sf ../../scripts/pre-commit .git/hooks/pre-commit # once per clone
```
The guard hook is `scripts/claude_pretool_guard.py`, wired up in
`.claude/settings.json`. Its tests are `tests/test_claude_pretool_guard.py`.
