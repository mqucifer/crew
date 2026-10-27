"""Git operations available to agents.

Every write an agent makes to a repository passes through here, which makes this
the one place the "never push to main" rail can be enforced in code.

Branch protection on GitHub is the outer rail: required review, required
`tests` check, no force-push, no deletion. This guard is the inner one. It
matters because protection only rejects a push *after* an agent has decided to
make it — the failure then arrives as a confusing tool error mid-task, rather
than as a clear refusal at the point of the mistake.

It refuses rather than warns, and has deliberately no override flag.
"""

from __future__ import annotations

import ast
import base64
import contextlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from crew_org.tokens import Token, current

# Branches an agent may never write to directly, under any circumstances.
PROTECTED_BRANCHES = frozenset({"main", "master", "trunk", "release", "develop"})

# `revert/<pr>-<summary>` is numbered for the pull request it undoes, not an
# issue: a revert has no card of its own, and the PR is what it is about.
BRANCH_TYPES = frozenset({"feat", "fix", "chore", "docs", "test", "refactor", "spike", "revert"})

# <type>/<issue>-<kebab-summary>, per the constitution §8.
BRANCH_PATTERN = re.compile(
    r"^(?P<type>[a-z]+)/(?P<issue>\d+)-(?P<summary>[a-z0-9]+(?:-[a-z0-9]+)*)$"
)


class ProtectedBranchError(RuntimeError):
    """Raised when an agent tries to write to a protected branch."""


class BranchNameError(ValueError):
    """Raised when a branch name does not follow the convention."""


def assert_writable(branch: str) -> None:
    """Refuse a write to a protected branch.

    Raises rather than returning a flag: a caller cannot ignore this by
    accident, and there is deliberately no force parameter.
    """
    if branch.strip().lower() in PROTECTED_BRANCHES:
        raise ProtectedBranchError(
            f"{branch!r} is protected. Agents never push to it — open a pull request "
            "from a branch named for the issue instead. See ways-of-working.md §8."
        )


def branch_name(issue: int, summary: str, *, kind: str = "feat") -> str:
    """Build a conventional branch name for an issue."""
    if kind not in BRANCH_TYPES:
        raise BranchNameError(f"{kind!r} is not one of: {', '.join(sorted(BRANCH_TYPES))}")
    if issue <= 0:
        raise BranchNameError(f"issue number must be positive, got {issue}")

    slug = re.sub(r"[^a-z0-9]+", "-", summary.lower()).strip("-")
    if not slug:
        raise BranchNameError(f"summary {summary!r} produced an empty slug")

    name = f"{kind}/{issue}-{slug}"
    assert_writable(name)  # a summary can't smuggle a protected name through
    return name


def kept_name(issue: int, at: datetime) -> str:
    """The branch a delivery's work is kept on: `crew/kept/<issue>-<yyyymmdd-hhmm>`, UTC."""
    return f"{KEPT}{issue}-{at.astimezone(UTC):%Y%m%d-%H%M}"


def validate_branch_name(branch: str) -> None:
    """Check a branch follows the convention, and is not protected."""
    assert_writable(branch)
    match = BRANCH_PATTERN.match(branch)
    if not match:
        raise BranchNameError(
            f"{branch!r} does not match <type>/<issue>-<summary>, e.g. 'feat/42-cycle-time'."
        )
    if match.group("type") not in BRANCH_TYPES:
        raise BranchNameError(
            f"{match.group('type')!r} is not one of: {', '.join(sorted(BRANCH_TYPES))}"
        )


# --- working copies ------------------------------------------------------

# Absolute, because git subcommands run with cwd set to the clone: a relative
# worktree path would be created inside the clone rather than beside it.
WORK_ROOT = Path("var").resolve()
CLONES = WORK_ROOT / "repos"
WORKTREES = WORK_ROOT / "worktrees"

# How a session's checkpoint commit starts (crew#778, P3): `checkpoint(<card>): ...`.
CHECKPOINT = "checkpoint("
# The trailer on the commit a session makes at `done`: a checkpoint too, so a delivery
# whose gates, push or pull request didn't finish resumes from it.
DONE_TRAILER = "Crew-Checkpoint: done"
# Where a delivery that leaves for a rewrite keeps its work (crew#778): a branch of
# its own, never the story's or a pull request's, so no review's head moves.
KEPT = "crew/kept/"

