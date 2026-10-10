"""Does the coverage map name the tests crew#584 found by hand? Step C1 of crew#583.

Builds the map for sprint-metrics at the commit epic 468's stories were written
against, in the sandbox, then asks which tests execute or read the definitions each
story's merged diff changed. crew#584 found, by hand, that the stories broke
`test_schema_gen.py::test_single_sprint_schema_required_and_cycle_time`, the
`test_schema.py` validations and `test_docs.py::test_drift_check`.

    uv run python experiments/coverage-584/check.py

Writes `result.json` here. Needs Docker; clones into `var/`, which git ignores.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from crew_org.tools.ast_edit import definitions
from crew_org.tools.coverage_map import build

HERE = Path(__file__).parent
CLONE = Path("var/experiments/coverage-584/sprint-metrics")
STORE = Path("var/experiments/coverage-584/store")
# The base of epic 468's stories: main just before sprint-metrics#536 merged.
BASE = "2ba575e"
# Epic 468's stories, by their merge commits.
STORIES = {
    536: "f912123",
    537: "d98f069",
    538: "62f099b",
    539: "8db5f33",
    540: "d8348c3",
    541: "9233fe7",
}
FOUND_BY_HAND = [
    "tests/test_schema_gen.py::test_single_sprint_schema_required_and_cycle_time",
    "tests/test_schema.py::",
    "tests/test_docs.py::test_drift_check",
]


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=CLONE, capture_output=True, text=True, check=True
    ).stdout


def changed(commit: str) -> set[str]:
    """The definitions in the parent whose lines the commit's diff changes."""
    found: set[str] = set()
    path = None
    for line in git("diff", "-U0", f"{commit}^", commit, "--", "src/").splitlines():
        if line.startswith("--- a/"):
            path = line[6:]
        hunk = re.match(r"@@ -(\d+)(?:,(\d+))? ", line)
        if not (hunk and path):
            continue
        start, count = int(hunk.group(1)), int(hunk.group(2) or 1)
        source = git("show", f"{commit}^:{path}")
        spans = [
            ((getattr(n, "decorator_list", None) or [n])[0].lineno, n.end_lineno or n.lineno, name)
            for name, n in definitions(source).items()
        ]
        for number in range(start, start + max(count, 1)):
            holding = [(end - s, name) for s, end, name in spans if s <= number <= end]
            if holding:
                found.add(f"{path}::{min(holding)[1]}")
    return found


def main() -> None:
    if not CLONE.exists():
        CLONE.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "-q", "https://github.com/mqucifer/sprint-metrics", str(CLONE)],
            check=True,
        )
    git("checkout", "-q", BASE)
    coverage = build(CLONE, "sprint-metrics", store=STORE)
    result = {"base": coverage.commit, "tests_in_map": len(coverage.tests), "stories": {}}
    for story, commit in STORIES.items():
        names = sorted(changed(commit))
        covering = coverage.covering(names=names)
        result["stories"][story] = {
            "changed": names,
            "covering": len(covering),
            "found_by_hand": {
                want: [t for t in covering if t.startswith(want)] for want in FOUND_BY_HAND
            },
        }
    (HERE / "result.json").write_text(json.dumps(result, indent=1) + "\n")
    print(
        json.dumps(
            {
                s: {w: len(t) for w, t in r["found_by_hand"].items()} | {"of": r["covering"]}
                for s, r in result["stories"].items()
            },
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
