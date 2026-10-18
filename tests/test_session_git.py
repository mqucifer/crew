"""The session's git tool: real command lines that read history, and restore (crew#778).

Each test runs real git on a clone and a worktree made from it, as delivery makes
them: the worktree's `.git` is a file naming its git directory in the clone. The
workspace container is `Fake`, which only the lint after a restore reaches.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from crew_org.events import EventKind
from crew_org.session import conversation, git
from crew_org.session.signals import Signals
from crew_org.session.tools import Tools
from tests.test_session import CHECKS, Fake, Proxy, kinds, said, seen  # noqa: F401

APP = "def f():\n    return 1\n"
HOOKED = "never-run"


def sh(root: Path, *args: str) -> str:
    """git as the fixture's own setup runs it, with no user config in the way."""
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    done = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A clone whose default branch has the merged code: `origin/HEAD` names it."""
    root = tmp_path / "clone"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text(APP)
    (root / "src" / "notes.md").write_text("merged notes\n")
    (root / "run.sh").write_text("#!/bin/sh\necho hi\n")
    (root / "run.sh").chmod(0o755)
    sh(root, "init", "-q", "-b", "main")
    sh(root, "add", "-A")
    sh(root, "commit", "-qm", "merged: f returns 1")
    sh(root, "update-ref", "refs/remotes/origin/HEAD", "HEAD")
    return root


@pytest.fixture
def work(clone: Path, tmp_path: Path) -> Path:
    """The story's worktree: one checkpoint committed on its branch, and one edit since."""
    path = tmp_path / "work"
    sh(clone, "worktree", "add", "-q", "-b", "feat/1-f", str(path), "main")
    (path / "src" / "app.py").write_text("def f():\n    return 2\n")
    sh(path, "commit", "-qam", "checkpoint(1): f returns 2")
    (path / "src" / "new.py").write_text("x = 1\n")
    (path / "src" / "notes.md").write_text("changed notes\n")
    return path.resolve()


def tools(root: Path) -> Tools:
    return Tools(root, Fake(), CHECKS)


def test_each_read_runs_as_git_does_with_base_the_stories_base(work: Path):
    t = tools(work)
    status = t.call("git", {"command": "git status --short"})
    assert status.kind == "ok" and " M src/notes.md" in status.text
    assert "?? src/new.py" in status.text
    # `base` is where the branch left the default branch: the checkpoint's change shows.
    diff = t.call("git", {"command": "git diff base -- src/app.py"})
    assert "-    return 1" in diff.text and "+    return 2" in diff.text
    log = t.call("git", {"command": "log --oneline base..HEAD"})  # with or without `git`
    assert "checkpoint(1): f returns 2" in log.text and "merged" not in log.text
    show = t.call("git", {"command": "git show base:src/app.py"})
    assert show.text.endswith("return 1") and show.detail == {"git": "show", "exit": 0, "lines": 2}
    blame = t.call("git", {"command": "git blame -L 2,2 src/app.py"})
    assert "return 2" in blame.text and blame.kind == "ok"
    grep = t.call("git", {"command": 'git grep -n "return" -- src'})
    assert "src/app.py:2:    return 2" in grep.text
    assert git.Git(work).run(["rev-parse", "base"]).output.decode().strip() == sh(
        work, "merge-base", "HEAD", "main"
    )


def test_a_lookup_that_finds_nothing_and_an_error_are_replies(work: Path):
    t = tools(work)
    nothing = t.call("git", {"command": "git grep zebra"})
    assert (nothing.kind, nothing.empty, nothing.text.splitlines()[-1]) == (
        "ok",
        True,
        "No lines match.",
    )
    bad = t.call("git", {"command": "git show nope:src/app.py"})
    assert bad.kind == "failed" and "(exit 128)" in bad.text and "nope" in bad.text


def test_a_long_output_is_cut_and_says_how_to_narrow_it(work: Path, monkeypatch):
    monkeypatch.setattr("crew_org.session.tools.OUTPUT_CHARS", 20)
    reply = tools(work).call("git", {"command": "git log"})
    assert reply.kind == "ok" and "(cut at 20 characters: narrow it with a path" in reply.text


