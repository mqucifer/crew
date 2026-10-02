"""The tables for README.md, from the panel's answers and their scores.

    uv run python experiments/panel-174/analyse.py

Reads `results/results.jsonl` (one line per member call) and
`results/scores.jsonl` (one line per note, scored by reading it: which of the
README's ten findings it names, or whether it's otherwise valid, unneeded, or
wrong: a note that would have led the split into one of the findings).
Per-request timing comes from LiteLLM's spend log, read-only, for each call's
window: the model's own time, separate from CrewAI's around it.
"""

from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median

HERE = Path(__file__).parent
FINDINGS = range(1, 11)
ROLES = ["architect", "ux_designer", "qa_engineer", "devops_engineer"]
SHORT = {"architect": "Arch", "ux_designer": "UX", "qa_engineer": "QA", "devops_engineer": "DevOps"}


def load(name: str) -> list[dict]:
    path = HERE / "results" / name
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def key(row: dict) -> tuple:
    return row["arm"], row["run"], row["epic"], row["role"]


def spend(start: str, end: str) -> list[dict]:
    """LiteLLM's records for requests that started in [start, end]."""
    sql = (
        'select "startTime", "endTime", model_group, prompt_tokens, completion_tokens, '
        'status from "LiteLLM_SpendLogs" '
        f'where "startTime" >= \'{start}\' and "startTime" <= \'{end}\' order by "startTime"'
    )
    out = subprocess.run(
        [
            "docker",
            "exec",
            "litellm-temp-db",
            "psql",
            "-U",
            "litellm_admin",
            "-d",
            "litellm",
            "-At",
            "-F",
            "\t",
            "-c",
            sql,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    rows = []
    for line in out.splitlines():
        s, e, model, p, c, status = line.split("\t")
        rows.append(
            {
                "start": s,
                "end": e,
                "model": model,
                "prompt": int(p or 0),
                "completion": int(c or 0),
                "status": status,
            }
        )
    return rows


def header(cells: list[str]) -> None:
    print("| " + " | ".join(cells) + " |")
    print("|" + "---|" * len(cells))


def main() -> None:
    calls = load("results.jsonl")
    scores = load("scores.jsonl") if (HERE / "results" / "scores.jsonl").exists() else []
    by_call: dict[tuple, list[dict]] = defaultdict(list)
    for s in scores:
        by_call[key(s)].append(s)

    arms = sorted({c["arm"] for c in calls})
    print("## Findings caught, per run (any member)\n")
    print("| Arm | Run | " + " | ".join(str(f) for f in FINDINGS) + " | Caught |")
    print("|---|---|" + "---|" * (len(FINDINGS) + 1))
    for arm in arms:
        for run in sorted({c["run"] for c in calls if c["arm"] == arm}):
            got: dict[int, set[str]] = defaultdict(set)
            part: dict[int, set[str]] = defaultdict(set)
            for s in scores:
                if s["arm"] == arm and s["run"] == run:
                    for f in s.get("findings", []):
                        got[f].add(SHORT[s["role"]])
                    for f in s.get("partial", []):
                        part[f].add(SHORT[s["role"]])
            caught = len(got)
            partial = len([f for f in part if f not in got])
            # Reading a defaultdict adds the key, so count before the cells.
            cells = [
                ",".join(sorted(got[f]))
                if got.get(f)
                else ("(" + ",".join(sorted(part[f])) + ")" if part.get(f) else "·")
                for f in FINDINGS
            ]
            print(f"| {arm} | {run} | " + " | ".join(cells) + f" | {caught}/10 (+{partial}) |")
    print("\nA role names the finding; (role) raises it only in part.")

    print("\n## Notes, per arm and role\n")
    header(
        [
            "Arm",
            "Role",
            "Calls",
            "Nothing to add",
            "Notes",
            "On a finding",
            "Other valid",
            "Unneeded",
            "Wrong",
            "Overlap",
            "Failed",
        ]
    )
    for arm in arms:
        for role in ROLES:
            rows = [c for c in calls if c["arm"] == arm and c["role"] == role]
            notes = [s for c in rows for s in by_call[key(c)]]
            print(
                f"| {arm} | {SHORT[role]} | {len(rows)} "
                f"| {sum(1 for c in rows if c.get('nothing_to_add'))} "
                f"| {sum(len(c.get('notes', [])) for c in rows)} "
                f"| {sum(1 for s in notes if s.get('findings'))} "
                f"| {sum(1 for s in notes if s['kind'] == 'valid')} "
                f"| {sum(1 for s in notes if s['kind'] == 'unneeded')} "
                f"| {sum(1 for s in notes if s['kind'] == 'wrong')} "
                f"| {sum(1 for s in notes if s.get('dup'))} "
                f"| {sum(1 for c in rows if 'error' in c)} |"
            )

    print("\n## Who would settle the needed notes, per arm\n")
    settlers = ["product_owner", "architect", "sponsor"]
    print("| Arm | " + " | ".join(settlers) + " |")
    print("|---|" + "---|" * len(settlers))
    for arm in arms:
        needed = [s for s in scores if s["arm"] == arm and s["kind"] in ("finding", "valid")]
        print(
            f"| {arm} | "
            + " | ".join(str(sum(1 for s in needed if s.get("settler") == who)) for who in settlers)
            + " |"
        )

    print("\n## Time and tokens, per arm\n")
    header(
        [
            "Arm",
            "Calls",
            "Median call (s)",
            "Median request (s)",
            "Wall per epic (s, median)",
            "Requests",
            "Prompt tokens",
            "Completion tokens",
            "Completion tok/s",
        ]
    )
    for arm in arms:
        rows = [c for c in calls if c["arm"] == arm]
        per_epic = defaultdict(list)
        for c in rows:
            per_epic[(c["run"], c["epic"])].append(c)
        walls = []
        for group in per_epic.values():
            s = min(datetime.fromisoformat(c["start"]) for c in group)
            e = max(datetime.fromisoformat(c["end"]) for c in group)
            walls.append((e - s).total_seconds())
        reqs = [r for c in rows for r in spend(c["start"], c["end"])]
        # In the parallel arm, a window holds the other members' requests too.
        reqs = list({(r["start"], r["model"], r["prompt"]): r for r in reqs}.values())
        took = [
            (datetime.fromisoformat(r["end"]) - datetime.fromisoformat(r["start"])).total_seconds()
            for r in reqs
        ]
        # Tokens per second of wall time across the arm's epics: what the
        # server delivered, all requests together.
        rate = sum(r["completion"] for r in reqs) / sum(walls)
        print(
            f"| {arm} | {len(rows)} | {median(c['seconds'] for c in rows):.0f} "
            f"| {median(took):.0f} | {median(walls):.0f} | {len(reqs)} "
            f"| {sum(r['prompt'] for r in reqs):,} | {sum(r['completion'] for r in reqs):,} "
            f"| {rate:.0f} |"
        )


if __name__ == "__main__":
    main()
