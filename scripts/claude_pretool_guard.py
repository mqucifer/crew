"""Claude Code PreToolUse guard for the crew repo (crew#458).

Run by `.claude/settings.json` before every Bash command Claude runs here. It
reads the hook's JSON on stdin and refuses four things CLAUDE.md forbids, so
they hold whether or not the rule is remembered:

- a push to main: every change lands through a pull request;
- a pull or merge from origin in the main checkout while a tick runs: the crew
  imports phase modules lazily, so a tick would mix old and new code;
- a bare issue number (`#123`) in a `gh` issue or PR body: GitHub links it to
  that number in whichever repository it is posted in;
- a secret on a command line or in a command's output: arguments show in `ps`
  and the session's command record (CWE-214), output reaches the transcript
  (CWE-532). It also runs before the Read tool, which would print a `.env`.

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
                "repository it's posted to. Write `owner/repo#N` or the full URL."
            )


# --- secrets ---------------------------------------------------------------
# The check above splits on separators and lets shlex drop the quotes. Secrets
# need more: '$KEY' is never expanded and "$KEY" is, and a heredoc body is a
# process's stdin, not its arguments. So this check has its own small parser.

SECRET_NAME = re.compile(r"TOKEN|KEY|SECRET|PASSW|CREDENTIAL", re.IGNORECASE)
# A path to a secret is not the secret, and MAX_TOKENS is a count.
NOT_A_SECRET = re.compile(r"(_PATH|_FILE|_DIR|TOKENS)$", re.IGNORECASE)

# Pasted as a literal: GitHub tokens, LiteLLM's sk- keys, a PEM private key.
TOKEN_SHAPE = re.compile(
    r"\bgh[pousr]_[A-Za-z0-9]{36}\b|\bgithub_pat_\w{22,}|\bsk-(?:proj-)?\w{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)

ASSIGNMENT = re.compile(r"[A-Za-z_]\w*(\[[^]]*\])?\+?=")
NAME = re.compile(r"[A-Za-z_]\w*")
OPERATOR = re.compile(r"\|\||&&|;;|\|&|[;&|]")
REDIRECT = re.compile(r"<<<|<<-|<<|&>>?|[<>]&|>>|<>|>\||[<>]")

KEYWORDS = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until"}
KEYWORDS |= {"!", "{", "}", "time", "esac"}
# Assignments, sourcing and the shell's own tests start no process.
NO_PROCESS = {"export", "local", "declare", "readonly", "typeset", "unset", ".", "source"}
NO_PROCESS |= {"[", "[[", "test", ":", "case", "for"}
PRINTERS = {"echo", "printf", "print"}
SHELLS = {"bash", "sh", "zsh"}
FILE_PRINTERS = {"cat", "head", "tail", "less", "more", "bat", "awk", "sed", "grep", "rg"}
QUIET_FLAGS = {"grep": set("clLq"), "rg": set("clq")}
QUIET_LONG = {"--count", "--files-with-matches", "--files-without-match", "--quiet", "--silent"}
STDOUT_REDIRECTS = {">", ">>", ">|", "&>", "&>>"}

SECRET_RULE = "CLAUDE.md, Never: a secret on a command line, or in a command's output."
SAFE_ROUTE = (
    "Use the environment: `set -a; . <path>/.env; set +a`, then read os.environ (or "
    "crew's llm settings) in the process; a leak check matches inside Python, reading "
    "both files itself."
)


def refuse_secret(what: str) -> None:
    refuse(f"{what}. {SECRET_RULE}\n{SAFE_ROUTE}")


class Word:
    def __init__(self) -> None:
        self.raw = ""
        self.text = ""
        self.names: list[str] = []
        self.subs: list[list[Command]] = []


class Heredoc:
    def __init__(
        self, delimiter: str, expands: bool, strip_tabs: bool, body: Word | None = None
    ) -> None:
        self.delimiter = delimiter
        self.expands = expands
        self.strip_tabs = strip_tabs
        self.body = body or Word()


class Command:
    def __init__(self) -> None:
        self.words: list[Word] = []
        self.redirects: list[tuple[str, Word]] = []
        self.heredocs: list[Heredoc] = []
        self.piped = False


def match(pattern: re.Pattern[str], text: str, at: int) -> str:
    found = pattern.match(text, at)
    if not found:
        raise ValueError(f"no {pattern.pattern} at {at}")
    return found.group()


class Shell:
    """Just enough shell grammar to see which words reach a process.

    Raises ValueError or IndexError on input it can't follow; the caller then
    allows the command, as the rest of the guard does.
    """

    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0

    def peek(self, ahead: int = 0) -> str:
        j = self.i + ahead
        return self.s[j] if j < len(self.s) else ""

    def commands(self, stop: str = "") -> list[Command]:
        out: list[Command] = []
        cmd = Command()
        pending: list[Heredoc] = []
        depth = 0

        def finish(piped: bool = False) -> None:
            nonlocal cmd
            if cmd.words or cmd.redirects or cmd.heredocs:
                cmd.piped = piped
                out.append(cmd)
            cmd = Command()

        while self.i < len(self.s):
            c = self.s[self.i]
            if c in " \t":
                self.i += 1
            elif c == "\n":
                self.i += 1
                finish()
                for doc in pending:
                    self.heredoc_body(doc)
                pending = []
            elif c == "#":
                while self.peek() not in ("\n", ""):
                    self.i += 1
            elif c == "(":
                depth += 1
                self.i += 1
                finish()
            elif c == ")":
                self.i += 1
                finish()
                if stop and depth == 0:
                    return out
                depth = max(depth - 1, 0)
            elif c in "<>" and self.peek(1) != "(" or c == "&" and self.peek(1) == ">":
                self.redirect(cmd, pending)
            elif c in ";&|":
                op = match(OPERATOR, self.s, self.i)
                self.i += len(op)
                finish(piped=op in ("|", "|&"))
            else:
                word = self.word()
                if not (word.raw.isdigit() and self.peek() in ("<", ">")):
                    cmd.words.append(word)
        if stop:
            raise ValueError("unclosed substitution")
        finish()
        return out

    def word(self) -> Word:
        w, text, start = Word(), [], self.i
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in " \t\n;&|()":
                break
            if c in "<>":
                if self.peek(1) != "(":
                    break
                self.i += 2
                w.subs.append(self.commands(stop=")"))
            elif c == "\\":
                text.append(self.peek(1))
                self.i += 2
            elif c == "'":
                end = self.s.index("'", self.i + 1)
                text.append(self.s[self.i + 1 : end])
                self.i = end + 1
            elif c == '"':
                self.i += 1
                self.expanding(w, text, close='"')
            elif c in "$`":
                self.expansion(w, text)
            else:
                text.append(c)
                self.i += 1
        w.raw, w.text = self.s[start : self.i], "".join(text)
        return w

    def expanding(self, w: Word, text: list[str], close: str) -> None:
        """Double-quoted text, or a heredoc body: expansions happen, words don't split."""
        while self.i < len(self.s):
            c = self.s[self.i]
            if close and c == close:
                self.i += 1
                return
            if c == "\\":
                text.append(self.s[self.i : self.i + 2])
                self.i += 2
            elif c in "$`":
                self.expansion(w, text)
            else:
                text.append(c)
                self.i += 1
        if close:
            raise ValueError("unclosed quote")

    def expansion(self, w: Word, text: list[str]) -> None:
        s, i = self.s, self.i
        if s[i] == "`":
            end = i + 1
            while s[end] != "`":
                end += 2 if s[end] == "\\" else 1
            w.subs.append(Shell(s[i + 1 : end]).commands())
            self.i = end + 1
        elif s.startswith("$((", i):
            self.i = s.index("))", i) + 2
        elif s.startswith("$(", i):
            self.i = i + 2
            w.subs.append(self.commands(stop=")"))
        elif s.startswith("${", i):
            end = s.index("}", i)
            inner = s[i + 2 : end]
            name = NAME.match(inner)
            # ${#KEY} is a length and ${KEY:+set} a fixed word: neither is the value.
            if name and not inner[name.end() :].lstrip(":").startswith("+"):
                w.names.append(name.group())
            self.i = end + 1
        elif s.startswith("$'", i):
            self.i = s.index("'", i + 2) + 1
        else:
            name = NAME.match(s, i + 1)
            self.i = name.end() if name else i + 1
            if name:
                w.names.append(name.group())
        text.append(s[i : self.i])

    def redirect(self, cmd: Command, pending: list[Heredoc]) -> None:
        op = match(REDIRECT, self.s, self.i)
        self.i += len(op)
        while self.peek() in (" ", "\t") and self.peek():
            self.i += 1
        target = self.word()
        if op in ("<<", "<<-"):
            quoted = any(q in target.raw for q in "'\"\\")
            doc = Heredoc(target.text, expands=not quoted, strip_tabs=op == "<<-")
            cmd.heredocs.append(doc)
            pending.append(doc)
        elif op == "<<<":
            cmd.heredocs.append(Heredoc("", expands=True, strip_tabs=False, body=target))
        else:
            cmd.redirects.append((op, target))

    def heredoc_body(self, doc: Heredoc) -> None:
        lines = []
        while self.i < len(self.s):
            end = self.s.find("\n", self.i)
            end = len(self.s) if end < 0 else end
            line = self.s[self.i : end]
            self.i = end + 1
            if (line.lstrip("\t") if doc.strip_tabs else line) == doc.delimiter:
                break
            lines.append(line)
        if doc.expands:
            Shell("\n".join(lines)).expanding(doc.body, [], close="")


