"""The Business Analyst's focused split of sprint-metrics#62, read-only (#231).

The same loop refinement runs: a map plus what the epic names, and up to two
asks for files. Nothing is posted; the result is written here.

Run from the repository root: uv run python experiments/analyst-231/run.py
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path

from crew_org.crews.refinement_crew import split_epic
from crew_org.flows.board_flow import ANALYST_ASK_LIMIT, REFINE_FOCUS_ABOVE_CHARS
from crew_org.project import brief, read_record
from crew_org.tools.repo_context import focused_context

OUT = Path(__file__).parent / "result.json"


def main() -> None:
    clone = Path(tempfile.mkdtemp()) / "sprint-metrics"
    subprocess.run(
        [
            "git",
            "clone",
            "-q",
            "--depth",
            "1",
            "https://github.com/mqucifer/sprint-metrics",
            str(clone),
        ],
        check=True,
    )
    record = brief(read_record(clone))
    raw = subprocess.check_output(
        ["gh", "issue", "view", "62", "--repo", "mqucifer/sprint-metrics", "--json", "title,body"]
    )
    epic = json.loads(raw)
    about = f"{epic['title']}\n\n{epic['body']}"
    asked: list[str] = []
    rounds = []
    for asks in range(ANALYST_ASK_LIMIT + 1):
        text, focus = focused_context(
            clone, about=about, extra=asked, editing=False, above=REFINE_FOCUS_ABOVE_CHARS
        )
        started = time.monotonic()
        proposal = split_epic(epic["title"], epic["body"], repository=f"{record}\n\n{text}")
        rounds.append(
            {
                "context_chars": len(record) + 2 + len(text),
                "in_full": focus.shown,
                "seconds": round(time.monotonic() - started),
                "asked": proposal.need_files,
                "stories": [s.title for s in proposal.stories],
            }
        )
        if not proposal.asks or asks == ANALYST_ASK_LIMIT:
            break
        asked += [f for f in proposal.need_files if f not in asked]
    OUT.write_text(json.dumps(rounds, indent=2))
    print(json.dumps(rounds, indent=2))


if __name__ == "__main__":
    main()
