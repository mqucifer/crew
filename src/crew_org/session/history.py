"""The session's conversation, and what it becomes if it's compacted (crew#778).

Kept whole (decision 3, as the replay corrected it): a session only appends, so
SGLang's prefix cache serves each call's history and only the newest reply is new.
Pass 3 kept 91% to 97% of its input cached, and its largest prompt was 115k tokens.
If a prompt would pass the limit, the history is compacted once, all at once, by
`compact`; when is the loop's to decide, and how is this module's alone, so the
form can change without the loop.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from crew_org.session.tools import Reply, edited


@dataclass(frozen=True)
class Turn:
    """One tool call's reply, as the conversation holds it."""

    reply: Reply
    call_id: str
    name: str
    args: dict[str, Any]
    # Compacted: shown as its one line, its text left out.
    masked: bool = False

    @property
    def content(self) -> str:
        return f"(older) {self.reply.summary}" if self.masked else self.reply.text


def messages(system: str, start: str, history: list[Any]) -> list[dict[str, Any]]:
    """The conversation as it's sent: the system text, the start, then every turn."""
    out: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": start},
    ]
    for item in history:
        if isinstance(item, Turn):
            out.append({"role": "tool", "tool_call_id": item.call_id, "content": item.content})
        else:
            out.append(item)
    return out


def reasoning_size(history: list[Any]) -> int:
    """The characters of `reasoning_content` the history holds."""
    return sum(
        len(item.get("reasoning_content") or "") for item in history if isinstance(item, dict)
    )


def cleared(history: list[Any]) -> list[Any]:
    """The history with `reasoning_content` emptied on every assistant turn but the newest's.

    The newest turn's replies haven't been answered yet, so the assistant message they
    answer stays whole, as the kept tail does in `compact`.
    """
    last = max((i for i, item in enumerate(history) if not isinstance(item, Turn)), default=-1)
    return [
        {**item, "reasoning_content": ""}
        if i < last and isinstance(item, dict) and item.get("reasoning_content")
        else item
        for i, item in enumerate(history)
    ]


def compact(history: list[Any]) -> list[Any]:
    """The history compacted, once: every tool reply shrinks to its one line but each
    file's latest view of it unchanged, the latest run, and the newest turn's replies.

    Pass 2's masking, a placeholder: the form is still being researched
    (`var/notes/778/research-compaction.md`, the Sponsor, 2026-10-18). Masking keeps
    every action, and a path in each line, so a file can be viewed again; it's what
    "The Complexity Trap" found as good as a model's summary at less cost.

    Before the masking, every assistant turn before the kept tail loses its
    `reasoning_content`, in this one pass: a field cleared a turn at a time would
    change the prefix, and break the cache, on every call.
    """
    history = cleared(history)
    turns = [i for i, item in enumerate(history) if isinstance(item, Turn)]
    kept: set[int] = set()
    latest_view: dict[str, int] = {}
    for i in turns:
        turn = history[i]
        path = str(turn.reply.detail.get("path", ""))
        if turn.name == "view" and turn.reply.kind == "ok":
            latest_view[path] = i
        else:
            # A changed file's earlier view is stale.
            for each in edited(turn.name, turn.reply):
                latest_view.pop(each, None)
    kept |= set(latest_view.values())
    runs = [i for i in turns if history[i].name == "run"]
    if runs:
        kept.add(runs[-1])
    # The newest turn's replies: the model hasn't answered them yet.
    last = max((i for i, item in enumerate(history) if not isinstance(item, Turn)), default=-1)
    kept |= {i for i in turns if i > last}
    return [
        replace(item, masked=True) if i in turns and i not in kept else item
        for i, item in enumerate(history)
    ]
