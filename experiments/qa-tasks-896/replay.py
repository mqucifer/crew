"""QA's test Task for a merged story, worked by its session, with nothing filed (crew#896).

On clones, never the real repositories: both clones' push URLs are disabled first, and
nothing here pushes, comments or moves a card. qa-suite's clone is checked out at
`suite_ref`, the branch carrying its record, as a worktree whose default branch is put
there too, so `base` and the checks are as a Task's session would have them. The
project's clone, at its default branch, is placed beside the Task's folder as the tick
places it. The session runs in the held workspace and calls QA's model through LiteLLM
with the builders' key, read inside this process. GitHub is only read: the story.

    uv run python experiments/qa-tasks-896/replay.py \
        <suite-clone> <suite-ref> <project-clone> <story>

The Task's body is the one `open_tests` would file. It writes the events, the ending,
the gates' verdict and the branch's diff to `var/sessions/qa-896-<story>/`, and prints
what the session did and where its ending would send the Task.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from crew_org import profiles
from crew_org.config import load_env
from crew_org.events import EventSink, forward_calls_to
from crew_org.flows import gates, qa_tasks
from crew_org.flows import role_tasks as rt
from crew_org.project import read_record
from crew_org.session import conversation
from crew_org.tools.container import Held
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import Card
from crew_org.work_types import STORY

PROJECT = "sprint-metrics"
OUT = Path("var/sessions")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


class Clone:
    """The project's clone where the tick's workspace would fetch one."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def for_repo(self, repo: str) -> Clone:
        return self

    def current(self) -> Path:
        return self.path


def main(suite: Path, suite_ref: str, project: Path, number: int) -> None:
    suite, project = suite.resolve(), project.resolve()
    for clone in (suite, project):
        if git(clone, "remote", "get-url", "--push", "origin").strip() != "DISABLED":
            sys.exit(f"disable {clone}'s push URL: git remote set-url --push origin DISABLED")
    key = load_env(Path.home() / ".config/crew/builders.env").get("CREW_LLM_API_KEY")
    if not key:
        sys.exit("no CREW_LLM_API_KEY in builders.env")
    os.environ["CREW_LLM_API_KEY"] = key

    base = git(suite, "rev-parse", suite_ref).strip()
    subprocess.run(["git", "symbolic-ref", "--delete", "refs/remotes/origin/HEAD"], cwd=suite)
    git(suite, "update-ref", "refs/remotes/origin/HEAD", base)
    worktree = suite.parent / f"qa-896-{number}"
    if worktree.exists():
        git(suite, "worktree", "remove", "--force", str(worktree))
    git(suite, "worktree", "add", "-q", "-B", f"qa-896-{number}", str(worktree), base)

    out = OUT / f"qa-896-{number}"
    out.mkdir(parents=True, exist_ok=True)
    log = out / "events.jsonl"
    log.unlink(missing_ok=True)
    sink = EventSink(log)
    forward_calls_to(sink, card=number)

    issues = IssueClient(os.environ["GH_TOKEN"], "mqucifer")
    story = issues.get(PROJECT, number)
    merged = Card(
        item_id=f"S{number}",
        number=number,
        title=story["title"],
        repo=PROJECT,
        status="Done",
        state="CLOSED",
        work_type=STORY,
    )
    kind, task_key = qa_tasks.test_key(merged)
    title, body = qa_tasks.test_body(issues, card=merged, kind=kind, key=task_key)
    (out / "task.md").write_text(f"# {title}\n\n{body}\n")
    task = Card(
        item_id="T",
        number=0,
        title=title,
        repo=rt.SUITE,
        status="In Progress",
        state="OPEN",
        work_type="Task",
        labels=frozenset({rt.QA.label, rt.project_label(PROJECT)}),
    )
    role = rt.role_of(task)
    assert role is not None
    qa_tasks.place_reference(Clone(project), worktree, PROJECT)
    record = read_record(worktree)
    profiles.set_project(record)
    today = datetime.now(UTC).date().isoformat()
    start = f"{rt.task_start(role, today, [], rt.SUITE)}\n\n{title}\n\n{body}"

    held = Held(worktree, f"qa-896-{number}")
    try:
        started = held.start()
        if not started.ok:
            sys.exit(f"the workspace didn't start: {started.output[-500:]}")
        setup = held.setup()
        if setup is not None and not setup.ok:
            sys.exit(f"setup failed: {setup.output[-500:]}")
        session = conversation.session(
            worktree,
            start,
            held,
            held.tools,
            role=role.spec,
            specs=rt.done_specs(role, (), rt.SUITE, kind),
            scope=role.scope_for(rt.SUITE),
            base=base,
            round=1,
        )
        ending = session.run()
        said = ending.said or {}
        (out / "ending.json").write_text(json.dumps({"how": ending.how, **said}, indent=2))
        verdict = None
        if ending.how == "done" and not said.get(rt.NO_TEST):
            git(worktree, "add", "-A")
            git(
                worktree,
                "-c",
                "user.name=replay",
                "-c",
                "user.email=replay@localhost",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                f"qa({number}): the session's work",
            )
            verdict = gates.at_done(worktree, said, card=task, story=start, record=record)
        (out / "diff.patch").write_text(git(worktree, "diff", base, "--", "."))
    finally:
        held.stop()

    events = [json.loads(line) for line in log.read_text().splitlines()]
    tools = Counter(e["detail"].get("tool") for e in events if e["kind"] == "tool.finished")
    calls = sum(e["kind"] == "llm.finished" for e in events)
    print(f"{PROJECT}#{number} as QA's {kind} Task: model calls {calls}")
    print(f"tools: {dict(tools)}")
    print(f"ended: {ending.how} {ending.why or ''}")
    if said.get(rt.NO_TEST):
        print(f"no_test: {said[rt.NO_TEST]}")
    for c in said.get("criteria") or []:
        print(f"  - {c.get('criterion', '')[:80]} -> {c.get('test')}")
    if verdict is not None:
        rules = [f.rule for f in verdict.findings]
        print(f"gates at done: {'passed' if verdict.passed else rules}")
        (out / "gates.json").write_text(json.dumps({"passed": verdict.passed, "rules": rules}))
    print(f"changed: {git(worktree, 'diff', '--stat', base).strip() or 'nothing'}")
    print(f"written to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), int(sys.argv[4]))
