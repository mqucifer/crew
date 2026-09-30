"""The UX Designer on sprint-metrics#62, read-only (#377's "done when").

#62 isn't split yet, so the Business Analyst splits it first, in memory. Then the
UX Designer writes its presentation note against those stories. Nothing is
posted: the stories get stand-in numbers, and the result is written here.

Run from the repository root: uv run python experiments/ux-377/run.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from crew_org.crews.presentation_note_crew import criterion_lines, render, write_note
from crew_org.crews.refinement_crew import split_epic
from crew_org.flows.board_flow import render_story_body
from crew_org.flows.presentation_notes import coverage, with_reader_criteria
from crew_org.project import ProjectRecordError, brief, read_record
from crew_org.tools.repo_context import repository_context

OUT = Path(__file__).parent / "results"
REPO = "mqucifer/sprint-metrics"


def gh_issue(number: int) -> dict:
    raw = subprocess.check_output(
        ["gh", "issue", "view", str(number), "--repo", REPO, "--json", "title,body"]
    )
    return json.loads(raw)


def main() -> int:
    OUT.mkdir(exist_ok=True)
    clone = Path(tempfile.mkdtemp()) / "sprint-metrics"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"https://github.com/{REPO}", str(clone)], check=True
    )
    try:
        project = brief(read_record(clone))
    except ProjectRecordError:
        project = ""
    repository = repository_context(clone, editing=False)
    goal, epic = gh_issue(48), gh_issue(62)

    started = time.monotonic()
    split = split_epic(epic["title"], epic["body"], repository=repository)
    split_s = time.monotonic() - started
    numbers = {s.title: 9001 + i for i, s in enumerate(split.stories)}
    stories = [
        {
            "number": numbers[s.title],
            "title": s.title,
            "state": "open",
            "body": render_story_body(s, 62, epic["title"]),
        }
        for s in split.stories
    ]
    story_text = "\n\n".join(f"### #{s['number']} {s['title']}\n\n{s['body']}" for s in stories)

    started = time.monotonic()
    note = write_note(
        goal=f"#48 {goal['title']}\n\n{goal['body']}",
        epic=f"#62 {epic['title']}\n\n{epic['body']}",
        stories=story_text,
        project=project,
        repository=repository,
    )
    note_s = time.monotonic() - started
    problems = coverage(note, stories)
    by_number = {s["number"]: s for s in stories}
    updated = {
        e.story: with_reader_criteria(by_number[e.story]["body"], e.criteria, criterion_lines)
        for e in note.stories
        if e.story in by_number
    }
    (OUT / "note.md").write_text(render(note))
    (OUT / "stories.md").write_text("\n\n---\n\n".join(updated.values()))
    (OUT / "result.json").write_text(
        json.dumps(
            {
                "stories": len(stories),
                "criteria_per_story": {e.story: len(e.criteria) for e in note.stories},
                "coverage_problems": problems,
                "beyond_reach": note.beyond_reach,
                "split_seconds": round(split_s),
                "note_seconds": round(note_s),
            },
            indent=2,
        )
    )
    print(
        json.dumps(
            {
                "stories": len(stories),
                "problems": problems,
                "split_s": round(split_s),
                "note_s": round(note_s),
            }
        )
    )
    return 0 if not problems and not note.beyond_reach else 1


if __name__ == "__main__":
    sys.exit(main())
