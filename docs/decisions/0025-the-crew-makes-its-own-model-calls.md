# 25. The crew makes its own model calls

- **Date:** 2026-10-10
- **Status:** Accepted (staged; to be built as Part D of `docs/plans/context-record-build.md`, the criteria check first)

## Context

CrewAI 1.15.22 is used at the 22 call sites in `crews/` as a one-task wrapper: an
Agent, a Task and a Crew around one prompt and one answer form. The crew's flows are its
own Python. Three things the wrapper does cost the crew:

- A refused answer is retried by resending identical messages, so the model never sees
  why. 97 of 609 calls in Sprints 17 to 20 were refused by a form, 34 percent of the
  Developer's, and mqucifer/sprint-metrics#529 was refused six times for the same
  criterion.
- When tools are present, its executor drops the answer schema, so a read tool and a
  structured answer can't coexist. ADR 0024 needs both.
- An empty answer is retried silently; the crew wraps the client only to see them
  (crew#312).

The Sponsor was unsure of the impact, so the change is staged.

## Decision

Sponsor, 2026-10-10 (D5 and D6 of the plan).

- **One module makes every model call.** It builds the messages from the role's entry
  in `agents.yaml` and the step's named parts, sends them to LiteLLM with the answer's
  JSON schema, validates the answer, and on a refusal asks again with the validation
  error appended. An empty answer is a refusal with that reason. Tools run in rounds
  before the final structured answer. Every attempt is recorded with its reason and each
  part's size, and the record's model-call events come from the module.
- **Staged.** The module and one site first, the criteria check, run for real on the
  next epic and compared with the sprint before. The other sites move only after that
  proof, a few per PR, their prompts unchanged except CrewAI's framing. CrewAI leaves
  `pyproject.toml` with the last one.
- **The criteria check runs one story per call.** It thought a median of 12.6k tokens
  to write 641 characters, at the edge where answers come back empty.
- **The forms' refusal rules stay.** Each was an incident. With the reason in the retry,
  a refusal teaches instead of looping.

**The standard.** Structured output with schema validation and a re-ask carrying the
error, the pattern the Instructor library made common; tool use through the
OpenAI-compatible API LiteLLM serves. All traffic stays on the proxy (ADR 0007).

## Consequences

- ADR 0002 no longer says Crews sit on the inside; its changelog records this.
- A refusal count per step, with reasons, is a query on the event log. The test of the
  change is how often the same refusal repeats, before and after.
- The roles, their rules, the forms, the flows, the board, the model and the proxy are
  unchanged. Prompts lose CrewAI's framing sentences only; the first real call per site
  is the check.
- Each site moves in its own PR and can be reverted alone.
