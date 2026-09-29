"""The panel experiment's verdict, from results.jsonl (crew Discussion #359).

    uv run python experiments/panel-359/analyse.py

Recall: for each of #335's four findings, the share of single serial runs that
raised it, against the share of every three-run combination of them whose
union did, and against each parallel arm (itself a panel of three). Cost: each
parallel arm's wall time against the median single run.
"""

from __future__ import annotations

import json
import statistics
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).parent
FINDINGS = ["1 bind", "2 entrypoint", "3 root", "4 base pin"]


def main() -> None:
    runs = [
        json.loads(line) for line in (HERE / "results" / "results.jsonl").read_text().splitlines()
    ]
    ok = [r for r in runs if "found" in r]
    failed = [r for r in runs if "error" in r]
    print(f"{len(ok)} runs answered, {len(failed)} failed")
    for r in failed:
        print(f"  {r['arm']} run {r['run']}: {r['error'][:100]}")
    for hidden in (False, True):
        serial = [
            r for r in ok if r["arm"].startswith("serial") and ("-hidden" in r["arm"]) == hidden
        ]
        arms: dict[str, list[dict]] = {}
        for r in ok:
            if r["arm"].startswith("parallel") and ("-hidden" in r["arm"]) == hidden:
                arms.setdefault(r["arm"], []).append(r)
        if not serial and not arms:
            continue
        print(f"\n== image rule {'HIDDEN' if hidden else 'shown'} ==")
        print("Recall (raised at all; blocking in brackets)")
        head = f"{'finding':14} {'single':>10} {'any 3 of serial':>16} "
        print(head + " ".join(f"{a:>22}" for a in arms))
        trios = list(combinations(serial, 3))
        for f in FINDINGS:
            single = sum(f in r["found"] for r in serial)
            blocking = sum(r["found"].get(f) == "block" for r in serial)
            union = sum(any(f in r["found"] for r in t) for t in trios)
            cells = [f"{single}/{len(serial)} ({blocking})", f"{union}/{len(trios)}"]
            cells += ["yes" if any(f in r["found"] for r in rs) else "no" for rs in arms.values()]
            row = f"{f:14} {cells[0]:>10} {cells[1]:>16} "
            print(row + " ".join(f"{c:>22}" for c in cells[2:]))
        if serial:
            median = statistics.median(r["seconds"] for r in serial)
            print(f"Cost: median single run {median:.0f}s")
            for arm, rs in arms.items():
                wall = max(r["seconds"] for r in rs)
                print(f"  {arm}: {len(rs)} runs, {wall:.0f}s wall ({wall / median:.2f}x one run)")


if __name__ == "__main__":
    main()
