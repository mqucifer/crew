"""Hold the mypy error count at or below the recorded baseline (crew#459).

`[tool.mypy]` is strict, but the code predates it and holds hundreds of
errors. Rather than leave mypy out of CI, this lets the count only go down: a
change that adds an error fails, and a change that fixes some is told to
lower the baseline in the same PR.

    uv run python scripts/mypy_ratchet.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "mypy-baseline"
SUMMARY = re.compile(r"^Found (\d+) errors? in ")


def count_errors(output: str) -> int:
    """The error count from mypy's summary line; 0 when it reports success."""
    for line in reversed(output.splitlines()):
        found = SUMMARY.match(line)
        if found:
            return int(found.group(1))
        if line.startswith("Success:"):
            return 0
    raise ValueError(f"no mypy summary line in:\n{output[-2000:]}")


def judge(count: int, baseline: int) -> tuple[bool, str]:
    if count > baseline:
        return False, (
            f"mypy: {count} errors, above the baseline of {baseline}. "
            "Fix the new errors; the baseline only goes down."
        )
    if count < baseline:
        return True, (
            f"mypy: {count} errors, below the baseline of {baseline}. "
            f"Lower mypy-baseline to {count} in this PR."
        )
    return True, f"mypy: {count} errors, at the baseline."


def main() -> int:
    done = subprocess.run(
        [sys.executable, "-m", "mypy", "src"], cwd=ROOT, capture_output=True, text=True
    )
    count = count_errors(done.stdout + done.stderr)
    ok, message = judge(count, int(BASELINE.read_text().strip()))
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
