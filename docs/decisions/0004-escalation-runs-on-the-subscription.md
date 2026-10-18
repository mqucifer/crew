# 4. Escalation runs on the subscription, never an API key

- **Date:** 2026-09-18
- **Status:** Accepted. Escalation retires when the Developer's session is built
  (ADR 0032, decision 9); until then it runs as below.

## Context

The crew escalates hard work to Claude. The Sponsor: "I will not wake up to a
surprise Claude bill." An API key can't be capped to a subscription. The
Anthropic API and Claude subscriptions are separate billing systems, and an
API key is metered pay-as-you-go.

## Decision

- Escalation runs headless Claude Code (`claude -p`) on the Sponsor's
  subscription OAuth. A usage limit stops work; it never bills.
- `ANTHROPIC_API_KEY` is never set anywhere in the project.
- A metered API path would need a new ADR, with prepaid credits, auto-reload
  off and a Console spend limit.
- **Escalation retires with the Developer's session** (the Sponsor, 2026-10-17,
  crew#778 decision 9; ADR 0032). This is decided and not built. A stuck session
  goes back to the Business Analyst instead, and `tools/claude_code.py` and the
  escalation budget leave in the build's Stage E. Decision 9 doesn't change the
  rule against an API key.

## Consequences

- A rate limit is an ordinary outcome: the card is parked and retried, not
  failed (`tools/claude_code.py`).
- The key is stripped from escalation's environment and blocked by
  `scripts/pre-commit`. `--bare` mode is ruled out because it authenticates
  only by API key (see 0013).

## Changelog

- **2026-10-17:** escalation retires when the Developer's session is built (ADR 0032,
  decision 9; the Sponsor, on crew#778). Decided, not built: until then, escalation
  runs as above.
