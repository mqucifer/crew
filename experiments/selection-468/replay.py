"""Epic 468's Developer first attempts again, with the graph's selection (crew#583, step C3).

Each stored first attempt is resent through LiteLLM with its focused context rebuilt by
today's `focused_context`, which now shows the named code's imports and names what is
close to it, from the coverage map. Does the first answer still only ask for files?

    uv run python experiments/selection-468/replay.py

The source is the context review's `calls.jsonl`; the clone is sprint-metrics at epic
468's base, as `experiments/coverage-584/check.py` leaves it. Writes `results.json`.
"""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from crew_org.llm import api_key, base_url
from crew_org.tools.coverage_map import build
from crew_org.tools.repo_context import focused_context

HERE = Path(__file__).parent
CLONE = Path("var/experiments/coverage-584/sprint-metrics")
STORE = Path("var/experiments/coverage-584/store")
CALLS = (
    Path(os.environ.get("INFRA_RESULTS", Path.home() / "infra-results"))
    / "context-review/epic-468/calls.jsonl"
)
START = "## The repository as it stands"
END = "Every other file is listed above by name"


def first_attempts() -> list[dict]:
    seen, found = set(), []
    for line in CALLS.read_text().splitlines():
        call = json.loads(line)
        start = call["start"]
        if start["role"] == "Developer" and start["card"] not in seen:
            seen.add(start["card"])
            found.append(call)
    return found


def rebuilt(prompt: str, story: str, coverage) -> str:
    """The stored prompt with its repository section rebuilt by today's selection."""
    head, rest = prompt.split(START, 1)
    tail = rest.split(END, 1)[1].split("\n\n", 1)[1]
    text, _ = focused_context(CLONE, about=story, coverage=coverage)
    return f"{head}{START}\n\n{text}\n\n{tail}"


def run(call: dict, coverage) -> dict:
    request = call["request"]["proxy_server_request"]
    system, prompt = (m["content"] for m in request["messages"])
    story = prompt.split("Current Task: ", 1)[-1][:6000]
    new = rebuilt(prompt, story, coverage)
    body = {k: request[k] for k in ("model", "max_tokens", "response_format") if k in request}
    body["messages"] = [{"role": "system", "content": system}, {"role": "user", "content": new}]
    started = time.monotonic()
    response = httpx.post(
        f"{base_url()}/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {api_key()}"},
        timeout=3600,
    ).json()
    text = response["choices"][0]["message"].get("content") or ""
    try:
        answer = json.loads(text)
    except json.JSONDecodeError:
        answer = {}
    before = json.loads(call["request"]["response"]["choices"][0]["message"]["content"] or "{}")
    changes = sum(len(answer.get(k) or []) for k in ("new_files", "edits", "text_edits", "moves"))
    return {
        "story": call["start"]["card"],
        "before": {
            "prompt_tokens": call["end"]["detail"].get("prompt_tokens"),
            "asked": [
                a.get("path", a) if isinstance(a, dict) else a
                for a in before.get("need_files") or []
            ],
        },
        "after": {
            "prompt_tokens": response.get("usage", {}).get("prompt_tokens"),
            "asked": [
                a.get("path", a) if isinstance(a, dict) else a
                for a in answer.get("need_files") or []
            ],
            "changes": changes,
            "empty": not text.strip(),
            "seconds": round(time.monotonic() - started),
        },
    }


def main() -> None:
    coverage = build(CLONE, "sprint-metrics", store=STORE)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda c: run(c, coverage), first_attempts()))
    (HERE / "results.json").write_text(json.dumps(results, indent=1) + "\n")
    for r in results:
        print(r["story"], r["before"], r["after"])


if __name__ == "__main__":
    main()
