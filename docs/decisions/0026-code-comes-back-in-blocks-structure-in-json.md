# 26. Code comes back in blocks, its structure in JSON

- **Date:** 2026-10-11
- **Status:** Accepted (built for the Developer and its steps, crew#626)

## Context

The Developer answers in one JSON object, held to its schema by the server while it is written. The structure earns its place:
- every answer has a known shape;
- about 40 checks on the forms, each from an incident, refuse a wrong field with a reason the re-ask can act on (ADR 0025);
- edits by name let the guards see that nothing unnamed changed (ways-of-working, "edit by name");
- QA and review read which test proves which criterion, and which merged tests are retired and why.

What fails is the code inside it. Each body of code is a JSON string, so every double quote in it is escaped, twice in a test that checks JSON output. Under the server's grammar a quote the model meant as code closes the string, the next token it wants is forbidden, and it writes whitespace to its token limit:
- mqucifer/sprint-metrics#567 slipped this way on 3 of its last 6 answers on 2026-10-11, once for 40 minutes;
- a field cut short at a quote is the same fault (crew#618).

mqucifer/crew#627 stops a runaway early and tells the model where it went wrong, but each slip still costs a whole answer. Escaping also makes answers longer.

**This is a new choice, not a reversal.** On 2026-09-18 the Sponsor asked for research on how an edit addresses code, and edits by name were chosen over whole files, search/replace and unified diff, with zero format failures against 11 and 31. That decision stands. Where the code sits in the answer was never weighed: the JSON came with CrewAI's typed answer (`output_pydantic`), and the substrate gate measured it only on small answers (5 of 5 valid). Since ADR 0025 the crew makes its own calls, so a mixed answer is an ordinary change.

The prior art that research missed: Aider measured in 2024 that models write worse code when it is wrapped in JSON. Markdown-delimited code beat JSON for every model tried, for example 60.5% against 54.1% (Claude 3.5 Sonnet) and 60.6% against 51.1% (DeepSeek-Coder V2) on 133 problems ([aider.chat, 2024-08-14](https://aider.chat/2024/08/14/code-in-json.html)).

Options weighed (Sponsor, 2026-10-11):

| Option | For | Against |
|---|---|---|
| **A. Structure in JSON, code in blocks** | Keeps every check and guard; no escaping; shorter answers | The answer has two parts to split and join |
| B. The same JSON, checked after it is written instead of enforced while it is | A slip can't run away | Malformed JSON comes back instead; escaping stays |
| C. As now, with the runaway guard | Nothing to build | A slip still costs a whole answer |

## Decision

Sponsor, 2026-10-11: **option A.**

- **A step that writes code answers in two parts:** a JSON part, held to the step's form as now, and plain code blocks. Each code field in the JSON names its block (`"source": "block 1"`), and each block is set off by markers the model can't mistake for code. A block's text is exactly the code, unescaped.
- **The crew joins them before anything reads the answer.** The forms' checks, the guards and the downstream roles see the same joined answer they see today. A block named but missing, or a block no field names, is a refusal with that reason (ADR 0025).
- **Edits by name stay as decided on 2026-09-18.** Only where the code sits changes, not how an edit addresses it.
- **The server holds the shape.** The two-part answer is held to its own grammar (EBNF, through LiteLLM to SGLang): JSON with bounded whitespace, then at most 60 blocks of plain lines. Measured when built: without a grammar, the model went on reading after its reading rounds, writing tool calls as text, and three answers in four came back empty.
- **The Developer first,** and its steps (`crews/stepped.py`). Other steps that write code or quote it (QA quoting criteria, the Architect's design note) follow only if their slips are measured.

**The standard.** Structured output for control and plain text for content, the edit-block format coding agents such as Aider use, with the crew's own validation on the joined result.

## Consequences

- The call layer gains a two-part answer: what it asks for, how it splits and joins it, and the refusals for a missing or unnamed block. One real call through LiteLLM proves it before the PR.
- The Developer's forms describe their code fields as naming a block. The schema's prompt copy (plan step L3) shrinks with them.
- The test of the change: mqucifer/sprint-metrics#567's logged requests replayed in the new form, with no quote slips in 10 runs where 3 in 6 slipped, and fewer tokens written.
- crew-presentation's web code (HTML, CSS, JavaScript) is all quotes and today changes by whole-file and quoted edits inside JSON strings. It gains the most.

## Changelog

- **2026-10-11:** decided (Sponsor), from the lean-prompts plan's step L5 and crew#626.
- **2026-10-11:** built for the Developer and its steps (`calls.ask(..., blocks=...)`,
  `delivery_crew.CODE_FIELDS`). Blocks are set off by `@@@ name @@@` and `@@@ end @@@`
  lines; a block wrapped whole in a ``` fence has the fence taken off.
- **2026-10-11:** from its replays on mqucifer/sprint-metrics#567. Held to a grammar
  (`calls.BLOCKS_GRAMMAR`), since without one the model drifted out of the answer. A
  block's name is read loosely (`"@@b1"` is b1). A block no field names is left out, not
  refused. At most 60 blocks: unbounded, two answers repeated one block thousands of
  times. Ten replays: no quote slip, against 3 in 6 the same morning in JSON strings.
