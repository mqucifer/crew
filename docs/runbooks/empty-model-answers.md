# Runbook: the model thinks, then answers nothing

**Tracks:** crew#312. **Started:** 2026-09-28. **Status:** investigating. See [Findings so far](#findings-so-far).

This is the process we followed and the evidence at each step, so the next person, or the DevOps/SRE role (crew#335), can repeat it. It moves to the `infra` repo once that exists.

## The symptom
- A model call thinks for thousands of tokens and returns an **empty answer**:
  - `completion_tokens_details.text_tokens = 0`
  - `finish_reason = stop`
  - no `content`
- The crew's own log shows nothing wrong:
  - **CrewAI's native client** resends the request without the JSON schema, inside the same call.
  - **The task** then retries the whole thing.
  - So you see `llm.finished` followed by `llm.started`, with no failure between them.
- **Since crew#341,** each empty answer is recorded as an `llm.empty` event in `var/events/*.jsonl`.
- **The cost:** throughput. sprint-metrics#268 spent about 24 of its 35 minutes on three empty answers, all from the same prompt.

## Where to look (read-only)

### 1. The crew's events
```bash
grep '"llm.empty"' var/events/tick.jsonl | tail
```
Each event carries the model, whether a structured answer was requested, and the card, role and purpose being worked on. The prompt size is on the `llm.finished` just before it.

### 2. LiteLLM's request log (every request, with usage and the response)
The container is `litellm-temp-db`, database `litellm`, user `litellm_admin`. Never print the `.env` values.

Each call split into thinking and answer tokens:
```sql
select to_char("startTime" at time zone 'UTC','HH24:MI:SS'), model_group, prompt_tokens,
       response->'usage'->'completion_tokens_details'->>'reasoning_tokens' as reasoning,
       response->'usage'->'completion_tokens_details'->>'text_tokens'      as text,
       response->'choices'->0->>'finish_reason'                            as finish
from "LiteLLM_SpendLogs" where "startTime" > now() - interval '2 hours' order by "startTime";
```

The empty-answer rate by prompt size (**this is the table that found the pattern**):
```sql
with c as (
  select prompt_tokens p,
         coalesce((response->'usage'->'completion_tokens_details'->>'text_tokens')::int, -1) t,
         (proxy_server_request->>'response_format') is not null js
  from "LiteLLM_SpendLogs"
  where model_group = 'crew-code-think' and status = 'success' and "startTime" > now() - interval '7 days')
select case when p < 20000 then 'a <20k' when p < 50000 then 'b 20-50k'
            when p < 90000 then 'c 50-90k' else 'd 90k+' end bucket,
       js, count(*), sum((t = 0)::int) empty, round(100.0*sum((t=0)::int)/count(*),1) pct
from c where t >= 0 group by 1,2 order by 1,2;
```
Read the `js = false` rows with care. They're mostly CrewAI's schema-less resend after an empty call, so they're biased towards the hard cases.

How an empty call ended, and what was asked for:
```sql
select right(response->'choices'->0->'message'->>'reasoning_content', 600) as reasoning_tail,
       proxy_server_request->>'stop' as stop, proxy_server_request->>'max_tokens' as max_tokens
from "LiteLLM_SpendLogs" where prompt_tokens = <N> order by "startTime" limit 1;
```
**What we saw:**
- the thinking ends mid-sentence, even mid-function
- no stop sequences were sent
- far below `max_tokens`

So the model or the server ended the generation. It didn't hit a limit or a stop string. LiteLLM doesn't keep SGLang's `matched_stop`.

### 3. SGLang on the Spark (SSH to `sparky`, read only)
Which image and speculative decoding are running (the last value of a repeated flag wins):
```bash
docker inspect qwen3.8-27b-sglang --format '{{.Config.Image}}'
docker inspect qwen3.8-27b-sglang --format '{{join .Args " "}}' | tr ' ' '\n' | grep -A1 speculative
```

Whether a request was served from the prefix cache:
```bash
docker logs --since <UTC time> qwen3.8-27b-sglang 2>&1 | grep 'Prefill batch'
```
- `#new-token: 27, #cached-token: 115840` means served from cache.
- `#cached-token: 0` means computed fresh.

Launch settings live in `~/Qwen3.8-27B-SGLang-DGX-Spark/.env` and `start-dflash.sh`.

### 4. The dashboard
Grafana's SGLang dashboard, panel **Prefill Effective Tokens by Cache Mode**:
- `device_hit` means served from the prefix cache.
- `input` means computed fresh.

It shows local time, not UTC. The Sponsor spotted the cache split here.

## Reproducing it

1. **Export the failing request.** LiteLLM stores the full request body:
   ```bash
   docker exec litellm-temp-db psql -U litellm_admin -d litellm -At -c \
     "select proxy_server_request::text from \"LiteLLM_SpendLogs\" where prompt_tokens = <N> order by \"startTime\" limit 1;" > request.json
   ```
2. **Replay it through the proxy,** never straight to SGLang:
   ```bash
   uv run python scripts/replay_request.py request.json dflash-hit 5    # warm: served from cache
   uv run python scripts/replay_request.py request.json dflash-miss 5   # cold: every run misses the cache
   ```
3. **Check each arm really hit or missed the cache,** using the `Prefill batch` lines in step 3 above.
4. **Watch out for these:**
   - **Leading spaces don't force a miss.** The chat template strips them. The script uses zero-width spaces (U+200B) instead.
   - **Killing the script doesn't stop the server.** SGLang keeps decoding a non-streaming request after the client leaves. Wait until no `Decode batch` lines have appeared for about 15 seconds before starting the next arm.
   - **The crew must be idle.** No tick should run during a test.

## Upstream research
- **The serving setup** is [MiaAI-Lab/Qwen3.8-27B-SGLang-DGX-Spark](https://github.com/MiaAI-Lab/Qwen3.8-27B-SGLang-DGX-Spark), cloned on the Spark. It's thorough. Read `README.md`, `CHANGELOG.md` and `docs/brainstorms/`. Relevant findings:
  - **Known DFlash2 issues it lists:**
    - sglang [#38009](https://github.com/sgl-project/sglang/issues/38009): DFlash2 output diverges from the target model when thinking is on
    - sglang [#36548](https://github.com/sgl-project/sglang/issues/36548): state corruption under concurrency

    It says MTP and DSpark are unaffected.
  - **Long context was validated on MTP only.**
  - **The image pin:** `start-dflash.sh` pins `nightly-cu134-20260909-708f51e`. The CHANGELOG for that pin says **"Re-pin to `v0.5.20` when tagged."**
- **The fix that matches our pattern:** sglang [#37818](https://github.com/sgl-project/sglang/pull/37818), *Track DFlash Mamba state at checkpoint boundaries*, merged 2026-09-12.
  - DFlash could miss a GDN ("mamba") state checkpoint when accepted draft tokens crossed a tracking boundary. The attention cache and the recurrent state then sat at different positions.
  - It was tested as warm-cache against cold-cache output.
  - We run the affected path: DFlash with `--mamba-radix-cache-strategy extra_buffer`.

**Is a build behind a fix?**
```bash
gh pr view <PR> --repo sgl-project/sglang --json mergeCommit --jq .mergeCommit.oid
gh api repos/sgl-project/sglang/compare/<merge commit>...<image commit or tag> --jq .status
```
`ahead` means the build contains the fix; `behind` or `diverged` means it doesn't. Nightly tags carry the commit, e.g. `nightly-cu134-20260909-708f51e`.
- **Our 09-09 image** (`708f51e`) is **behind** #37818.
- **`v0.5.20`** (released 2026-09-18) is **ahead**. Its notes list #37818, #37165 and #34820, all hybrid cache fixes. It also contains everything the 09-09 pin was chosen for: #35255, #35496, #34763, #35371 and #34859.
- **Image:** `lmsysorg/sglang:v0.5.20-cu130` (arm64), index digest `sha256:06e4f2ed21afde4ff513cda65070124e727ba23ccaeff7712b8c40e1097d611f`.

## Findings so far
| Arm | Image | Cache | Runs | Empty | Notes |
|---|---|---|---|---|---|
| warm | 09-09 nightly, DFlash2 | hit | 5 | **5** | 8,211–16,833 thinking tokens, 0 answer tokens, `stop` |
| cold | 09-09 nightly, DFlash2 | miss | 5 | *running* | |
| warm | v0.5.20-cu130, DFlash2 | hit | 5 | *next* | |

**The request:** sprint-metrics#268, the Developer, 115,867 prompt tokens, `crew-code-think`, JSON schema `FirstOrDone`.

**Empty-answer rate by prompt size,** `crew-code-think`, 7 days, constrained JSON:

| Prompt size | Calls | Empty |
|---|---|---|
| under 20k | 46 | 0% |
| 20–50k | 53 | 0% |
| 50–90k | 66 | 6% |
| 90k+ | 35 | 26% |

**Warm against cold, the crew's real calls,** 2026-09-28 12:30–18:20 UTC. That's everything the Spark's logs still held. A call is **warm** when at least half its prompt came from the prefix cache, going by the first `Prefill batch` line within 8 seconds of LiteLLM's start time. 62 of 77 calls matched a prefill line; the rest were mostly CrewAI's immediate resends.

| Prompt size | Cache | Calls | Empty |
|---|---|---|---|
| under 50k | cold | 23 | 0 |
| under 50k | warm | 11 | 0 |
| 50–90k | cold | 8 | 0 |
| 90k+ | cold | 7 | 1 (14%) |
| 90k+ | **warm** | 13 | **8 (62%)** |

Long prompts served from cache are where the empty answers are. The single cold empty answer suggests a second, smaller cause, possibly sglang #38009.

**How to repeat this analysis:**
1. Save the prefill lines: `docker logs --since <t> qwen3.8-27b-sglang 2>&1 | grep "Prefill batch"`.
2. Save the calls: LiteLLM's start time, alias, prompt tokens and `text_tokens` for the same window.
3. Match each call to the first prefill line within 8 seconds after it, and compare `#cached-token` against `#new-token`.

## Other possible symptoms of the same bug
If the recurrent GDN state sometimes sits at a different position from the attention cache, the model is reading a subtly corrupted context. An empty answer is only the most visible outcome. Worth checking once the fix is in:
- **Circular or looping thinking on long structured generations.** This is the recorded reason the Developer's thinking was switched off (the `crew-code` comment in `deploy/litellm/config.yaml`: 27 minutes, no output), and later limited to low effort. If it was this bug, both were workarounds for a serving fault. **Re-measure the Developer with thinking on at normal effort once the fix is confirmed.**
- **Plausible but wrong answers.** Ignored instructions, invented names (for example, sprint-metrics#261 naming "CI run of release.yml" as an existing test), details missed deep in a long context. Some SCHEMA retries and first-attempt misses may be this rather than the model.
- **Identical retries that behave very differently,** depending on whether the prefix was cached.
- **Our prompt design increases exposure.** Every role's prompt is ordered stable-first so the prefix cache hits, which is exactly the path the bug corrupts.
- **Separately, DFlash2's cross-request context bleed (sglang #36548)** when calls overlap: a role answering about another prompt. It's less likely here, because the crew mostly runs one request at a time.

## Decisions
- **No thinking-off fallback.** The Sponsor, 2026-09-28: *"Making it dumber shouldn't be a solution. Correctness is most important."*
- **No switch to MTP** until the Sponsor has weighed its impact. It's slower than DFlash2: 2.25× slower for code and 1.41× for prose on this box, per the repo's measurements.
- **Prefer a released SGLang to a nightly** when re-pinning.

## Next steps
1. Finish the cold arm. If cold answers and warm stays empty, the #37818 mechanism is confirmed on this box.
2. Restart on v0.5.20 and repeat the warm arm:
   ```bash
   IMAGE=lmsysorg/sglang@sha256:06e4f2ed21afde4ff513cda65070124e727ba23ccaeff7712b8c40e1097d611f ./start-dflash.sh
   ```
   To roll back, run `./start-dflash.sh` with no override.
3. If v0.5.20 answers, pin it in `.env` and run a normal tick to confirm the `llm.empty` count falls.
4. Either way: keep the Developer's prompts focused (#231). Huge prompts trigger this, and they're also noise for the model.
