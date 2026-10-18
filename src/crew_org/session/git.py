"""The session's git: real git command lines that read history, and restore (crew#778).

The Sponsor, on crew#778: "It should know and be trained on how to use git. Let's
keep it close to that shape as a tool." So the session writes a git command line as
it would in a terminal, and the crew runs it on the host, since the worktree's git
directory isn't mounted in the workspace container. `status`, `diff`, `log`, `show`,
`blame` and `grep` read; `restore --source=<ref> -- <paths>` is read here and written
by the tools as an edit (`Tools.git`). Nothing else runs.

Git on the host must not run what the session's code can change:
- no shell: the line is split as a shell would split it, and its words are run;
- every option that writes, reads outside the repository or runs a program is
  refused, with every abbreviation git would accept (`blame --cont <file>` is
  `--contents`), and so is any option before the command;
- hooks, fsmonitor, the pager and external diff and text-conversion drivers are off on
  every call, and the environment is built from nothing, so no system, global or
  environment config is read;
- git runs on the git directory the clone records for the worktree, never one inside
  the worktree, where the session's code could write a config or hooks. The container
  sees `.git` read-only (crew#788); this holds without that (`git_dir`).

The story's base, the commit its branch started from, is the ref `base`: `refs/base`,
which the crew sets to `regression.merged_base`'s commit before each call.
"""

from __future__ import annotations

import os
import posixpath
import shlex
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from crew_org.rulebook import refuse
from crew_org.tools.workspace import shell_operators

READS = ("status", "diff", "log", "show", "blame", "grep")
ALLOWED = (
    "`status`, `diff`, `log`, `show`, `blame` and `grep`, which read, and "
    "`restore --source=<ref> -- <paths>`, which puts files in your branch back"
)
# Before every command: no pager, no lock taken, and nothing configured that runs.
GLOBAL = [
    "--no-pager",
    "--no-optional-locks",
    *("-c", "core.hooksPath=/dev/null"),
    *("-c", "core.fsmonitor=false"),
    *("-c", "core.pager=cat"),
    *("-c", "core.attributesFile=/dev/null"),
    *("-c", "log.showSignature=false"),
    *("-c", "protocol.allow=never"),
    *("-c", "color.ui=never"),
]
# After the command, where git takes them. A submodule's own repository is in the
# worktree, so `status` and `diff` leave submodules alone rather than run git in one.
INJECTED = {
    "diff": ["--no-ext-diff", "--no-textconv", "--ignore-submodules=all"],
    "log": ["--no-ext-diff", "--no-textconv"],
    "show": ["--no-ext-diff", "--no-textconv"],
    "blame": ["--no-ext-diff", "--no-textconv"],
    "grep": ["--no-textconv"],
    "status": ["--ignore-submodules=all"],
}
# Long options that write, read outside the repository or run a program, with what
# they'd do. Any abbreviation is refused too, since git's parse-options takes one.
REFUSED = {
    "output": "writes a file",
    "ext-diff": "runs an external diff program",
    "textconv": "runs a text-conversion program",
    "no-index": "reads files outside the repository",
    "show-signature": "runs gpg",
    "open-files-in-pager": "runs a pager",
    "file": "reads patterns from a file",
    "contents": "reads another file in place of the one named",
    "ignore-revs-file": "reads a file",
    "pathspec-from-file": "reads paths from a file",
    "submodule": "runs git in a submodule",
    "recurse-submodules": "runs git in submodules",
    "ignore-submodules": "is the crew's to set: submodules are left alone",
    "remerge-diff": "writes objects into the repository",
    "help": "runs a manual viewer",
    "git-dir": "names another repository",
    "work-tree": "names another worktree",
    "exec-path": "runs git's programs from elsewhere",
    "namespace": "is the crew's to set",
    "super-prefix": "is git's own",
    "config-env": "reads config from the environment",
}
# Whole option names that begin like a refused one, and are themselves.
TAKEN = {"text", "ignore-rev"}
# Short options that read a file or run a program: diff's orderfile `-O` (which
# `blame` and `log` take too), grep's pager `-O` and patterns file `-f`, and blame's
# revisions file `-S`.
SHORT_REFUSED = {"grep": "Of", "blame": "OS"}
SHORT_REFUSED_ELSE = "O"
# Short options whose value follows in the same word, so the rest of it is a value.
SHORT_VALUED = {
    "grep": "efABCmO",
    "blame": "LSMC",
    "status": "u",
}
SHORT_VALUED_ELSE = "UMCBlSGOXInL"
# A git command that runs longer than this is stopped.
TIMEOUT = 60
# The most of a reply's output read: the rest is cut, and git stopped.
OUTPUT_CHARS = 40_000
# HOME, so git reads no user config or attributes: a path under a file never exists.
NO_HOME = "/dev/null/home"


