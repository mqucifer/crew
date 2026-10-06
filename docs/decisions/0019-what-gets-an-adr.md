# 19. What gets an ADR

- **Date:** 2026-10-06
- **Status:** Accepted

## Context

ADR 0001 gave an ADR to every decision the Sponsor makes or approves about the crew.
Taken literally, that is every answer the Sponsor gives, and on 2026-10-05 it produced
four ADRs in one day. Some recorded choices inside a single issue. The Sponsor,
2026-10-06: "Gonna end up with 10k ADRs from every message I send."

## Decision

Sponsor, 2026-10-06. This replaces the "What gets one" bullet of ADR 0001.

- **An ADR records a decision that changes how the crew works** across roles,
  steps or projects, or that would be costly to reverse. Examples: infra owns the
  deployed runtime (0017), the Product Owner's leeway within the Goal (0018).
- **A choice inside one issue isn't an ADR.** Which option meets a criterion, a
  retry count, a threshold or a wording goes in the issue and the PR that carry it.
- **Asking stays the same.** A decision that's the Sponsor's is asked in chat, and
  the answer is recorded where it belongs: an ADR when it meets the line above,
  otherwise the issue or the PR.

## Consequences

- Fewer, weightier ADRs. The log stays readable as the crew's design, not a chat history.
- When it's unclear which side of the line a decision falls, it's asked in the same
  question as the decision itself, not decided by writing an ADR.
- ADR 0015, a detail of the decisions rule, would not have met this line. It stays as
  it is: ADRs aren't rewritten.
