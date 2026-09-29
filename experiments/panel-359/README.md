# The panel test (crew Discussion #359)

**The question:** when should the crew run several agents on one task (a panel), and how should the members differ? Run on 2026-09-29, in the quiet window after Sprint 10 closed, on the DGX Spark (Qwen3.8-27B under SGLang v0.5.20, through the LiteLLM proxy), with nothing else running.

**The short answer:**

| Role | What works | Evidence here |
|---|---|---|
| Reviews (code, deploy) | **Three identical reviews in parallel, findings combined** | Random misses: 2/5 → 9/10, 1/5 → 6/10 |
| Developer | **Best of three identical attempts, the sandbox picks** | 5/12 single runs pass; 3/4 stories get a pass |
| Architect | **A critic step ("what does this miss?")**, not a panel | Panels: 0/6 found the gap. Critics: 1/2 |
| Any role | **Correlated blind spots need critique, rules or checks** | `ENTRYPOINT` unprompted: 0/5, 0/10 as panels |

Parallel is cheap on the Spark. Three requests at once took 1.0–1.65× the wall time of one. KV cache use peaked at 8%. Total throughput rose from ~31 to ~57 tokens/s, so each request gets slower but the total goes up.

It's small-sample: 1 review case, 4 Developer stories, 1 design case. It gives direction, not settled rates.

