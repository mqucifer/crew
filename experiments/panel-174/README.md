# The refinement panel test (sprint-metrics Goal #174)

**Status:** run 2026-10-02 (02:15–07:00Z), all three arms, 108 calls, none failed. Scored by reading every note (269). Results and recommendation below. Part 5 of the plan agreed with the Sponsor on 2026-10-01: Architect, UX Designer, QA and DevOps each take a focused pass on an epic before it's split. This test checks that panel before it's built into the crew.

## The result

**The panel is worth building, with three changes.** Before the split, it reliably names the problems between epics and the missing dependency. It rarely adds a note that isn't needed. But it misses four of the ten findings in every run, together. And most of what it raises is for the Architect to settle, not the Product Owner.

How many of three runs named each finding (with its source). In brackets: runs that raised it only in part.

| # | Finding | Serial | Parallel | Serial, without crew#280 |
|---|---|---|---|---|
| 1 | History in memory; Postgres decided | **3** | 1 (+2) | 0 (+3) |
| 2 | No `unblocked` event | 1 | 0 | 1 |
| 3 | A card counted where it merges | 0 (+1) | 0 | 0 |
| 4 | Traces must be OTLP | 2 (+1) | 2 (+1) | 3 |
| 5 | A new intake version is MINOR | 0 (+2) | 0 (+1) | 0 |
| 6 | Trends planned nowhere | 2 | 3 | 2 |
| 7 | #184 and #185 must share one event format | 3 | 2 (+1) | 2 (+1) |
| 8 | Intake versioned like the answers | 3 | 2 (+1) | 2 (+1) |
| 9 | #187 must instrument the service, not file mode | 0 (+3) | 0 (+2) | 0 (+1) |
| 10 | Settings per environment, test bed, production | 0 (+1) | 0 | 0 (+1) |
| | **Named per run** | 6, 5, 3 | 2, 5, 3 | 3, 3, 4 |

- **What the panel catches on its own:**
  - 7 and 8, the contract between epics, were each named in 7 of the 9 runs, and raised in part in the other two.
  - 4 and 6 came up in most runs.
  - Each comes mostly from the Architect. QA adds trend (6) and the `unblocked` event (2).
- **What the decisions add: finding 1, and protection from the wrong answer.**
  - With crew#280 in context, the serial panel named Postgres in all three runs, and the parallel one in one run, plus two in part.
  - Without it, no run did. Roles asked "in memory or a file?", and one QA note told the epic to keep history in memory and leave persistence to #186: the exact error the review found.
  - Finding 2 is barely helped by the decisions: one serial run with them, none in parallel, and one without them. In that run the Architect reasoned it out from blocked-card aging.
