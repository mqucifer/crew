# crew

An agile engineering organization run as CrewAI agents, orchestrated by a
GitHub Projects v2 board.

The board is the orchestrator. Crews are stateless workers that claim a card,
perform exactly one state transition, and write the result back to the issue.
The process is a reconciliation loop: idempotent, restartable, and auditable by
reading the board.

## Roles

The human is **Product Sponsor** and nothing else: sets goals, approves epics,
accepts the increment at sprint review. Refinement, estimation, sprint
mechanics, implementation, review, QA, merge, and release notes are executed by
agents.

## Operating rules

Read [`docs/ways-of-working.md`](docs/ways-of-working.md) — the constitution
every agent follows. Process constants live in
[`src/crew_org/config/org.yaml`](src/crew_org/config/org.yaml).

Four documents, each answering one question, so that none of them has to be
kept true by hand:

| | Answers | Goes stale when |
|---|---|---|
| [`docs/ways-of-working.md`](docs/ways-of-working.md) | What are the rules? | we change the rules |
| [`docs/running-the-crew.md`](docs/running-the-crew.md) | How do I operate it? | the code changes |
| [`docs/final-state.md`](docs/final-state.md) | What are we building? | we change our minds |
| `crew capability` | How far along are we? | never — it is computed |

The constitution wins where any of them disagree. **Where the crew is against
its target is deliberately not written down** — every status claim we have put
in prose has gone stale silently, so ask the command or read a test.

## Usage

```
crew doctor                    # prove the inference path, through the proxy, before trusting it
crew auth                      # verify the crew's credential, and what it must NOT do
crew tick                      # goals become epics; approved epics become stories
crew sprint start              # fill the sprint from approved epics
crew deliver                   # implement a story and show the diff
crew review                    # review every open pull request
crew qa                        # verify delivered work against its criteria
crew sprint close              # merge what you approved, and report
crew revert <pr> --reason "…"  # undo a merged change, through review like any other
```

A tick runs to quiescence. The human controls when the process runs, not the
individual transitions between states.

`crew deliver` **lands what it produces**: the work is implemented and verified
in a sandbox, then committed, pushed and opened as a pull request. There is no
dry mode — the pull request is where a diff is read before it lands, and a
rehearsal that spent the same inference and left nothing landable was a
substitute for using that gate.

## Data packages: how projects use them

The crew publishes data about how it works as **packages**, like reports
(ADR 0021). `delivery-history` is the first: the board's sprints and cards, and
every move, attempt, model call and merge, up to its date. What it may carry is
ADR 0020; who does what with it is ADR 0022.

- **Its schema** is in [`contracts/delivery-history/`](contracts/delivery-history/):
  `schema.json` for the package's index, `events.schema.json` for an events
  file and `manifest.schema.json` for its manifest.
  Both are generated from the code that builds it (`crew package schema`), and
  `versions.json` lists every schema version.
- **A release** is dated, not versioned: the tag `delivery-history-<date>`, with
  the archive as its asset. The archive holds `manifest.json`, the index
  `delivery-history.json` (sprints, cards and periods), one events file per
  sprint under `events/` (days between sprints get their own), and the schemas
  it follows. It carries everything up to its date, so a consumer needs only
  the latest, and loads only the events files for what it shows.
- **The manifest's `schema_version`** says which schema the package follows.
  MAJOR changes when a consumer could break, MINOR when fields are only added.
  A consumer checks that the MAJOR is one it can display, validates the package
  against the schema in the archive, and keeps no copy of its own.
- **Nothing is released yet.** The first release follows once sprint-metrics'
  answers join the package (crew#521). `crew package build` builds one locally,
  under `var/packages/`.

## The two gates

Everything else is the crew's. The Sponsor:

1. **Approves epics** — each proposed epic is a card in `Inbox (Goals)` labelled
   `needs:human`. Move it to `Needs Refinement` to approve, close it to reject.
   Approving an epic *is* the sprint scope decision; nothing asks again.
2. **Reviews the increment** at sprint close: what landed, as a whole. Pull
   requests are approved by the crew's reviewing identity once review and QA
   pass, and land through the merge queue as they go, so there is no decision
   per story.

Stories, estimates, sprint contents, implementation, review and merge are not
Sponsor decisions. A manager reading eight stories to understand a sprint has
been put back into the work.

## Running generated code

Testing what the crew writes means executing code an LLM wrote, so it runs in a
container with no network during tests, a non-root user, a read-only root
filesystem, dropped capabilities and hard resource limits. **When no container
engine is available the check fails rather than falling back to the host** — a
silent fallback looks protected while running arbitrary code as you. See §14 of
the constitution.

## Inference

Primary backend is a local DGX Spark serving Qwen3.8-27B under SGLang, fronted
by a LiteLLM proxy. Escalation for genuinely hard problems runs through headless
Claude Code (`claude -p`) on an existing subscription — **no `ANTHROPIC_API_KEY`
is configured, by design**, so escalation cannot incur metered API charges.

Escalation is budgeted and classified: schema and scope failures may never
escalate. See §9 of the constitution.
