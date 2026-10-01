# sprint-metrics#268 on Qwen3.8-Flash-Next, against the 27B (crew#422, crew#312)

A read-only replay, 2026-09-30 to 2026-10-01, through the LiteLLM proxy to the DGX Spark. Nothing was posted or applied. The request is the Developer's for sprint-metrics#268: 115,867 prompt tokens and JSON schema `FirstOrDone`. On it, production's 27B answers empty 4 times in 5. The story is to trim the README to an entry point, move its 15 tests into `tests/test_docs.py`, and delete `tests/test_readme.py`.

Each arm replays the stored request with `scripts/replay_request.py`. The per-run rows are in `results/`. The answers themselves stay in LiteLLM's spend log, which isn't committed because it holds the prompt. `check_answers.py` judges them as the crew's parser would.

## Results

| Arm | Model and server | Distinct samples | Usable answers |
|---|---|---|---|
| `27b-production-low` (2026-09-28) | Qwen3.8-27B NVFP4, SGLang v0.5.20, DFlash2, 8-bit KV, `low` | 5 | 1 (4 empty) |
| `27b-medium-effort` | same, `medium` | 5 | 2 (3 empty) |
| `27b-kv-bf16` | same, 16-bit KV cache | 5 | 2 (3 empty) |
| `27b-fp8-weights` | `Qwen/Qwen3.8-27B-FP8` | 3 | 0 (3 empty, stopped early) |
| `flash-next-default-seed` + `flash-next-seeds-1-5` | Qwen3.8-Flash-Next, TensorFold | 6 | **3 (0 empty)** |

The 27B arms are written up in `docs/runbooks/empty-model-answers.md`.

**Flash-Next, sample by sample** (`check_answers.py crew-flash-next 2026-10-01T02:20`):

| Seed | Finish | Tokens | Verdict |
|---|---|---|---|
| default (run 3 times) | `stop` | 23,444 | Valid, 3 criteria tests. **Identical all three times** (see below) |
| 1 | `stop` | 26,599 | Valid, 13 criteria tests |
| 2 | `stop` | 20,100 | Cut off mid-answer, inside a test body |
| 3 | `stop` | 19,330 | Complete, but tool-call markup (`<edit>…</invoke>`), not JSON |
| 4 | `length` | 32,768 | Out of budget after about 27k tokens of thinking |
| 5 | `stop` | 27,035 | Valid, 12 criteria tests, after a paragraph of prose |

## What it shows
- **Flash-Next never answered empty** and was about twice as fast: 55–65 tok/s generated on a 115k prompt against the 27B's 22–35, and 5–9 minutes a run.
- **The early end-of-turn is the model family's, not our serving's.** Seed 2 stopped cleanly (`stop`) at an arbitrary point, as the 27B does, on a different server and different weights. It happened in the answer, not the thinking, and once in six, not four in five.
- **Two of the three failures are fixable at our end.** A parser can take the JSON out of a fence or after prose, and could read tool-call markup. A larger `max_tokens`, or a firmer effort setting, may let seed 4 finish.
- **Schema-valid isn't crew-valid.** The default-seed answer lists the existing `tests/test_docs.py` as a new file, which delivery refuses. Seed 1 edits a definition, `add_json_import`, that may not exist. The trial has to go through apply, the sandbox and QA, as `experiments/multilang-404` does.

## Things to know before the next experiment
- **TensorFold samples with a fixed default seed.** Identical requests return identical output: the three unseeded runs above were token-for-token the same, and each took the full 6 minutes, so this isn't a cache. Use `replay_request.py … --seed` (each run sends its number) or a seed per call.
- **It ignores `response_format`** and a top-level `reasoning_effort`. Effort goes in `chat_template_kwargs`, as the commented `crew-flash-next` alias in `deploy/litellm/config.yaml` does.
- **It takes the whole Spark and port 8888,** so production's 27B must be stopped first, and restarted, with the alias commented out again, before any tick.
- **One request is one data point.** #268 is the 27B's hardest known case. A fair comparison needs the crew's ordinary stories too, and the DevOps team's experiment design (crew#422).
