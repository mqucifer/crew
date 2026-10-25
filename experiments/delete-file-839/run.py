"""One real Developer session that deletes a file, then the gates at `done` (crew#839).

On a clone of sprint-metrics, never the real repository: the clone's push URL is
disabled first, and nothing here pushes, comments or moves a card. The session runs
held, as a delivery's does, and calls the Developer's model through LiteLLM with the
builders' key, read inside this process. It writes the events to `<clone>/../events.jsonl`
and prints the calls, the tools and the gate's verdict.

    uv run python experiments/delete-file-839/run.py <clone>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

from crew_org import profiles
from crew_org.config import load_env
from crew_org.events import EventSink, forward_calls_to
from crew_org.flows import gates
from crew_org.project import read_record
from crew_org.session import conversation
from crew_org.tools.container import Held
from crew_org.tools.github_project import Card
from crew_org.tools.workspace import check

BRANCH = "feat/9839-remove-the-scrape-page"
STORY = """Remove the scrape page

The scrape command's page goes with the command (the retire Goal). Its link and \
its generated section go with it.

## Acceptance criteria
- docs/scrape.md is removed: the file isn't in the repository.
- No doc page, nor the docs generator, names docs/scrape.md.

**Existing tests** — this story changes what they assert: tests/test_docs.py, \
which lists scrape.md among the generated pages.
"""


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def main(clone: Path) -> None:
    clone = clone.resolve()
    if git(clone, "remote", "get-url", "--push", "origin").strip() != "DISABLED":
        sys.exit("disable the clone's push URL: git remote set-url --push origin DISABLED")
    git(clone, "checkout", "-q", "-B", BRANCH, "origin/main")
    since = git(clone, "rev-parse", "HEAD").strip()
    key = load_env(Path.home() / ".config/crew/builders.env").get("CREW_LLM_API_KEY")
    if not key:
        sys.exit("no CREW_LLM_API_KEY in builders.env")
    os.environ["CREW_LLM_API_KEY"] = key

    log = clone.parent / "events.jsonl"
    log.unlink(missing_ok=True)
    sink = EventSink(log)
    forward_calls_to(sink, card=9839)
    record = read_record(clone)
    profiles.set_project(record)
    held = Held(clone, "sprint-metrics-9839")
    try:
        started = held.start()
        if not started.ok:
            sys.exit(f"the workspace didn't start: {started.output[-500:]}")
        setup = held.setup()
        if setup is not None and not setup.ok:
            sys.exit(f"the setup failed: {setup.output[-500:]}")
        session = conversation.session(clone, STORY, held, held.tools, base=since, round=1)
        ending = session.run()
        verdict = None
        if ending.how == "done":
            card = Card(
                item_id="S9839",
                number=9839,
                title="Remove the scrape page",
                repo="sprint-metrics",
                status="In Progress",
                state="OPEN",
                work_type="Story",
            )
            verdict = gates.at_done(
                clone,
                ending.said,
                card=card,
                story=STORY,
                record=record,
                run_checks=lambda at: check(at, held=held, since=since),
                permissions={},
            )
    finally:
        held.stop()

    events = [json.loads(line) for line in log.read_text().splitlines()]
    tools = Counter(e["detail"].get("tool") for e in events if e["kind"] == "tool.finished")
    deleted = [
        e["detail"].get("target")
        for e in events
        if e["kind"] == "tool.finished" and e["detail"].get("tool") == "delete_file"
    ]
    print(f"ended: {ending.how} ({ending.why or '-'}), model calls: {session.calls}")
    print(f"model time: {session.model_s:.0f}s; tools: {dict(tools)}")
    print(f"delete_file targets, from tool.finished: {deleted}")
    print(f"docs/scrape.md exists: {(clone / 'docs/scrape.md').exists()}")
    print("branch diff:\n" + git(clone, "status", "--short"))
    if verdict is not None:
        print(f"gate passed: {verdict.passed}")
        for f in verdict.findings:
            print(f"- {f.rule}: {f.says[:300]}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
