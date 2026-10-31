# crew

An agile engineering organization of model-backed roles, orchestrated by a
GitHub Projects v2 board. Each role's model call is the crew's own: a role's text
from `agents.yaml`, the step's parts, an answer form checked on the way back, and
a refusal asked for again with its reason ([ADR 0025](docs/decisions/0025-the-crew-makes-its-own-model-calls.md)).

The board is the orchestrator. Roles are stateless workers that claim a card,
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
crew log --follow              # what a tick is doing and deciding, as it happens
crew runs --card repo#N        # a card's runs, joined from the record alone
crew qa-start repo#N           # QA's test Task's session start, rendered, no model
crew announced <project>       # what a pass files for producers' announced majors
```

What the crew records, how its records join, and what leaves the machine:
[docs/the-crews-record.md](docs/the-crews-record.md).

A tick runs to quiescence. The human controls when the process runs, not the
individual transitions between states.

`crew deliver` **lands what it produces**: the work is implemented and verified
in a sandbox, then committed, pushed and opened as a pull request. There is no
dry mode — the pull request is where a diff is read before it lands, and a
rehearsal that spent the same inference and left nothing landable was a
substitute for using that gate.

## Data packages: how projects use them

The crew publishes data about how it works as **packages**, like reports
(ADR 0021). `delivery-history` is the first: the board's sprints that have started, its cards, and
every move, attempt, model call and merge, up to its date, sprint-metrics'
answers for each sprint, as it computed them, and the crew's own analysis of
them: what changed each sprint, what needed attention at its end, and each
story's pattern. Since 1.6.0 it carries the Developer's sessions (crew#778) by
their names and counts, never their paths, commands or words, and each story's
deliveries in them: its sessions, the times it reached review, the gate's rounds
before that, what it cost and how it ended. Since 1.10.0 it carries every send-back
(crew#862): finished work sent back to its own step or an earlier one, by the step that
sent it, the step it went back to, the rule that decided, and its round. What counts as
one is in [`docs/reference/records.md`](docs/reference/records.md). Since 1.11.0 a session's tools include `delete_file`
(crew#839). The steps are a closed
list today, and a later MINOR version may add to it, so read a step you don't know as
another step rather than refusing the package. The crew's code writes
that analysis from fixed sentences, never a model. What it may carry is ADR 0020; who does what with it
is ADR 0022.

**2.0.0 replaced 1.x** (crew#905), as announced a week before: it carries sprint-metrics
4.2.1's answers in place of 1.3.1's.
- **What leaves:** `escalation_count` and `escalation_rate_percent`, from each answer and its
  `prior`, `delta` and `flags`, which sprint-metrics took out in its 4.0.0; and
  `escalation_count` as a measure in what changed. The crew's own record of escalations stays:
  the `escalated` events and pattern, and each story's `escalations`.
- **What changes shape:** the failure causes, `top_failure_causes`, become a list of class,
  role and count, as `failure_breakdown` already is, in place of a map of class to count.
- **What's added:** 4.x's costs, rework, roster, working and idle time, and DORA measures,
  with cards in them named as `repo#n`.

**2.1.0 adds how each card was filed** (crew#974), as `filed` on each card. Nothing leaves, so
a 2.0.0 reader reads on.
- **`by`:** who filed it, by the issue's author: `crew`, `sponsor`, `operator`, `other`, or
  `unknown`. A card under the Sponsor's login from before the operator had its own (crew#566,
  2026-10-09) is `unknown`: the two can't be told apart.
- **`activity`:** for the crew's own, what opened it: `finding`, `task`, `technical_epic`,
  `split`, `proposed_goal`, `skeleton_goal`, `record_change`, `retro` or `standup`. It's None
  for anyone else's, and for an older card of the crew's with nothing written to read it from.
- **`kind`:** a Task's kind, a technical epic's cause (`main-red`, `release-check`, `adoption`,
  `design`) or a record's change. A later MINOR may add one, so accept one you don't know.
- **`for_project`:** the project a card filed in another repository is for: QA's Tasks, whose
  `repo` is `qa-suite`.
It says what the crew wrote and adds no counts: what it means is the reader's.

