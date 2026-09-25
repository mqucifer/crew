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

import base64
import contextlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

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

# git waits forever on a prompt; fail instead.
NO_PROMPT = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GCM_INTERACTIVE": "never"}


class GitError(RuntimeError):
    pass


class MergeConflict(GitError):
    """Two histories that change the same lines. Aborted, never forced."""

    verb = "merge"

    def __init__(self, files: list[str]) -> None:
        self.files = files
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


def _run(args: list[str], *, cwd: Path | None = None, token: str | None = None) -> str:
    """Run git, with credentials supplied per-command.

    The token is passed with -c http.extraheader rather than embedded in the
    remote URL, so it is never written into .git/config where it would outlive
    its hour and end up in a backup.
    """
    import os  # noqa: PLC0415

    command = ["git"]
    if token:
        command += ["-c", f"http.extraheader={_auth_header(token)}"]
    command += args

    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, **NO_PROMPT},
    )
    if result.returncode != 0:
        # Never echo the command: it carries the credential.
        raise GitError(f"git {args[0]} failed: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


class Workspace:
    """An isolated worktree for one card.

    Each developer agent works in its own worktree so concurrent cards cannot
    see or stomp each other's changes.
    """

    def __init__(self, owner: str, repo: str, token: str, identity: BotIdentity) -> None:
        self.owner = owner
        self.repo = repo
        self.token = token
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
        return Workspace(self.owner, repo, self.token, self.identity)

    @property
    def url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}.git"

    def _ensure_clone(self) -> None:
        if (self.clone / ".git").exists():
            _run(["fetch", "origin", "--prune"], cwd=self.clone, token=self.token)
            return
        self.clone.parent.mkdir(parents=True, exist_ok=True)
        _run(["clone", self.url, str(self.clone)], token=self.token)

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

    def open(self, branch: str, *, resume: bool = False) -> Path:
        """Create a worktree on `branch`.

        Off `origin/HEAD` by default: a story being delivered for the first
        time starts from current `main` and nothing else.

        `resume` starts from `origin/<branch>` instead when that branch holds
        work `main` does not, so a repair builds on the attempt it is repairing
        rather than silently discarding it. Whether it actually resumed is on
        `self.resumed` — the caller has to tell the Developer which world it is
        in, because a prompt describing files that are not there is worse than
        one describing none.
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

        self.resumed = bool(resume) and self.unfinished_work_on(branch)
        base = f"origin/{branch}" if self.resumed else "origin/HEAD"
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
