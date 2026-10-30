# 32. The Developer works in a session

- **Date:** 2026-10-17
- **Status:** Accepted, built but for the measures. Every part below is decided. The deliver phase runs the Developer as a session in the story's workspace container (Stages B to D of `docs/plans/developer-session.md`), and nothing else: the one-call Developer of ADRs 0024 to 0026 and escalation (ADRs 0004 and 0005) are deleted (Stage E). The measures are Stage F. The rule sort (crew#788) is read by nothing in the tick.

## Context

**The crew is built to get the answer right without feedback, and a developer works by getting feedback fast** ([the synthesis on crew#778](https://github.com/mqucifer/crew/issues/778#issuecomment-6242856875), the Sponsor and the operator, 2026-10-17). The Developer can't run anything during its call. So each incident was answered with more prediction up front: a rule, a story naming more tests, a selection that tries to pre-pick every file, a size limit. The evidence (`var/notes/first-pass-evidence.md`, `var/notes/778-context-selection.md`):
- In 10 of 12 failed first answers, the file or fact the failure needed wasn't shown.
- A sprint-metrics Python file over about 20k characters couldn't be seen whole through either `read_file` or `need_files`.
- Repairs are cheap once the failure names the missing fact. First answers are expensive because they predict: one first attempt reasoned 142,842 tokens over 8 calls and 73 minutes, and the largest single call reasoned 49,238.
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
| `view(path, from_line, to_line)` or `view(path, definition)` | Part of a file with line numbers: a range of lines, or one Python definition by name. A range's header names what its first line is in, as `search` names it |
| `search(pattern, path)` | A regular expression across the repository, `.package/` included, or in one file or folder. A pattern that isn't a valid expression is searched as plain text, and the reply says so. Each line shown names what it's in: its Python definition (`Class.method`) or module level, its YAML or JSON key path (`jobs.docker.services.postgres`), or its Markdown heading. Lines in a row with one name are put under it once. A file that doesn't parse is shown plain, and the reply says so. Past 50 lines, it gives each file's count, `src/` and `tests/` first, with the parts its lines are in when there are three or fewer |
| `find(name)` | A Python definition, with its callers and importers, from the graph (ADR 0024) |
| `tests_for(definition)` | The merged tests that run a Python definition, from the coverage map at the story's base (ADR 0024) |
| `replace_text(path, old, new)` | Replaces whole lines that occur once. Part of a line is refused, with that line shown. A miss shows the closest block, numbered |
| `replace_definition(path, name, source)` | Replaces a Python definition or method, by name |
| `write_file(path, content, overwrite)` | Writes a whole file; an existing one only with `overwrite` |
| `delete_file(path)` | Deletes one file, never a folder, and each folder that leaves empty, as `git rm` does. It's for a file a criterion says is gone. The record's protected paths and a Task's scope are the gate's, from the diff, as for every edit |
| `run(what, tests)` | Runs in the workspace: named tests, the project's whole checks, or the checks' own lint. It's feedback, not a verdict, and its reply leads with the first error |
| `git(command)` | One real git command line, run by the crew on the host. `status`, `diff`, `log`, `show`, `blame` and `grep` read. `restore --source=<ref> -- <paths>` is an edit: each file as it is at the ref, through the edit tools' own write, or deleted where the ref doesn't have it. The story's base is the ref `base` |
| `done` | Names the test that proves each criterion, the merged tests changed and why, and the docs updated. It checks first that the tests it names exist, and that each merged test the branch changed or removed is declared with why: a failure is a reply, and the session writes the test, names the right one, or declares or puts back the changed one. Its description says what the gate returns a branch for |
| `blocked(missing, kind)` | Says what's missing: a file, a fact the story doesn't give, or a choice no one has made |

Every tool:
- **refuses an argument it doesn't take,** listing the ones it does;
- **judges text by its content,** not its extension: no NUL byte in the first 8k bytes, and valid UTF-8 (git's test);
- **replies to an edit with the edited lines, numbered, with 4 lines either side,** and only the lint findings the edit added, from the checks' own lint;
- **doesn't apply an edit that would stop a Python file parsing** ([the Sponsor, 2026-10-18](https://github.com/mqucifer/crew/issues/778#issuecomment-6245222608)). The reply shows the parse error, the lines as they would have been and the original lines, and the session tries again.

There's no shell, commit or network. The session's git reads history and restores files, and nothing else ([the Sponsor, 2026-10-18](https://github.com/mqucifer/crew/issues/778#issuecomment-6251077522), in [the shape of a real git command line](https://github.com/mqucifer/crew/issues/778#issuecomment-6251088059)). On every call, the crew:
- **runs it with no shell:** the line is split as a shell would split it, and its words are run;
- **refuses every other command, and any option before the command,** with the allowed list;
- **refuses each option that writes, reads outside the repository or runs a program,** with every abbreviation git accepts: `--output`, `--ext-diff`, `--textconv`, `--no-index` and a `diff` path outside the repository, `--show-signature` and `%G` formats, `-O`, grep's `-f` and `--open-files-in-pager`, blame's `--contents`, `--ignore-revs-file` and `-S`, `--pathspec-from-file`, `--help`, the submodule options, and `--git-dir`, `--work-tree` and `--exec-path`;
- **turns off** hooks, fsmonitor, the pager, signatures, transports, colour, and external diff and text-conversion drivers, and leaves submodules alone;
- **runs git in an environment built from nothing:** no system, global or environment config, and a home that doesn't exist;
- **points git at the git directory the clone records for the worktree,** never one inside the worktree, where the session's code could write a config or hooks. The container already sees `.git` read-only (crew#788).

`base` is `refs/base`, which the crew sets to the commit `merged_base` finds before each call. The clone's worktrees share it, which holds while one session runs at a time (P1).

**3. How a session ends.**
- **`done`:** the gates take over (decisions 5 and 6), then review and QA.
- **`blocked`:** a missing file or fact goes to the Business Analyst who wrote the story, in the Developer's own words. An undecided choice goes to the Sponsor.
- **No progress:** the same first error in the same test after 3 runs, with edits between them.
- **The backstop:** 3 hours of model time per delivery of a story, shared by its sessions ([the Sponsor, 2026-10-18](https://github.com/mqucifer/crew/issues/778#issuecomment-6253145768)). It started at 2 hours, between the p75 (89 minutes) and p90 (167 minutes) of 35 merged stories. On mqucifer/sprint-metrics#662 the 2 hours stopped a green session about two calls short of `done`. The workspace container lives 4 hours, so it can't end under a live session. The number is to be tuned from the records.
- **The gates' same finding, again:** 3 gate rounds in a row in one delivery that send back the same findings, by rule and by each one's first 10 lines, end the delivery ([the Sponsor, 2026-10-19](https://github.com/mqucifer/crew/issues/790#issuecomment-6274308722)). The count is the no-progress stop's, which can't see these rounds: each starts a new session. On mqucifer/sprint-metrics#671 the gates refused five times with one finding, lint and tests unable to reach PyPI from a workspace with no network, and only the backstop bounded it. The note quotes the finding, with no line for the rewrite: there the cause was the workspace's, not the story's.
- **A finding no change can meet, at once:** a gate's finding marked final ends the delivery in its first round, as the same stop. So far one is: a bug's branch that changes only tests, the named ones among them, none failing as merged (`guard.reproduce.check`, crew#949). On mqucifer/sprint-metrics#868 the defect was a stale test, and the gate sent the same finding back 4 times and refused a fifth, over 7 sessions.
- **A ceiling,** set high to start ([the Sponsor, 2026-10-18](https://github.com/mqucifer/crew/issues/778#issuecomment-6250000843)): about three times the largest value a successful replay reached. **The starting numbers are here for the Sponsor's review:**

  | Ceiling | Starts at | Largest in a replay that reached `done` |
  |---|---|---|
  | Calls in a row with no edit and no run | 60 | 19 |
  | Views of one file with no edit to it between | 50 | 18 in pass 1, 5 in pass 3 |

  There's no ceiling on one call's reasoning, or on edits without a run. The backstop covers the first, and the gate at `done` covers the second.

No progress, the backstop, a ceiling and a full context each end the session as `blocked`, and the gates' same finding again ends the delivery the same way. Its evidence goes with it (decision 9). These endings replace the attempt limits. **There are no nudges in the first build.** Whether a warning before a stop helps is a question for evidence, under mqucifer/crew#790. The research found no published measured effect of a nudge. Its one measurement, on a 32B model, found warnings cut repeats without raising the resolve rate (`var/notes/778/research-stuck-detection.md`).

**The context: the history stays whole, compacted each time it nears 125k tokens** ([the Sponsor, 2026-10-18](https://github.com/mqucifer/crew/issues/778#issuecomment-6244856687)). This replaces decision 3's first rule, that older tool output shrinks to one line.
- **The rule:** every tool reply stays as it was. A session is compacted all at once, and only before a prompt would pass 125k tokens, each time it would (mqucifer/crew#857). A compaction that leaves the prompt at or over 125k ends the session as a stop, `context_full`: what is left is what masking can't shrink. The window is 262,144 tokens, about 196k for input once 65,536 are kept for output.
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
- Setup runs at the start, with network. Then the session runs with none.
- Setup runs again, the same way, before a `run`, the lint after an edit, or the gates at `done`, when the dependency files have changed since it last passed (crew#814). That's the devcontainer `updateContentCommand`. The files are `pyproject.toml`, `uv.lock`, `requirements*.txt`, `package.json` and its lockfiles, at the project's root (`MANIFESTS` in `tools/container.py`).
  - A failure is tried once more. In a `run`, the reply is setup's command, exit code and the end of its output, and nothing else runs. After an edit, the edit stays and its reply says the same, in place of the lint. At the gates, it's a failed check.
  - The session's end and the gates' check count how many ran and how many failed, with no output.
- It keeps today's sandbox hardening (ADR 0006): not root, all capabilities dropped, no new privileges, and memory, CPU and process caps.
- It's removed at the session's end, and cleaned up after a crash.
- **`devcontainer.json` is the one source of the workspace** ([the Sponsor, 2026-10-18](https://github.com/mqucifer/crew/issues/778#issuecomment-6250085435)). It's the standard file. The project's design points to it, and DevOps owns it, through its role Tasks (ADR 0034): the workspace has to match CI, and DevOps owns CI ([the Sponsor, 2026-10-22](https://github.com/mqucifer/crew/issues/860)). A repository with no file gets DevOps's first Task to write it.
  - The crew reads only a subset of it: the image, the setup command and the environment.
  - It always applies its own hardening, so nothing in the file can loosen the container. `runArgs`, `mounts`, `privileged`, features and host commands are not honoured.
  - The project's checks come from its design, as today.
- **The services its tests need run beside it** (crew#814 part 4, crew#860). The file names a compose file (`dockerComposeFile`), its own `service` in it, and optionally `runServices`, as the specification does. The workspace's image is its service's.
  - The crew never runs `docker compose`. It reads each other service's image (by digest), `user` (never root), `environment`, `command`, `healthcheck` (`["CMD", ...]`) and `tmpfs`, and starts it itself with the sandbox's hardening and no mounts or ports. The rest is named as not honoured.
  - The services are read from the default branch, never the story's branch, so a story's code can't choose what the host pulls and runs. A change to them takes effect once it's reviewed and merged. Images are pulled on the host by digest before the session starts.
  - **One exception: a DevOps Task that changes the workspace file or its compose file** ([the Sponsor, 2026-10-23](https://github.com/mqucifer/crew/issues/839)). Its gate at `done` runs the whole checks in a container of its own, with the services and setup its branch declares. They're held to the default branch's rules, and each image's digest is read from its registry before anything is pulled. Setup runs with the network, as a returned story's session already runs its branch's. Otherwise mqucifer/sprint-metrics#739's session could never run what it wrote. A merged test those services turn on that fails, where the branch changes neither the test nor its module or `conftest.py`, is named in the pull request and filed as one bug for the Developer when it merges. Once the first Task is closed and the tests that need the services pass in the workspace on main, DevOps gets its second Task: CI's test job runs the same services. A story's checks passing at `done` with the default branch's services up shows it, and so do main's own, run in a container with its services at most once per head commit while the Task isn't opened.
  - They share an internal network, with no route out, with the workspace only. A project that declares none runs with no network, as before.
  - A service that won't start is a finding at `done` naming it, and filed for DevOps; never a silent skip.
- **The gate at `done` refuses a skipped test that CI runs** ([the Sponsor, 2026-10-21](https://github.com/mqucifer/crew/issues/860)). Every pytest run in the workspace writes a report into the worktree's own home, which is never committed. What CI skips too is declared in the file's `customizations.crew.ciOnlySkips`, each entry a test id (its parameters included) or text inside a skip's reason (crew#917). A test the branch adds or changes that skips for another reason goes back. One already merged is DevOps's finding, once the project has the file; until then, its first Task covers them.
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
- The first-attempt rate stays. An attempt is the branch handed to review and QA, and a first attempt is a story accepted by review and QA the first time it reaches them ([the Sponsor, 2026-10-18](https://github.com/mqucifer/crew/issues/778#issuecomment-6250605453)). A return by the crew's own gate at `done` is the Developer still working, as a CI run failing before a pull request opens is: it's counted apart, as a gate round with its rules' ids, and doesn't spoil the first attempt.
- The crew sends sprint-metrics what happened, never a count: each return of the work once handed on, by review, QA, CI, a conflict with main, changes requested at merge or a revert, and no number of attempts. sprint-metrics decides what a first attempt is from them ([the Sponsor, 2026-10-30](https://github.com/mqucifer/crew/issues/963)). The gate's returns before review aren't sent as returns.
- The sprint measures the model time and tokens per story, across roles.
- Everything a session does is tracked as new measures, but not as sprint headlines.

sprint-metrics computes the sprint measures, so the new definitions reach it through a Goal the Sponsor writes once the session exists.

**9. No escalation to a stronger model.**
- A stuck session goes back to the Business Analyst who wrote the story, with its evidence, in plain words: why it ended (its own `blocked` in its words; a stop with its number), its last run, the gate's earlier findings, the files and merged tests it changed, with those the story doesn't name called out, and a stopped session's last words. A line for the rewrite is added only when the evidence points at the story: no progress, a ceiling, or merged tests changed that the story doesn't name. An undecided choice goes to the Sponsor.
- **Its work is kept** ([the Sponsor, 2026-10-18](https://github.com/mqucifer/crew/issues/778#issuecomment-6253145768)). Before the note is written, the work, with any edits since the last checkpoint as one more commit, is pushed to a branch of its own, `crew/kept/<story>-<yyyymmdd-hhmm>`, with no pull request, and the note links it. The same holds for a `done` the contract ruling sends back. A push that fails is handled as any failed push. A later delivery of the story resumes from its newest kept branch (the Sponsor, 2026-10-24, crew#839), when the crew's own App pushed it, it hasn't moved since, and it merges cleanly with main; otherwise it starts from main, and its session is told the kept branch exists and why it wasn't used. A checkpoint or a returned pull request's branch comes first.
- The Sponsor's reason: escalation "has been hiding problems", and the escalated Claude sessions gave little to learn from.
- The escalation measure becomes the rate of stories returned to the Business Analyst.
- Claude Code escalation (`tools/claude_code.py`, `crew_org.escalation`, the ledger and the per-sprint budget) is deleted (Stage E; ADRs 0004 and 0005). The retro and `crew export` count the stories returned to the Business Analyst instead.

**How sessions fit the tick (P1 to P6):**
- **P1, one session at a time.** The deliver phase starts a session and runs it to its end, one at a time to start, so the Spark serves one session's calls. The backstop bounds a tick's deliver phase. Parallel sessions (the In Progress limit is 3) wait until the records show the Spark's headroom (mqucifer/crew#790).
- **P2, rework is a new session.** It starts from the branch as it stands, with the gate's findings and the last `done` statement in place of the hints. It isn't the old conversation, which carries the context of a different question.
- **P3, checkpoints.** The crew commits the branch after every green `run` and at `done`. A session interrupted by a crash or a stopped tick resumes as a new session from the last checkpoint, told what the previous one's last `done` or `run` said.
- **P4, review judges the final diff and the `done` statement, and QA verifies in a session.** QA reads the story's branch and runs its tests in the story's workspace container, with the project's services, through the Developer's read tools and `run`, and none that edit (mqucifer/crew#895). Its verdict cites what each criterion rests on, and `done` holds each citation to what it ran and what the branch says.
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
| The session and its tools (1, 2) | Yes, with pass 3's tools, and a git tool that reads and restores (the Sponsor, 2026-10-18) | Built in `crew_org/session/` (Stage C, mqucifer/crew#797). The deliver phase runs it for every story (`flows/session_delivery.py`, Stage D). The git tool is `session/git.py` |
| How a session ends, and its context (3) | Yes. The ceilings' numbers are for the Sponsor's review here | Built with the session: its endings, the ceilings at 60 and 50, and the replay's masking as its compaction, each time a prompt nears 125k. The 3-hour backstop is one budget per delivery of a story, shared by its sessions (the operator and the Sponsor, on crew#778). The gates' same findings 3 rounds in a row end a delivery (crew#790) |
| The start (4) | Yes | Built with the session. Its story section is the card's title and body, with the epic's decided rows and design note, each part measured (the operator, on crew#778) |
| The workspace (4b) | Yes, with `devcontainer.json` as its one source | Built (Stage B, mqucifer/crew#796). The deliver phase holds one per story, for its sessions and the gates at `done`. Setup runs again when the dependency files change (crew#814) |
| The rule sort (5) | Yes | Applied in Stage E: the retired rules left the catalog, except 12 other roles still meet. `rule_sort.py` holds what's left (crew#788), and nothing in the tick reads it |
| Stories and the contract ruling (6) | Yes | The gates at `done`, the ruling among them, are built in `flows/gates.py` and `flows/session_end.py` (Stage D part 1, mqucifer/crew#798). The deliver phase runs them at every `done`, in the story's workspace container. The mid-attempt ruling is deleted with the one-call Developer (Stage E) |
| Measures (8) | Yes | delivery-history 1.6.0 carries the session, by names and counts, and each story's deliveries in it (Stage F). sprint-metrics' feed counts a session story's attempts as the times it was handed to review. The sprint measures wait for the Sponsor's Goal in sprint-metrics |
| No escalation (9) | Yes | Where a `blocked` or stopped session goes is built in `flows/session_end.py` (mqucifer/crew#798), and the deliver phase runs it. Its work is kept on a `crew/kept/` branch, and the note says what happened in plain words (crew#778). The story's next delivery resumes from it (crew#839). Nothing escalates, and the escalation code is deleted (Stage E). The retro and `crew export` count stories returned to the Business Analyst |
| P1 to P6 | Yes | P5's records and P6's Python-only `find` and `tests_for` are built with the session. P1 to P3 are built in the deliver phase: one session at a time, a new session for rework, and checkpoints committed after each green run, never pushed to the story's branch, resumed after an interruption. Work that leaves for a rewrite is pushed to a `crew/kept/` branch of its own. P4 is as before |

## Consequences

- **It's built in stages** (`docs/plans/developer-session.md`): B the workspace, C the session, D delivery, E retiring what it replaces, F measures. Every stage that calls a model or GitHub runs once for real through LiteLLM before its PR.
- **Stage E retired** (crew#778):
  - the Developer's retired rules (crew#788), 44 of them, keeping 12 other roles meet;
  - the answer forms and refusals the session replaces, the code in blocks, and the one-call Developer with its steps;
  - Claude Code escalation, its ledger and its budget;
  - the Developer's reading ceilings, which stay for refinement and QA.

  The guidance budgets came down to what remains: the Developer's to 0, the Senior Engineer's to 8.
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
- **2026-10-18:** the deliver phase runs the Developer as a session (Stage D part 2): P1 to P3, one 2-hour budget per delivery, and the gates at `done` in the story's workspace container. The Sponsor: no switch back to the one-call Developer, whose code stays unused until Stage E (on crew#778). Checkpoints aren't pushed, so a rework never moves the head under review.
- **2026-10-18:** decision 8's attempt corrected (the Sponsor, on crew#778): an attempt is the branch handed to review and QA, not a submission at `done`. The gate's returns before review are gate rounds. The measures reach delivery-history (Stage F): each story's deliveries in sessions, and the session's events by names and counts. Escalation's fields stay in the package for the sprints before it retired.
- **2026-10-18:** Stage E: the one-call Developer, its answer forms, steps and code in blocks, its reading, Claude Code escalation and the Developer's retired rules are deleted (crew#778; the Sponsor's no-fallback decision). A story with no epic, past the round-trip limit, is blocked for a person instead of going round review and rework without end. ADRs 0004, 0005, 0013, 0020, 0023, 0024, 0025, 0026 and 0031 say so.
- **2026-10-18:** `done` checks that the tests it names exist, and nothing more (the Sponsor, on crew#778). The real run on mqucifer/sprint-metrics#662 found 5 of the 10 tests its `done` named didn't exist, although `done`'s description already asked for a test per criterion. A failure is a reply in the same session, not a failure or an attempt, and it leads with writing the test: on #662 no proving test existed under any name. The gate's own copy of the check goes. Whether a test proves its criterion stays with QA.
- **2026-10-18:** the backstop is 3 hours, from 2, and the workspace container lives 4 hours, from 3 ([the Sponsor](https://github.com/mqucifer/crew/issues/778#issuecomment-6253145768)). On mqucifer/sprint-metrics#662 the 2 hours stopped a green session about two calls short of `done`, and its work was dropped with the clone's branch. Work that leaves for a rewrite (`blocked`, a stop, or the contract ruling) is now kept on a `crew/kept/` branch with no pull request, and the note links it and says what happened in plain words. Resuming from kept work, and choosing between a resume and a rewrite, come later.
- **2026-10-18:** the session has git, which reads history and restores files, and nothing else ([the Sponsor](https://github.com/mqucifer/crew/issues/778#issuecomment-6251077522), changing decision 2's "no git"). It's one `git(command)` tool taking a real git command line, the shape the model was trained on ([the Sponsor](https://github.com/mqucifer/crew/issues/778#issuecomment-6251088059)). The crew runs it on the host, with what it adds and refuses on every call in decision 2. `restore` is an edit. The story's base is the ref `base` (mqucifer/crew#808). One live session on mqucifer/sprint-metrics#662 used it twice without being asked, `git log` to orient and `git diff base` before `done`. It reached `done` with the gate green in 4.3 model minutes.
- **2026-10-18:** `search` names what each line it shows is in, and `view` names what a range's first line is in (the Sponsor, on crew#807: "That's a big bug"). On the panel for mqucifer/sprint-metrics#667, QA searched `.github` and read two hits in `tests.yml`, a Postgres service and `SPRINT_METRICS_DB`, as the `tests` job's. Both were in the `docker` job. A Python line names its definition, a YAML or JSON line its key path, and a Markdown line its heading. Any other file is shown as before.
- **2026-10-18:** corrected the evidence line: 142,842 reasoning tokens was one first attempt's total over 8 calls, not one answer (`var/notes/first-pass-evidence.md`). The largest single call in the event log reasoned 49,238 (`var/notes/thinking-tokens-2026-10-18.md`).
- **2026-10-18:** the session serves refinement too (crew#807, ADR 0033): it takes a role, its tools and its ending, with the Developer's as the defaults, and each event names its step. Refinement's ceiling is 100 calls and its backstop 2 hours per step.
- **2026-10-19:** the session keeps each assistant message's `reasoning_content` and sends it back, as Qwen's template expects (the Sponsor, on crew#778). A compaction empties it on every turn before the kept tail, in one pass, then masks. `session.turn` and `session.compacted` carry its size as counts; its text goes nowhere (ADR 0010).
- **2026-10-19:** setup runs again, with the network, before a `run`, the lint after an edit, or the gates at `done` when the dependency files have changed since it last passed (the Sponsor, on crew#814). mqucifer/sprint-metrics#671's branch bumped its version, so `uv.lock` went stale. `uv run` re-locks a stale lockfile, which needs the network, so every run and gate failed on a DNS error. Before the sessions, the gates ran setup before every check. Nothing else gets the network.
- **2026-10-19:** the gates' same findings 3 rounds in a row end a delivery, as a stop `gate_repeat` ([the Sponsor, on crew#790](https://github.com/mqucifer/crew/issues/790#issuecomment-6274308722)). On mqucifer/sprint-metrics#671 the gates refused the branch five times with one finding, and each refusal started a new session, so the in-session no-progress stop never counted them; the operator stopped the tick by hand. Rounds are compared by each finding's rule and its first 10 lines. The work is kept, and the note quotes the finding. `story.returned` carries the stop, and delivery-history 1.8.0 counts it, taking a delivery's stop from its return when it stopped between sessions.
- **2026-10-22:** DevOps owns `devcontainer.json`, not the Architect, and keeps it true through role Tasks (the Sponsor, on crew#860): no step ever wrote one, and the workspace has to match CI, which DevOps owns. A project's declared services run beside the workspace on an internal network, read from the default branch; and the gate at `done` refuses a skipped test that CI runs (crew#814 part 4, crew#860).
- **2026-10-22:** a session compacts each time a prompt would reach 125k tokens, not once (mqucifer/crew#857). mqucifer/sprint-metrics#709's session reached it a second time and overflowed. Each compaction is its own `session.compacted`, and one that leaves the prompt at or over 125k ends the session as a stop, `context_full`, which delivery-history 1.9.0 names. The estimate now counts the reasoning and tool-call arguments sent back, so a cleared field shows in it.
- **2026-10-23:** a project's first workspace is three steps (the Sponsor, on crew#839): DevOps writes the workspace file and its services, and its gate at `done` runs that branch's own services and setup; the Developer fixes, as a bug, the merged tests they turn on that fail; then DevOps makes CI run the same services. A session reads an image's digest with `image_digest`, and a compose file's images are checked at `done` as a Dockerfile's are. General network access stays off.
- **2026-10-30:** the feed to sprint-metrics sends returns, not counts (the Sponsor, on crew#963). It had sent `attempts` on each finished story, counted as the times it was handed to review and QA, and no event for CI failing or a conflict rebuild, so sprint-metrics couldn't see those. Each return now goes as a missed attempt by its step, and `finished` carries no count. The crew's own counts for its stops and blocks stay.
- **2026-10-24:** a delivery resumes from the story's newest kept branch (the Sponsor, "Resume first", on crew#839). mqucifer/sprint-metrics#742 was stopped by the backstop after 181 minutes over 5 sessions, with its work kept; moved back, it would have started from main and redone it. The kept branch is used only when GitHub's activity record shows the crew's own App made its newest push and left it at its tip, and only when it merges with main as a checkpoint does; a check that fails trusts nothing. The session's start names the branch, the files it changes against main, and the last `done`'s findings and last run from the crew's note. Each delivery still has its own 3 hours, so a card can spend more than 3 hours over several; the backstop and the gates' repeated-finding stop still end each one.
- **2026-10-24:** DevOps's CI Task also opens from main itself (the Sponsor, "Ok build", on crew#839). In the reset an existing project's stories wait on the step this Task finishes, so no story reached `done` to open it. Each pass, once the first workspace Task is closed and main declares services, main's whole checks run in a container with them, at most once per head commit; a pass opens the Task, and its event names which path opened it.
- **2026-10-23:** `done`'s first check also compares the merged tests the branch changed or removed with `changed_tests` (the Sponsor, "Seems like a need", on mqucifer/crew#839). That finding was 11 of the event log's 61 gate checks, on 7 sprint-metrics cards, each a gate round for one missing field. It asks the gate's own computation, so a `done` that passes it passes the gate's, and the gate keeps its check. One declared with a blank why counts as undeclared, at `done` and at the gate. The refused `done`'s `tool.finished` counts the tests it named, by rule.
- **2026-10-24:** a session deletes a file with `delete_file` (mqucifer/crew#839). The first delivery of mqucifer/sprint-metrics#749, mqucifer/sprint-metrics#769, ended blocked: it could write and restore files but not delete one, so it emptied four doc pages that its own test said were gone. The tool refuses what `write_file` refuses and a folder or a missing file. The crew commits a deletion as it commits any edit, and its `tool.finished` carries the path.
- **2026-10-25:** QA verifies in a session (mqucifer/crew#895; M10, ADR 0006 in mqucifer/project-print), changing P4. It runs in the story's held workspace with the project's services: its own sandbox had none, so the database tests failed there (mqucifer/sprint-metrics#785). The one call and its read tool go.
- **2026-10-26:** a `ciOnlySkips` entry matches a skipped test by its id as well as by its reason (crew#917): sprint-metrics declared its four Docker tests by id, and mqucifer/sprint-metrics#807 went back three times for them.
- **2026-10-26:** an ending's arguments are refused at the reply unless each is in the form its schema gives (mqucifer/crew#926): a list is a list, each entry an object of its fields as strings, a string a string, an enum one of its values. It's the argument check every call has (`session.arguments`), not a check of the work, so `done`'s own checks are still the one above. On mqucifer/sprint-metrics#820, `done`'s `changed_tests` came as one object, and reading it blocked the card; now the model is told and calls `done` again. The crew's readers of `done`'s statement take only that form, so a wrong shape is a finding, never an exception. A refinement step's `done` isn't checked here: its form is, after reading a list written as JSON text (crew#807).
- **2026-10-27:** an approved story that conflicts with main gets one session to resolve the conflicts, before any rebuild (mqucifer/crew#933, asked for by the Sponsor). Main is merged in with the conflicts left in the files, the session's edits are held to those files (`session.path`), and its `done` refuses a conflict's markers left in (`check.gates.conflicts_left`). The gates run on the merged branch, and review and QA judge only what changed in those files since the approval. Any other ending, a gate's finding, or an edit beyond the conflicts rebuilds it, as before. Four approved mqucifer/sprint-metrics stories were rebuilt whole on 2026-10-27 for small shared-line conflicts; mqucifer/sprint-metrics#809's took 135 calls.
- **2026-10-29:** a final finding ends a delivery in its first round, as the stop `gate_repeat`, with `final` in its evidence (mqucifer/crew#949, agreed with the Sponsor on the issue). The first is the reproduction check's for a bug whose fix is only its tests. No new stop kind: delivery-history counts it as `gate_repeat`, and its contract is unchanged.
