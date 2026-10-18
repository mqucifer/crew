"""Run refinement's sessions on a real epic, through LiteLLM, with nothing posted or moved.

    uv run python scripts/run_refinement.py sprint-metrics 667 --goal 665

For proving refinement's sessions before a tick runs them (crew#807). The epic is
refined as a tick refines it, as if the Sponsor had approved it: the panel, the
Product Owner's settle, the Architect's design questions, the split with its
criteria check and repair, then the UX Designer's note, each a session over a
checkout of the project at main. `--goal` runs the decomposition on a Goal first.

GitHub is read. Every write (a comment, an edit, a label, a story, a board move) is
kept here and read back by the steps after, so each reads what the one before
wrote, and nothing reaches GitHub or the board. The events go to
`var/events/refine-run.jsonl`; the writes and a summary to
`var/sessions/refine-<repo>-<epic>-<time>/`.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crew_org import events, git_ops
from crew_org.config import load_org
from crew_org.design import DesignPolicy
from crew_org.events import EventSink
from crew_org.process import ProcessRules
from crew_org.tools.github_issues import IssueClient
from crew_org.tools.github_project import ProjectClient

RUNS = Path("var/sessions")
CREW = "mqucifer-crew[bot]"


class Recorded:
    """An issue client that reads GitHub and keeps every write, as it would read back."""

    def __init__(self, real: IssueClient) -> None:
        self.real = real
        self.owner = real.owner
        self.trust = real.trust
        self.writes: list[dict[str, Any]] = []
        self._bodies: dict[tuple[str, int], str] = {}
        self._comments: dict[tuple[str, int], list[dict[str, Any]]] = {}
        self._labels: dict[tuple[str, int], set[str]] = {}
        self._made: dict[tuple[str, int], dict[str, Any]] = {}
        self._children: dict[tuple[str, int], list[int]] = {}
        self._closed: set[tuple[str, int]] = set()
        self._next = 90_000

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real, name)

    def _wrote(self, kind: str, repo: str, number: int, **detail: Any) -> None:
        self.writes.append({"kind": kind, "repo": repo, "number": number, **detail})

    def get(self, repo: str, number: int) -> dict[str, Any]:
        key = (repo, number)
        issue = dict(self._made.get(key) or self.real.get(repo, number))
        if key in self._bodies:
            issue["body"] = self._bodies[key]
        if key in self._labels:
            issue["labels"] = [{"name": n} for n in sorted(self._labels[key])]
        if key in self._closed:
            issue["state"] = "closed"
        return issue

    def labels_of(self, repo: str, number: int) -> set[str]:
        key = (repo, number)
        if key not in self._labels:
            found = self.get(repo, number).get("labels") or []
            self._labels[key] = {(x["name"] if isinstance(x, dict) else str(x)) for x in found}
        return self._labels[key]

    def comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        key = (repo, number)
        before = [] if key in self._made else self.real.comments(repo, number)
        return before + self._comments.get(key, [])

    def comment(self, repo: str, number: int, body: str) -> dict[str, Any]:
        made = {"body": body, "user": {"login": CREW}, "id": len(self.writes) + 1}
        self._comments.setdefault((repo, number), []).append(made)
        self._wrote("comment", repo, number, body=body)
        return made

    def has_comment_marked(self, repo: str, number: int, marker: str) -> bool:
        return any(marker in (c.get("body") or "") for c in self.comments(repo, number))

    def edit_issue(self, repo: str, number: int, *, body: str) -> dict[str, Any]:
        self._bodies[(repo, number)] = body
        self._wrote("edit", repo, number, body=body)
        return self.get(repo, number)

    def create(
        self, repo: str, title: str, body: str, labels: list[str] | None = None
    ) -> dict[str, Any]:
        self._next += 1
        number = self._next
        made = {
            "number": number,
            "id": number,
            "node_id": f"NODE{number}",
            "title": title,
            "body": body,
            "state": "open",
            "labels": [{"name": n} for n in labels or []],
            "html_url": f"https://github.com/{self.owner}/{repo}/issues/{number}",
        }
        self._made[(repo, number)] = made
        self._wrote("create", repo, number, title=title, body=body)
        return made

    def add_labels(self, repo: str, number: int, labels: list[str]) -> None:
        self.labels_of(repo, number).update(labels)
        self._wrote("label", repo, number, add=labels)

    def remove_label(self, repo: str, number: int, label: str) -> None:
        self.labels_of(repo, number).discard(label)
        self._wrote("label", repo, number, remove=label)

    def ensure_label(self, repo: str, name: str, *, color: str, description: str) -> None:
        self._wrote("ensure_label", repo, 0, name=name)

    def assign(self, repo: str, number: int, logins: list[str]) -> None:
        self._wrote("assign", repo, number, logins=logins)

    def close(self, repo: str, number: int, *, reason: str = "completed") -> dict[str, Any]:
        self._closed.add((repo, number))
        self._wrote("close", repo, number, reason=reason)
        return self.get(repo, number)

    def add_sub_issue(self, repo: str, parent_number: int, child_id: int) -> None:
        self._children.setdefault((repo, parent_number), []).append(child_id)
        self._wrote("sub_issue", repo, parent_number, child=child_id)

    def sub_issues(self, repo: str, number: int) -> list[dict[str, Any]]:
        key = (repo, number)
        before = [] if key in self._made else self.real.sub_issues(repo, number)
        return before + [self.get(repo, n) for n in self._children.get(key, [])]


class RecordedBoard:
    """The board as it is, with the epics given as approved, and every write kept."""

    def __init__(self, real: ProjectClient, approved: set[tuple[str, int]]) -> None:
        self.real = real
        self.writes: list[dict[str, Any]] = []
        self._cards = [
            c.model_copy(update={"status": "Needs Refinement"}) if c.key in approved else c
            for c in real.cards()
        ]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real, name)

    def cards(self) -> list[Any]:
        return list(self._cards)

    def add_issue(self, issue_node_id: str) -> str:
        self.writes.append({"kind": "add_issue", "node": issue_node_id})
        return f"ITEM-{issue_node_id}"

    def repo_of(self, item_id: str) -> str | None:
        return None if item_id.startswith("ITEM-") else self.real.repo_of(item_id)

    def _set(self, kind: str, *args: Any) -> None:
        self.writes.append({"kind": kind, "args": [str(a) for a in args]})

    def set_status(self, item_id: str, column: str) -> None:
        self._set("set_status", item_id, column)

    def set_owner_agent(self, item_id: str, role: str) -> None:
        self._set("set_owner_agent", item_id, role)

    def set_select(self, item_id: str, field: str, option: str) -> None:
        self._set("set_select", item_id, field, option)

    def set_number(self, item_id: str, field: str, value: float) -> None:
        self._set("set_number", item_id, field, value)

    def set_iteration(self, item_id: str, field: str, title: str) -> None:
        self._set("set_iteration", item_id, field, title)

    def clear_field(self, item_id: str, field: str) -> None:
        self._set("clear_field", item_id, field)

    def remove_item(self, item_id: str) -> None:
        self._set("remove_item", item_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("repo")
    parser.add_argument("epic", type=int)
    parser.add_argument("--goal", type=int, help="also decompose this Goal")
    args = parser.parse_args()
    from crew_org.crews import presentation_note_crew as ux  # noqa: PLC0415
    from crew_org.crews.refinement_crew import propose_epics  # noqa: PLC0415
    from crew_org.flows import board_flow, presentation_notes  # noqa: PLC0415

    token = os.environ["GH_TOKEN"]
    owner = "mqucifer"
    org = load_org()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    out = RUNS / f"refine-{args.repo}-{args.epic}-{stamp}"
    out.mkdir(parents=True)
    sink = EventSink(Path("var/events/refine-run.jsonl"))
    events.forward_calls_to(sink, card=args.epic)
    issues = Recorded(IssueClient(token, owner))
    board = RecordedBoard(ProjectClient(token, owner, 1), {(args.repo, args.epic)})
    ws = git_ops.Workspace(owner, args.repo, token, git_ops.BotIdentity("quarters-claude", 0))
    context = board_flow.RepoContext(ws, sink)
    known = board_flow.KnownDelivered(issues, sink)
    summary: dict[str, Any] = {"repo": args.repo, "epic": args.epic, "goal": args.goal}
    began = time.monotonic()
    try:
        if args.goal:
            goal = issues.get(args.repo, args.goal)
            with events.working_on(card=args.goal, repo=args.repo, purpose="decompose"):
                proposal = propose_epics(
                    f"{goal['title']}\n\n{goal.get('body') or ''}",
                    goal_ref=f"{owner}/{args.repo}#{args.goal}",
                    delivered=known.for_repo(args.repo).render(),
                    reading=context.reading_for(args.repo),
                )
            (out / "decomposition.json").write_text(proposal.model_dump_json(indent=1))
        cards = [c for c in board.cards() if (c.repo or args.repo) == args.repo]
        known.cards = cards
        result = board_flow.TickResult()
        with events.working_on(card=args.epic, repo=args.repo, purpose="refine"):
            board_flow.refine_epics(
                board,
                issues,
                sink,
                ProcessRules.from_config(org),
                DesignPolicy.from_config(org),
                result,
                # Only the epic given is approved, so only it is refined.
                cards=cards,
                default_repo=args.repo,
                context=context,
                known=known,
                panel_search=["crew", *sorted((org.get("delivery") or {}).get("repos") or [])],
            )
        summary["refine"] = {
            "refined": result.epics_refined,
            "stories": result.stories_created,
            "failed": result.failed,
            "skipped": result.skipped,
        }
        # The UX Designer's note, as the design phase writes it, on the epic as now.
        epic = next(c for c in cards if c.key == (args.repo, args.epic))
        epic = epic.model_copy(update={"labels": frozenset(issues.labels_of(args.repo, args.epic))})
        with events.working_on(card=args.epic, repo=args.repo, purpose="design"):
            shown = presentation_notes.write_notes(
                issues,
                sink,
                ws,
                [epic],
                default_repo=args.repo,
                repos={args.repo},
                write=ux.write_note,
                render=ux.render,
                line=ux.criterion_lines,
            )
        summary["presentation_note"] = {
            "written": shown.written,
            "blocked": shown.blocked,
            "failed": shown.failed,
        }
    finally:
        summary["wall_minutes"] = round((time.monotonic() - began) / 60, 1)
        (out / "writes.json").write_text(json.dumps(issues.writes, indent=1))
        (out / "board.json").write_text(json.dumps(board.writes, indent=1))
        (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
        print(json.dumps(summary, indent=1, default=str))
        print(f"written to {out}")


if __name__ == "__main__":
    main()
