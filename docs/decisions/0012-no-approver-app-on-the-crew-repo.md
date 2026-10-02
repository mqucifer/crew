# 12. No approver App on the crew repo

- **Date:** 2026-09-30
- **Status:** Accepted

## Context

Claude opens PRs through `gh` as the Sponsor, so the Sponsor is the author and
branch protection won't let them approve. In delivery repos, the crew's
approver App (`crew review --repo <name>`) approves them. The crew repo also
requires a review.

## Decision

The approver App gets no access to the crew repo. The Sponsor merges crew PRs
themselves.

## Consequences

- `crew review --repo crew` gets a 403; it isn't run, and App access isn't
  proposed again.
- An admin merge past protection is never suggested as the default.