**3.0.0 is announced** and replaces 2.x on 2026-11-07: what changes is under ["Announced: delivery-history 3.0.0"](#announced-delivery-history-300) below.

- **Its schema** is in [`contracts/delivery-history/`](contracts/delivery-history/):
  `schema.json` for the package's index, `events.schema.json` for an events
  file and `manifest.schema.json` for its manifest.
  Both are generated from the code that builds it (`crew package schema`), and
  `versions.json` lists every schema version. `schema.json` embeds
  sprint-metrics' own schema for its answers, from the release the crew runs:
  `RELEASE` in `src/crew_org/packages/sprint_metrics_answers.py`. The service
  runs a release at the same MAJOR, which a release's deploy keeps it to
  (crew#829). A breaking version is announced a week before it ships: its schemas and
  mock sit beside the current ones in `contracts/delivery-history/<version>/`, and
  `versions.json` records it, so the version that ships is exactly the one announced.
- **An announcement is published, so its readers' crews file it** (crew#960, crew#969):
  `announced.json`, beside the contract files a project's record names, maps each
  announced version to the major it `replaces` (`"1.x"`), the date it ships `on`, where its
  `contract` is (`contracts/delivery-history/2.0.0/`), its `changelog`, as a `file` and the
  `section` heading in it, and its `mock`, the mock's files under that folder. Each pass, a
  project whose record `uses` the producer and pins a release at the replaced major gets one
  technical epic to adopt it, in Needs Refinement, with that changelog whole; the crew reads
  what the project pins from `versions.json` at the pinned tag, leaving out what was only
  announced there. Its refinement rules on what the project reads: if nothing it reads
  changes, the epic closes with that said, and staying on the old major is a decision written
  on the epic. Until the version ships, the project's sessions are shown what differs from the
  pinned fields, and have its schemas and mock at
  `.announced/<producer>/<package>-<version>/` in their checkout, never committed: a test
  reads them with no network, and one that stays carries what it uses as its own fixture. The
  release that ships a version takes it out of `announced.json`. Any producer can announce
  this way; one that publishes no `announced.json` files nothing, and each pass notes it.
  `crew announced <project>` shows what a pass would file.
- **A mock** is in [`contracts/delivery-history/mock/`](contracts/delivery-history/mock/):
  a whole package, laid out as a release's archive is, built by the same code
  from illustrative inputs, so a project can build against it before the first
  release. It's regenerated with the schema and shows every kind of event,
  pattern and finding the schema allows. Its numbers and titles are made up.
- **A release** is dated, not versioned: the tag `delivery-history-<date>`
  (`-2` for a second on one day), with the archive as its asset. It reaches a
  project as a pull request that changes one line: `delivery-history.tag` at the
  project's root, which names the release it shows. Old releases keep their
  tags, but only the latest seven and each month's first keep their archives.
  The archive holds one directory, named for the tag
  (`delivery-history-<date>/`), and in it `manifest.json`, the index
  `delivery-history.json` (sprints, cards, periods and the analysis), one events
  file per sprint under `events/` (days between sprints get their own), and the
  schemas it follows: `schema.json`, `manifest.schema.json` and
  `events.schema.json`. It carries everything up to its date, so a consumer needs only
  the latest, and loads only the events files for what it shows.
- **The manifest's `schema_version`** says which schema the package follows.
  MAJOR changes when a consumer could break, MINOR when fields are only added.
  A consumer checks that the MAJOR is one it can display, validates the package
  against the schema in the archive, and keeps no copy of its own.
- **The first release is `delivery-history-2026-10-13`.** A package is released
  only with sprint-metrics' answer for every sprint it covers (crew#521).
  `crew package build` builds one locally, under `var/packages/`, and
  `crew package release` releases it.
- **Each sprint's close releases one** (crew#724), whether a tick closes it or
  `crew sprint close` does, and its retro says which, or why not. A sprint left
  without one gets it at the next tick. A package carries the sprints up to the
  one that ended last, never one under way (crew#738).

### Announced: delivery-history 3.0.0

**3.0.0 replaces 2.x on 2026-11-07** (crew#963), a week after the announcement merged; if that
merge slips, the date moves with it. Its schemas and mock are in
[`contracts/delivery-history/3.0.0/`](contracts/delivery-history/3.0.0/), and until then the
package follows 2.x. The crew logs what happened and sprint-metrics decides what first try
means (ADR 0022), so the package stops carrying the crew's own result for each story:
- **What leaves:** `analysis.stories`, whole, with every field of each entry: `card`,
  `pattern` (`first_try`, `crew_fix`, `escalated`, `retried`, `superseded`, `blocked`, `open`),
  `misses`, `blocks`, `escalations`, `crew_fixes`, `repeated`, `delivered`,
  `wall_clock_seconds`, `cost` and `text`. `StoryPattern`, `Repeated` and `StoryCost` leave the
  schema with it.
- **What crew-presentation reads today that leaves,** at its `build.py` and templates:
  - **The sprint overview's story list** comes only from `analysis.stories`: an entry shows when
    its `delivered` falls in the sprint, by its `card`, with its `blocks` as the attempt blocks
    and its `pattern` as the status word and badge. `crew_fixes` is carried to the template,
    which doesn't show it.
  - **A story's journey** takes its sentence from `text`, its path's delivered state from
    `delivered`, and its "What it cost" from `wall_clock_seconds` and `cost.model_calls`.
  - **Its tests:** its own tests' fixtures build `analysis.stories`, and two files in
    mqucifer/qa-suite read it, which act differently on a package without it:
    - `crew-presentation/test_sprint_overview.py` reads it and `pattern`. All three of its tests
      fail, at `assert sprints`: with no stories, no sprint qualifies. `qa-suite / qa` is a
      required check, so the pin's pull request can't merge without an admin's override, which
      is how mqucifer/crew-presentation#250 merged past other tests' failures.
    - `crew-presentation/test_story_journey_details.py` reads it in two tests,
      `test_journey_page_shows_cost_section` and `test_journey_page_omits_cost_when_no_wall_clock`.
      Both loop over an empty list and pass having checked nothing, so nothing says "What it
      cost" has lost its check. `test_journey_page_shows_event_timeline` reads events and `cards`,
      and still checks.
    - QA's per-story test Tasks are paused (mqucifer/crew#981), so no such Task updates these
      tests. A Developer's dispute of a failing test still opens one, which only the failing file
      can reach.
  - **Not read today:** `misses`, `escalations`, `repeated`, and the rest of `cost`.
- **What each is now,** from a story's events, which all stay, and `cards`:
  - `delivered`: its `card_moved` to Done, or a card's `closed` when `closed_as` is `completed`.
  - `blocks`: its `card_moved` events into Blocked. `escalations`: its `escalated` events.
  - `misses` and `repeated`: its `attempt`, `returned` and `sent_back` events, its
    `work_finished` events with a verdict, and each `attempt`'s `reason`.
  - `crew_fixes`: `pull_merged` events with `crews_own` whose `about` names the story.
  - `cost` and `wall_clock_seconds`: its `model_call` events, timed from its first
    `work_started` or `model_call`.
  - The `superseded` pattern: a card's `closed_as` of `not_planned`.
  - `pattern` and `text`: nothing replaces them, because a verdict on a story is what leaves.
    A sprint's first-try figures are sprint-metrics': `first_attempt_numerator`,
    `first_attempt_denominator` and `first_attempt_rate_percent` in `sprint_metrics.answers`.
- **What stays:** every event; sprint-metrics' answers; each card, with the `filed` 2.1.0 added;
  `analysis.sprints` (`changed`, `attention`, `decided`), whose `first_attempt_rate_percent` is sprint-metrics' number the crew
  words; and `analysis.deliveries`, which says what a story's Developer sessions did and gives
  no verdict.
- **What changes shape:** nothing else in the package. **What's added:** nothing.
- **The mock's numbers change:** its sprint-metrics answers are now computed from its own
  events, so each sprint's totals agree with its stories. Its Sprint 6 was 6 delivered and 3
  first attempts by hand; it's now 5 and 2, and its other measures moved with them.

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
by a LiteLLM proxy. Every model call goes through it, and **no `ANTHROPIC_API_KEY`
is configured, by design**, so nothing can incur metered API charges.

Nothing escalates to a stronger model. A story the Developer can't finish goes
back to the Business Analyst who wrote it, with its evidence, or to the Sponsor
for a choice nobody has made. See §9 of the constitution.