def is_secret_name(name: str) -> bool:
    return bool(SECRET_NAME.search(name)) and not NOT_A_SECRET.search(name)


def secret_in(word: Word) -> str:
    """What in this word expands to a secret, or "". Checks its substitutions too."""
    found = next((f"${n}" for n in word.names if is_secret_name(n)), "")
    for sub in word.subs:
        if check_commands(sub, captured=True) and not found:
            found = "a $(...) that prints a secret"
    return found


def prints_env_file(name: str, args: list[Word], cmd: Command) -> bool:
    paths = [w.text for w in args] + [t.text for op, t in cmd.redirects if op == "<"]
    if not any(Path(p).name.endswith(".env") for p in paths):
        return False
    flags = [w.text for w in args if w.text.startswith("-")]
    if name == "sed":
        return not any(f.startswith("-i") or f == "--in-place" for f in flags)
    if name in QUIET_FLAGS:
        short = {c for f in flags if not f.startswith("--") for c in f[1:]}
        return not (short & QUIET_FLAGS[name] or set(flags) & QUIET_LONG)
    return True


def prints_environment(name: str, args: list[Word]) -> bool:
    texts = [w.text for w in args]
    if name == "printenv":
        return not texts or any(is_secret_name(t) for t in texts)
    if name == "env":
        return all(t.startswith("-") or ASSIGNMENT.match(t) for t in texts)
    if name == "set":
        return not texts
    if name == "export":
        return not texts or texts == ["-p"]
    return False


