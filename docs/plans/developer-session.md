# The Developer works in a session: plan

**Status:** accepted (mqucifer/crew#789, 2026-10-18). It comes from the design discussion on mqucifer/crew#778 (2026-10-17), decisions 1 to 9. Stage R is done, and the Sponsor has read its results (`var/notes/778/replay.md`). Stage A is ADR 0032, which holds the decisions, P1 to P6 and what the replay changed; where this plan and the ADR differ, the ADR is current.

## Why

**The crew is built to get the answer right without feedback, and a developer works by getting feedback fast** (crew#778's synthesis). The Developer can't run anything during its call. So each incident was answered with more prediction up front: a rule, a story naming more tests, a selection that tries to pre-pick every file, a size limit. Meanwhile the real loop is slow and lossy: verify runs only after the answer, each repair is a full new call, and a refusal can replace the failure it followed.

The evidence (`var/notes/first-pass-evidence.md`, `var/notes/778-context-selection.md`):
- In 10 of 12 failed first answers, the file or fact the failure needed wasn't shown.
- The file listing takes about 30k of the 50k characters shown. Any sprint-metrics Python file over about 20k characters can't be seen whole by either route.
- 64% of the Developer's asks were for files the ceiling had just left out. 48 of 69 recorded reads were of test files.
- Repairs are cheap once the failure names the missing fact. First answers are expensive because they predict: one reasoned 142,842 tokens over 73 minutes.
- In mqucifer/sprint-metrics#662, attempts 2 and 4 fixed the bug and were discarded for dropping a test retirement. The next attempts saw only the refusal, so the fix was lost.

## What was decided (crew#778)

| # | Decision |
|---|---|
| 1 | **A session driven by tools.** The Developer calls view, search, edit and run as plain tool calls, with code inside the edit tools (the probe: 18 of 18 code arguments survived exactly, `var/notes/778/`). It ends with a short structured `done`; the end gates read the branch's diff. |
| 2 | **The tools.** See: `view(path, from, to)` or `view(path, definition)`. Find: `search(text)` (the whole repository, `.package/` included), `find(name)` (a definition, its callers and importers), `tests_for(definition)` (the coverage map). Change: `replace_text`, `replace_definition`, `write_file`, each applied at once, with a reply saying whether the file parses and lints. Check: `run`, the targeted tests or the whole suite, or lint. Finish: `done`, `blocked(what's missing)`. No shell, no git, no network. QA is expected to use the same tools in time. |
| 3 | **What ends a session.** `done`, then the gates. `blocked`, routed by what's missing. Or it stops progressing: the same first error in the same test after 3 runs with edits between, or a 2-hour backstop of model time per story. Older tool output shrinks to one line; the latest view and run stay whole. The 95k to 181k empty-answer edge isn't assumed: it was measured on prompts that started that size. |
| 4 | **What a session starts with.** The story (its criteria and the tests it declares, always shown); how the project works; a map (the tree with sizes and top-level names); the graph's picks as hints (paths and why); the rules for its tools and its `done`. Near 10k characters, against about 50k today. |
| 4b | **A workspace container per story,** defined by the project on the Development Container specification (`devcontainer.json`). The worktree is mounted; `run` and `search` execute inside; setup once with network, then none; today's sandbox hardening; removed at the end. |
| 5 | **Guards sorted by when they apply.** Workspace (the container enforces it), feedback (a tool reply), a gate on the diff at `done`, or retired. The sort is mqucifer/crew#788: of the Developer's 170 rules, 56 retired, 40 gates, 28 feedback, 11 workspace, 35 that fit none. |
| 6 | **Stories declare the behaviour they change,** mostly through the acceptance criteria. The Developer finds and changes the tests that pin it and declares them in `done`; the gate checks that against the diff. The contract ruling (crew#584) moves to the gate, only on a disagreement. Test-first (Discussion 665) waits on the session's results. |
| 7 | **The code's shape: no change for now.** Strain (crew#192) stays; whether struggling files should trigger it is decided from session records. |
| 8 | **Measures.** The first-attempt rate stays: an attempt is a submission at `done`, judged by review and QA. The sprint measures model time and tokens per story. Everything the session does is tracked as new measures. |
| 9 | **No escalation to a stronger model.** A stuck session goes back to the Business Analyst who wrote the story, with its evidence; an undecided choice goes to the Sponsor. The escalation measure becomes the rate of stories returned. Claude Code escalation retires with the build. |

## Proposed here, for the Sponsor to review

| # | Question | Proposal |
|---|---|---|
| P1 | **How a session fits the tick** | The deliver phase starts a session and runs it to its end, **one session at a time** to start, so the Spark serves one session's calls. The 2-hour backstop bounds a tick's deliver phase. Parallel sessions (the In Progress limit is 3) are a later step, once the records show the Spark's headroom. |
| P2 | **Rework after a gate returns a story** | A **new session** from the branch as it stands, started with the gate's findings and the last `done` statement in place of the hints. Not the old conversation: it carries the context of a different question. |
| P3 | **Commits and recovery** | The crew commits the branch after every green `run` (a checkpoint) and at `done`. A session interrupted by a crash or a stopped tick resumes as a new session from the last checkpoint, told what the previous one's last `done` or `run` said. |
| P4 | **QA and review** | Unchanged at first: they judge the final diff and the `done` statement. QA gets the session's tools (and may run the tests itself) as a later step, when the Developer's session is proven. |
| P5 | **The session's records** | Every tool call is an event: tool, path and name where there is one, result kind, sizes, duration, and the rule behind a refusal. Every `run` records its commands, exit codes and first failure. The measures in decision 8 are computed from these. Nothing goes to telemetry beyond counts (ADR 0010). |
| P6 | **Projects that aren't Python** | `find` and `tests_for` are **Python only** to start, and the tools' descriptions say so. For every other language the Developer uses plain `search` (a text search across the repository). `view`, `search`, `replace_text`, `write_file` and `run` work for any language. |

## Stages

Each stage is one or more PRs from their own worktrees, by Claude (ADR 0030). Every PR that calls a model or GitHub runs once for real through LiteLLM before it opens. Nothing is pulled while a tick runs; ticks stay paused until Stage D's first story.

### Stage R: the replay (first; its results decide the rest)

**Question:** does a tool-driven session converge for this model on stories that really failed, and at what model time and tokens, against the attempts those stories actually took?

- **The prototype:** the session loop outside the crew's delivery path, in `experiments/session_replay/`. It provides the decision 2 tools over a worktree at the story's base commit, `run` in a container from the project's image, and `done` and `blocked`. It runs through the call layer (LiteLLM :4000, the Developer's alias). No board moves, comments, PRs or pushes. Its records go to its own folder, not `var/events`.
- **The cases:** 3 to 5 traced failures from the evidence pack and the analysis, at their own base commits. mqucifer/sprint-metrics#662 is one; mqucifer/sprint-metrics#596, mqucifer/sprint-metrics#567 and a crew-presentation case from 2026-10-15 to 2026-10-17 are the candidates.
- **Measured per case:**
  - whether it reached `done` with the tests green, or ended `blocked` and why;
  - the run that turned green;
  - model time, tokens and reasoning tokens;
  - the tools used, and the files read and edited;
  - the same story's real history from the event log, beside it.
- **Done when:** a short report in `var/notes/778/replay.md`, bottom line first, read with the Sponsor.

### Stage A: the ADRs, from the replay's results

One ADR for the session (decisions 1 to 4b and P1 to P6 as the replay left them), with edits to the ADRs it changes: 0024 (selection), 0025 (the call layer's rounds), 0026 (code in blocks, for the Developer), 0031 (rules sorted by when they apply), and the escalation ADR or policy for decision 9.

### Stage B: the workspace

- The workspace container from each project's definition: sprint-metrics and crew-presentation first, from their current designs, and `crew ready` checks that it builds.
- `.git` read-only or left out of the mount (crew#788's first hard call).
- Clean-up after a session and after a crash.

### Stage C: the session

- The tools of decision 2 in the call layer, over the workspace.
- The endings of decision 3.
- The start of decision 4.
- Context kept bounded (P1, decision 3).
- The records of P5.

Built in `crew_org/session/`, with each model call made by `calls.converse` (crew#778, Stage C). `scripts/run_session.py` runs one session on a story at a commit, outside the tick, for proving it before Stage D wires it into delivery.

### Stage D: delivery

- The deliver phase runs sessions (P1), commits and resumes (P2, P3), and routes `blocked` (decision 9).
- The gates at `done` read the diff: protected paths, workflows and scope; tests prove criteria; changed merged tests declared (decision 6); docs; CI. Then review and QA as now (P4).
- The first real story is the proof, then the stories blocked behind mqucifer/sprint-metrics#662.

Part 1, the gates at `done` and where an ending goes, is built in `flows/gates.py` and `flows/session_end.py` (mqucifer/crew#798). Part 2 runs sessions in the deliver phase and acts on what `session_end.route` returns.

### Stage E: retire

- The Developer's retired rules (crew#788).
- The answer forms and refusals the session replaces.
- Claude Code escalation and its budget.
- The reading ceilings for the Developer (they stay for refinement and QA).
- The guidance budgets lowered to what remains.

### Stage F: measures

- The session's events summarised per story.
- The Sponsor's sprint-metrics Goal for the first-attempt rate as redefined, model time and tokens per story, and the rate of stories returned to the Business Analyst.

## What this doesn't decide

- Parallel sessions (P1's later step).
- QA with the session's tools (P4's later step).
- Test-first (Discussion 665).
- Whether struggling files trigger strain (decision 7).
- Paring the rules down further, which the Sponsor expects once the session is in use.