# git waits forever on a prompt; fail instead.
NO_PROMPT = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GCM_INTERACTIVE": "never"}


class GitError(RuntimeError):
    pass


class MergeConflict(GitError):
    """Two histories that change the same lines. Aborted, never forced."""

    verb = "merge"

    def __init__(self, files: list[str]) -> None:
        self.files = files
        # The subject of the commit that landed in these files first, when known (#436).
        self.landed = ""
        super().__init__(f"{self.verb} conflicts in: " + (", ".join(files) or "unknown files"))


class RevertConflict(MergeConflict):
    """A revert that does not apply cleanly. It was aborted, never forced."""

    verb = "revert"


@dataclass(frozen=True)
class BotIdentity:
    """Who the crew's commits are authored by.

    Commit authorship comes from git config, not from the token, so it must be
    set explicitly or the work is attributed to whoever runs the tick.
    """

    login: str
    user_id: int

    @property
    def name(self) -> str:
        return self.login

    @property
    def email(self) -> str:
        return f"{self.user_id}+{self.login}@users.noreply.github.com"

    @property
    def author(self) -> str:
        return f"{self.name} <{self.email}>"


def _auth_header(token: str) -> str:
    """Basic auth for a GitHub App installation token."""
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return f"Authorization: Basic {encoded}"


def _auth_env(token: str) -> dict[str, str]:
    """The token as git config in git's environment: `http.extraheader`, after any set.

    Not in argv, where `ps` shows it to every user of the machine (crew#778): a
    process's environment is readable only by its own user and root. Not in the
    remote URL either, where it would be written into .git/config and outlive its
    hour in a backup. And not through a credential helper: git on a Mac names
    osxkeychain in its own config, and a successful push would store the token there.
    `GIT_CONFIG_COUNT` needs git 2.31 or later.
    """
    import os  # noqa: PLC0415

    n = int(os.environ.get("GIT_CONFIG_COUNT") or 0)
    return {
        "GIT_CONFIG_COUNT": str(n + 1),
        f"GIT_CONFIG_KEY_{n}": "http.extraheader",
        f"GIT_CONFIG_VALUE_{n}": _auth_header(token),
    }