def check_command(cmd: Command, captured: bool) -> bool:
    """Refuse a secret in this command's arguments, or printed where it isn't captured.

    Returns whether the command prints a secret, for a $(...) that captures it.
    """
    found = [secret_in(w) for w in cmd.words]
    for _, target in cmd.redirects:
        secret_in(target)
    in_stdin = any(secret_in(doc.body) for doc in cmd.heredocs)

    start = 0
    while start < len(cmd.words) and (
        ASSIGNMENT.match(cmd.words[start].raw) or cmd.words[start].raw in KEYWORDS
    ):
        start += 1
    if start == len(cmd.words):
        return False
    name, args = Path(cmd.words[start].text).name, cmd.words[start + 1 :]
    leaked = next((f for f in found[start + 1 :] if f), "")
    texts = [w.text for w in args]

    def prints(what: str) -> bool:
        if not captured:
            refuse_secret(f"{what} prints a secret into the transcript")
        return True

    if leaked and name in PRINTERS:
        return prints(f"`{name}` with {leaked}")
    if leaked and name not in NO_PROCESS:
        refuse_secret(f"{leaked} puts a secret in `{name}`'s arguments, where ps shows it")

    printed = False
    inner = ""
    if name in SHELLS:
        flag = next((k for k, t in enumerate(texts) if re.fullmatch(r"-[a-z]*c[a-z]*", t)), -1)
        inner = texts[flag + 1] if 0 <= flag < len(texts) - 1 else ""
    elif name == "eval":
        inner = " ".join(texts)
    if inner:
        with contextlib.suppress(ValueError, IndexError):
            printed = check_commands(Shell(inner).commands(), captured)

    if name == "gh" and texts[:2] == ["auth", "token"]:
        return True if captured or cmd.piped else prints("`gh auth token`")
    if name == "gh" and texts[:2] == ["auth", "status"] and {"-t", "--show-token"} & set(texts):
        return prints("`gh auth status --show-token`")
    if prints_environment(name, args):
        return prints(f"`{' '.join([name, *texts])}`")
    if name in FILE_PRINTERS and prints_env_file(name, args, cmd):
        return prints(f"`{name}` on a .env file")
    to_stdout = not cmd.piped and not {op for op, _ in cmd.redirects} & STDOUT_REDIRECTS
    if in_stdin and name in ("cat", "tee") and to_stdout:
        return prints(f"`{name}` of a heredoc holding a secret")
    return printed


def check_commands(commands: list[Command], captured: bool) -> bool:
    return any([check_command(cmd, captured) for cmd in commands])


def check_secrets(command: str) -> None:
    if TOKEN_SHAPE.search(command):
        refuse_secret("the command holds a literal token or private key")
    try:
        commands = Shell(command).commands()
    except (ValueError, IndexError):
        return
    check_commands(commands, captured=False)


def check_read(path: str) -> None:
    if Path(path).name.endswith(".env"):
        refuse_secret(f"the Read tool on {Path(path).name} puts its secrets in the transcript")


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    if payload.get("tool_name") == "Read":
        check_read((payload.get("tool_input") or {}).get("file_path") or "")
        return
    if payload.get("tool_name") != "Bash":
        return
    command = (payload.get("tool_input") or {}).get("command") or ""
    cwd = Path(payload.get("cwd") or ".")

    check_gh_body(command, cwd)
    check_secrets(command)
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
