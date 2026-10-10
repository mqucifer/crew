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

The Sponsor then asked whether the crew had outgrown the framework or was only not
using it. Its unused features were weighed one by one against the decisions already
made, in the installed version and in the newest release, 1.15.27 of 2026-10-09, whose
executor is identical. Its memory is a recall system over model-written notes, the
opposite of the baseline ADR 0023 keeps. Its knowledge store is the retrieval index ADR
0024 rules out. Its planner, delegation and Flows duplicate `crews/stepped.py`, the board
and `flows/`. Its tool loops never send the answer schema, in the Crew path and in
`Agent.kickoff` alike, and a schema set on the LLM goes on every call, which blocks the
tool call itself. Its task guardrails do carry a failure's reason into a retry, after its
own parsing. So nothing in it does tools then a structured answer or stops the
schema-less resend, and one feature does part of the retry with the reason.

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
- **No CrewAI layer is kept** (Sponsor, 2026-10-10: "neither"). Not for calls, not for
  memory, which is the epic's record (ADR 0023), and not for routing, where the board
  holds the state and the flows are the crew's Python (ADR 0002). CrewAI leaves
  `pyproject.toml` with the last site, and nothing built on it stays to be moved later.
  What stays of it is the shape it gave the crew: roles with goals and rules in
  `agents.yaml`, one task per step with an answer form.
- **The fallback is inside CrewAI, taken only if the module fails on its first site.**
  Task guardrails re-run the agent with the error and the previous answer. They run only
  after CrewAI's own parsing, so the forms' rules, about 40 validators, would move into
  guardrail functions, and the identical agent retry and the schema-less resend would
  stay underneath. The test of the change, whether the same refusal repeats, reads the
  same either way.
- **A framework is reconsidered only if** a step needs orchestration the board cannot
  hold, or a call pattern the SDK does not provide. The module is the layer a framework
  would wrap, so one adopted later would sit on it, not replace it.

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
- Which framework features were weighed, and why each lost, is in the Context above. The
  next time one is proposed, that is where to start, not from a new analysis.

## Changelog

- **2026-10-10:** scope widened the same day, from the 22 call sites to every layer,
  after the Sponsor asked whether the crew had outgrown the framework or only under-used
  it: no CrewAI layer is kept, and the guardrail fallback and the condition for
  reconsidering a framework are recorded (Sponsor, "neither").
- **2026-10-10:** the module is built, `crew_org/calls.py`, and the criteria check is its
  first site (crew#583, step D1a). The other sites wait for its proof on a real epic.
- **2026-10-10:** tools in the call layer (crew#583, step D2). A step's model calls
  its tools in rounds, without the answer's schema, and is given each result; then
  it is asked for the answer in its form. Proven once through LiteLLM with a read
  tool: two files read in one round, then a valid answer.