def _run(args: list[str], *, cwd: Path | None = None, token: str | None = None) -> str:
    """Run git, with credentials supplied per-command, in its environment (`_auth_env`)."""
    import os  # noqa: PLC0415

    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, **NO_PROMPT, **(_auth_env(token) if token else {})},
    )
    if result.returncode != 0:
        # git's own words only: never the environment, which carries the credential.
        raise GitError(f"git {args[0]} failed: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


# What the crew never commits in any project, whatever its `.gitignore` says:
# crew-presentation's first story committed `.DS_Store` and `.pyc` files, with no
# `.gitignore` yet (crew#690, ADR 0030). A clone's `info/exclude` holds for every
# worktree made from it.
MACHINE_FILES = (".DS_Store", "__pycache__/", "*.pyc", ".crew-home/")
_EXCLUDE_MARKER = "# crew: never committed (ADR 0030)"


def exclude_machine_files(clone: Path) -> None:
    """Add `MACHINE_FILES` to the clone's `info/exclude`, once."""
    path = clone / ".git" / "info" / "exclude"
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if _EXCLUDE_MARKER in current:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = "\n".join([_EXCLUDE_MARKER, *MACHINE_FILES])
    path.write_text(
        current + ("\n" if current and not current.endswith("\n") else "") + lines + "\n"
    )


class Workspace:
    """An isolated worktree for one card.

    Each developer agent works in its own worktree so concurrent cards cannot
    see or stomp each other's changes.
    """

    def __init__(self, owner: str, repo: str, token: Token, identity: BotIdentity) -> None:
        self.owner = owner
        self.repo = repo
        self._token = token
        self.identity = identity
        self.clone = CLONES / repo
        self.path: Path | None = None
        self.branch: str | None = None
        # Whether the open worktree carries a previous attempt's work. Read by
        # delivery to decide what the Developer is told about its own files.
        self.resumed = False

    def for_repo(self, repo: str) -> Workspace:
        """A workspace for another repository in the same organization.

        Returns self when the repository already matches, so the common case
        costs nothing and callers need not check.
        """
        if repo == self.repo:
            return self
        return Workspace(self.owner, repo, self._token, self.identity)

    @property
    def token(self) -> str:
        """The token as it is now: resolved at each git operation (#182)."""
        return current(self._token)

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}.git"

    def _ensure_clone(self) -> None:
        if (self.clone / ".git").exists():
            _run(["fetch", "origin", "--prune"], cwd=self.clone, token=self.token)
        else:
            self.clone.parent.mkdir(parents=True, exist_ok=True)
            _run(["clone", self.url, str(self.clone)], token=self.token)
        exclude_machine_files(self.clone)

    def _default_branch(self) -> str:
        """The repo's default branch, asked rather than assumed."""
        try:
            ref = _run(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=self.clone)
            return ref.rsplit("/", 1)[-1]
        except GitError:
            _run(["remote", "set-head", "origin", "--auto"], cwd=self.clone, token=self.token)
            ref = _run(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=self.clone)
            return ref.rsplit("/", 1)[-1]

    def current(self) -> Path:
        """The repository at its default branch, for reading.

        Refinement has no branch of its own — it is deciding what work should
        exist, not doing it — so it reads the clone directly rather than paying
        for a worktree it would only throw away.
        """
        self._ensure_clone()
        default = self._default_branch()
        _run(["checkout", "--force", f"origin/{default}"], cwd=self.clone, token=self.token)
        return self.clone

    def at_main(self) -> Path:
        """A worktree of the default branch as it is now, for refinement's sessions to read.

        One per repository, shared by the tick's refinement steps (the Sponsor, crew#807).
        A worktree, not the clone: the session's `git` runs on the git directory a
        worktree's `.git` names (`session.git.git_dir`). Nothing writes to it, and each
        call puts it back at the default branch's tip.
        """
        self._ensure_clone()
        tip = f"origin/{self._default_branch()}"
        path = (WORKTREES / f"main__{self.repo}").resolve()
        _run(["worktree", "prune"], cwd=self.clone)
        if (path / ".git").is_file():
            _run(["checkout", "--detach", "--force", tip], cwd=path)
            _run(["clean", "-fdx", "--quiet"], cwd=path)
        else:
            if path.exists():
                shutil.rmtree(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            _run(["worktree", "add", "--detach", str(path), tip], cwd=self.clone)
        return path

    def unfinished_work_on(self, branch: str) -> bool:
        """Does `origin/<branch>` hold work that `origin/HEAD` does not?

        The question a re-delivery has to ask before it resets anything. A
        story returned by a gate still has its previous attempt on the remote
        branch, and `open` used to overwrite that branch from `origin/HEAD` —
        so the accepted implementation the verdict was *about* vanished, while
        the verdict describing it was still handed to the Developer.
        sprint-metrics #31 spent an attempt trying to edit a function that had
        been deleted out from under it.

        False once the branch has merged: its commits are in `origin/HEAD` and
        resuming onto them would re-apply work that already landed.
        """
        self._ensure_clone()
        try:
            _run(["rev-parse", "--verify", f"origin/{branch}"], cwd=self.clone)
        except GitError:
            return False
        try:
            _run(
                ["merge-base", "--is-ancestor", f"origin/{branch}", "origin/HEAD"],
                cwd=self.clone,
            )
        except GitError:
            return True
        return False

    def checkpoint_on(self, branch: str) -> str | None:
        """The message of the checkpoint the clone's `branch` ends in, or None.

        A session commits a checkpoint after each green run and at `done` (crew#778,
        P3). The clone's branch outlives the worktree, so a crashed or stopped tick
        leaves it for the next one to resume. A delivery that ends forgets it, so one
        still here is unfinished, even if `done`'s was pushed and its pull request
        failed. None when the branch ends in anything else, or has merged.
        """
        self._ensure_clone()
        try:
            tip = _run(["rev-parse", "--verify", f"refs/heads/{branch}"], cwd=self.clone)
        except GitError:
            return None
        message = _run(["log", "-1", "--format=%B", tip], cwd=self.clone)
        if not (message.startswith(CHECKPOINT) or DONE_TRAILER in message):
            return None
        try:
            _run(["merge-base", "--is-ancestor", tip, "origin/HEAD"], cwd=self.clone)
        except GitError:
            return message
        return None

    def keep(self, name: str, *, base: str, message: str) -> str | None:
        """Push the open worktree, as it stands, to a new branch `name`; its commit.

        Edits since the worktree's last commit go in one more commit, `message`, made
        on `name` alone: the worktree's own branch stays at its last checkpoint. A
        plain push of a new name, never forced. None when the work is no different
        from `base`, and nothing is pushed. The clone's copy of `name` goes once the
        push is made; a push that fails leaves it there, with the work.
        """
        if self.path is None:
            raise GitError("no worktree open")
        assert_writable(name)
        _run(["add", "-A"], cwd=self.path)
        tree = _run(["write-tree"], cwd=self.path)
        if tree == _run(["rev-parse", f"{base}^{{tree}}"], cwd=self.path):
            return None
        sha = _run(["rev-parse", "HEAD"], cwd=self.path)
        if tree != _run(["rev-parse", "HEAD^{tree}"], cwd=self.path):
            sha = _run(
                [
                    "-c",
                    f"user.name={self.identity.name}",
                    "-c",
                    f"user.email={self.identity.email}",
                    "commit-tree",
                    tree,
                    "-p",
                    sha,
                    "-m",
                    message,
                ],
                cwd=self.path,
            )
        _run(["branch", name, sha], cwd=self.path)
        _run(["push", "origin", f"refs/heads/{name}"], cwd=self.path, token=self.token)
        with contextlib.suppress(GitError):
            _run(["branch", "-D", name], cwd=self.path)
        return sha

    def kept_for(self, issue: int) -> tuple[str, str] | None:
        """The newest `crew/kept/<issue>-<when>` on the remote, as (branch, tip), or None.

        Newest by the name's UTC stamp, which sorts as text. One already in the default
        branch is passed over: resuming it would re-apply work that landed (crew#839).
        """
        self._ensure_clone()
        listed = _run(
            [
                "for-each-ref",
                "--sort=-refname",
                "--format=%(refname:strip=3) %(objectname)",
                f"refs/remotes/origin/{KEPT}{issue}-*",
            ],
            cwd=self.clone,
        )
        for line in listed.splitlines():
            name, sha = line.split()
            try:
                _run(["merge-base", "--is-ancestor", sha, "origin/HEAD"], cwd=self.clone)
            except GitError:
                return name, sha
        return None

    def forget(self, branch: str) -> None:
        """Drop the clone's own copy of `branch`, and any checkpoint on it, once closed."""
        with contextlib.suppress(GitError):
            _run(["branch", "-D", branch], cwd=self.clone)

    def open(
        self,
        branch: str,
        *,
        resume: bool = False,
        checkpoint: bool = False,
        kept: str | None = None,
    ) -> Path:
        """Create a worktree on `branch`.

        Off `origin/HEAD` by default: a story being delivered for the first
        time starts from current `main` and nothing else.

        `resume` starts from `origin/<branch>` instead when that branch holds
        work `main` does not, so a repair builds on the attempt it is repairing
        rather than silently discarding it. Whether it actually resumed is on
        `self.resumed` — the caller has to tell the Developer which world it is
        in, because a prompt describing files that are not there is worse than
        one describing none.

        `checkpoint` starts from the clone's own `branch`, which `checkpoint_on`
        found ending in a session's checkpoint.

        `kept` starts from that commit, a kept branch's tip (`kept_for`), when the
        story's work left for a rewrite and comes back to be resumed (crew#839).
        """
        assert_writable(branch)
        validate_branch_name(branch)
        self._ensure_clone()

        path = (WORKTREES / branch.replace("/", "__")).resolve()
        if path.exists():
            self.close(path)

        # Git records worktrees in the clone, and that record outlives the
        # directory: a tree deleted from the filesystem is still "checked out"
        # as far as git is concerned, and the branch cannot be claimed again.
        # Prune reconciles the registry with what is actually on disk.
        _run(["worktree", "prune"], cwd=self.clone)
        path.parent.mkdir(parents=True, exist_ok=True)

        self.resumed = checkpoint or (bool(resume) and self.unfinished_work_on(branch))
        base = branch if checkpoint else f"origin/{branch}" if self.resumed else "origin/HEAD"
        if kept and not self.resumed:
            self.resumed, base = True, kept
        _run(
            ["worktree", "add", "-B", branch, str(path), base],
            cwd=self.clone,
            token=self.token,
        )
        self.path, self.branch = path, branch
        return path

    def open_existing(self, branch: str) -> Path:
        """Check out a branch that already exists on the remote, to inspect it."""
        self._ensure_clone()
        _run(["worktree", "prune"], cwd=self.clone)

        path = (WORKTREES / f"review__{branch.replace('/', '__')}").resolve()
        if path.exists():
            self.close(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _run(
            ["worktree", "add", "--detach", str(path), f"origin/{branch}"],
            cwd=self.clone,
            token=self.token,
        )
        self.path, self.branch = path, branch
        return path

    def head(self) -> str:
        """The commit the open worktree is on.

        What a verdict is *about*. A verdict that outlives the code it judged is
        worse than no verdict, and one that is thrown away every time the code
        is unchanged wastes a model call on an answer nobody's disagreed with.
        """
        if self.path is None:
            raise GitError("no worktree is open")
        return _run(["rev-parse", "HEAD"], cwd=self.path)

    def commit(self, message: str, *, allow_empty: bool = False) -> bool:
        """Commit everything in the worktree. False if there was nothing to commit.

        `allow_empty` commits even so: an answer to a review that needs no change
        still has to move the pull request's head, or the review it answers keeps
        applying to it (#161).
        """
        if self.path is None:
            raise GitError("no worktree open")
        _run(["add", "-A"], cwd=self.path)
        if not allow_empty and not _run(["status", "--porcelain"], cwd=self.path):
            return False
        _run(
            [
                "-c",
                f"user.name={self.identity.name}",
                "-c",
                f"user.email={self.identity.email}",
                "commit",
                *(["--allow-empty"] if allow_empty else []),
                "-m",
                message,
                "--author",
                self.identity.author,
            ],
            cwd=self.path,
        )
        return True

    def catch_up(self) -> bool:
        """Bring the open worktree up to date with the default branch.

        A resumed branch was cut from `main` as it was then. Work that has
        landed since, often the very story this one depends on, is missing
        from it (#119). Merged in, not rebased: the history the reviewer read
        stays as it was, so the push that follows is a fast-forward.

        True if `main` had anything new. A conflict aborts the merge, leaves
        the worktree as it was, and raises with the conflicting paths.
        """
        if self.path is None:
            raise GitError("no worktree open")
        before = _run(["rev-parse", "HEAD"], cwd=self.path)
        identity = [
            "-c",
            f"user.name={self.identity.name}",
            "-c",
            f"user.email={self.identity.email}",
        ]
        try:
            _run([*identity, "merge", "--no-edit", "origin/HEAD"], cwd=self.path)
        except GitError:
            files = _run(["diff", "--name-only", "--diff-filter=U"], cwd=self.path).split()
            with contextlib.suppress(GitError):
                _run(["merge", "--abort"], cwd=self.path)
            raise MergeConflict(files) from None
        return _run(["rev-parse", "HEAD"], cwd=self.path) != before

    def catch_up_keeping_both(self) -> list[str]:
        """Bring the open worktree up to date, keeping both sides of a pure addition.

        Approved stories in different epics add to the same files: sprint-metrics
        #380, #381, #382 and #384 each appended tests to one file, and #384 was
        rebuilt from scratch twice, then blocked, for a conflict between two
        additions (#436). Where both sides only added lines at the same place, a
        person keeps both. Anything else, a line both sides had and one changed,
        aborts and raises with the paths, exactly as `catch_up` does.

        Returns the files where both additions were kept. Empty if `main`
        merged cleanly.
        """
        if self.path is None:
            raise GitError("no worktree open")
        identity = [
            "-c",
            f"user.name={self.identity.name}",
            "-c",
            f"user.email={self.identity.email}",
        ]

        def give_up(files: list[str]) -> MergeConflict:
            with contextlib.suppress(GitError):
                _run(["merge", "--abort"], cwd=self.path)
            return MergeConflict(files)

        try:
            _run(
                [*identity, "-c", "merge.conflictStyle=diff3", "merge", "--no-edit", "origin/HEAD"],
                cwd=self.path,
            )
            return []
        except GitError:
            files = _run(["diff", "--name-only", "--diff-filter=U"], cwd=self.path).split()
        if not files:
            raise give_up(files)
        for name in files:
            target = self.path / name
            try:
                text = target.read_text()
            except (OSError, UnicodeDecodeError):
                raise give_up(files) from None
            kept = keep_both_additions(text)
            if kept is None or (name.endswith(".py") and not _still_whole(kept)):
                raise give_up(files)
            target.write_text(kept)
            _run(["add", name], cwd=self.path)
        _run([*identity, "commit", "--no-edit"], cwd=self.path)
        return files

    def merge_leaving_conflicts(self) -> list[str]:
        """Merge the default branch into the open worktree, its conflicts left in the files.

        For a session that resolves them (crew#933): each conflict in diff3 style, so it
        sees the branch's side, the base both started from, and main's. Nothing is
        committed; the session's `done` commits the merge. Empty when main merged
        cleanly, which is committed as `catch_up` would. A merge that fails with
        nothing conflicted is aborted and raised.
        """
        if self.path is None:
            raise GitError("no worktree open")
        identity = [
            "-c",
            f"user.name={self.identity.name}",
            "-c",
            f"user.email={self.identity.email}",
        ]
        try:
            _run(
                [*identity, "-c", "merge.conflictStyle=diff3", "merge", "--no-edit", "origin/HEAD"],
                cwd=self.path,
            )
            return []
        except GitError:
            files = _run(["diff", "--name-only", "--diff-filter=U"], cwd=self.path).split()
        if not files:
            self.abort_merge()
            raise MergeConflict(files)
        return files

    def abort_merge(self) -> None:
        """Abandon a merge in progress in the open worktree, leaving it as it was."""
        if self.path is not None:
            with contextlib.suppress(GitError):
                _run(["merge", "--abort"], cwd=self.path)

    def revert(self, sha: str) -> None:
        """Commit the inverse of `sha` onto the open worktree.

        A merge commit is reverted against its first parent, the branch it was
        merged into; a squash commit has one parent and needs no mainline.

        A conflict means the code has moved on since `sha` landed, and what the
        right end state is becomes a person's decision. So the revert is
        aborted, the worktree left clean, and the conflicting paths raised.
        """
        if self.path is None:
            raise GitError("no worktree open")
        parents = _run(["rev-list", "--parents", "-n", "1", sha], cwd=self.path).split()[1:]
        mainline = ["-m", "1"] if len(parents) > 1 else []
        identity = [
            "-c",
            f"user.name={self.identity.name}",
            "-c",
            f"user.email={self.identity.email}",
        ]
        try:
            _run([*identity, "revert", "--no-edit", *mainline, sha], cwd=self.path)
        except GitError:
            files = _run(["diff", "--name-only", "--diff-filter=U"], cwd=self.path).split()
            with contextlib.suppress(GitError):
                _run(["revert", "--abort"], cwd=self.path)
            raise RevertConflict(files) from None

    def diff(self) -> str:
        """The change as a patch, without committing it.

        Staging is how untracked files become visible to diff; nothing is
        committed, so a dry run leaves the worktree exactly as it found it.
        """
        if self.path is None:
            raise GitError("no worktree open")
        _run(["add", "-A"], cwd=self.path)
        return _run(["diff", "--cached"], cwd=self.path)

    def diff_if_open(self) -> str | None:
        """The patch, or None when there is no worktree left to read.

        Used to keep the evidence from a card that failed, where the caller
        cannot know whether a worktree was ever opened — and where raising
        would replace the failure being recorded with a different one.
        """
        if self.path is None:
            return None
        try:
            return self.diff()
        except GitError:
            return None

    def push(self, *, force: bool = False) -> None:
        """Publish the branch.

        `force` uses `--force-with-lease`, which is what a re-delivery needs and
        nothing else does. `open()` resets the branch to `origin/HEAD`, so when
        a story is delivered a second time its local history no longer descends
        from whatever is on the remote from the first attempt — and the push is
        rejected as a non-fast-forward. Story #31 burned two repair attempts and
        twelve minutes before hitting exactly that.

        The lease is the safety: the push fails if the remote moved since the
        last fetch, so this overwrites the crew's own stale attempt and never
        someone else's work. The caller decides — it is the one that knows
        whether a pull request is open on the branch.
        """
        if self.path is None or self.branch is None:
            raise GitError("no worktree open")
        assert_writable(self.branch)
        args = (
            ["push", "--force-with-lease", "-u", "origin", self.branch]
            if force
            else ["push", "-u", "origin", self.branch]
        )
        _run(args, cwd=self.path, token=self.token)

    def close(self, path: Path | None = None) -> None:
        """Remove the worktree. The branch and its commits survive on the remote."""
        target = path or self.path
        if target is None:
            return
        try:
            _run(["worktree", "remove", "--force", str(target)], cwd=self.clone)
        except GitError:
            shutil.rmtree(target, ignore_errors=True)
        if target == self.path:
            self.path = None

    def __enter__(self) -> Workspace:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


# A conflict in diff3 style: the branch's side, the common base, then main's (#436).
_CONFLICT = re.compile(
    r"^<<<<<<< [^\n]*\n(.*?)^\|\|\|\|\|\|\| [^\n]*\n(.*?)^=======\n(.*?)^>>>>>>> [^\n]*\n",
    re.MULTILINE | re.DOTALL,
)


def last_landed(path: Path, files: list[str]) -> str:
    """The subject of the last commit on the default branch that touched these files.

    A squash merge's subject names its pull request, so this says which story
    landed first and caused the conflict (#436). Empty if it can't be read.
    """
    if not files:
        return ""
    try:
        return _run(["log", "-1", "--format=%s", "origin/HEAD", "--", *files], cwd=path).strip()
    except GitError:
        return ""


def keep_both_additions(text: str) -> str | None:
    """Every conflict in `text` where both sides only added lines, resolved by keeping both.

    Main's lines come first, then the branch's, as if the branch's commit had
    been written after main's. None if any conflict changes a line the two
    sides shared: that one needs judgement, not concatenation.
    """
    unresolved = False

    def resolve(found: re.Match) -> str:
        nonlocal unresolved
        branch, base, main = found.group(1), found.group(2), found.group(3)
        if base.strip():
            unresolved = True
            return found.group(0)
        return main + branch

    resolved = _CONFLICT.sub(resolve, text)
    if unresolved or "<<<<<<< " in resolved or "\n>>>>>>> " in resolved:
        return None
    return resolved


def conflicts_in(text: str) -> list[tuple[str, str, str]]:
    """Each diff3 conflict in `text`, as (the branch's side, their base, main's side)."""
    return [(m.group(1), m.group(2), m.group(3)) for m in _CONFLICT.finditer(text)]


def beyond_the_merge(path: Path, approved: str, main: str = "origin/HEAD") -> list[str]:
    """The files HEAD has otherwise than git's own merge of `approved` and `main` would.

    What a resolution changed (crew#933): every file it resolved, and any other it
    touched, which git alone would have merged. git's merge leaves the conflicted
    files with their markers, so each one resolved is among them.
    """
    import os  # noqa: PLC0415

    merged = subprocess.run(
        ["git", "merge-tree", "--write-tree", "--no-messages", approved, main],
        cwd=path,
        capture_output=True,
        text=True,
        env={**os.environ, **NO_PROMPT},
    )
    # 1 is a merge with conflicts, which is the point; anything else is git failing.
    if merged.returncode not in (0, 1) or not merged.stdout.strip():
        raise GitError(f"git merge-tree failed: {merged.stderr.strip()[:300]}")
    tree = merged.stdout.split("\n", 1)[0].strip()
    return _run(["diff", "--name-only", tree, "HEAD"], cwd=path).split()


# A line only a conflict leaves: its opening, its base's or its closing marker.
_MARKER = re.compile(r"^(<<<<<<<|\|\|\|\|\|\|\||>>>>>>>)( |$)", re.MULTILINE)


def unresolved(root: Path, files: list[str]) -> list[str]:
    """Those of `files` under `root` that still hold a conflict's markers (crew#933).

    `=======` alone isn't one: it underlines a heading in Markdown.
    """
    left = []
    for name in files:
        try:
            text = (root / name).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if _MARKER.search(text):
            left.append(name)
    return left


def _still_whole(source: str) -> bool:
    """Python that parses and defines nothing twice at the top level.

    Two sides adding a test of the same name would leave pytest running only
    the second, silently. That is a loss, not a merge.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    names = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    return len(names) == len(set(names))
