"""Replay of epic 468's split and design note with the record in place of the latest answer.

Step A0 of `docs/plans/context-record-build.md` (crew#583, ADR 0023). See README.md here.

Each arm resends one stored request from LiteLLM's log, unchanged except for its
decision block, through the proxy, and records which field names its answer uses.

    uv run python experiments/record-468/replay.py --runs 3
    uv run python experiments/record-468/replay.py --runs 1 --arms split-rows design-rows

The source is the context review's join of the proxy's log to the crew's events:
`$INFRA_RESULTS/context-review/epic-468/calls.jsonl` (default `~/infra-results`). It
holds prompts, so it stays on the Mac; this writes answers and scores to
`var/experiments/record-468/`, which git ignores, and only the scores to `results/`.

Run it only when no tick is running: the crew and this would share the Spark.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

HERE = Path(__file__).parent
RESULTS = HERE / "results" / "replay.jsonl"
ANSWERS = Path("var/experiments/record-468")
CALLS = (
    Path(os.environ.get("INFRA_RESULTS", Path.home() / "infra-results"))
    / "context-review"
    / "epic-468"
    / "calls.jsonl"
)

# The calls replayed, by start minute and role, as the review names them.
SPLIT = ("2026-10-09T21:04", "Business Analyst")
DESIGN = ("2026-10-09T21:25", "Architect")
SETTLE = ("2026-10-08T14:24", "Product Owner")

# The names the Product Owner settled on at 13:30, 19:45 and 20:22, and the ones
# that replaced them in the design note and shipped in 1.2.0.
KEPT = ["points_delivered", "first_attempt_count", "first_attempt_total", "escalation_count"]
LOST = ["first_attempt_numerator", "first_attempt_denominator"]
HYPHENATED = ["points-delivered", "first-attempt-numerator", "escalation-count"]


def load_calls() -> list[dict]:
    return [json.loads(line) for line in CALLS.read_text().splitlines() if line.strip()]


def find(calls: list[dict], key: tuple[str, str]) -> dict:
    for call in calls:
        start = call["start"]
        if (start["at"][:16], start["role"]) == key and start.get("card") == 468:
            return call
    raise SystemExit(f"no call {key} in {CALLS}")


def answer_of(call: dict) -> str:
    return call["request"]["response"]["choices"][0]["message"]["content"]


@dataclass
class Row:
    id: str
    context: str
    decision: str
    source: str
    set_by: str
    date: str
    consequences: str = ""


def cell(text: str) -> str:
    """One table cell: the text as it was written, on one line, pipes escaped."""
    return re.sub(r"\s+", " ", text).replace("|", "\\|").strip()


def answer_rows(calls: list[dict]) -> list[Row]:
    """Every Product Owner answer on the epic after its settle, as a row, oldest first.

    The Decision cell is the answer verbatim and the Source cell is what it says it
    follows. The Context is the headline of the story problem it answered, taken
    from that call's own prompt. Nothing is summarised: the replay tests where the
    decisions are put, not how they are worded.
    """
    rows = []
    for call in calls:
        start = call["start"]
        if start["role"] != "Product Owner" or start.get("card") != 468:
            continue
        if (start["at"][:16], start["role"]) == SETTLE:
            continue
        answer = json.loads(answer_of(call))
        prompt = call["request"]["proxy_server_request"]["messages"][1]["content"]
        why = prompt.split("## Why it came back", 1)[-1]
        headline = re.search(r"\*\*(.+?)\*\*", why)
        rows.append(
            Row(
                id=f"R{len(rows) + 3}",
                context="Story problem: " + (headline.group(1) if headline else "returned split"),
                decision=answer["answer"],
                source="; ".join(answer.get("based_on", [])),
                set_by="Product Owner",
                # When the answer was written, as the review dates it.
                date=call["end"]["at"][:16].replace("T", " "),
            )
        )
    return rows


# The two rows the settle wrote, as the conclusion held them.
SETTLED = [
    Row(
        id="R1",
        context="Points column dependency",
        decision="#468 stories summing points are ordered after #467's schema migration",
        consequences=(
            "Store/metrics stories touching points wait; non-points #468 stories may proceed"
        ),
        source="Goal: crew sends points, returns them; #467: 'cards carrying points…persists them'",
        set_by="Product Owner",
        date="2026-10-08 14:26",
    ),
    Row(
        id="R2",
        context="API version for new fields",
        decision="New fields are MINOR addition to API v1; schema.py changes are additive",
        consequences="No new API version; METRIC_KEYS and schemas gain keys, none removed",
        source="Project decision log 2: 'additions (including a new API version) are MINOR'",
        set_by="Product Owner",
        date="2026-10-08 14:26",
    ),
]

Q1 = "JSON field names and nesting for points delivered, first-attempt counts, escalation count"
Q1_IMPACT = "Crew's delivery-history schema (crew#546) pins shape once ingested"


def record(rows: list[Row], *, q1_replaced_by: str = "") -> str:
    """The epic's record in the form ADR 0023 gives it: every row, its status and author."""
    lines = [
        "## The epic's record",
        "",
        "Every decision on this epic so far, oldest first. A binding row holds until a "
        "later row replaces it; the Replaces column says which.",
        "",
        "### Decisions",
        "",
        "| ID | Status | Context | Decision | Consequences | Set by | Date | Replaces | Source |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in SETTLED + rows:
        replaces = "Q1" if row.id == q1_replaced_by else "—"
        lines.append(
            f"| {row.id} | binding | {cell(row.context)} | {cell(row.decision)} | "
            f"{cell(row.consequences)} | "
            f"{row.set_by} | {row.date} | {replaces} | {cell(row.source)} |"
        )
    lines += ["", "### Open", ""]
    if q1_replaced_by:
        lines.append(f"None. Q1 ({Q1}) was settled by {q1_replaced_by}.")
    else:
        lines += [
            "| ID | Question | Impact | Settled by |",
            "|---|---|---|---|",
            f"| Q1 | {Q1} | {Q1_IMPACT} | Design note |",
        ]
    return "\n".join(lines)


def split_prompt(original: str, block: str) -> str:
    """The 21:04 split, its conclusion replaced by the record and the latest answer removed."""
    head, rest = original.split("## The epic's conclusion\n\n", 1)
    _, instructions = rest.split("_Settled by the Product Owner from the panel's notes", 1)
    instructions = instructions.split("\n\n", 1)[1]
    returned, after = instructions.split("**Decided:**", 1)
    after = after.split("— *Product Owner*", 1)[1]
    return f"{head}{block}\n\n{returned.rstrip()}\n{after}"


def design_prompt(original: str, block: str, *, settled: bool) -> str:
    """The 21:25 design note, the conclusion and "Decided for this epic" replaced by the record."""
    head, rest = original.split("## Refinement conclusion\n\n", 1)
    _, rest = rest.split("The design note follows this; it doesn't contradict it.", 1)
    if settled:
        stories, tail = rest.split("## The questions the epic's conclusion left open for you", 1)
        tail = tail.split("Don't leave one open.\n\n", 1)[1]
        rest = stories + tail
    return f"{head}{block}{rest}"


@dataclass
class Arm:
    name: str
    call: tuple[str, str]
    build: Callable[[str], str] | None  # None resends the original prompt


def arms(calls: list[dict]) -> dict[str, Arm]:
    rows = answer_rows(calls)
    # The answer that settled the names and the nesting (20:22) is the one a record
    # would have had replace Q1. The later 21:02 answer stays a row as it was written.
    settling = next(r.id for r in rows if r.date == "2026-10-09 20:22")
    as_written = record(rows)
    settled = record(rows, q1_replaced_by=settling)
    return {
        "split-orig": Arm("split-orig", SPLIT, None),
        "split-rows": Arm("split-rows", SPLIT, lambda p: split_prompt(p, as_written)),
        "split-settled": Arm("split-settled", SPLIT, lambda p: split_prompt(p, settled)),
        "design-orig": Arm("design-orig", DESIGN, None),
        "design-rows": Arm(
            "design-rows", DESIGN, lambda p: design_prompt(p, as_written, settled=False)
        ),
        "design-settled": Arm(
            "design-settled", DESIGN, lambda p: design_prompt(p, settled, settled=True)
        ),
    }


def score(answer: str) -> dict:
    text = answer.lower()
    return {
        "kept": {name: text.count(name) for name in KEPT},
        "lost": {name: text.count(name) for name in LOST},
        "hyphenated": {name: text.count(name) for name in HYPHENATED},
    }


def criteria_text(arm: str, answer: str) -> str:
    """The part of the answer the plan's test reads: the split's criteria, the note whole."""
    if not arm.startswith("split"):
        return answer
    try:
        stories = json.loads(answer).get("stories", [])
    except json.JSONDecodeError:
        return answer
    return json.dumps([s.get("acceptance_criteria", []) for s in stories])


def send(request: dict, prompt: str, system: str) -> tuple[str, dict, float]:
    from crew_org.llm import api_key, base_url

    body = {
        "model": request["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": request.get("max_tokens"),
        "response_format": request.get("response_format"),
    }
    started = time.monotonic()
    response = httpx.post(
        f"{base_url()}/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {api_key()}"},
        timeout=1800,
    )
    response.raise_for_status()
    data = response.json()
    return (
        data["choices"][0]["message"].get("content") or "",
        data.get("usage", {}),
        round(time.monotonic() - started, 1),
    )


def run_one(arm: Arm, call: dict, run: int) -> dict:
    request = call["request"]["proxy_server_request"]
    system, original = (m["content"] for m in request["messages"])
    prompt = arm.build(original) if arm.build else original
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    ANSWERS.mkdir(parents=True, exist_ok=True)
    (ANSWERS / f"{arm.name}-prompt.txt").write_text(prompt)
    try:
        answer, usage, seconds = send(request, prompt, system)
        error = ""
    except httpx.HTTPError as exc:
        answer, usage, seconds, error = "", {}, 0.0, f"{type(exc).__name__}: {exc}"
    (ANSWERS / f"{arm.name}-{run}-{stamp}.txt").write_text(answer)
    row = {
        "arm": arm.name,
        "run": run,
        "at": stamp,
        "seconds": seconds,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "empty": not answer.strip(),
        "error": error,
        "criteria": score(criteria_text(arm.name, answer)),
        "whole": score(answer),
    }
    with RESULTS.open("a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps(row), flush=True)
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--arms", nargs="*")
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument("--dry", action="store_true", help="write the prompts, call nothing")
    args = parser.parse_args()
    calls = load_calls()
    every = arms(calls)
    chosen = [every[name] for name in (args.arms or every)]
    if args.dry:
        ANSWERS.mkdir(parents=True, exist_ok=True)
        for arm in chosen:
            call = find(calls, arm.call)
            original = call["request"]["proxy_server_request"]["messages"][1]["content"]
            prompt = arm.build(original) if arm.build else original
            (ANSWERS / f"{arm.name}-prompt.txt").write_text(prompt)
            print(f"{arm.name}: {len(prompt):,} chars (original {len(original):,})")
        return
    RESULTS.parent.mkdir(exist_ok=True)
    jobs = [(arm, find(calls, arm.call), run) for run in range(1, args.runs + 1) for arm in chosen]
    with ThreadPoolExecutor(args.parallel) as pool:
        list(pool.map(lambda job: run_one(*job), jobs))


if __name__ == "__main__":
    main()
