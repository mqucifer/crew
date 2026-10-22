"""GitHub and the board, as far as opening a role's Task touches them (crew#839)."""

from __future__ import annotations

from typing import Any

from crew_org.events import EventSink


class Board:
    def __init__(self) -> None:
        self.done: list[tuple] = []
        self.fail: str | None = None

    def add_issue(self, node_id: str) -> str:
        if self.fail == "add":
            raise RuntimeError("board refused")
        self.done.append(("add", node_id))
        return f"item-{node_id}"

    def set_select(self, item: str, field: str, option: str) -> None:
        self.done.append(("select", item, field, option))

    def set_number(self, item: str, field: str, value: float) -> None:
        self.done.append(("number", item, field, value))

    def set_status(self, item: str, column: str) -> None:
        self.done.append(("status", item, column))

    def set_owner_agent(self, item: str, role: str) -> None:
        self.done.append(("owner", item, role))

    def repo_of(self, item: str) -> str:
        return "sm"


class Issues:
    def __init__(self) -> None:
        self.issues: dict[int, dict[str, Any]] = {}
        self.subs: dict[int, list[int]] = {}
        self.comments_on: dict[int, list[dict[str, Any]]] = {}
        self.files: dict[str, str] = {}
        self.done: list[tuple] = []
        self.next = 900
        self.fail: str | None = None

    def add(self, number: int, body: str = "", labels: tuple[str, ...] = (), **kw: Any) -> dict:
        issue = {
            "number": number,
            "id": number * 10,
            "node_id": f"N{number}",
            "body": body,
            "state": kw.pop("state", "open"),
            "labels": [{"name": n} for n in labels],
            "title": kw.pop("title", f"Issue {number}"),
            **kw,
        }
        self.issues[number] = issue
        return issue

    def ensure_label(self, repo: str, name: str, *, color: str, description: str) -> None:
        self.done.append(("ensure_label", name))

    def create(self, repo: str, title: str, body: str, labels: list[str] | None = None) -> dict:
        if self.fail == "create":
            raise RuntimeError("GitHub refused")
        self.next += 1
        self.done.append(("create", self.next, title))
        return self.add(self.next, body, tuple(labels or ()), title=title)

    def labelled(self, repo: str, label: str) -> list[dict]:
        return [i for i in self.issues.values() if label in {lab["name"] for lab in i["labels"]}]

    def sub_issues(self, repo: str, number: int) -> list[dict]:
        return [self.issues[n] for n in self.subs.get(number, [])]

    def add_sub_issue(self, repo: str, parent: int, child_id: int) -> None:
        if self.fail == "link":
            raise RuntimeError("link refused")
        self.subs.setdefault(parent, []).append(child_id // 10)
        self.done.append(("link", parent, child_id // 10))

    def get(self, repo: str, number: int) -> dict:
        return self.issues[number]

    def comments(self, repo: str, number: int) -> list[dict]:
        return self.comments_on.get(number, [])

    def comment(self, repo: str, number: int, body: str) -> dict:
        self.comments_on.setdefault(number, []).append({"body": body})
        self.done.append(("comment", number, body))
        return {}

    def close(self, repo: str, number: int, *, reason: str = "completed") -> dict:
        self.issues.setdefault(number, {})["state"] = "closed"
        self.done.append(("close", number))
        return {}

    def file_at(self, repo: str, path: str, ref: str) -> str | None:
        return self.files.get(path)

    def default_branch(self, repo: str) -> str:
        return "main"

    def add_labels(self, repo: str, number: int, labels: list[str]) -> None:
        self.done.append(("label", number, *labels))

    def remove_label(self, repo: str, number: int, label: str) -> None:
        self.done.append(("unlabel", number, label))


def sink(tmp_path) -> EventSink:
    return EventSink(tmp_path / "events.jsonl")


def events(tmp_path) -> list[dict]:
    import json  # noqa: PLC0415

    path = tmp_path / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