# One case each: what's refused, by which rule, and what its reply says. None runs git.
REFUSED = [
    ("git push", "session.git", "isn't run here"),
    ("git commit -am x", "session.git", "isn't run here"),
    ("git checkout main", "session.git", "isn't run here"),
    ("git reset --hard base", "session.git", "isn't run here"),
    ("git config core.pager x", "session.git", "isn't run here"),
    ("git -c core.pager=x log", "session.git", "git's own options are the crew's"),
    ("git -C /tmp status", "session.git", "git's own options are the crew's"),
    ("git --git-dir=/tmp/x log", "session.git", "git's own options are the crew's"),
    ("git --no-pager diff", "session.git", "git's own options are the crew's"),
    ("git diff --output=out.txt", "session.git", "writes a file"),
    ("git log -p --outp=out.txt", "session.git", "writes a file"),
    ("git diff --ext-diff", "session.git", "runs an external diff program"),
    ("git diff --textconv", "session.git", "runs a text-conversion program"),
    ("git show --textc HEAD", "session.git", "runs a text-conversion program"),
    ("git diff --no-index a b", "session.git", "reads files outside the repository"),
    ("git diff --no-ind a b", "session.git", "reads files outside the repository"),
    ("git log --show-signature", "session.git", "runs gpg"),
    ("git log --format=%G?", "session.git", "checks signatures with gpg"),
    ("git show --pretty=format:%h%GS HEAD", "session.git", "checks signatures with gpg"),
    ("git diff -O/etc/passwd", "session.git", "`-O` isn't taken: it reads a file"),
    ("git diff -pO../order", "session.git", "`-O` isn't taken: it reads a file"),
    ("git grep -O f", "session.git", "`-O` isn't taken: it runs a program"),
    ("git grep --open-files-in-pager f", "session.git", "runs a pager"),
    ("git grep -f ../patterns", "session.git", "`-f` isn't taken: it reads a file"),
    ("git grep --file=../patterns", "session.git", "reads patterns from a file"),
    ("git blame --contents ../x src/app.py", "session.git", "reads another file"),
    ("git blame --cont ../x src/app.py", "session.git", "reads another file"),
    ("git blame --ignore-revs-file ../x src/app.py", "session.git", "reads a file"),
    ("git blame --ignore-revs ../x src/app.py", "session.git", "reads a file"),
    ("git blame -S ../revs src/app.py", "session.git", "`-S` isn't taken: it reads a file"),
    ("git diff --pathspec-from-file=../x", "session.git", "reads paths from a file"),
    ("git log --git-dir=/x", "session.git", "names another repository"),
    ("git log --work-tree=/x", "session.git", "names another worktree"),
    ("git log --exec-path=/x", "session.git", "runs git's programs from elsewhere"),
    ("git diff --submodule=diff", "session.git", "runs git in a submodule"),
    ("git grep --recurse-submodules f", "session.git", "runs git in submodules"),
    ("git status --ignore-submodules=none", "session.git", "is the crew's to set"),
    ("git show --remerge-diff HEAD", "session.git", "writes objects"),
    ("git log --diff-merges=remerge", "session.git", "writes objects"),
    ("git diff --help", "session.git", "runs a manual viewer"),
    ("git log | head", "session.git", "There's no shell: `|`"),
    ("git diff > out.txt", "session.git", "There's no shell: `>`"),
    ("git status && git diff", "session.git", "There's no shell: `&&`"),
    ("git log $(touch out.txt)", "session.git", "There's no shell"),
    ('git log --grep="unclosed', "session.git", "can't be split into words"),
    ("git", "session.git", "Give a git command line"),
    ("git diff /etc/passwd src/app.py", "session.path", "isn't a path in the repository"),
    ("git diff ../outside src/app.py", "session.path", "isn't a path in the repository"),
    ("git log -- /etc", "session.path", "isn't a path in the repository"),
    ("git restore src/app.py", "session.restore", "takes `--source=<ref>`"),
    ("git restore --source=base", "session.restore", "Name the files to restore"),
    ("git restore --staged --source=base -- src/app.py", "session.restore", "never the index"),
    ("git restore -p --source=base src/app.py", "session.restore", "never the index"),
]


