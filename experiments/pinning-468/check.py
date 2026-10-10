"""What the split is shown as pinning tests at epic 468's splits (crew#583, step C2).

Runs `pinning_tests` with the coverage map on sprint-metrics at epic 468's base,
given each stored split's epic text and record, and counts how many of the merged
tests sm#507 and sm#529 went on to break are among those shown.

    uv run python experiments/coverage-584/check.py   # the clone and its map, once
    uv run python experiments/pinning-468/check.py
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from crew_org.tools.coverage_map import build
from crew_org.tools.pinning import pinning_tests

CLONE = Path("var/experiments/coverage-584/sprint-metrics")
STORE = Path("var/experiments/coverage-584/store")
CALLS = (
    Path(os.environ.get("INFRA_RESULTS", Path.home() / "infra-results"))
    / "context-review/epic-468/calls.jsonl"
)
SPLITS = ("2026-10-09T19:15", "2026-10-09T19:46", "2026-10-09T21:04")
# The merged tests each story broke in delivery, from the event log.
BROKE = {
    507: {
        "tests/test_docs.py::test_drift_check",
        "tests/test_docs.py::test_drift_check_detects_stale_docstring",
        "tests/test_docs.py::test_readme_unchanged_by_docs_gen",
        "tests/test_docs_gen.py::test_generate_file_preserves_handwritten_content",
        "tests/test_docs_gen.py::test_generated_metrics_section_contains_required_content",
    },
    529: {
        "tests/test_docs.py::test_drift_check",
        "tests/test_schema.py::test_single_sprint_json_output_validates_against_schema",
        "tests/test_schema.py::test_empty_sprint_json_output_validates_against_schema",
        "tests/test_schema_gen.py::test_single_sprint_schema_required_and_cycle_time",
    },
}


def shown(block: str) -> set[str]:
    """The tests a pinning block shows, as `path::test`."""
    found, current = set(), None
    for line in block.splitlines():
        if heading := re.match(r"### `([^`]+)`", line):
            current = heading.group(1)
        if (test := re.match(r"def (test\w+)", line)) and current:
            found.add(f"{current}::{test.group(1)}")
    return found


def main() -> None:
    coverage = build(CLONE, "sprint-metrics", store=STORE)
    calls = [json.loads(line) for line in CALLS.read_text().splitlines()]
    for when in SPLITS:
        call = next(
            c
            for c in calls
            if c["start"]["at"][:16] == when and c["start"]["role"] == "Business Analyst"
        )
        prompt = call["request"]["proxy_server_request"]["messages"][1]["content"]
        epic = prompt[prompt.index("Split this epic into stories.") :]
        record = prompt[
            prompt.index("## Refinement conclusion") : prompt.index("## The Sponsor sent")
        ]
        block = pinning_tests(CLONE, f"{epic}\n\n{record}", coverage=coverage)
        names = shown(block)
        counts = " | ".join(f"sm#{s} {len(b & names)}/{len(b)}" for s, b in BROKE.items())
        print(f"split {when[11:]}: {len(names)} tests shown, {len(block):,} characters | {counts}")
        print(f"  {block.splitlines()[-1]}")


if __name__ == "__main__":
    main()
