"""Claude Code PreToolUse guard for the crew repo (crew#458).

Run by `.claude/settings.json` before every Bash command Claude runs here. It
reads the hook's JSON on stdin and refuses what CLAUDE.md forbids, so it holds
whether or not the rule is remembered:

- a push to main: every change lands through a pull request;
- a pull or merge from origin in the main checkout while a tick runs: the crew
  imports phase modules lazily, so a tick would mix old and new code;
- a bare issue number (`#123`) in a `gh` issue or PR body: GitHub links it to
  that number in whichever repository it is posted in;
- a closing keyword before an issue reference in a PR body, except on the PR's
  own closing line: GitHub closes that issue when the PR merges (crew#951);
- a secret on a command line or in a command's output: arguments show in `ps`
  and the session's command record (CWE-214), output reaches the transcript
  (CWE-532). It also runs before the Read tool, which would print a `.env`, and
  refuses sourcing one, which runs a value with a space as a command;
- reaching into a running service (crew#997): `docker exec`, `cp` or `inspect` on
  one, a Postgres client against the shared server, or reading a service's
  environment. It reads the commands in the call, and a script the call runs. A
  script that holds the container's name in a variable gets past it. A read of
  LiteLLM's own logs stays open, told from a write by its text alone.

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

# GitHub closes an issue when one of these comes right before its reference,
# anywhere in a merged PR's body, so "couldn't close: owner/repo#N" closes it.
GH_PR_BODY = re.compile(r"\bgh\s+pr\s+(create|edit)\b")
KEYWORD = r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)"
REFERENCE = (
    r"(?:[\w.-]+/[\w.-]+#\d+|#\d+"
    r"|https?://github\.com/[\w.-]+/[\w.-]+/(?:issues|pull)/\d+)"
)
CLOSES = re.compile(
    rf"(?<![A-Za-z0-9])[*_]*{KEYWORD}[*_]*(?::[\s*_]*|[\s*_]+)(?P<ref>{REFERENCE})", re.IGNORECASE
)
# The intended closing line holds the keyword and one reference and nothing
# else, so a sentence that happens to start with "Fixes" is still prose.
CLOSING_LINE = re.compile(
    rf"[*_]*{KEYWORD}[*_]*:?[ \t*_]+{REFERENCE}[*_]*[ \t.\"'`]*", re.IGNORECASE
)

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
    if GH_PR_BODY.search(command):
        for body in [*inline_bodies(command), *texts[1:]]:
            check_closing(body)
    for text in texts:
        found = BARE_ISSUE.search(text)
        if found:
            refuse(
                f"bare issue number {found.group()} in a gh body. GitHub links it in the "
                "repository it's posted to. Write `owner/repo#N` or the full URL."
            )


def inline_bodies(command: str) -> list[str]:
    """The `--body` values of `gh pr create/edit`, each as its own text."""
    try:
        commands = Shell(command).commands()
    except (ValueError, IndexError):
        return []
    bodies = []
    for cmd in commands:
        words = [w.text for w in cmd.words]
        if not GH_PR_BODY.match(" ".join(words[:3])):
            continue
        for i, word in enumerate(words):
            if word in ("--body", "-b") and i + 1 < len(words):
                bodies.append(words[i + 1])
            elif word.startswith("--body="):
                bodies.append(word.removeprefix("--body="))
    return bodies


def check_closing(text: str) -> None:
    for line in text.splitlines():
        found = CLOSES.search(line)
        if found and not CLOSING_LINE.fullmatch(line):
            ref = found.group("ref")
            refuse(
                f"a closing keyword before a reference in a PR body: `{line.strip()}`. "
                f"GitHub closes {ref} when this PR merges. Only the PR's own closing line, "
                "`Closes owner/repo#N` alone on its line, may pair them. Reword the rest, "
                f"such as `left open: {ref}`."
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
    "Read the secret inside the process that needs it: `crew_org.config.load_env(<path to "
    ".env>)` parses the file and exports nothing, or `os.environ` where it's already set. "
    "Never source a .env with a shell. A leak check matches inside Python, reading both "
    "files itself."
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
        self.text = self.body.text


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
                # In double quotes the shell drops a backslash only before these.
                pair = self.s[self.i : self.i + 2]
                text.append(pair[1:] if close and pair[1:] in ('"', "\\", "$", "`") else pair)
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
        doc.text = "\n".join(lines)
        if doc.expands:
            # What the program gets: the shell drops a backslash before these here too.
            doc.text = re.sub(r"\\([\\$`])", r"\1", doc.text)
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

    # `Path(".").name` is "", so the dot is matched on the word itself.
    sourced = cmd.words[start].text
    if sourced in (".", "source") and any(Path(t).name.endswith(".env") for t in texts):
        # A dotenv value with a space, unquoted, is valid dotenv, and the shell runs its
        # second word as a command whose error prints it (crew#458, 2026-10-24).
        refuse_secret(f"`{sourced}` on a .env file runs its values as commands, which print them")
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


# --- running services ------------------------------------------------------
# A builder told to use a throwaway reached the real ones (crew#997). Read with
# the parser above, so a name in a commit message or a grep pattern is only a word.
# The shared Postgres publishes no port (infra's runbooks/the-stack.md), so a client
# reaches it from inside a container or over its network, never from the host.

SERVICE = re.compile(r"(?<![\w./-])(?:litellm(?:-[\w.-]+)?|crew-[\w.-]+)(?![\w.-])")
SHARED_POSTGRES = re.compile(r"litellm-temp-db|litellm_default")
# infra's compose/mac projects: their service names don't all say whose they are.
MAC_PROJECT = re.compile(
    r"(?:-p|--project-name)[ =](?:litellm|sprint-metrics|telemetry|open-webui)\b"
)
PROC_ENVIRON = re.compile(r"/proc/[^/]+/environ")
PYTHON = re.compile(r"python[\d.]*")
BSD_PS_ENV = re.compile(r"[a-zA-Z]*e[a-zA-Z]*")
PS_ENV = re.compile(r"-[a-zA-Z]*E[a-zA-Z]*")

DOCKERS = {"docker", "docker-compose"}
CLIENTS = {"psql", "pg_dump", "pg_restore", "pg_dumpall"}
# Each runs the command it's given, so the guard looks at that command instead.
WRAPPERS = {"sudo", "env", "timeout", "gtimeout", "nice", "nohup", "command", "exec", "xargs"}
WRAPPERS |= {"ssh", "uv", "caffeinate", "stdbuf"}

# The options that take a value, so the word after one isn't read as the target.
DOCKER_OPTIONS = {"--context", "-c", "--host", "-H", "--config", "--log-level", "-l"}
DOCKER_OPTIONS |= {"--tlscacert", "--tlscert", "--tlskey"}
EXEC_OPTIONS = {"-e", "--env", "--env-file", "-u", "--user", "-w", "--workdir", "--detach-keys"}
INSPECT_OPTIONS = {"--type"}
COMPOSE_OPTIONS = {"-f", "--file", "-p", "--project-name", "--project-directory", "--env-file"}
COMPOSE_OPTIONS |= {"--profile", "--ansi", "--parallel", "--progress", "--workdir"}
COMPOSE_EXEC_OPTIONS = {"-e", "--env", "--index", "-u", "--user", "-w", "--workdir"}

# What the runbooks and everyday checks read of a service. None holds its environment.
# Not .Config.Cmd or .Args: a secret can sit on a command line. `.Source` and its
# neighbours are a mount's own fields, read from inside `range .Mounts`.
INSPECT_FIELD = re.compile(
    r"\.(?:Id|Name|Image|Created|RestartCount|Config\.Image|Config\.Labels|Config\.User"
    r"|State(?:\.\w+)*|HostConfig\.RestartPolicy(?:\.\w+)?|HostConfig\.PortBindings"
    r"|NetworkSettings(?:\.\w+)*|Mounts|Source|Destination|Type)"
)

# LiteLLM's logs stay open to reads (the Sponsor, on crew#997), and only by this route.
# It tells a read by its text. This is not a permission: litellm_admin owns the server,
# so nothing in the database stops what this matching lets past. So anything it can't
# read as a read is refused, and it leaves the role, the database and the SQL to check.
LOGS_CONTAINER, LOGS_ROLE, LOGS_DATABASE = "litellm-temp-db", "litellm_admin", "litellm"
LOGS_ROUTE = "`docker exec litellm-temp-db psql -U litellm_admin -d litellm`"
LOGS_EXEC_OPTIONS = {"-i", "-t", "-it", "-ti", "--interactive", "--tty"}
# How psql formats its answer changes nothing else. Not -f, -o, -L, -h, -p, -l or -v.
PSQL_FLAGS = set("AtqXx")
PSQL_VALUES = set("UdcFR")
PSQL_LONG_FLAGS = {"--no-align", "--tuples-only", "--quiet", "--no-psqlrc", "--expanded", "--csv"}
PSQL_LONG_VALUES = {
    "--username": "U",
    "--dbname": "d",
    "--command": "c",
    "--field-separator": "F",
    "--record-separator": "R",
}
# Strings, quoted names and comments, in one pass from the left, as Postgres reads them:
# stripped one kind at a time, `select '--'; drop table t; --'` loses its drop. In an E''
# string a backslash can hide its closing quote, so it has its own rule, and one that
# never closes is refused. U&'' is refused: it can spell a name or a keyword in escapes.
SQL_SKIP = re.compile(
    r"""\b[eE]'(?:[^'\\]|\\.|'')*'|(?P<escape>\b[eE]'|\b[uU]&['"])"""
    r"""|'(?:[^']|'')*'|"(?P<name>(?:[^"]|"")*)"|--[^\r\n]*|/\*.*?\*/""",
    re.DOTALL,
)
# Left over outside a string: psql's own commands, a shell value, a dollar-quoted body
# or a string that never closed. The guard can't say what any of them does. The one psql
# command it allows is describing a table or listing them, alone: `\d "LiteLLM_SpendLogs"`.
SQL_UNKNOWN = re.compile(r"""['"$`\\]""")
SQL_DESCRIBE = re.compile(r'\s*\\dt?\+?(?:\s+"?[\w.]+"?)?\s*')
SQL_FIRST_WORD = re.compile(r"[(\s]*(\w+)")
SQL_READS = {"select", "with", "explain", "show", "values", "table"}
# What a statement that starts as a read can still write with: a CTE, `select into`,
# `for update`, `explain analyze`. The rest are refused by their first word already,
# and named here so a reader needn't work that out.
SQL_WRITE = re.compile(
    r"\b(?:insert|update|delete|merge|into|create|execute|declare|truncate|drop|alter|grant"
    r"|revoke|copy|for\s+(?:key\s+)?share)\b",
    re.IGNORECASE,
)
# A read past LiteLLM's logs, or one that changes state. litellm_admin can do all of these.
# The last line takes a query as a string, which the matching above doesn't read.
SQL_ELSEWHERE = re.compile(
    r"\b(?:sprint_metrics|dblink\w*|lo_\w+|loread|lowrite|set_config|nextval|setval"
    r"|(?:query|table|schema|database|cursor)_to_\w+|ts_stat|ts_rewrite|crosstab\w*)\b",
    re.IGNORECASE,
)
SQL_CATALOG = re.compile(r"\bpg_\w+", re.IGNORECASE)
SQL_CATALOG_READS = {"pg_catalog", "pg_class", "pg_namespace", "pg_attribute", "pg_tables"}
SQL_CATALOG_READS |= {"pg_indexes", "pg_stat_user_tables", "pg_stat_user_indexes", "pg_typeof"}
SQL_CATALOG_READS |= {"pg_size_pretty", "pg_total_relation_size", "pg_relation_size"}
SQL_CATALOG_READS |= {"pg_table_size", "pg_indexes_size", "pg_column_size"}

