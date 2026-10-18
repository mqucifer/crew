# 4. Never an Anthropic API key

- **Date:** 2026-09-18
- **Status:** Accepted. Escalation, which this decision first governed, is deleted
  (ADR 0032, decision 9; crew#778, Stage E). The rule against an API key stands.

## Context

The crew escalated hard work to Claude. The Sponsor: "I will not wake up to a
surprise Claude bill." An API key can't be capped to a subscription. The
Anthropic API and Claude subscriptions are separate billing systems, and an
API key is metered pay-as-you-go.

On 2026-10-17 the Sponsor retired escalation (crew#778, decision 9). The crew's
model calls all go to the local model through LiteLLM (ADR 0007), and Claude's own
work on the repository runs on the subscription.

## Decision

- `ANTHROPIC_API_KEY` is never set anywhere in the project.
- A metered API path would need a new ADR, with prepaid credits, auto-reload
  off and a Console spend limit.
- **Nothing escalates to Claude** (the Sponsor, 2026-10-17, crew#778 decision 9;
  ADR 0032). A stuck session goes back to the Business Analyst instead.
  `tools/claude_code.py` and the escalation budget were deleted in Stage E.

## Consequences

- The key is blocked by `scripts/pre-commit`.
- The crew has no path to Claude at all, so a usage limit can't stop a tick.

## Changelog

- **2026-10-17:** escalation retires when the Developer's session is built (ADR 0032,
  decision 9; the Sponsor, on crew#778). Decided, not built: until then, escalation
  runs as above.
- **2026-10-18:** escalation is deleted (crew#778, Stage E): `claude -p` on the
  subscription, its rate-limit parking and its stripped environment went with it.
  The title was "Escalation runs on the subscription, never an API key"; what's left
  is the rule against a key.