class GitUnavailable(RuntimeError):
    """The worktree's git directory can't be found where the clone keeps it."""


def parse(command: str) -> tuple[str, list[str]]:
    """A git command line as its command and its words, or refused: what isn't run, an
    option that isn't taken, or a path outside the repository."""
    operators = shell_operators(command)
    if operators:
        refuse("session.git", "shell", operators=", ".join(f"`{o}`" for o in operators))
    try:
        words = shlex.split(command)
    except ValueError as exc:
        refuse("session.git", "unparsed", error=str(exc))
    if words[:1] == ["git"]:
        words = words[1:]
    if not words:
        refuse("session.git", "empty", allowed=ALLOWED)
    if words[0].startswith("-"):
        refuse("session.git", "global_option", option=words[0], allowed=ALLOWED)
    sub, args = words[0], words[1:]
    if sub == "restore":
        return sub, args
    if sub not in READS:
        refuse("session.git", "not_allowed", command=sub, allowed=ALLOWED)
    ended = False
    for word in args:
        if word == "--":
            ended = True
            continue
        if not ended and word.startswith("-") and word != "-":
            _check_option(sub, word)
        # `git diff` with a path outside the repository reads it as `--no-index` would.
        if ended or (sub == "diff" and not word.startswith("-")):
            outside(word)
    return sub, args


def _check_option(sub: str, word: str) -> None:
    if word.startswith("--"):
        name, _, value = word[2:].partition("=")
        for refused, why in REFUSED.items():
            if name == refused or (name and refused.startswith(name) and name not in TAKEN):
                refuse("session.git", "option", option=word, why=why)
        if sub in ("log", "show"):
            if name in ("format", "pretty") and "%G" in value.replace("%%", ""):
                refuse("session.git", "option", option=word, why="checks signatures with gpg")
            if name == "diff-merges" and value in ("r", "remerge"):
                refuse("session.git", "option", option=word, why=REFUSED["remerge-diff"])
        return
    refused = SHORT_REFUSED.get(sub, SHORT_REFUSED_ELSE)
    valued = SHORT_VALUED.get(sub, SHORT_VALUED_ELSE)
    for letter in word[1:]:
        if letter in refused:
            why = "runs a program" if (sub, letter) == ("grep", "O") else "reads a file"
            refuse("session.git", "option", option=f"-{letter}", why=why)
        if letter in valued:
            return


def outside(word: str) -> None:
    """Refuse a path that is absolute or leads out of the repository."""
    normal = posixpath.normpath(word)
    if word.startswith("/") or normal == ".." or normal.startswith("../"):
        refuse("session.path", "outside", path=word)


def restore_args(args: list[str]) -> tuple[str, list[str]]:
    """`restore`'s ref and paths: `--source=<ref>` (or `--source <ref>`, `-s <ref>`) and
    the files, after `--` or not. It writes the worktree only, so `--worktree` is taken
    and every other option refused."""
    source: str | None = None
    paths: list[str] = []
    i = 0
    while i < len(args):
        word = args[i]
        if word == "--":
            paths += args[i + 1 :]
            break
        if word in ("--source", "-s"):
            source = args[i + 1] if i + 1 < len(args) else ""
            i += 2
            continue
        if word.startswith("--source="):
            source = word.partition("=")[2]
        elif word.startswith("-s"):
            source = word[2:]
        elif word in ("--worktree", "-W"):
            pass
        elif word.startswith("-"):
            refuse("session.restore", "option", option=word)
        else:
            paths.append(word)
        i += 1
    if not source or source.startswith("-"):
        refuse("session.restore", "no_source")
    if not paths:
        refuse("session.restore", "no_paths", ref=source)
    return source, paths


