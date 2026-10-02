# 4. Escalation runs on the subscription, never an API key

- **Date:** 2026-09-18
- **Status:** Accepted

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

## Consequences

- A rate limit is an ordinary outcome: the card is parked and retried, not
  failed (`tools/claude_code.py`).
- The key is stripped from escalation's environment and blocked by
  `scripts/pre-commit`. `--bare` mode is ruled out because it authenticates
  only by API key (see 0013).
