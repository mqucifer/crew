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
  root filesystem, and memory and PID limits (`tools/sandbox.py`).
- **Fail loudly:** no container engine means the check fails. There is never a
  silent fallback to the host.
- **Refined 2026-09-26:** released, reviewed software the crew built (such as
  sprint-metrics) is ordinary software and runs like any other service. It is
  still containerised where possible, but as normal deployment.

## Consequences

- Dependency resolution is the only sandboxed step with network access.
- Host execution would need an explicit, named opt-in. None exists today.
