"""Judge each replayed answer as the crew's parser would see it (crew#422).

Reads the answers from LiteLLM's spend log, where the proxy keeps every
response, and checks each against the Developer's `FirstOrDone` schema. A
server that ignores `response_format` (TensorFold) wraps the JSON in a ```json
fence, sometimes after prose, so the outer fence is stripped first: the answer's
own content can hold fences too, so only the last one closes it.

    uv run python experiments/flash-next-268/check_answers.py crew-flash-next 2026-10-01T02:42
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

from crew_org.crews.delivery_crew import FirstOrDone

QUERY = """
select to_char("startTime", 'HH24:MI:SS'), completion_tokens, proxy_server_request->>'seed',
       response->'choices'->0->>'finish_reason', response->'choices'->0->'message'->>'content'
from "LiteLLM_SpendLogs"
where model_group = '{alias}' and "startTime" > '{since}' and prompt_tokens > 90000
order by "startTime";
"""


def rows(alias: str, since: str) -> list[list[str]]:
    out = subprocess.run(
        ["docker", "exec", "litellm-temp-db", "psql", "-U", "litellm_admin", "-d", "litellm"]
        + ["-At", "-R", "\x1e", "-F", "\x1f", "-c", QUERY.format(alias=alias, since=since)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [r.split("\x1f") for r in out.strip("\x1e\n").split("\x1e") if r.strip()]


def verdict(content: str, finish: str) -> str:
    fenced = re.search(r"```json\s*(.*)\s*```\s*$", content, re.S)
    body = fenced.group(1) if fenced else content.strip()
    try:
        answer = json.loads(body)
        FirstOrDone.model_validate_json(body)
    except ValueError:
        if not content.strip():
            return "empty"
        if finish == "length":
            return "out of token budget"
        if "</invoke>" in content[-200:]:
            return "complete, but tool-call markup, not JSON"
        return "cut off mid-answer"
    prose = " (prose before the JSON)" if not content.lstrip().startswith(("```", "{")) else ""
    return f"valid: {len(answer.get('criteria_tests') or [])} criteria tests{prose}"


def main() -> None:
    alias, since = sys.argv[1], sys.argv[2]
    for start, tokens, seed, finish, content in rows(alias, since):
        print(f"{start} seed={seed or '-'} {finish} {tokens} tokens: {verdict(content, finish)}")


if __name__ == "__main__":
    main()
