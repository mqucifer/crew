"""Claude Code PreToolUse guard for the crew repo (crew#458).

Run by `.claude/settings.json` before every Bash command Claude runs here. It
reads the hook's JSON on stdin and refuses three things CLAUDE.md forbids, so
they hold whether or not the rule is remembered:

- a push to main: every change lands through a pull request;
- a pull or merge from origin in the main checkout while a tick runs: the crew
  imports phase modules lazily, so a tick would mix old and new code;
- a bare issue number (`#123`) in a `gh` issue or PR body: GitHub links it to
  that number in whichever repository it is posted in.

Exit 2 refuses the command, with the reason on stderr for Claude to read.
Anything this script can't parse is allowed: it is a guard, not a gate.
Standard library only, so it runs under any python3.
"""

from __future__ import annotations

import contextlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

PROTECTED = {"main", "master", "refs/heads/main", "refs/heads/master"}

SEPARATORS = re.compile(r"&&|\|\||[;|\n]")

GH_WRITE = re.compile(r"\bgh\s+(issue|pr)\s+(create|comment|edit|review|close)\b")

# `#123` with nothing that scopes it in front: `crew#123`, `owner/repo#123`
# and full URLs pass. A digit run followed by a letter is a colour, not an issue.
BARE_ISSUE = re.compile(r"(?<![\w/#])#\d+(?![\w])")

TICK = "crew tick"


def refuse(reason: str) -> None:
    print(f"BLOCKED by scripts/claude_pretool_guard.py: {reason}", file=sys.stderr)
    sys.exit(2)


def segments(command: str) -> list[list[str]]:
    """Split a shell command into simple commands, each as its words."""
    out = []
    for part in SEPARATORS.split(command):
        try:
            words = shlex.split(part)
        except ValueError:
            continue
        if words:
            out.append(words)
    return out


def git_args(words: list[str], cwd: Path) -> tuple[list[str], Path] | None:
    """The arguments after `git` and the directory git runs in, or None."""
    if not words or words[0] != "git":
        return None
    rest, where = words[1:], cwd
    while rest and rest[0].startswith("-"):
        if rest[0] == "-C" and len(rest) > 1:
            where = (cwd / rest[1]).resolve()
            rest = rest[2:]
        elif rest[0] in {"-c", "--git-dir", "--work-tree"} and len(rest) > 1:
            rest = rest[2:]
        else:
            rest = rest[1:]
    return rest, where


def git_out(where: Path, *args: str) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", str(where), *args], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def is_main_checkout(where: Path) -> bool:
    """True in the repository's own checkout, False in a linked worktree."""
    git_dir = git_out(where, "rev-parse", "--absolute-git-dir")
    common = git_out(where, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return bool(git_dir) and git_dir == common


def tick_running() -> bool:
    try:
        done = subprocess.run(["pgrep", "-f", TICK], capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def check_push(args: list[str], where: Path) -> None:
    flags = [a for a in args[1:] if a.startswith("-")]
    if {"--all", "--mirror"} & set(flags):
        refuse("`git push --all/--mirror` would push main. Push the branch and open a PR.")
    positional = [a for a in args[1:] if not a.startswith("-")]
    refspecs = positional[1:]
    if not refspecs:
        branch = git_out(where, "rev-parse", "--abbrev-ref", "HEAD")
        if branch in PROTECTED:
            refuse(f"`git push` from {branch} pushes main. Branch, push that, open a PR.")
        return
    for spec in refspecs:
        target = spec.lstrip("+").split(":")[-1]
        if target == "HEAD":
            target = git_out(where, "rev-parse", "--abbrev-ref", "HEAD")
        if target in PROTECTED:
            refuse(f"`git push ... {spec}` writes to {target}. Every change lands by PR.")


def check_pull(args: list[str], where: Path) -> None:
    pulls = args[0] == "pull" or (
        args[0] in {"merge", "rebase"} and any(a.startswith("origin/") for a in args[1:])
    )
    if pulls and is_main_checkout(where) and tick_running():
        refuse(
            "a crew tick is running, and this would change the code it imports lazily. "
            "Wait for the tick to end, or stop it at a pass boundary, then pull."
        )


def check_gh_body(command: str, cwd: Path) -> None:
    if not GH_WRITE.search(command):
        return
    texts = [command]
    for words in segments(command):
        for flag in ("--body-file", "-F"):
            if flag in words[:-1]:
                path = (cwd / words[words.index(flag) + 1]).expanduser()
                with contextlib.suppress(OSError):
                    texts.append(path.read_text())
    for text in texts:
        found = BARE_ISSUE.search(text)
        if found:
            refuse(
                f"bare issue number {found.group()} in a gh body. GitHub links it in the "
                "repository it's posted to. Write `crew#N`, `owner/repo#N` or the full URL."
            )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    if payload.get("tool_name") != "Bash":
        return
    command = (payload.get("tool_input") or {}).get("command") or ""
    cwd = Path(payload.get("cwd") or ".")

    check_gh_body(command, cwd)
    for words in segments(command):
        if words[0] == "cd" and len(words) > 1:
            cwd = (cwd / words[1]).expanduser().resolve()
            continue
        parsed = git_args(words, cwd)
        if not parsed or not parsed[0]:
            continue
        args, where = parsed
        if args[0] == "push":
            check_push(args, where)
        elif args[0] in {"pull", "merge", "rebase"}:
            check_pull(args, where)


if __name__ == "__main__":
    main()
