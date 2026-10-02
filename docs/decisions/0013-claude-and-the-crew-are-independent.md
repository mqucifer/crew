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
- **Technical choice (Claude, costly to reverse):** escalations run
  `claude -p --safe-mode`, so no CLAUDE.md, hook or skill from the repo reaches
  the crew. `--bare` would do the same but authenticates only by API key,
  which 0004 forbids.

## Consequences

- Either set of rules can change without the other.
- Escalations also don't see a delivery repo's own CLAUDE.md. Giving a project's
  instructions to escalations would need a new ADR.
- Proven live on 2026-10-02: a normal `claude -p` answered from a canary
  CLAUDE.md, and the safe-mode run didn't see it (crew#460).
