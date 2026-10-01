"""Replay a stored model request through the LiteLLM proxy and record what came back.

Used for crew#312 (empty answers). Export the request first (see
docs/runbooks/empty-model-answers.md), then:

    uv run python scripts/replay_request.py <request.json> <arm> <runs> [--seed]

`<arm>` names the results file (`results-<arm>.jsonl`, beside the request).
An arm ending in `miss` makes every run miss SGLang's prefix cache by putting a
different number of zero-width spaces (U+200B) at the start of the first message:
the chat template strips leading whitespace, not these, and they mean nothing to
the model.

`--seed` sends each run its number as `seed`. A server that samples with a
fixed default seed, as TensorFold does, returns the same output to the same
request: without it, five runs are one sample (crew#422).

Runs are sequential and non-streaming. Killing this script does NOT stop the
request on the server: SGLang keeps decoding a non-streaming request after the
client disconnects, so wait for the server to go idle before starting another arm.
"""

from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

import httpx

from crew_org.llm import api_key, base_url


def main() -> None:
    path, arm, runs = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
    seeded = "--seed" in sys.argv[4:]
    stored = json.loads(path.read_text())
    body = {
        k: stored[k] for k in ("model", "messages", "max_tokens", "response_format") if k in stored
    }
    body["stream"] = False
    miss = arm.endswith("miss")
    out = path.parent / f"results-{arm}.jsonl"
    with httpx.Client(timeout=2400) as client:
        for run in range(1, runs + 1):
            send = copy.deepcopy(body) if miss or seeded else body
            if seeded:
                send["seed"] = run
            if miss:
                send["messages"][0]["content"] = "​" * (run + 1) + send["messages"][0]["content"]
            started = time.time()
            try:
                response = client.post(
                    f"{base_url()}/chat/completions",
                    json=send,
                    headers={"Authorization": f"Bearer {api_key()}"},
                )
                response.raise_for_status()
                reply = response.json()
                usage = reply.get("usage") or {}
                details = usage.get("completion_tokens_details") or {}
                message = reply["choices"][0]["message"]
                row = {
                    "arm": arm,
                    "run": run,
                    "seconds": round(time.time() - started),
                    "reasoning": details.get("reasoning_tokens"),
                    "text": details.get("text_tokens"),
                    "finish": reply["choices"][0].get("finish_reason"),
                    "content_chars": len(message.get("content") or ""),
                    "reasoning_tail": (message.get("reasoning_content") or "")[-160:],
                }
            except Exception as exc:  # noqa: BLE001
                row = {
                    "arm": arm,
                    "run": run,
                    "seconds": round(time.time() - started),
                    "error": str(exc)[:300],
                }
            with out.open("a") as results:
                results.write(json.dumps(row) + "\n")
            print(json.dumps({k: v for k, v in row.items() if k != "reasoning_tail"}), flush=True)


if __name__ == "__main__":
    main()