def git_dir(root: Path) -> Path:
    """The worktree's git directory, as the clone records it.

    A worktree's `.git` is a file naming its git directory, which is in the clone,
    outside the worktree, where nothing the session runs can write. It's taken only
    if it is there and the clone's record names this worktree back; then git is
    pointed at it directly, and the `.git` file is never read again.
    """
    marker = root / ".git"
    if marker.is_symlink() or not marker.is_file():
        raise GitUnavailable(f"{root} isn't a worktree of a clone: its `.git` isn't a file")
    named = marker.read_text(encoding="utf-8").strip().removeprefix("gitdir:").strip()
    found = (root / named).resolve()
    if found.is_relative_to(root):
        raise GitUnavailable("the worktree's `.git` names a git directory inside it")
    try:
        back = (found / (found / "gitdir").read_text(encoding="utf-8").strip()).resolve()
    except OSError as exc:
        raise GitUnavailable(f"no clone records this worktree at {found}") from exc
    if back != marker.resolve() or not (found / "commondir").is_file():
        raise GitUnavailable(f"the clone's record at {found} names another worktree")
    return found


@dataclass
class Ran:
    code: int
    output: bytes
    errors: str = ""
    cut: bool = False


class Git:
    """git over one worktree, run with nothing of the host's: the fixed environment and
    options above, and the git directory the clone records."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._dir: Path | None = None
        self._base: str | None = None

    def env(self) -> dict[str, str]:
        if self._dir is None:
            self._dir = git_dir(self.root)
        return {
            "PATH": os.pathsep.join(dict.fromkeys([str(Path(_git()).parent), "/usr/bin", "/bin"])),
            "HOME": NO_HOME,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_DIR": str(self._dir),
            "GIT_WORK_TREE": str(self.root),
        }

    def run(self, args: list[str], *, limit: int | None = None) -> Ran:
        """`git <args>`, with the crew's options before them. With `limit`, its output
        and errors together, read up to `limit` characters and the rest cut."""
        argv = [_git(), *GLOBAL, *args]
        if limit is None:
            try:
                done = subprocess.run(
                    argv,
                    cwd=self.root,
                    env=self.env(),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                return Ran(-1, b"", f"still running after {TIMEOUT}s: stopped")
            return Ran(done.returncode, done.stdout, done.stderr.decode("utf-8", "replace"))
        with subprocess.Popen(
            argv,
            cwd=self.root,
            env=self.env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        ) as proc:
            timer = threading.Timer(TIMEOUT, proc.kill)
            timer.start()
            try:
                assert proc.stdout is not None
                # Characters are at most 4 bytes: enough bytes for `limit` of them.
                output = proc.stdout.read(limit * 4 + 1)
                cut = len(output.decode("utf-8", "replace")) > limit or bool(proc.stdout.read(1))
                if cut:
                    proc.kill()
                code = proc.wait()
            finally:
                timer.cancel()
        if code < 0 and not cut:
            return Ran(-1, output, f"still running after {TIMEOUT}s: stopped")
        return Ran(0 if cut else code, output, cut=cut)

    def set_base(self) -> None:
        """Point `refs/base` at the story's base: the commit `regression.merged_base`
        finds, where the branch left the default branch. The clone's worktrees share
        it, so it's set before each call; one session runs at a time (P1)."""
        if self._base is None:
            found = self.run(["merge-base", "HEAD", "origin/HEAD"])
            self._base = found.output.decode().strip() if found.code == 0 else ""
        if self._base:
            self.run(["update-ref", "refs/base", self._base])
        else:
            # No base to name: `base` resolves to nothing rather than another story's.
            self.run(["update-ref", "-d", "refs/base"])


def _git() -> str:
    return shutil.which("git") or "git"