@pytest.mark.parametrize(("command", "rule", "says"), REFUSED)
def test_what_isnt_run_is_refused_with_why(work: Path, monkeypatch, command, rule, says):
    ran: list[list[str]] = []
    monkeypatch.setattr(git.Git, "run", lambda self, args, **_: ran.append(args))
    reply = tools(work).call("git", {"command": command})
    assert (reply.kind, reply.rule) == ("refused", rule)
    assert says in reply.text
    assert ran == [] and not (work / "out.txt").exists()


# Refused once git has looked at the ref: one case each.
RESTORE_REFUSED = [
    ("git restore --source=base -- ../outside", "session.path", "isn't a path"),
    ("git restore --source=base -- .git/config", "session.path", "doesn't write"),
    ("git restore --source=nope -- src/app.py", "session.restore", "`nope` isn't a commit"),
    ("git restore --source=base -- src", "session.restore", "is a folder"),
    ("git restore --source=base -- src/none.py", "session.restore", "neither at `base`"),
]


@pytest.mark.parametrize(("command", "rule", "says"), RESTORE_REFUSED)
def test_a_restore_that_cant_be_applied_is_refused(work: Path, command, rule, says):
    t = tools(work)
    reply = t.call("git", {"command": command})
    assert (reply.kind, reply.rule) == ("refused", rule) and says in reply.text
    assert t.edited == [] and (work / "src" / "app.py").read_text() != APP


def test_restore_puts_a_file_back_as_an_edit(work: Path):
    t = tools(work)
    reply = t.call("git", {"command": "git restore --source=base -- src/app.py src/new.py"})
    assert reply.kind == "ok"
    assert (work / "src" / "app.py").read_text() == APP and not (work / "src" / "new.py").exists()
    assert "Restored as at `base`: `src/app.py`." in reply.text
    assert "    2|     return 1" in reply.text  # the edit's reply: the lines, numbered
    assert "Deleted `src/new.py`: `base` doesn't have it." in reply.text
    assert t.edited == ["src/app.py", "src/new.py"] and t.edits_since_run == 2
    # It counts as an edit, as the edit tools do, and a view of it is stale.
    watch = Signals(views={"src/app.py": 3})
    after = watch.after([("git", reply)], {})
    assert (after["since_action"], after["edits_since_run"], watch.views) == (0, 1, {})
    again = t.call("git", {"command": "git restore --source=base src/app.py"})
    assert "already as it is at `base`" in again.text and not again.detail["edit"]


def test_restore_keeps_the_executable_bit_the_ref_has(work: Path):
    (work / "run.sh").unlink()
    tools(work).call("git", {"command": "git restore --source=base -- run.sh"})
    assert (work / "run.sh").stat().st_mode & stat.S_IXUSR


def test_a_restore_that_stops_a_file_parsing_isnt_applied(work: Path):
    (work / "src" / "app.py").write_text("def f(:\n")
    sh(work, "commit", "-qam", "checkpoint(1): broken")
    (work / "src" / "app.py").write_text(APP)
    reply = tools(work).call("git", {"command": "git restore --source=HEAD -- src/app.py"})
    assert (reply.kind, reply.rule) == ("refused", "session.parse_guard")
    assert (work / "src" / "app.py").read_text() == APP


def test_a_restore_through_a_link_is_refused(work: Path, tmp_path: Path):
    (tmp_path / "outside.py").write_text("secret = 1\n")
    (work / "src" / "app.py").unlink()
    (work / "src" / "app.py").symlink_to(tmp_path / "outside.py")
    reply = tools(work).call("git", {"command": "git restore --source=base -- src/app.py"})
    assert (reply.kind, reply.rule) == ("refused", "session.path")
    assert (tmp_path / "outside.py").read_text() == "secret = 1\n"


