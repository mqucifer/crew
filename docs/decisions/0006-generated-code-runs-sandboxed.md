# 6. Generated code runs sandboxed

- **Date:** 2026-09-18
- **Status:** Accepted

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
- 2026-10-18: the worktree's `.git` is mounted read-only. Code run inside could
  otherwise write a hook or a config that git runs on the host at the crew's next
  commit (crew#788's first hard call, crew#778).
