"""The session: the loop, its endings and its records (crew#778, decisions 1 and 3, P5).

One conversation with the Developer's model, a call at a time through the call layer
(`calls.converse`), in which it calls the tools until it calls `done` or `blocked`, or
is stopped. A stop ends it as `blocked`, with the session's evidence, for the Business
Analyst (decision 9):

- `no_progress`: the same first error in the same test after 3 runs with edits between;
- `backstop`: 3 hours of model time, one budget for a delivery's sessions;
- `ceiling` (`signals`): 60 calls with no edit and no run, or 50 views of one file with
  no edit between;
- `no_tool_call`: 3 replies in a row that call no tool. Each gets one line back, since
  a conversation can't go on without a turn: not a nudge about progress (the operator,
  crew#778).

A model call that fails, or a workspace container that's gone, ends it as
`interrupted`: nothing about the story, so it's resumed (P3), not returned.

Every call is recorded: its `llm.*` events by the call layer, each tool call as a
`tool.finished`, the signals after it as a `session.turn`, and a compaction as a
`session.compacted`. Telemetry gets their counts only (ADR 0010).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from crew_org.calls import Answered, converse, role_spec
from crew_org.events import EventKind, record
from crew_org.rulebook import Refusal
from crew_org.session import signals
from crew_org.session.container import Gone, Workspace
from crew_org.session.history import Turn, compact, messages
from crew_org.session.signals import Signals
from crew_org.session.start import start, system_text
from crew_org.session.tools import ENDINGS, SPECS, Reply, Tools, check_arguments, target_of

ROLE = "developer"
# One delivery's model time, shared by its sessions: the Sponsor's 3 hours, from 2, after
# sprint-metrics#662's stop (crew#778, 2026-10-18).
BACKSTOP_S = 3 * 60 * 60
QUIET_LIMIT = 3
QUIET = "Call a tool to go on, or `done` or `blocked`."
# What a refusal of `done` by its own checks begins with.
REFUSED_DONE = (
    "`done` checked your branch, and the session goes on. Answer each of these, then call "
    "`done` again."
)
# A prompt this size or larger is compacted first, once (the Sponsor's 125k): the window
# is 262,144 tokens, about 196k once 65,536 of output is kept. Pass 3's largest was 115k.
COMPACT_AT = 125_000
# How much of the session's last text its return note quotes (crew#778).
LAST_WORDS = 600


@dataclass
class Ending:
    """How a session ended, in the fields delivery reads (Stage D).

    - `done`: `said` is `done`'s own: `criteria` (criterion, test), `changed_tests`
      (test, why), `docs` and `summary`.
    - `blocked`: `said` is `missing` and `kind`. The session's own `blocked` says file,
      fact or choice; a stop is kind `stop`, `stop` names it, and `missing` says what
      it saw.
    - `interrupted`: `why` names it, `call_failed` or `workspace_gone`; `said` has the
      error's text, and `error` the exception, for the caller to handle as it handles
      any (a server that's down).

    `evidence`, on each: the last run and the last failure, what it tried, the signals,
    and its last words, the last text it wrote, for the return note only: the package
    and telemetry leave them out.
    """

    how: str  # "done", "blocked" or "interrupted"
    said: dict[str, Any] = field(default_factory=dict)
    stop: str = ""
    why: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    error: BaseException | None = None


@dataclass
class Session:
    tools: Tools
    system: str
    start: str
    send: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    # Called with each run's record once it has run, such as to commit a checkpoint
    # when it passed (P3). One that raises is recorded in `after_run_errors`.
    after_run: Callable[[dict[str, Any]], None] | None = None
    after_run_errors: list[str] = field(default_factory=list)
    # `done` checks itself before it ends the session: the findings, each with its rule
    # and what it says (`gates.missing_named_tests`, the Sponsor's one check).
    check_done: Callable[[dict[str, Any]], list[Any]] | None = None
    backstop_s: float = BACKSTOP_S
    compact_at: int = COMPACT_AT
    # Who the session is and what it may call: the Developer's, unless a refinement
    # step's session says its own (crew#807). `name` is that step, on each event.
    role: str = ROLE
    step: str | None = None
    name: str = ""
    specs: list[dict[str, Any]] = field(default_factory=lambda: SPECS)
    endings: tuple[str, ...] = ENDINGS
    quiet: str = QUIET
    refused_done: str = REFUSED_DONE
    model_s: float = 0.0
    calls: int = 0
    usage: Counter[str] = field(default_factory=Counter)
    used: Counter[str] = field(default_factory=Counter)
    watch: Signals = field(default_factory=Signals)
    compacted: dict[str, Any] = field(default_factory=dict)
    # Each empty, cut-off or malformed answer, with its call's prompt size.
    troubles: list[dict[str, Any]] = field(default_factory=list)
    history: list[Any] = field(default_factory=list)
    _chars_per_token: float = 0.0  # from the last call, to estimate the next prompt
    _quiet: list[str] = field(default_factory=list)

    def run(self) -> Ending:
        ending: Ending | None = None
        while ending is None:
            if self.model_s >= self.backstop_s:
                minutes = round(self.model_s / 60)
                ending = self._stopped(
                    "backstop",
                    f"{minutes} minutes of model time, the backstop",
                    {"model_minutes": minutes},
                )
                break
            sent = self._messages()
            try:
                answered = converse(
                    self.role,
                    sent,
                    self.specs,
                    step=self.step,
                    send=self.send,
                    turn=self.calls + 1,
                )
            except Exception as exc:  # noqa: BLE001 - a failed call interrupts, never blames
                ending = Ending(
                    "interrupted",
                    {"error": f"{type(exc).__name__}: {str(exc)[:300]}"},
                    why="call_failed",
                    evidence=self._evidence(),
                    error=exc,
                )
                break
            self.calls += 1
            self.model_s += answered.seconds
            self.usage.update(answered.usage)
            if answered.usage.get("prompt_tokens"):
                self._chars_per_token = _chars(sent) / answered.usage["prompt_tokens"]
            ending = self._answer(answered)
        self._ended(ending)
        return ending

    def _messages(self) -> list[dict[str, Any]]:
        """The conversation to send, compacted first if it would pass the limit."""
        sent = messages(self.system, self.start, self.history)
        if not self.compacted and self._chars_per_token:
            estimated = int(_chars(sent) / self._chars_per_token)
            if estimated >= self.compact_at:
                self.history = compact(self.history)
                masked = sum(isinstance(t, Turn) and t.masked for t in self.history)
                self.compacted = {"turn": self.calls + 1, "estimated_tokens": estimated}
                self.compacted["turns_masked"] = masked
                sent = messages(self.system, self.start, self.history)
                record(
                    EventKind.SESSION_COMPACTED,
                    f"compacted before call {self.calls + 1}: {masked} replies masked",
                    **self._who(),
                    chars_after=_chars(sent),
                    **self.compacted,
                )
        return sent

    def _answer(self, answered: Answered) -> Ending | None:
        """Run what one answer asked for, record it, and say whether the session ends."""
        message = answered.message
        made = message.get("tool_calls") or []
        content = message.get("content") or ""
        if answered.finish_reason == "length":
            self._trouble("cut off", answered.usage)
        elif not made and not content.strip():
            self._trouble("empty", answered.usage)
        if not made:
            self.history.append({"role": "assistant", "content": content})
            self._quiet.append(content[:2000])
            self._turn([], answered.usage)
            if len(self._quiet) >= QUIET_LIMIT:
                return self._stopped(
                    "no_tool_call",
                    f"{QUIET_LIMIT} replies in a row called no tool",
                    {"replies": list(self._quiet)},
                )
            self.history.append({"role": "user", "content": self.quiet})
            return None
        self._quiet = []
        self.history.append({"role": "assistant", "content": content, "tool_calls": made})
        replies: list[tuple[str, Reply]] = []
        ending: Ending | None = None
        for i, m in enumerate(made):
            call_id = str(m.get("id") or f"call-{self.calls}-{i}")
            function = m.get("function") or {}
            name = str(function.get("name") or "")
            self.used[name] += 1
            raw = function.get("arguments") or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = None
            if not isinstance(args, dict):
                self._trouble("malformed", answered.usage, tool=name)
            began = time.monotonic()
            if name in self.endings and ending is None:
                try:
                    check_arguments(name, args, self._accepted)
                    statement = dict(args)
                    if name == "done" and "changed_tests" in self._accepted[name]["properties"]:
                        statement.setdefault("changed_tests", [])
                    refused = self._refused_done(statement) if name == "done" else None
                    if refused is not None:
                        reply = refused
                    else:
                        ending = Ending(name, statement, evidence=self._evidence())
                        reply = Reply("The session ends here.", "end", name)
                except Refusal as exc:
                    reply = Reply(str(exc), "refused", f"{name}: refused", rule=exc.rule)
            elif ending is None:
                try:
                    reply = self.tools.call(name, args)
                except Gone as exc:
                    reply = Reply(str(exc), "error", f"{name}: the workspace is gone")
                    ending = Ending(
                        "interrupted",
                        {"error": str(exc)[:300]},
                        why="workspace_gone",
                        evidence=self._evidence(),
                        error=exc,
                    )
                else:
                    if name == "run" and reply.kind in ("passed", "failed"):
                        self._after_run(reply.detail)
            else:
                reply = Reply("Not run: the session had ended.", "end", f"{name}: not run")
            self._tool(name, args, raw, reply, time.monotonic() - began)
            self.history.append(Turn(reply, call_id, name, args if isinstance(args, dict) else {}))
            replies.append((name, reply))
        self._turn(replies, answered.usage)
        if ending is not None:
            return ending
        if self.tools.no_progress():
            last = self.tools.runs[-1]
            test = (last["failing"] or [""])[0]
            return self._stopped(
                "no_progress",
                f"the same first error in `{test}` after 3 runs with edits between: "
                f"{last['first_error']}",
                {"first_error": last["first_error"], "test": test},
            )
        reached = self.watch.ceiling()
        if reached is not None:
            seen = (
                f"{reached['calls']} calls with no edit and no run"
                if reached["ceiling"] == "reading"
                else f"{reached['views']} views of `{reached['file']}` with no edit between"
            )
            return self._stopped("ceiling", seen, reached)
        return None

    def _refused_done(self, said: dict[str, Any]) -> Reply | None:
        """`done`'s reply when its own checks find something, or None to end the session.

        A refusal is a reply, and the session goes on to answer it (decision 5's
        feedback): the Sponsor, on crew#778, "we expect failures, we handle them
        gracefully". Nothing is thrown away, and it isn't a failure or an attempt. A
        check that can't run lets `done` through, recorded: QA still judges the proof.
        """
        if self.check_done is None:
            return None
        try:
            findings = self.check_done(said)
        except Exception as exc:  # noqa: BLE001 - a check that can't run holds nothing
            said_why = f"{type(exc).__name__}: {str(exc)[:300]}"
            record(
                EventKind.TOOL_FAILED,
                f"done's own checks: {said_why}",
                **self._who(),
                tool="check_done",
                turn=self.calls,
                error=said_why,
            )
            return None
        if not findings:
            return None
        return Reply(
            f"{self.refused_done}\n\n"
            + "\n\n".join(f"{i}. {f.says.strip()}" for i, f in enumerate(findings, 1)),
            "refused",
            f"done: refused by its own checks ({len(findings)})",
            detail={"rules": [f.rule for f in findings]},
            rule=findings[0].rule,
        )

    def _after_run(self, run: dict[str, Any]) -> None:
        """The caller's step after a run. One that fails is recorded, and the session
        goes on: its work is still on the branch, for the next run or `done`."""
        if self.after_run is None:
            return
        try:
            self.after_run({**run, "turn": self.calls})
        except Exception as exc:  # noqa: BLE001 - the caller's failure, never the session's end
            said = f"{type(exc).__name__}: {str(exc)[:300]}"
            self.after_run_errors.append(said)
            record(
                EventKind.TOOL_FAILED,
                f"after a run: {said}",
                **self._who(),
                tool="after_run",
                turn=self.calls,
                error=said,
            )

    @property
    def _accepted(self) -> dict[str, Any]:
        """What each of this session's tools takes."""
        return {s["function"]["name"]: s["function"]["parameters"] for s in self.specs}

    # --- the records --------------------------------------------------------------------
    def _named(self) -> dict[str, Any]:
        """The refinement step a session is, for its events (crew#807)."""
        return {"step": self.name} if self.name else {}

    def _who(self) -> dict[str, Any]:
        return {"role": _role(self.role, self.step), **self._named()}

    def _tool(self, name: str, args: Any, raw: str, reply: Reply, seconds: float) -> None:
        """One tool call as an event: names and sizes, with what a run ran and how it went."""
        detail: dict[str, Any] = {
            "tool": name,
            "turn": self.calls,
            "target": target_of(args),
            "result": reply.kind,
            "args_chars": len(raw),
            "reply_chars": len(reply.text),
            "duration_s": round(seconds, 3),
        }
        if reply.rule:
            detail["rule"] = reply.rule
        if name == "done" and reply.detail.get("rules"):
            detail["rules"] = reply.detail["rules"]  # every check `done` failed
        if reply.empty:
            detail["found_nothing"] = True
            if isinstance(args, dict) and args.get("pattern"):
                detail["looked_for"] = str(args["pattern"])[:200]
        if name == "run" and "commands" in reply.detail:
            run = reply.detail
            detail.update(
                {
                    "run": run["what"],
                    "commands": run["commands"],
                    "first_error": run["first_error"],
                    "failing": run["failing"],
                    "ran_s": run["seconds"],
                }
            )
        record(EventKind.TOOL_FINISHED, reply.summary, **self._who(), **detail)

    def _turn(self, replies: list[tuple[str, Reply]], usage: dict[str, int]) -> None:
        seen = self.watch.after(replies, usage)
        record(
            EventKind.SESSION_TURN,
            f"call {self.calls}: {len(replies)} tool calls",
            **self._who(),
            turn=self.calls,
            tool_calls=len(replies),
            model_s=round(self.model_s, 1),
            **seen,
        )

    def _trouble(self, kind: str, usage: dict[str, int], **more: Any) -> None:
        self.troubles.append(
            {"call": self.calls, "kind": kind, "prompt_tokens": usage.get("prompt_tokens"), **more}
        )

    def _stopped(self, stop: str, missing: str, seen: dict[str, Any]) -> Ending:
        """A stop: `blocked`, kind `stop`, with the session's evidence (decision 9)."""
        said = {"missing": missing, "kind": "stop"}
        return Ending("blocked", said, stop, evidence=self._evidence(seen))

    def _evidence(self, stop: dict[str, Any] | None = None) -> dict[str, Any]:
        """The last run and failure, what the session tried, its signals and its last
        words; and what stopped it."""
        runs = self.tools.runs
        last = runs[-1] if runs else None
        return {
            "last_run": (
                {k: last[k] for k in ("what", "ok", "first_error", "failing")} if last else None
            ),
            "last_failure": last if last and not last["ok"] else None,
            "tried": {
                "runs": len(runs),
                "files_read": sorted({r.split(":", 1)[0] for r in self.tools.read}),
                "files_edited": sorted(set(self.tools.edited)),
                "tools_used": dict(self.used),
            },
            "signals": self.watch.now(),
            "last_words": self._last_words(),
            **({"stop": stop} if stop else {}),
        }

    def _last_words(self) -> str:
        """The last text the session wrote, its end kept when it's long."""
        said = next(
            (
                str(m.get("content") or "").strip()
                for m in reversed(self.history)
                if isinstance(m, dict)
                and m.get("role") == "assistant"
                and str(m.get("content") or "").strip()
            ),
            "",
        )
        return said if len(said) <= LAST_WORDS else "…" + said[-LAST_WORDS:].lstrip()

    def _ended(self, ending: Ending) -> None:
        record(
            EventKind.SESSION_ENDED,
            f"{ending.how}{f' ({ending.stop or ending.why})' if ending.stop or ending.why else ''}",
            **self._who(),
            how=ending.how,
            stop=ending.stop,
            why=ending.why,
            said=ending.said if not self.name else shape(ending.said),
            evidence=ending.evidence,
            model_calls=self.calls,
            model_s=round(self.model_s, 1),
            edits_applied=len(self.tools.edited),
            test_runs=len(self.tools.runs),
            tools_used=dict(self.used),
            compaction=self.compacted or None,
            troubles=self.troubles,
            after_run_errors=self.after_run_errors,
            **{k: v for k, v in self.usage.items() if isinstance(v, int)},
        )


def session(
    worktree: Path,
    story: str,
    workspace: Workspace,
    toolchain: Any,
    *,
    coverage: Any = None,
    send: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    instead_of_hints: str | None = None,
    after_run: Callable[[dict[str, Any]], None] | None = None,
    **about: Any,
) -> Session:
    """A session on `story`, over its worktree at the base and a started workspace.

    `about` goes on the `session.started` event, such as the base commit.
    """
    opening, sizes, notes = start(worktree, story, coverage, instead_of_hints)
    tools = Tools(worktree, workspace, toolchain, coverage, notes)
    record(
        EventKind.SESSION_STARTED,
        f"session: {sizes['total']} characters to start",
        role=_role(),
        start_chars=sizes,
        limits={
            "backstop_s": BACKSTOP_S,
            "compact_at": COMPACT_AT,
            "reading_calls": signals.READING_CALLS,
            "views_of_one_file": signals.VIEWS_OF_ONE_FILE,
            "quiet_limit": QUIET_LIMIT,
        },
        **about,
    )
    return Session(
        tools,
        system_text(ROLE),
        opening,
        send=send,
        after_run=after_run,
        check_done=own_checks(worktree),
    )


def own_checks(worktree: Path) -> Callable[[dict[str, Any]], list[Any]]:
    """`done`'s one check of itself on this worktree: the tests it names exist (crew#778)."""
    # Here, not at the top: delivery, which the gates read, runs the session.
    from crew_org.flows import gates  # noqa: PLC0415

    def checked(said: dict[str, Any]) -> list[Any]:
        return gates.missing_named_tests(worktree, said)

    return checked


def unfit(worktree: Path) -> str | None:
    """Why a worktree can't be given a workspace as it is, or None. Asked before setup.

    A `.venv` there before setup is the host's, and the container would run it
    (crew#780). After setup, one is the container's own.
    """
    if (worktree / ".venv").exists():
        return f"{worktree} has a .venv the host made (crew#780)"
    return None


def shape(said: Any) -> Any:
    """A refinement step's answer by its fields' names and sizes: a list's length, a
    text's characters. Its words stay out of the log, as a story's do (ADR 0010)."""
    if isinstance(said, dict):
        return {k: shape(v) for k, v in said.items()}
    if isinstance(said, list):
        return len(said)
    if isinstance(said, str):
        return len(said)
    return said


def _role(key: str = ROLE, step: str | None = None) -> str:
    return str(role_spec(key, step)["role"])


def _chars(messages: list[dict[str, Any]]) -> int:
    """A request's size in characters: its messages' text."""
    return sum(len(str(m.get("content") or "")) for m in messages)