**A caveat on the review results:** the deploy review's four questions were written looking at #269's Dockerfile. "As which user?" and "what's pinned?" pointed at its findings, which flatters recall. They were generalized afterwards (PR #375). The finding about correlated misses holds regardless: with the rule hidden, even those pointed questions never led a run to `ENTRYPOINT`.

## 1. Reviews: the deploy review of sprint-metrics#269

**The case:** the Dockerfile PR with four known problems (crew#335): the scrape server binds `127.0.0.1`, no `ENTRYPOINT`, runs as root, and a base image not pinned by digest. `run_panel.py` calls the crew's `review_deploy` with the evidence it was given on 2026-09-28 (`inputs/`). `analyse.py` makes the table.

**The arms:**
- **5 serial:** the image rule (§19.8), which names these problems, is shown.
- **Two parallel panels of 3:** the rule is shown.
- **5 serial with the rule hidden:** tests catching them unprompted.

| Finding | Rule shown: one run | …any 3 of 5 | Rule hidden: one run | …any 3 of 5 |
|---|---|---|---|---|
| Bind | 5/5 (0 blocking) | 10/10 | 2/5 | 9/10 |
| `ENTRYPOINT` | 2/5 | 9/10 | **0/5** | **0/10** |
| Root | 5/5 | 10/10 | 5/5 | 10/10 |
| Base pin | 5/5 | 10/10 | 1/5 | 6/10 |

- **The two real parallel panels (rule shown) caught all four.**
- **Random misses,** caught by some runs and not others, are what a panel fixes.
- **Correlated misses,** missed by every run, it can't. `ENTRYPOINT` unprompted was never caught, so a panel of identical prompts only repeats the blind spot.
- **Severity varies more than detection.** With the rule shown, the bind was raised 5/5 but blocked 0/5. A panel needs a rule for combining severity; "block if any member blocks" is the safe one.
- **Cost:**
  - median single run 176s; the panels took 182s and 290s wall time (1.04× and 1.65×)
  - peak KV use 8%, 3 running requests
  - `sglang_gen_throughput` averaged 57 tok/s in the parallel window, against 31 serial

## 2. Developer: best of three on real first attempts

**The cases:** four code stories whose first attempt failed, all completed later, so a correct answer exists.
- #200: first-attempt rate in the standup
- #264: the docs generator
- #266: a drift-check test
- #334: the scrape server's bind

**The method (`replay_dev.py`):**
- sprint-metrics is checked out at the commit `main` was on when each story's first attempt began (from the crew's event log).
- The Developer's context is built as delivery builds it: `focused_context` and the record's brief. The design note is left out.
- `implement_story` is called three times in parallel.
- Each answer goes through delivery's first-attempt path: up to two asks, then the overwrite, workflow-permission, bounds and regression guards, then apply, then the sandbox check.
- "Passed" means a green check. Crew-written code runs only in the configured sandbox; the script refuses to start without it.

| Story | Same prompt ×3 | Focus ×3 (smallest / most robust / tests first) |
|---|---|---|
| #334 | verify, verify, **passed** | verify, verify, verify |
| #266 | verify, edit, verify | verify, verify, verify |
| #264 | verify, **passed**, **passed** | verify, verify, **passed** (tests first) |
| #200 | already done, **passed**, **passed** | already done, already done, **passed** (tests first) |

- **Same prompt:** 5/12 single runs pass; best of three gets a pass on 3/4 stories.
- **Focus:** 2/12. Both passes came from **"tests first"**; "smallest change" and "most robust" passed 0/4 each. A focus line that competes with the story's own criteria pulls the model off them.
- **"Already done"** answers on #200 need a closer look: at that commit the work may have been partly present. They're counted as not passing.
- One real call came back empty mid-run (crew#312). CrewAI retried it.
- Each arm of three took 4–24 minutes of wall time, sandbox checks included (`results/dev-run.log`).

## 3. Architect: the design revision sprint-metrics#302

**The case:** the design for the image rule, whose reason says a service must answer from outside the container. The real Architect declared no change to `serve.py`'s `127.0.0.1` bind, though it was shown the whole repository, that line included. The deploy review caught it one step later (PR #330).

**The method (`replay_design.py`):** the crew's `propose_design`, with the real reason (`inputs/design-reason-19.8.txt`), on sprint-metrics at `446d6ec`, just before #302. Each proposal's declared changes are tagged by what they cover.

| Arm | Named the bind | Covered |
|---|---|---|
| Same prompt ×3 | 0/3 | digest, user, updater, lint, smoke (`ENTRYPOINT` in 2/3) |
| Lenses ×3 ("what runs where", "what could break it", "simplest") | 0/3 | the same set, all three |
| Advocate + 2 critics ("another architect proposed…; what does it miss?") | **1/2 critics** | the same set, plus *"Make the scrape server bind to all interfaces (0.0.0.0) rather than loopback."* |

- **Proposals converge.** All nine declared essentially the same set, differing in how many changes (3–8). The bind is a correlated blind spot, and neither identical prompts nor lenses shook it loose.
- **Critique did, once.** It was the only one of nine proposals to name it: a signal, not proof.
- **Cost:** a parallel arm of three took 7–8 minutes. The critic arm is sequential (a proposal, then critics): about 31 minutes.

## What this points to (crew#299)
- **Code Reviewer and deploy review:** a panel of 3 in parallel; findings combined; block if any member blocks.
- **Developer:**
  - parallel across cards and projects for throughput
  - best of three for a retry, instead of a single repair, with "tests first" as one of the three (crew#276, option A)
- **Architect:** a critic step. This fits the design review that exists, and the DevOps design review in crew#335.
- **QA:** measure first (3× QA on past stories) before any panel.
- **Capacity:** the limits are speed per request (throughput shared) and the Mac's sandbox (parallel `uv sync` and pytest), not the KV cache. Per-role concurrency is the DevOps Engineer's to set, from load measurements (the `infra` Goal: baselines, infrastructure and app mapping, capacity).

## Tools, for next time
Existing harnesses cover the scaffolding. The crew-specific parts (context at the base commit, delivery's guards, the finding tags) stay ours.
- **Serving capacity and baselines:** SGLang's `bench_serving` or GuideLLM, through the proxy, with a dataset exported from the LiteLLM spend log (the crew's real prompt sizes). That's the DevOps Engineer's first research question.
- **Quality panels:** Inspect (solvers, scorers, repeated runs, Docker sandboxes) or promptfoo, if panels become a regular test.
- Their current status needs checking before choosing.

## Running it again
From the crew repo, with no tick running:

```
uv run python experiments/panel-359/run_panel.py serial 5 [--hide-rule]
uv run python experiments/panel-359/run_panel.py parallel 3
uv run python experiments/panel-359/analyse.py
uv run python experiments/panel-359/replay_dev.py <card> 3 [--lens]
uv run python experiments/panel-359/replay_design.py same|lens|critic
```

- `replay_dev.py` reads the crew's local event log (`var/events`) for each story's first-attempt time, and clones sprint-metrics into the temp directory.
- The raw model-call log goes to `var/experiments/panel-359/events.jsonl`, which isn't committed: it holds prompt text.
- Traces go to the collector: in Tempo, `{resource.service.name="crew" && name=~"panel.*"}`.

**Metrics during a window** (Grafana, `grafanacloud-prom`):
- `max_over_time(sglang_token_usage[w])`
- `max_over_time(sglang_num_running_reqs[w])`
- `avg_over_time(sglang_gen_throughput[w])`
- time to first token: `increase` of `sglang_time_to_first_token_seconds_sum` over `_count`
- The spend log has no time to first token for these calls, because they don't stream.
