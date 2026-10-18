"""The session loop and its endings (crew#778, decisions 1 and 3; Stage R).

One conversation with the Developer's model, through LiteLLM, in which it calls the
tools until it calls `done` or `blocked`, stops making progress, or reaches the
2-hour backstop of model time. Older tool output shrinks to its one line; the latest
view of each file, while the file is unchanged, and the latest run stay whole.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tools import SPECS, Reply, Tools  # type: ignore[import-not-found]

from crew_org.calls import _usage, post, role_spec

BACKSTOP_S = 2 * 60 * 60
# Replies with no tool call in a row before the session is ended: a session that
# only talks isn't working, and a nudge each time costs a call.
QUIET_LIMIT = 3
NUDGE = (
    "Call a tool to go on, or `done` when the work is finished and checked, or "
    "`blocked` if it can't be finished from what's here."
)


@dataclass
class Turn:
    reply: Reply
    call_id: str
    name: str
    args: dict[str, Any]


@dataclass
class Session:
    tools: Tools
    system: str
    start: str
    send: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    log: Callable[[dict[str, Any]], None] = lambda _: None
    backstop_s: float = BACKSTOP_S
    model_s: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)
    calls: int = 0
    used: dict[str, int] = field(default_factory=dict)
    ending: dict[str, Any] = field(default_factory=dict)
    # How older tool output is kept: "cut", each reply shrinks to its line once a newer
    # one of its kind arrives (pass 1); or "whole", every reply stays whole and the
    # conversation is compacted once, all at once, before a prompt would pass
    # `compact_at` tokens (pass 2: a cut rewrites the prefix SGLang has cached). The
    # Sponsor set 125k: the window is 262,144, about 196k once 65,536 of output is kept.
    context: str = "cut"
    compact_at: int = 125_000
    compacted: dict[str, Any] = field(default_factory=dict)
    # Each empty, cut-off or malformed answer, with its call's prompt size.
    troubles: list[dict[str, Any]] = field(default_factory=list)
    _chars_per_token: float = 0.0  # from the last call, to estimate the next prompt
    _history: list[Any] = field(default_factory=list)  # assistant messages and Turns
    _latest: set[str] = field(default_factory=set)  # the newest turn's call ids
    _frozen: set[int] = field(default_factory=set)  # turns cut by the one compaction

    def _body(self) -> dict[str, Any]:
        spec = role_spec("developer")
        params = spec.get("llm_params") or {}
        return {
            "model": spec.get("llm", "crew-code-think"),
            "messages": self.messages(),
            "max_tokens": params.get("max_tokens", 65536),
            "tools": SPECS,
            "tool_choice": "auto",
        }

    def messages(self) -> list[dict[str, Any]]:
        """The conversation as it's sent, older tool output cut by the context rule."""
        if self.context == "whole":
            return self._sent(lambda i: i not in self._frozen)
        kept = self._kept()
        return self._sent(lambda i: i in kept)

    def _sent(self, whole: Callable[[int], bool]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": self.start},
        ]
        index = 0
        for item in self._history:
            if isinstance(item, Turn):
                text = item.reply.text if whole(index) else f"(older) {item.reply.summary}"
                out.append({"role": "tool", "tool_call_id": item.call_id, "content": text})
                index += 1
            else:
                out.append(item)
        return out

    def _compact(self, estimated: int) -> None:
        """Once, all at once: every reply the cut rule wouldn't keep shrinks to its line."""
        kept = self._kept()
        turns = sum(isinstance(t, Turn) for t in self._history)
        self._frozen = set(range(turns)) - kept
        self.compacted = {
            "before_call": self.calls + 1,
            "estimated_tokens": estimated,
            "cut": len(self._frozen),
        }
        self.log({"compacted": self.compacted})

    def _kept(self) -> set[int]:
        """The replies the cut rule keeps whole: each file's latest view, the latest run."""
        turns = [t for t in self._history if isinstance(t, Turn)]
        kept: set[int] = set()
        latest_view: dict[str, int] = {}
        for i, t in enumerate(turns):
            if t.name == "view" and t.reply.kind == "ok":
                latest_view[t.reply.detail.get("path", "")] = i
            elif t.name in ("replace_text", "replace_definition", "write_file"):
                # A changed file's earlier view is stale: it shrinks to its line.
                latest_view.pop(t.reply.detail.get("path", ""), None)
        kept |= set(latest_view.values())
        runs = [i for i, t in enumerate(turns) if t.name == "run"]
        if runs:
            kept.add(runs[-1])
        # The latest turn's replies are whole: the model hasn't seen them yet.
        last = [i for i, t in enumerate(turns) if t.call_id in self._latest]
        kept |= set(last)
        return kept

    def run(self) -> dict[str, Any]:
        sender = self.send or (lambda body: post(body, timeout=3600))
        quiet = 0
        while not self.ending:
            if self.model_s >= self.backstop_s:
                self._end("backstop", f"{round(self.model_s / 60)} minutes of model time")
                break
            body = self._body()
            if self.context == "whole" and not self.compacted and self._chars_per_token:
                estimated = int(_chars(body) / self._chars_per_token)
                if estimated >= self.compact_at:
                    self._compact(estimated)
                    body = self._body()
            began = time.monotonic()
            try:
                data = sender(body)
            except Exception as exc:  # noqa: BLE001 - the call's failure ends the session
                self._end("call failed", f"{type(exc).__name__}: {str(exc)[:300]}")
                break
            seconds = time.monotonic() - began
            self.model_s += seconds
            self.calls += 1
            usage = _usage(data)
            for k, v in usage.items():
                self.usage[k] = self.usage.get(k, 0) + v
            if usage.get("prompt_tokens"):
                self._chars_per_token = _chars(body) / usage["prompt_tokens"]
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            made = message.get("tool_calls") or []
            trouble = (
                "cut off"
                if choice.get("finish_reason") == "length"
                else "empty"
                if not made and not (message.get("content") or "").strip()
                else None
            )
            if trouble:
                self._trouble(trouble, usage)
            self.log(
                {
                    "call": self.calls,
                    "seconds": round(seconds, 1),
                    "finish": choice.get("finish_reason"),
                    "usage": usage,
                    "content": message.get("content") or "",
                    "reasoning_chars": len(message.get("reasoning_content") or ""),
                    "tool_calls": [
                        {
                            "name": (m.get("function") or {}).get("name"),
                            "args": (m.get("function") or {}).get("arguments"),
                        }
                        for m in made
                    ],
                    "prompt_chars": _chars(body),
                }
            )
            if not made:
                quiet += 1
                self._history.append({"role": "assistant", "content": message.get("content") or ""})
                if quiet >= QUIET_LIMIT:
                    self._end("no tool call", f"{quiet} replies in a row called no tool")
                    break
                self._history.append({"role": "user", "content": NUDGE})
                self._latest = set()
                continue
            quiet = 0
            self._history.append(
                {"role": "assistant", "content": message.get("content") or "", "tool_calls": made}
            )
            self._latest = set()
            for m in made:
                call_id = str(m.get("id") or f"call-{self.calls}-{len(self._latest)}")
                function = m.get("function") or {}
                name = str(function.get("name") or "")
                self.used[name] = self.used.get(name, 0) + 1
                try:
                    args = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = None
                if not isinstance(args, dict):
                    self._trouble("malformed", usage, tool=name)
                if not isinstance(args, dict):
                    reply = Reply(
                        "Those arguments aren't a JSON object: call the tool again.",
                        "refused",
                        f"{name}: bad arguments",
                    )
                elif name in ("done", "blocked"):
                    self._end(name, args)
                    reply = Reply("The session ends here.", "end", name)
                else:
                    reply = self.tools.call(name, args)
                self._latest.add(call_id)
                self._history.append(Turn(reply, call_id, name, args or {}))
                self.log(
                    {
                        "tool": name,
                        "args": _short(args),
                        "reply": reply.summary,
                        "kind": reply.kind,
                        "text": reply.text,
                    }
                )
                if self.ending:
                    break
            if not self.ending and self.tools.no_progress():
                last = self.tools.runs[-1]
                self._end(
                    "no progress",
                    {"first_error": last["first_error"], "test": (last["failing"] or [""])[0]},
                )
        return self.ending

    def _trouble(self, kind: str, usage: dict[str, int], **more: Any) -> None:
        self.troubles.append(
            {"call": self.calls, "kind": kind, "prompt_tokens": usage.get("prompt_tokens"), **more}
        )

    def _end(self, how: str, said: Any) -> None:
        self.ending = {"how": how, "said": said}


def _chars(body: dict[str, Any]) -> int:
    """A request's size in characters: its messages' text."""
    return sum(len(str(m.get("content") or "")) for m in body["messages"])


def _short(args: Any) -> Any:
    """Arguments as the log keeps them: long text cut, its size kept."""
    if not isinstance(args, dict):
        return args
    return {
        k: (f"{v[:200]}… ({len(v)} chars)" if isinstance(v, str) and len(v) > 240 else v)
        for k, v in args.items()
    }