def test_nothing_of_the_hosts_config_or_environment_is_read(work: Path, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[diff]\n\tnoprefix = true\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "diff.mnemonicPrefix")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    monkeypatch.setenv("GIT_DIR", str(tmp_path))
    reply = tools(work).call("git", {"command": "git diff base -- src/app.py"})
    assert "--- a/src/app.py" in reply.text and "+++ b/src/app.py" in reply.text


def test_drivers_and_hooks_the_clone_configures_never_run(work: Path, clone: Path, tmp_path):
    """The flags the crew adds on every call, against a clone that names a program for
    each: an external diff, a text conversion, fsmonitor and a hook on ref updates."""
    marker = tmp_path / HOOKED
    program = tmp_path / "program.sh"
    program.write_text(f"#!/bin/sh\ntouch {marker}\n")
    program.chmod(0o755)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "reference-transaction").write_text(program.read_text())
    (hooks / "reference-transaction").chmod(0o755)
    for key, value in [
        ("diff.external", str(program)),
        ("diff.conv.textconv", str(program)),
        ("core.fsmonitor", str(program)),
        ("core.hooksPath", str(hooks)),
        ("core.pager", str(program)),
    ]:
        sh(clone, "config", key, value)
    (work / ".gitattributes").write_text("*.py diff=conv\n")
    t = tools(work)
    for command in [
        "git status",
        "git diff base",
        "git log -p -1",
        "git show HEAD",
        "git blame src/app.py",
        "git grep return",
        "git restore --source=base -- src/app.py",
    ]:
        assert t.call("git", {"command": command}).kind == "ok", command
    assert not marker.exists()


def test_the_worktrees_git_file_is_never_trusted(work: Path, tmp_path: Path):
    """A git directory inside the worktree would bring a config the session's code can
    write. The container sees `.git` read-only (crew#788); this holds without that."""
    evil = work / "evil"
    sh(tmp_path, "init", "-q", str(evil))
    (evil / ".git" / "config").write_text("[core]\n\tpager = touch never\n")
    (work / ".git").rename(work / "git-file")
    (work / ".git").write_text(f"gitdir: {evil / '.git'}\n")
    refused = tools(work).call("git", {"command": "git log"})
    assert refused.kind == "error" and "names a git directory inside it" in refused.text
    # Found before the change, the clone's git directory is the one that's used after it.
    (work / ".git").unlink()
    (work / "git-file").rename(work / ".git")
    t = tools(work)
    assert t.call("git", {"command": "git status"}).kind == "ok"
    (work / ".git").write_text(f"gitdir: {evil / '.git'}\n")
    log = t.call("git", {"command": "git log --oneline -1"})
    assert "checkpoint(1): f returns 2" in log.text


def test_a_session_records_its_git_calls_and_its_restores_as_edits(work: Path, seen):  # noqa: F811
    proxy = Proxy(
        said(("git", {"command": "git diff base --stat"})),
        said(("git", {"command": "git restore --source=base -- src/notes.md"})),
        said(("blocked", {"missing": "nothing", "kind": "fact"})),
    )
    s = conversation.session(work, "# Story\n\nf returns 2.", Fake(), CHECKS, send=proxy)
    s.run()
    assert "git" in [t["function"]["name"] for t in proxy.sent[0]["tools"]]
    finished = [e.detail for e in kinds(seen, EventKind.TOOL_FINISHED)]
    assert [(e["tool"], e["target"], e["result"]) for e in finished[:2]] == [
        ("git", "git diff base --stat", "ok"),
        ("git", "git restore --source=base -- src/notes.md", "ok"),
    ]
    turns = [e.detail for e in kinds(seen, EventKind.SESSION_TURN)]
    assert [t["since_action"] for t in turns] == [1, 0, 1]
    assert kinds(seen, EventKind.SESSION_ENDED)[0].detail["edits_applied"] == 1
