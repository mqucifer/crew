# 32. The Developer works in a session

- **Date:** 2026-10-17
- **Status:** Accepted, partly built. Every part below is decided, and none of it runs in a tick yet. The session is built beside delivery (Stage C, `crew_org/session/`, mqucifer/crew#797): until Stage D of `docs/plans/developer-session.md` wires it in, the Developer answers in one call as ADRs 0024 to 0026 say, and escalation stands (ADRs 0004 and 0005). The rule sort (crew#788) is read by nothing in the tick.

## Context

**The crew is built to get the answer right without feedback, and a developer works by getting feedback fast** ([the synthesis on crew#778](https://github.com/mqucifer/crew/issues/778#issuecomment-6242856875), the Sponsor and the operator, 2026-10-17). The Developer can't run anything during its call. So each incident was answered with more prediction up front: a rule, a story naming more tests, a selection that tries to pre-pick every file, a size limit. The evidence (`var/notes/first-pass-evidence.md`, `var/notes/778-context-selection.md`):
- In 10 of 12 failed first answers, the file or fact the failure needed wasn't shown.
- A sprint-metrics Python file over about 20k characters couldn't be seen whole through either `read_file` or `need_files`.
- Repairs are cheap once the failure names the missing fact. First answers are expensive because they predict: one reasoned 142,842 tokens over 73 minutes.
- In mqucifer/sprint-metrics#662, attempts 2 and 4 fixed the bug and were refused for dropping a test retirement. The attempts after saw only the refusal, so the fix was lost.

**Code survives as a tool-call argument.** The day-one bet against a tool loop (2026-09-18: "a multi-turn edit loop compounds every tool-call mistake") was never measured. [A probe through LiteLLM](https://github.com/mqucifer/crew/issues/778#issuecomment-6243289556) made six real edits three times each. All 18 code arguments survived exactly, ADR 0026's triple-escaping case among them.

**The replay (Stage R) ran the session on five stories that really failed,** at the commits their first attempts started from, through LiteLLM, in a workspace container. Its results are the evidence for this ADR: [the summary on crew#778](https://github.com/mqucifer/crew/issues/778#issuecomment-6245723595), the report (`var/notes/778/replay.md` in the main checkout) and the code (mqucifer/crew#791). In short:
- with pass 3's tools and the history kept whole, all 4 stories that had a solution reached `done` with the crew's gate green, in 2.8 to 32.0 model minutes against 14.9 to 252.6 for their real attempts;
- the fifth, mqucifer/crew-presentation#124, has a criterion no code can meet. Its session read for 44 calls without an edit, a run or `blocked` until the Sponsor stopped it.

Each case ran once per pass, and pass 3 changed the tools and the context rule together, so the figures give a direction, not a measured effect. [The Sponsor accepted the report](https://github.com/mqucifer/crew/issues/778#issuecomment-6250000843), apart from its proposed stops.

## Decision

The Sponsor, on crew#778: decisions 1 to 9 and 4b on 2026-10-17. Then, on 2026-10-18 after the replay: the parse guard, the context rule, pass 3's tools as the build's start (by accepting the report), the stops, and `devcontainer.json` as the workspace's one source. P1 to P6 are from the plan, which the Sponsor approved on 2026-10-18 (mqucifer/crew#789, with P6 as the Sponsor edited it). **Everything in this section is decided and not yet built.**

**1. A session driven by tools.** The Developer works in one conversation of plain tool calls: view, search, edit and run, with code inside the edit tools' arguments. Each edit applies to the draft branch at once. The session ends with a short structured `done`, and the gates at the end read the branch's diff.

**2. Its tools, as pass 3 left them.** They're built for more than the Developer, since the Sponsor expects QA to need them in time.

| Tool | What it does |
|---|---|
| `view(path, from_line, to_line)` or `view(path, definition)` | Part of a file with line numbers: a range of lines, or one Python definition by name |
| `search(pattern, path)` | A regular expression across the repository, `.package/` included, or in one file or folder. A pattern that isn't a valid expression is searched as plain text, and the reply says so. Past 50 lines, it gives each file's count, `src/` and `tests/` first |
| `find(name)` | A Python definition, with its callers and importers, from the graph (ADR 0024) |
| `tests_for(definition)` | The merged tests that run a Python definition, from the coverage map at the story's base (ADR 0024) |
| `replace_text(path, old, new)` | Replaces whole lines that occur once. Part of a line is refused, with that line shown. A miss shows the closest block, numbered |
| `replace_definition(path, name, source)` | Replaces a Python definition or method, by name |
| `write_file(path, content, overwrite)` | Writes a whole file; an existing one only with `overwrite` |
| `run(what, tests)` | Runs in the workspace: named tests, the project's whole checks, or the checks' own lint. It's feedback, not a verdict, and its reply leads with the first error |
| `done` | Names the test that proves each criterion, the merged tests changed and why, and the docs updated. Its description says what the gate returns a branch for |
| `blocked(missing, kind)` | Says what's missing: a file, a fact the story doesn't give, or a choice no one has made |

Every tool:
- **refuses an argument it doesn't take,** listing the ones it does;
- **judges text by its content,** not its extension: no NUL byte in the first 8k bytes, and valid UTF-8 (git's test);
- **replies to an edit with the edited lines, numbered, with 4 lines either side,** and only the lint findings the edit added, from the checks' own lint;
- **doesn't apply an edit that would stop a Python file parsing** ([the Sponsor, 2026-10-18](https://github.com/mqucifer/crew/issues/778#issuecomment-6245222608)). The reply shows the parse error, the lines as they would have been and the original lines, and the session tries again.

There's no shell, git, commit or network.

**3. How a session ends.**
- **`done`:** the gates take over (decisions 5 and 6), then review and QA.
- **`blocked`:** a missing file or fact goes to the Business Analyst who wrote the story, in the Developer's own words. An undecided choice goes to the Sponsor.
- **No progress:** the same first error in the same test after 3 runs, with edits between them.
- **The backstop:** 2 hours of model time per story. That's between the p75 (89 minutes) and p90 (167 minutes) of 35 merged stories, and it's to be tuned from the records.
- **A ceiling,** set high to start ([the Sponsor, 2026-10-18](https://github.com/mqucifer/crew/issues/778#issuecomment-6250000843)): about three times the largest value a successful replay reached. **The starting numbers are here for the Sponsor's review:**

  | Ceiling | Starts at | Largest in a replay that reached `done` |
  |---|---|---|
  | Calls in a row with no edit and no run | 60 | 19 |
  | Views of one file with no edit to it between | 50 | 18 in pass 1, 5 in pass 3 |

  There's no ceiling on one call's reasoning, or on edits without a run. The backstop covers the first, and the gate at `done` covers the second.

No progress, the backstop and a ceiling each end the session as `blocked`. Its evidence goes with it (decision 9). These endings replace the attempt limits. **There are no nudges in the first build.** Whether a warning before a stop helps is a question for evidence, under mqucifer/crew#790. The research found no published measured effect of a nudge. Its one measurement, on a 32B model, found warnings cut repeats without raising the resolve rate (`var/notes/778/research-stuck-detection.md`).

**The context: the history stays whole, with one compaction near 125k tokens** ([the Sponsor, 2026-10-18](https://github.com/mqucifer/crew/issues/778#issuecomment-6244856687)). This replaces decision 3's first rule, that older tool output shrinks to one line.
- **The rule:** every tool reply stays as it was. A session is compacted once, all at once, and only before a prompt would pass 125k tokens. The window is 262,144 tokens, about 196k for input once 65,536 are kept for output.
- **Why:** a session only appends, so SGLang's prefix cache serves almost all of each call's input: 91% to 97% in the replay's whole-history runs, against 73% to 88% with older output cut. Cutting also made sessions view the same files again.
- **The 95k to 181k empty-answer edge isn't carried over.** It was measured on prompts that started at that size. No answer in the replay came back empty, cut off or malformed, with prompts up to 115k tokens.
- **Compaction never ran in the replay:** the largest prompt was 115k tokens, on mqucifer/sprint-metrics#567. So how it compacts isn't decided here.

**4. What a session starts with.**
1. The story: its criteria, and the tests it declares (kept or changed), always shown, at least as the named definitions.
2. How the project works: running its tests and lint, and its parts and conventions, from the project's record and design.
3. A map: the file tree with sizes and each file's top-level names. It replaces today's listing of about 30k characters.
4. The graph's picks as hints: paths, each with why it was picked, and no bodies.
5. The rules for its tools and its `done`, each told once.

Other tests, and the bodies of importers and callers, come through `tests_for`, `find` and `view` when the work needs them. About 10k characters were expected. The replay's starts measured 14.7k to 16.4k, against about 50k today.

**4b. A workspace container per story.** The project defines it on the Development Container specification (`devcontainer.json`, containers.dev), the way OpenHands runs each session in its own runtime container.
- The worktree is mounted, and `run` and `search` execute inside the container.
- Setup runs once at the start, with network. Then the session runs with none.
- It keeps today's sandbox hardening (ADR 0006): not root, all capabilities dropped, no new privileges, and memory, CPU and process caps.
- It's removed at the session's end, and cleaned up after a crash.
- **`devcontainer.json` is the one source of the workspace** ([the Sponsor, 2026-10-18](https://github.com/mqucifer/crew/issues/778#issuecomment-6250085435)). It's the standard file. The project's design points to it, and the Architect owns it.
  - The crew reads only a subset of it: the image, the setup command and the environment.
  - It always applies its own hardening, so nothing in the file can loosen the container. `runArgs`, `mounts`, `privileged`, features and host commands are not honoured.
  - The project's checks come from its design, as today.
- crew-presentation's Playwright image and `.package/` become part of its workspace, not special cases.
- `crew ready` checks that a project's workspace builds.

One environment for working and testing removes the mismatch between host and container (crew#780). In the replay, mqucifer/crew-presentation#83's first run failed on `net::ERR_INTERNET_DISCONNECTED`. That was the fact behind its real failure, and the session fixed it from the run.

**5. Rules sorted by when they apply** (ADR 0031):
- **The workspace enforces safety.** Those rules become properties of the container, kept in the catalog as config, with nothing told and no refusals.
- **Feedback comes during the session as tool replies.** It's never a discarded answer or a counted failure.
- **Gates check the diff at `done`.**
  - Policy: protected paths and workflow files are untouched unless the story allows it, and nothing is outside the story's scope.
  - Quality: tests prove the criteria, changed merged tests are declared, docs are updated, and CI is green.
  - Then review and QA.
- **Retired:** rules that exist only because an answer had to be complete in one shot.

The sort is crew#788. Of the Developer's 170 rules, 11 are workspace, 28 feedback, 40 gates and 56 retired. 35 fit none of the four, each with why.

**6. Stories declare the behaviour they change, mostly through the acceptance criteria.**
- The criteria state the new behaviour, including any existing behaviour the story intends to change.
- The Developer finds the tests that pin it (`tests_for`, `run`) and changes them as part of the work. It declares in `done` which merged tests it changed, and why.
- The gate checks that declaration against the diff and the criteria. A changed test the criteria don't cover goes to review as a question, not a refusal.
- The Business Analyst's contract ruling (crew#584, ADR 0023) moves from mid-attempt to the gate, and runs only when the declaration and the criteria disagree.

Test-first (Discussion 665) waits on how well the session works.

**7. The code's shape: no change for now.** Strain (crew#192) stays. Whether files a session struggles with should also trigger it is decided from session records.

**8. Measures.**
- The first-attempt rate stays. An attempt is a submission at `done`, and a first attempt is a story accepted by review and QA on its first submission.
- The sprint measures the model time and tokens per story, across roles.
- Everything a session does is tracked as new measures, but not as sprint headlines.

sprint-metrics computes the sprint measures, so the new definitions reach it through a Goal the Sponsor writes once the session exists.

**9. No escalation to a stronger model.**
- A stuck session goes back to the Business Analyst who wrote the story, with its evidence: the last failure, what the Developer said was missing, and what it tried. An undecided choice goes to the Sponsor.
- The Sponsor's reason: escalation "has been hiding problems", and the escalated Claude sessions gave little to learn from.
- The escalation measure becomes the rate of stories returned to the Business Analyst.
- Claude Code escalation (`tools/claude_code.py`, `crew_org.escalation` and the per-sprint budget) retires with the build (ADRs 0004 and 0005).

**How sessions fit the tick (P1 to P6):**
- **P1, one session at a time.** The deliver phase starts a session and runs it to its end, one at a time to start, so the Spark serves one session's calls. The backstop bounds a tick's deliver phase. Parallel sessions (the In Progress limit is 3) wait until the records show the Spark's headroom (mqucifer/crew#790).
- **P2, rework is a new session.** It starts from the branch as it stands, with the gate's findings and the last `done` statement in place of the hints. It isn't the old conversation, which carries the context of a different question.
- **P3, checkpoints.** The crew commits the branch after every green `run` and at `done`. A session interrupted by a crash or a stopped tick resumes as a new session from the last checkpoint, told what the previous one's last `done` or `run` said.
- **P4, QA and review are unchanged at first.** They judge the final diff and the `done` statement. QA gets the session's tools later, once the Developer's session is proven (mqucifer/crew#790).
- **P5, every tool call is an event:**
  - each records its tool, the path and name where there is one, the result kind, sizes, duration, and the rule behind a refusal;
  - every `run` records its commands, exit codes and first failure;
  - every call records the stop signals ([the Sponsor, 2026-10-18](https://github.com/mqucifer/crew/issues/778#issuecomment-6250000843)): calls since the last edit or run, views of a file since its last edit, reasoning tokens, edits since the last run, and lookups that found nothing.

  The ceilings are brought down from these records on real stories, not from five replays. Nothing goes to telemetry beyond counts (ADR 0010).
- **P6, Python first for the graph's tools.** `find` and `tests_for` are Python only to start, and their descriptions say so. For other languages, the Developer uses `search`. `view`, `search`, `replace_text`, `write_file` and `run` work for any language.

**The standards.** This follows the edit–run loop of working coding agents:
- SWE-agent's agent–computer interface (Yang et al., 2024) for windowed views, summarised search and the edit guardrail;
- OpenHands' runtime container per session;
- the Development Container specification for the workspace;
- an append-only context for prefix caching (SGLang's RadixAttention);
- test impact analysis for `tests_for` (ADR 0024).

## What's decided and what's true now

| Part | Decided | True now |
|---|---|---|
| The session and its tools (1, 2) | Yes, with pass 3's tools | Built in `crew_org/session/` (Stage C, mqucifer/crew#797), run only by `scripts/run_session.py`. In a tick, the Developer answers in one call with `read_file` and `need_files` (ADRs 0024, 0025), its code in blocks (ADR 0026) |
| How a session ends, and its context (3) | Yes. The ceilings' numbers are for the Sponsor's review here | Built with the session: its endings, the ceilings at 60 and 50, and the replay's masking as its one compaction. In a tick: attempt limits, repairs and escalation |
| The start (4) | Yes | Built with the session. In a tick: a prompt of about 50k characters, and the reading ceilings |
| The workspace (4b) | Yes, with `devcontainer.json` as its one source | Each check is its own container (ADR 0006). The replay's held container is a prototype |
| The rule sort (5) | Yes | `rule_sort.py` holds it (crew#788). Nothing in the tick reads it |
| Stories and the contract ruling (6) | Yes | The gates at `done`, the ruling among them, are built in `flows/gates.py` and `flows/session_end.py` (Stage D part 1, mqucifer/crew#798), and nothing in a tick runs them yet. In a tick, the ruling runs mid-attempt, on the first failure of a merged test the story didn't declare (ADR 0023) |
| Measures (8) | Yes | As before, until the Sponsor's Goal in sprint-metrics |
| No escalation (9) | Yes | Where a `blocked` or stopped session goes is built in `flows/session_end.py` (mqucifer/crew#798), and nothing in a tick runs it yet. Escalation is in the code. Ticks are paused until Stage D's first story |
| P1 to P6 | Yes | P5's records and P6's Python-only `find` and `tests_for` are built with the session. P1 to P4 are Stage D's |

## Consequences

- **It's built in stages** (`docs/plans/developer-session.md`): B the workspace, C the session, D delivery, E retiring what it replaces, F measures. Every stage that calls a model or GitHub runs once for real through LiteLLM before its PR.
- **Stage E retires:**
  - the Developer's retired rules (crew#788);
  - the answer forms and refusals the session replaces;
  - Claude Code escalation and its budget;
  - the Developer's reading ceilings, which stay for refinement and QA.

  The guidance budgets come down to what remains.
- **The ADRs it changes,** each with a changelog line:
  - 0004 and 0005: no escalation;
  - 0006: the workspace container;
  - 0013: escalation's `--safe-mode` goes with escalation;
  - 0023: the contract ruling moves to the gate;
  - 0024: hints, and the session's lookups for the Developer;
  - 0025: the Developer's calls become a session;
  - 0026: the Developer's code goes in tool arguments;
  - 0031: the sort by when a rule applies.
- **What it costs:**
  - a container held for each running session, with tools that execute in it and reliable clean-up. Docker Desktop's memory fits three workspaces only just (`var/notes/778/research-workspace.md`), and P1 starts with one;
  - input per story is about what the real attempts used, but mostly served from cache.
- **What this doesn't decide:**
  - how a compaction compacts, since it never ran. Stage C built the replay's masking, once near 125k, as a placeholder: its form is a later experiment (mqucifer/crew#790);
  - how sprint-metrics and crew-presentation get their first `devcontainer.json` (Stage B);
  - from mqucifer/crew#790: how a session recognises a criterion no code can meet, parallel sessions, mechanical checks at `done` for a test per criterion and for docs, QA and other roles as sessions, nudges, and tuning the ceilings;
  - test-first (Discussion 665), and whether struggling files trigger strain (decision 7);
  - paring the rules further once the session is in use.

## Changelog

- **2026-10-17:** decided. Decisions 1 to 9 and 4b are the Sponsor's, on crew#778.
- **2026-10-18:** after the replay (Stage R), on crew#778. The Sponsor decided the parse guard and the whole history with one compaction near 125k. They accepted the report, with pass 3's tools as the build's start, and replaced its proposed stops with high ceilings, no nudges, and every signal recorded. They also made `devcontainer.json` the workspace's one source, read as a subset under the crew's hardening. The Sponsor approved P1 to P6 with the plan (mqucifer/crew#789). Written as this ADR in Stage A.
- **2026-10-18:** the session is built, beside delivery (Stage C, mqucifer/crew#797): `crew_org/session/`, its calls through `calls.converse`. A reply that calls no tool gets one line back, and 3 in a row end it as `blocked` (the operator's call, on crew#778). The compaction is the replay's masking, its form a later experiment (mqucifer/crew#790). One real session, on mqucifer/sprint-metrics#662, reached `done` with the gate green in 5.3 model minutes.
- **2026-10-18:** the gates at `done` and where a session's ending goes are built (Stage D part 1, mqucifer/crew#798), run by nothing in a tick until part 2. The operator's calls inside decision 6, on crew#778:
  - a changed merged test the story's Existing tests line names is fine;
  - one a line saying the story changes tests doesn't name is a question for review;
  - a line saying the tests stay unchanged, while `done` changed some, is the disagreement the Business Analyst rules on;
  - an empty diff at `done` takes today's already-done path (mqucifer/crew#221).
