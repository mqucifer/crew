# 6. Generated code runs sandboxed

- **Date:** 2026-09-18
- **Status:** Accepted (the Developer's workspace container per story is decided and not built, ADR 0032)

## Context

Told that the crew would run generated tests on the Mac with only a worktree
and a timeout as bounds, the Sponsor chose containers at once: "That should be
a given for all projects honestly."

## Decision

- Code a model has just written, and nobody has vetted, runs in a container:
  no network during tests, capabilities dropped, a non-root user, a read-only
  root filesystem, the worktree's `.git` read-only, and memory and PID limits
  (`tools/sandbox.py`).
- **Each command is its own container,** and only the worktree carries over. Each
  worktree has its own home directory inside it, ignored by git and removed with the
  worktree, so what the dependency step installs is there for the checks in any
  language. Downloads are shared through one cache mount (ADR 0030).
- **The Developer's session will hold one container per story** (the Sponsor,
  2026-10-17, crew#778 decision 4b; ADR 0032). This is decided and not built. The
  project defines it on the Development Container specification (`devcontainer.json`),
  the one source of its image and setup (the Sponsor, 2026-10-18). The crew reads only
  the image, the setup command and the environment from it, and always applies the
  hardening above, so nothing in the file can loosen the container: `runArgs`,
  `mounts`, `privileged`, features and host commands are not honoured. The worktree is
  mounted, setup runs once with network, and then the session runs
  with none, under the hardening above. It's removed at the session's end and
  cleaned up after a crash. It changes only the Developer's session. Until it's
  built, each command is its own container.
- **Fail loudly:** no container engine means the check fails. There is never a
  silent fallback to the host.
- **Refined 2026-09-26:** released, reviewed software the crew built (such as
  sprint-metrics) is ordinary software and runs like any other service. It is
  still containerised where possible, but as normal deployment.

## Consequences

- Dependency resolution is the only sandboxed step with network access.
- Host execution would need an explicit, named opt-in. None exists today.

## Changelog

- 2026-10-13: each worktree has its own home inside it. What setup installed under the
  container's home was gone before the checks ran, so a project with its own image
  couldn't keep its dependencies (crew#690, ADR 0030; Sponsor).
- 2026-10-17: decided, not built: the Developer's session runs in one workspace
  container held per story, defined by the project, with network only for setup
  (ADR 0032, decision 4b; the Sponsor, on crew#778).
- 2026-10-18: decided, not built: `devcontainer.json` is the workspace's one source.
  The crew reads only its image, setup command and environment, and always applies
  the hardening above (ADR 0032; the Sponsor, on crew#778).
- 2026-10-18: the worktree's `.git` is mounted read-only. Code run inside could
  otherwise write a hook or a config that git runs on the host at the crew's next
  commit (crew#788's first hard call, crew#778).