# A Python script's call to one of these, as a list or as a string, up to where the
# call ends. A shell script needs none of this: the parser reads it as it is. Without
# a way to shell out, the words are only data, as in a script that edits a test.
SHELLS_OUT = re.compile(r"subprocess|\bos\.(?:system|popen|exec|spawn)")
SCRIPT_CALL = re.compile(
    r"""\b(?:docker(?:-compose)?|psql|pg_dump|pg_restore|pg_dumpall)["',\s][^\])]{0,300}"""
)
SCRIPT_TOKEN = re.compile(r"""[\s"',\[]+""")
PY_COMMENT = re.compile(r"(?m)^\s*#.*$")
SCRIPT_LIMIT = 1_000_000

REACH_RULE = "CLAUDE.md, Never: reaching into a running service."
REACH_ROUTE = (
    "Real data comes through a service's HTTP API or a file the operator provides. A throwaway "
    "of your own, under another name, is fine, with these same clients. A person can run this "
    "command themselves with `!`."
)


def refuse_reach(what: str) -> None:
    refuse(f"{what}. {REACH_RULE}\n{REACH_ROUTE}")


def after_options(words: list[Word], takes_value: set[str]) -> int:
    """The index of the first word that isn't an option or an option's value."""
    i = 0
    while i < len(words) and words[i].text.startswith("-"):
        i += 2 if words[i].text in takes_value else 1
    return i