- **What every run missed together (correlated blind spots, as in Discussion #359):**
  - **10, environments,** was never raised by DevOps, the role it belongs to. Two runs proposed `OTEL_*` settings, and that's all.
  - **5, MINOR vs MAJOR:** #186's SemVer rule was in every context. The Architect quoted it in three runs and never applied it.
  - **9, #187 instruments the service:** raised in part in six runs, always as an open choice. Some notes leaned towards the error: instrument today's scrape mode only.
  - **3, the counting rule:** its decision (crew#389) never names the Goal, so the rule couldn't find it. No role worked it out unprompted.
- **Unneeded notes are few: 8 of 269 (3%), all DevOps on #185 reviewing #186,** the delivered sibling. Twice it quoted #186's text as #185's. One note was wrong (above).
- **"Nothing to add" came up 4 times in 108 calls,** and twice it was wrong:
  - QA on #184 in serial run 3. That run lost findings 2 and 6.
  - DevOps on #184 in a run without decisions.
- **Overlap is high, mostly QA:** 75 of 269 notes repeat another member on the same epic. QA's notes on #185 often restate #184's problems.
- **Who would settle the notes that are needed:** the Architect about 70%, the Product Owner about 25%, the Sponsor none (by my reading). Most of what the panel raises is design.
- **Parallel costs nothing in quality:** the spread is the same as serial (2–5 against 3–6 named per run).
  - **Speed:** the four calls on one epic took 313 seconds of wall time, against 810 serial (2.6× faster).
  - **Load:** each request was slower (median 252s against 193s), and the Spark delivered 81 tokens/s against 29.
  - **Cost:** the same prompt tokens (312k for 36 calls) and about 6k answer tokens a call.

## Recommendation

Build the panel (crew#440, reshaped as part 5 describes), in parallel, with three changes:

1. **Record the Sponsor's decisions where a rule finds them.** The rule found two of the four sources the design expected. crew#283 (OTLP) and crew#389 (the counting rule) never name the Goal. The panel only gets finding 1 right because crew#280 does name it, and finding 3 is lost without its decision. One way: a decision that applies to a Goal is recorded on the Goal, or names it. **This is the Sponsor's call.**
2. **Give each member a check, not only a lens, for its blind spot.** Discussion #359 found that correlated misses need critique, rules or checks. Here that means:
   - **DevOps:** each environment the plan names (local checks, CI, production) and the settings each needs. Part 1 of the plan now gives it that list.
   - **The Architect:** a version change is classified by the #186 SemVer rule, and an epic that depends on a sibling's code names it.
   - These are scope lines in each member's task, not design rules.
3. **Keep each member on its own epic.** "Comment on this epic only. A sibling's problem belongs to its own panel, and a delivered epic is context, not under review." That removes the unneeded DevOps notes and most of QA's overlap.

**What happens to the notes:** about 70% are design questions, and part 5 keeps the design note after the split. So the panel's design notes travel to the design note as its inputs. The Product Owner settles the rest, from the sources or with one question to the Sponsor. Finding 7 shows a catch: the same shared-format note came up on both #184's and #185's panels in 7 of 9 runs. A design note that names a sibling has to reach both epics' design notes, or two design notes decide the same thing differently. That is exactly what happened on 2026-10-01.

**Not needed:** a separate critic call. The misses are specific and known, so checks in the members' tasks (change 2) are cheaper, and can be tested by replaying these inputs.

## Caveats

- **One reader scored it,** against a written rule: a finding counts when a note names the requirement that rules the error out, with its source. Every score has its reason in `results/scores.jsonl`, so it can be checked.
- **Three runs per arm** give direction, not rates. The arms differ by one or two findings per run, which is within the run-to-run spread.
- **The arm without decisions still saw #186's body,** whose "Sponsor decisions (2026-09-28)" section is part of it as a sibling epic. So that arm removed crew#280 and the Goal's comment, not every decision.
- **The members saw the epic bodies only,** before any design note, as the panel would. Findings 5, 7, 8 and 9 can only be prevented at that point, not caught.

**The data:** `results/tables.md` (all tables), `results/results.jsonl` (every answer), `results/scores.jsonl` (every note's score and why), `results/run.log`.

**The question:** would that panel have caught, before the split, what went wrong with Goal #174's first epics? Or would the members miss the same things together, as identical panels did in the earlier panel test (Discussion #359: "correlated blind spots need critique, rules or checks")?

## How it runs

**One run** is 12 model calls: three epics, each read by four roles.

| Order | Epic | Calls, in order |
|---|---|---|
| 1 | #184 (accept events, retained history) | Architect, UX Designer, QA, DevOps |
| 2 | #185 (versioned intake format) | the same four |
| 3 | #187 (logs and traces) | the same four |

- **Each call stands alone.** No role sees another's notes, another epic's results, or an earlier run.
- **Every call gets the same context** (see "What each member is shown"). Only the role and the epic under review change.
- **Each role answers with notes, or "nothing to add".** A note names a problem, quotes its source, and says what to settle before the split.
- **Each role runs on its own model alias from `agents.yaml`:** the Architect and UX Designer on `crew-analysis`, QA and DevOps on `crew-code-think`.

**Each arm repeats the run three times**, because the model doesn't answer the same way twice. One run can't tell a role that always misses a finding from one that missed it by chance (Discussion #359: random misses, which a panel fixes, against correlated ones, which it doesn't). So each finding is scored per run: caught in 3/3, 1/3 or 0/3.

| Arm | Calls per run | Runs | Calls | Differs by | Command |
|---|---|---|---|---|---|
| Serial | 12 | 3 | 36 | the baseline | `run_panel.py serial 3` |
| Serial, no decisions | 12 | 3 | 36 | item 4 left out | `run_panel.py serial 3 --no-decisions` |
| Parallel | 12 | 3 | 36 | each epic's four roles at once; timing only | `run_panel.py parallel 3` |

That's 108 calls in total. One call took 3–4 minutes in the first run, so a serial run is about 50 minutes.

**The files:**
- `collect_decisions.py`: the rule for item 4. It runs first, and its output goes in `inputs/`.
- `run_panel.py`: the calls. Each one is a line in `results/results.jsonl`.
- `results/scores.jsonl`: every note, scored by reading it.
- `analyse.py`: the tables.

## The case

Goal #174's first epics as they stood on the morning of 2026-10-01, before the Goal was revised:

- **The Goal:** #174's body before its revision (from its edit history).
- **The epics:** #184 (accept events, retained history), #185 (versioned intake format), #187 (logs and traces), with #186 (the container, delivered) as a sibling.
- **Nothing is written to GitHub.** The epics are closed and superseded. Notes go to files here, as in `panel-359`.

## What each member is shown

This is what's really under test. Every member gets the same context, **collected mechanically, the way the crew would collect it in production**:

1. the Goal
2. the project record (`.crew/project.yaml` as it was that morning, before #409 and #412)
3. the epic, and its sibling epics under the Goal
4. **the Sponsor's recorded decisions:** comments by the Sponsor on the Goal's cards, and on crew issues that name the Goal or the project (crew#280, crew#283, the #186 decisions, the counting rule from 2026-09-30)

If item 4 is hand-picked rather than found by a rule, the test flatters the panel. The collection rule is written down and run before the panel, and its output is kept (`inputs/`).

## The arms

- **Panel, serial.** Four focused calls per epic, one per role. Each answers with a note, or "nothing to add" (a first-class answer).
- **Panel, parallel.** The same four calls at once, to measure wall time and per-request time from LiteLLM's per-request records under load (not from idle gauges). It runs only after the serial arm's notes are judged correct.
- **Context without item 4.** The serial panel again, without the Sponsor's recorded decisions. This shows how much of any catch comes from the context rather than the roles.

Three runs per arm. Small sample, so it gives direction.

## The score sheet

These are the epic-level findings from the review (https://claude.ai/artifact/TXQstfAr8xrYqXFEzkjva6). A finding counts if any member names it, with its source.

| # | Finding | Kind | Expected from |
|---|---|---|---|
| 1 | History kept in memory only; the Sponsor decided Postgres (crew#280) | Sponsor decision | DevOps, Architect |
| 2 | No `unblocked` event; crew#280 pushes one | Sponsor decision | QA, Architect |
| 3 | A card bound to its creation sprint; "a sprint counts a story where it merges" | Sponsor decision | QA |
| 4 | Traces as custom JSON on stdout, not OTLP (crew#283, and #187's own promise) | Sponsor decision | DevOps |
| 5 | A new event version called MAJOR; the #186 decisions say MINOR | Sponsor decision | Architect |
| 6 | Trends asked for twice, planned nowhere | Coverage | QA, Architect |
| 7 | #184 and #185 define different event formats | Between epics | Architect, QA |
| 8 | Intake versioned differently from the answers (`"1"` vs `1`) | Between epics | Architect |
| 9 | #187 instruments only the old file mode, not the service | Between epics | DevOps, QA |
| 10 | No settings per environment, no test bed, nothing for production | Environments | DevOps |

**What "names it" means here.** The members see the epic bodies only. The design notes and the split that produced findings 5, 7, 8 and 9 came later that day. So a member can't catch those errors; it can only prevent them. A finding counts when a note names the requirement that rules the error out, with its source. For example, "#184 and #185 must share one event format" counts for 7.

The story-level contradictions (#393, #394, #395, #402) aren't scored here. The panel runs before the split, and the criteria check (crew#428) covers those after it. It was proven live on them.

**Also counted:**
- notes that aren't needed (to check warning 1, that a model asked for input will produce some)
- the cost per epic in calls, tokens and wall time
- **overlap:** notes that raise the same point as another member's on the same epic. That's how much a combining step would have to merge.
- **who would settle each note:** the Product Owner, from the Goal, the record or a decision; the Architect; or the Sponsor. That's how many questions would reach the Sponsor.

These two shape the mechanism after the panel, discussed with the Sponsor on 2026-10-02: the panel raises concerns, the Product Owner settles them (as `answer_story_problem` does now: an answer grounded in the project, or one question), and the Business Analyst splits the epic with them settled. A critic step is a separate thing, only if members miss the same findings together.

## What would change the plan

- The panel catches most of 1–10 only with item 4: the collection of decisions is the work, more than the roles.
- Members miss the same findings together: add a critic step, as #359 found for the Architect.
- Many unneeded notes: tighten "nothing to add" before building.
