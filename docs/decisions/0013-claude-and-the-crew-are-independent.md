# 13. Claude's standards and the crew's are independent

- **Date:** 2026-10-02
- **Status:** Accepted

## Context

Claude develops the crew repo, and the crew's agents follow
`docs/ways-of-working.md`. A first plan held Claude to the crew's rules. The
Sponsor rejected it: "Claude and the crew are completely different. We can
model the crew after our standards but they need to be independent so we can
change the crew as needs change."

## Decision

- Claude's standards live in `CLAUDE.md`, which never refers to the crew's
  rules. The crew's rules never refer to it.
- Influence runs one way: the crew may be modelled on Claude's standards, by a
  deliberate change through a PR.
- The crew's agents call the model through the crew's own call layer (ADR 0025),
  never Claude Code, so no CLAUDE.md, hook or skill from the repo reaches them.

## Consequences

- Either set of rules can change without the other.
- The crew's agents don't see a delivery repo's own CLAUDE.md either.
- While escalation ran Claude Code, it ran `claude -p --safe-mode` for the same
  reason, proven live on 2026-10-02: a normal `claude -p` answered from a canary
  CLAUDE.md, and the safe-mode run didn't see it (crew#460).

## Changelog

- **2026-10-17:** escalation retires when the Developer's session is built (ADR 0032,
  decision 9; the Sponsor, on crew#778). Decided, not built. The `--safe-mode` choice
  stands until then, and then has nothing to apply to. The two sets of rules stay
  independent.
- **2026-10-18:** escalation is deleted (crew#778, Stage E), and the `--safe-mode`
  choice with it. Nothing in the crew runs Claude Code.