def narrow_format(template: str) -> bool:
    """True for an inspect template that reads only the fields in INSPECT_FIELD."""
    if "$" in template:
        return False
    inner = " ".join(re.findall(r"\{\{(.*?)\}\}", template))
    fields = re.findall(r"(?<![\w.])\.[\w.]*", re.sub(r'"[^"]*"|`[^`]*`', "", inner))
    return bool(fields) and all(INSPECT_FIELD.fullmatch(f) for f in fields)


def parse_psql(texts: list[str], call_only: bool) -> tuple[list[str], list[str], list[str]] | None:
    """The roles, databases and -c values in these words, or None if off the logs route.

    A Python script's call was split into words without its quoting, so `call_only`
    stops at -c: what follows isn't reliable, and usually sits in a variable anyway.
    """
    found: dict[str, list[str]] = {"U": [], "d": [], "c": [], "F": [], "R": []}
    i = 0
    while i < len(texts):
        text = texts[i]
        i += 1
        if text in PSQL_LONG_FLAGS:
            continue
        if text.startswith("--"):
            name, equals, value = text.partition("=")
            letter = PSQL_LONG_VALUES.get(name)
            if not letter or (not equals and i >= len(texts)):
                return None
            if call_only and letter == "c":
                break
            if not equals:
                value = texts[i]
                i += 1
            found[letter].append(value)
        elif text.startswith("-") and len(text) > 1:
            j = 1
            while j < len(text):
                letter = text[j]
                j += 1
                if letter in PSQL_FLAGS:
                    continue
                if letter not in PSQL_VALUES:
                    return None
                if call_only and letter == "c":
                    return found["U"], found["d"], found["c"]
                value = text[j:]
                j = len(text)
                if not value:
                    if i >= len(texts):
                        return None
                    value = texts[i]
                    i += 1
                found[letter].append(value)
        else:
            return None
    return found["U"], found["d"], found["c"]


def sql_reads(sql: str) -> bool:
    """True if this text, as far as text can tell, only reads LiteLLM's logs."""
    if SQL_DESCRIBE.fullmatch(sql):
        return not SQL_ELSEWHERE.search(sql)
    outside, names, at = [], [], 0
    for found in SQL_SKIP.finditer(sql):
        if found["escape"]:
            return False
        outside.append(sql[at : found.start()])
        if found["name"] is not None:
            names.append(found["name"])
        at = found.end()
    outside.append(sql[at:])
    rest = " ".join(outside)
    statements = [s for s in rest.split(";") if s.strip()]
    if not statements or SQL_UNKNOWN.search(rest) or SQL_WRITE.search(rest):
        return False
    for statement in statements:
        first = SQL_FIRST_WORD.match(statement)
        if not first or first.group(1).lower() not in SQL_READS:
            return False
    named = " ".join([rest, *names])
    if SQL_ELSEWHERE.search(named):
        return False
    return all(c.lower() in SQL_CATALOG_READS for c in SQL_CATALOG.findall(named))


class Reach:
    """Walks one Bash call, and refuses what reaches a running service."""

    def __init__(
        self, whole: str, cwd: Path, read_files: bool = True, call_only: bool = False
    ) -> None:
        self.whole = whole
        self.cwd = cwd
        self.read_files = read_files
        self.call_only = call_only
        self.written: dict[str, str] = {}
        self.piped: Command | None = None

    def run(self, text: str, inside: bool = False, around: str = "") -> None:
        with contextlib.suppress(ValueError, IndexError):
            self.sequence(Shell(text).commands(), inside, around)

    def sequence(self, commands: list[Command], inside: bool, around: str) -> None:
        before, self.piped = self.piped, None
        for cmd in commands:
            self.command(cmd, inside, around)
            self.piped = cmd if cmd.piped else None
        self.piped = before

    def names_service(self, word: Word) -> bool:
        if SERVICE.search(word.raw.lstrip("\"'/")):
            return True
        # `$C` or xargs' `{}`: unknown, unless this call also names a service.
        return self.unresolved(word) and bool(SERVICE.search(self.whole))

    @staticmethod
    def unresolved(word: Word) -> bool:
        return bool(word.names or word.subs or "{" in word.text)

    def command(self, cmd: Command, inside: bool, around: str) -> None:
        targets = [t for _, t in cmd.redirects]
        for word in [*cmd.words, *targets, *(doc.body for doc in cmd.heredocs)]:
            if PROC_ENVIRON.fullmatch(word.text):
                refuse_reach(f"`{word.text}` holds a running process's environment")
            for sub in word.subs:
                self.sequence(sub, inside, around)
        for op, target in cmd.redirects:
            if op in (">", ">>") and cmd.heredocs:
                self.written[target.text] = cmd.heredocs[0].text

        start = 0
        while start < len(cmd.words) and (
            ASSIGNMENT.match(cmd.words[start].raw) or cmd.words[start].raw in KEYWORDS
        ):
            start += 1
        if start == len(cmd.words):
            return
        around = " ".join([around, *(w.raw for w in cmd.words)])
        self.program(cmd, cmd.words[start:], inside, around)

    def program(self, cmd: Command, words: list[Word], inside: bool, around: str) -> None:
        first, args = words[0].text, words[1:]
        name, texts = Path(first).name, [w.text for w in args]
        if inside and (name == "printenv" or prints_environment(name, args)):
            refuse_reach(f"`{' '.join([name, *texts])}` in a container prints its environment")
        if name == "ps" and (
            texts and BSD_PS_ENV.fullmatch(texts[0]) or any(PS_ENV.fullmatch(t) for t in texts)
        ):
            refuse_reach("`ps` with its environment flag prints a process's environment")
        if name == "cd" and texts:
            self.cwd = (self.cwd / Path(texts[0]).expanduser()).resolve()
        elif name in WRAPPERS:
            self.wrapped(name, cmd, args, inside, around)
        elif name in DOCKERS:
            self.docker(name, cmd, args, inside, around)
        elif name in CLIENTS:
            self.client(name, args, around)
        elif name in SHELLS or name == "eval":
            self.shell(name, cmd, texts, inside, around)
        elif PYTHON.fullmatch(name):
            self.python(cmd, texts)
        elif "/" in first:
            self.script_file(first)

    def wrapped(self, name: str, cmd: Command, args: list[Word], inside: bool, around: str) -> None:
        for i, word in enumerate(args):
            base = Path(word.text).name
            if (
                base in DOCKERS | CLIENTS | SHELLS
                or base == "eval"
                or PYTHON.fullmatch(base)
                or word.text.startswith(("./", "../"))
            ):
                self.program(cmd, args[i:], inside, around)
                return
        if name == "ssh" and args and " " in args[-1].text:
            self.run(args[-1].text, inside, around)

    def docker(self, name: str, cmd: Command, args: list[Word], inside: bool, around: str) -> None:
        rest = args
        if name == "docker":
            rest = args[after_options(args, DOCKER_OPTIONS) :]
            if rest and rest[0].text == "compose":
                self.compose(rest[1:], inside, around)
                return
            if rest and rest[0].text == "container":
                rest = rest[1:]
        else:
            self.compose(args, inside, around)
            return
        if not rest:
            return
        sub, rest = rest[0].text, rest[1:]
        if sub == "exec":
            at = after_options(rest, EXEC_OPTIONS)
            target = rest[at] if at < len(rest) else None
            if target and self.names_service(target):
                if self.reads_logs(cmd, rest[:at], target, rest[at + 1 :]):
                    return
                if LOGS_CONTAINER in target.raw:
                    refuse_reach(
                        f"Only a read of LiteLLM's own database passes on {LOGS_CONTAINER}: "
                        f"{LOGS_ROUTE}, with SQL in -c or on stdin that the guard can read as a "
                        "SELECT. Nothing else on that server, and no write"
                    )
                refuse_reach(f"`docker exec {target.text}` reaches into a running service")
            self.inner(rest[at + 1 :], True, around)
        elif sub == "cp":
            self.copy(rest)
        elif sub == "inspect":
            self.inspect(rest)
        elif sub == "run":
            for i, word in enumerate(rest):
                if Path(word.text).name in CLIENTS | SHELLS:
                    self.inner(rest[i:], False, around)
                    return

    def reads_logs(
        self, cmd: Command, options: list[Word], target: Word, words: list[Word]
    ) -> bool:
        """True for the one route into a service that stays open: see LOGS_CONTAINER."""
        if target.text != LOGS_CONTAINER or self.unresolved(target):
            return False
        if {w.text for w in options} - LOGS_EXEC_OPTIONS or not words or words[0].text != "psql":
            return False
        texts = [w.text for w in words[1:]]
        parsed = parse_psql(texts, self.call_only)
        if not parsed or parsed[0] != [LOGS_ROLE] or parsed[1] != [LOGS_DATABASE]:
            return False
        if self.call_only:
            return not SQL_WRITE.search(" ".join(texts))
        return all(sql_reads(sql) for sql in parsed[2] or [self.stdin_sql(cmd)])

    def stdin_sql(self, cmd: Command) -> str:
        """The SQL this command gets on stdin, if the guard can see it. "" if not."""
        if cmd.heredocs:
            return cmd.heredocs[-1].text
        for op, target in cmd.redirects:
            if op == "<":
                return self.script_text(target.text)
        if not self.piped or not self.piped.words:
            return ""
        name = Path(self.piped.words[0].text).name
        args = [w.text for w in self.piped.words[1:] if not w.text.startswith("-")]
        if name == "cat" and self.piped.heredocs and not args:
            return self.piped.heredocs[-1].text
        if name in ("echo", "printf"):
            # All of it, format included: only a bare %s is known to add nothing. echo and
            # printf may turn a backslash into something else, so that can't be read.
            text = " ".join(args[1:] if name == "printf" and args[:1] == ["%s"] else args)
            return "" if "\\" in text else text
        return self.script_text(args[0]) if name == "cat" and len(args) == 1 else ""

    def inner(self, words: list[Word], inside: bool, around: str) -> None:
        if words:
            self.program(Command(), words, inside, around)

    def copy(self, words: list[Word]) -> None:
        for word in words:
            host = word.text.partition(":")[0]
            if ":" in word.text and (
                SERVICE.search(host) or self.unresolved(word) and SERVICE.search(self.whole)
            ):
                refuse_reach(f"`docker cp {word.text}` reaches into a running service")

    def inspect(self, words: list[Word]) -> None:
        template, targets, i = None, [], 0
        while i < len(words):
            text = words[i].text
            if text in ("-f", "--format") and i + 1 < len(words):
                template = words[i + 1].text
                i += 1
            elif text.startswith(("-f=", "--format=")):
                template = text.partition("=")[2]
            elif text in INSPECT_OPTIONS:
                i += 1
            elif not text.startswith("-"):
                targets.append(words[i])
            i += 1
        if template and re.search(r"\bEnv\b", template):
            refuse_reach("`docker inspect` of `.Config.Env` prints a container's environment")
        reached = any(self.names_service(t) for t in targets) or (
            not targets and SERVICE.search(self.whole)
        )
        if reached and not (template and narrow_format(template)):
            refuse_reach(
                "`docker inspect` of a running service shows its environment. Only a --format "
                "of its image, labels or state reads less"
            )

    def compose(self, args: list[Word], inside: bool, around: str) -> None:
        at = after_options(args, COMPOSE_OPTIONS)
        options, rest = " ".join(w.text for w in args[:at]), args[at:]
        if not rest or rest[0].text not in ("exec", "cp"):
            return
        sub, rest = rest[0].text, rest[1:]
        theirs = bool(MAC_PROJECT.search(options)) or "compose/mac" in f"{options} {self.cwd}"
        if sub == "cp":
            if theirs:
                refuse_reach("`docker compose cp` in infra's stack reaches into a running service")
            self.copy(rest)
            return
        at = after_options(rest, COMPOSE_EXEC_OPTIONS)
        target = rest[at] if at < len(rest) else None
        if target and (theirs or self.names_service(target)):
            refuse_reach(f"`docker compose exec {target.text}` reaches into a running service")
        self.inner(rest[at + 1 :], True, around)

    def client(self, name: str, args: list[Word], around: str) -> None:
        if SHARED_POSTGRES.search(around) or (
            any(self.unresolved(w) for w in args) and SHARED_POSTGRES.search(self.whole)
        ):
            refuse_reach(f"`{name}` against the shared Postgres reaches into a running service")

    def shell(self, name: str, cmd: Command, texts: list[str], inside: bool, around: str) -> None:
        if name == "eval":
            self.run(" ".join(texts), inside, around)
            return
        flag = next((k for k, t in enumerate(texts) if re.fullmatch(r"-[a-z]*c[a-z]*", t)), -1)
        if 0 <= flag < len(texts) - 1:
            self.run(texts[flag + 1], inside, around)
            return
        for doc in cmd.heredocs:
            self.run(doc.text, inside, around)
        path = next((t for t in texts if not t.startswith("-")), "")
        self.read_shell(self.script_text(path))

    def python(self, cmd: Command, texts: list[str]) -> None:
        if "-m" in texts:
            return
        for doc in cmd.heredocs:
            self.read_python(doc.text)
        if "-c" in texts[:-1]:
            self.read_python(texts[texts.index("-c") + 1])
            return
        self.read_python(self.script_text(next((t for t in texts if not t.startswith("-")), "")))

    def script_file(self, path: str) -> None:
        text = self.script_text(path)
        if path.endswith(".py") or "python" in text.partition("\n")[0]:
            self.read_python(text)
        else:
            self.read_shell(text)

    def script_text(self, path: str) -> str:
        """A script this call wrote, or the one on disk. "" if it can't be read."""
        if path in self.written:
            return self.written[path]
        if not path or not self.read_files:
            return ""
        with contextlib.suppress(OSError, ValueError):
            file = self.cwd / Path(path).expanduser()
            if file.stat().st_size <= SCRIPT_LIMIT:
                return file.read_text()
        return ""

    def read_shell(self, text: str) -> None:
        if text:
            Reach(text, self.cwd, read_files=False).run(text)

    def read_python(self, text: str) -> None:
        text = PY_COMMENT.sub("", text)
        if re.search(r"/proc/(?:\d+|self|\{[^}]*\})/environ", text):
            refuse_reach("a script reads `/proc/<pid>/environ`, a running process's environment")
        if not SHELLS_OUT.search(text):
            return
        for call in SCRIPT_CALL.finditer(text):
            words = " ".join(SCRIPT_TOKEN.split(call.group()))
            Reach(text, self.cwd, read_files=False, call_only=True).run(words)


def check_reach(command: str, cwd: Path) -> None:
    Reach(command, cwd).run(command)


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
    check_reach(command, cwd)
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
