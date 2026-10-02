# Operator retro log

What the operator looked at, did and why, and what it gained, sprint by sprint. The checklist is in [operator-retro.md](operator-retro.md). Newest sprint first.

## Sprint 12 (opened 2026-10-01)

Before this runbook existed, the operator fixed defects live as ticks found them. These entries are the evidence that led to "watch and file; fix only incidents".

### Sprint 12, 2026-10-01: the stack was down before tick 1
- **Looked at:** `crew doctor`: the proxy was unreachable. Docker Desktop wasn't running on the Mac, so the LiteLLM stack was down too. The Spark's production model was up.
- **Did:** started Docker Desktop and the LiteLLM stack; `crew doctor` passed six of six.
- **Why:** an incident. No tick can run without the proxy.
- **Gained:** ten minutes, and nothing else. The tick would have refused to start anyway (`health()`).
- **Follow-up:** none. Production is moving to dedicated hardware (docs/plans/goal-174-service.md).

### Sprint 12, 2026-10-01: #184 blocked for a person, misread as a routing bug
- **Looked at:** the Architect's block on sprint-metrics#184. Story #393's criteria 4 and 5 expected 404 and 400 for the same request.
- **Did:** treated it as a routing defect. Filed crew#425 and merged #426 (a criterion conflict goes to the Product Owner). Then #427: a blocked design epic's stories had been admitted, five of them (#392–#396), and I stopped tick 1 before delivery. I unblocked #184 and ran it again.
- **Why:** I read the label, not the content.
- **Gained:** two valid mechanism fixes. But the Sponsor pointed out the problem was content. The epics contradicted the Goal, each other, and the Sponsor's decisions.
- **Follow-up:** the rule "read the block before the mechanism" (now in the runbook).

### Sprint 12, 2026-10-01: the Goal #174 review
- **Looked at:** Goal #174, epics #184–#187, stories #392–#403, both design notes, the merged code, crew#280, crew#283, the #186 decisions. The story-level claims were checked by running sprint-metrics' own code.
- **Did:** wrote the review (https://claude.ai/artifact/TXQstfAr8xrYqXFEzkjva6). The Sponsor revised the Goal, the old epics were superseded, and the Product Owner proposed #406–#408.
- **Why:** to find where each contradiction lives before anything was built.
- **Gained:** 15 contradictions, 6 of them against the Sponsor's own recorded decisions (Postgres, unblocked events, counting a card where it finishes, OTLP, versioning, trends), none of which were in the Goal. Nothing had been built, so the cost was planning time only. It led to the service plan (docs/plans/goal-174-service.md).
- **Follow-up:** crew#428 (contradictions at the split, built), #429, #430, #431 (stale-Goal forecast).

### Sprint 12, 2026-10-01: an incident of my own making, epics closed as completed
- **Looked at:** tick 3: "all 5 children done — closing epic" on #184, #185 and #187.
- **Did:** stopped the tick before Goal #174 closed, re-closed the epics as not planned, and fixed it in #433 (crew#432): a superseded child doesn't count as done, and a parent being planned again never closes.
- **Why:** an incident. Superseded work was being recorded as delivered.
- **Gained:** the defect was real and general. GitHub moves an issue closed as not planned to Done. It was triggered by closing the stories by hand instead of letting the Goal's rework supersede them.
- **Follow-up:** none beyond the fix.

### Sprint 12, 2026-10-01: contradictory criteria checked at the split
- **Looked at:** #393, #394 and #395's real criteria and the code they touch.
- **Did:** built #434 (crew#428), a QA pass on a split's criteria before any story exists.
- **Why:** design review had caught one of these once and missed it the next time.
- **Gained:** the live run found all four contradictions I'd found by hand, in 337 s. Its first in-tick run (#414's split, tick 6) passed and created #420.
- **Follow-up:** compare its catches with the retro's over the next sprints.

### Sprint 12, 2026-10-01: a refusal message that misled the model
- **Looked at:** `llm.failed` on #381, twice, then #384: "'docs/formats.md' isn't a test file: name it tests/test_*.py" for a documentation criterion.
- **Did:** #435: the refusal now says a doc criterion is proven by the doc.
- **Why:** every retry repeated the same mistake, because the advice was impossible to follow.
- **Gained:** three failed attempts traced to one sentence.
- **Follow-up:** checklist item 4 (did any message mislead the model?).

### Sprint 12, 2026-10-01: a design PR held nothing
- **Looked at:** #406–#408 approved while design PR #412 was open. The next pass would have split them against the design being replaced.
- **Did:** stopped tick 4 at the start of its next pass, losing no work. Built #438 (crew#437): any open design PR holds its project's epics.
- **Why:** treated as an incident, since the split was imminent.
- **Gained:** the hold works. Tick 5 left the epics unsplit while #412 was open.
- **Follow-up:** none.

### Sprint 12, 2026-10-01: technical epics from the design that were product work
- **Looked at:** #414–#419, filed from #412's declared changes.
- **Did:** stopped tick 5 before they were split and brought a proposal to the Sponsor. I acted before approval (closed four) and reverted. The Sponsor then decided: keep #414, hold #415 and #416, close #417–#419. Filed crew#439.
- **Why:** three of the six were #406–#408's product work, outside the Sponsor's gate, and one was a review note about a typo.
- **Gained:** a classification gap in how design changes become work. It also led to crew#440 and the plan's refinement panel (DevOps and QA before the split).
- **Follow-up:** crew#439, and the panel experiment (experiments/panel-174).

### Sprint 12, 2026-10-01: #384 rebuilt twice for two additions, then blocked
- **Looked at:** #384 (epic #65) against #380, #381 and #382 (epic #64), all appending tests to `tests/test_crew_performance.py`. #384 was approved three times and rebuilt twice, then blocked at the rebuild limit.
- **Did:** the Sponsor closed PR #405 for a rebuild. I built #443 (crew#436): two approved additions in one place are kept, not rebuilt.
- **Why:** each rebuild repeated a full delivery, review and QA (about 30–40 minutes) for a conflict between two additions.
- **Gained:** proven on the real conflict. Both sides were kept, and sprint-metrics' whole suite and lint passed on the result.
- **Follow-up:** crew#436 criterion 3 (ordering stories in different epics that touch the same files).

### Sprint 12, 2026-10-01: mixed code versions, my slip
- **Looked at:** tick 6: "deliver failed: ImportError: cannot import name 'keeping_both'".
- **Did:** stopped tick 6 and started tick 7 on one version.
- **Why:** an incident. I had pulled #443 into the checkout while tick 6 ran, and phases import lazily.
- **Gained:** one lost delivery start, and the rule "never update the checkout while a tick runs" (in the runbook).
- **Follow-up:** none.

### Sprint 12, 2026-10-01: a push GitHub rejected, blocked as the story's failure
- **Looked at:** #383 blocked on `remote: fatal error in commit_refs`. GitHub reported all systems operational minutes later.
- **Did:** unblocked #383 and filed crew#444 (#390 only recognises HTTP errors as passing, not `git` server errors).
- **Why:** treated as an incident. The card was blocked for a reason that wasn't its own.
- **Gained:** a gap in #390's coverage.
- **Follow-up:** crew#444. From here on, entries follow the runbook: watch and file.

### Sprint 12, from the watch-and-file rule to close

The working log from `var/notes`, unchanged: the tick-watcher's entries, the operator's corrections and entries, the retro-evidence agent's section, and the operator's retro.

### Tick observed: 2026-10-01T22:58:50.407527Z–2026-10-01T23:24:24.436276Z

**What moved:**
- sprint-metrics#384 "Move the Crew Performance Summary above the sprint sections" delivered, PR #421 (2026-10-01T23:14:54Z)
- sprint-metrics#420 "Declare psycopg and OpenTelemetry as runtime dependencies and update installation docs" delivered, PR #422 (2026-10-01T23:23:44Z)

**Where a person was asked or a card stopped:**
- sprint-metrics#383 "Show retry count in the first-attempt-rate line in the markdown standup report" blocked at 2026-10-01T23:08:20Z; summary: "could not push `feat/383-show-retry-count-in-the-first-attempt-rate-line-in-the-markdown-standup-report`: git push failed: remote: fatal error in commit_refs..."; last comment (first 300 chars): "**Blocked.** could not push `feat/383-show-retry-count-in-the-first-attempt-rate-line-in-the-markdown-standup-report`: git push failed: remote: fatal error in commit_refs        
To https://github.com/mqucifer/sprint-metrics.git
 ! [remote rejected] feat/383-show-retry-count-in-the-first-attempt-rat"; unblocked at 2026-10-01T23:09:09Z with "GitHub rejected the push server-side (passing); retried".

**Failures:**
- sprint-metrics#420 escalation EDIT attempt 1: "'test_installation_states_python_312_and_third_party_packages' already exists — use replace, or choose another name" (2026-10-01T23:18:04Z)
- sprint-metrics#420 escalation VERIFY attempt 1: "AssertionError: assert [] == ['psycopg>=3....r-otlp>=1.24']" (2026-10-01T23:21:00Z) 
- sprint-metrics#420 escalation VERIFY attempt 2: "AssertionError: assert ['psycopg>=3....r-otlp>=1.24'] == []" (2026-10-01T23:22:35Z)

**Operator and Sponsor steps:**
- None in this window.

**Possible incidents:**
- Tick 2 started at 2026-10-01T23:23:52Z (pass 2) but ended without `tick.finished` before Tick 3 started at 2026-10-01T23:24:24Z
  - *Operator, checked:* not an incident. "pass 2" and "reading board" are two steps inside tick 7. The skill's rule was wrong and has been removed (first live run of tick-watcher).
- *Operator, correction:* the operator step in this window was the #383 unblock (`operator.jsonl`, 23:09:09Z). The watcher listed it under "Where a card stopped" instead.

<!-- watched-to: 2026-10-01T23:24:24.436276Z -->

### Tick observed: 2026-10-01T23:24:30.925948Z–2026-10-02T00:49:33.993968Z

**What moved:**
- sprint-metrics#383 "Show retry count in the first-attempt-rate line in the markdown standup report" delivered, PR #423 (2026-10-01T23:44:35Z), then returned from Merging to In Progress due to test failure at 2026-10-02T00:13:27Z, re-delivered at 2026-10-02T00:24:24Z
- sprint-metrics#385 "Add a Definitions section at the end of the markdown report" delivered, PR #424 (2026-10-02T00:05:10Z); PR #424 joined merge queue; card moved to Merging (2026-10-02T00:13:10Z)
- sprint-metrics#386 "Add a Card Detail section at the end of the markdown report" delivered, PR #425 (2026-10-02T00:49:12Z)
- sprint-metrics#420, #384 reviewed and moved to Merging; #421, #422 PRs approved; #423 (PR for #383) re-approved; #424 (PR for #385) approved
- sprint-metrics#414 epic closed automatically (all children done)

**Where a person was asked or a card stopped:**
- None

**Failures:**
- sprint-metrics#385 escalation VERIFY attempt 1: "AssertionError: assert '- **Cycle time**' not in '# Crew Perf..." (2026-10-02T00:01:52Z)
- sprint-metrics#386 escalation VERIFY attempt 1: "F841 Local variable `section_body` is assigned to but never used" (2026-10-02T00:32:07Z)
- sprint-metrics#386 escalation SCHEMA attempt 1: "OpenAI API call failed: 1 validation error for Implementation" (2026-10-02T00:37:07Z)
- sprint-metrics#386 escalation REGRESSION attempts 1–2: "test_markdown_definitions_is_last_section_heading removed entirely; not a move" (2026-10-02T00:41:47Z, 2026-10-02T00:46:06Z)
- sprint-metrics#386 escalation VERIFY attempt 2: "F841 Local variable `section_body` is assigned to but never used" (2026-10-02T00:45:16Z)
- sprint-metrics#386 escalation VERIFY attempt 3 escalated with "local repair exhausted" (2026-10-02T00:48:13Z)

**Operator and Sponsor steps:**
- None in this window.

**Possible incidents:**
- None

<!-- watched-to: 2026-10-02T00:49:33.993968Z -->

### Sprint 12, 2026-10-01: tick-watcher's first live run
- **Looked at:** the watcher's entry for tick 7 (above), checked against `var/events`.
- **Did:** fixed the operator-retro skill before its PR. Removed the "tick without tick.finished" rule, which can't be applied from events. Operator and Sponsor steps are now listed in their own section. Quotes go on one line.
- **Why:** its one "possible incident" was a false alarm, and it filed my #383 unblock under the wrong heading.
- **Gained:** the facts were accurate and sourced (2 deliveries, the #383 block, #420's three failed attempts) for 48k tokens of Haiku in under two minutes. One false alarm in one run.
- **Follow-up:** judge it over the rest of Sprint 12: false alarms, and missed facts I'd have logged myself.

### Sprint 12, 2026-10-02: #386 took 7 attempts and the sprint's escalation
- **Looked at:** sprint-metrics PR #425 ("attempts: 7, escalated: yes", Sponsor asked), and #386's events.
- **Did:** filed crew#448.
- **Why:** not an incident. A refusal steered the model wrong again: an existing test was cited as a new one, and the refusal didn't name `proven_by_existing`. Then two regression and verification rounds over a retired test.
- **Gained:** 4 of the 7 attempts traced to two refusal messages, neither of which named the field the model needed. The sprint's escalation budget (1) is spent.
- **Follow-up:** crew#448. Checklist item 4 (did any message mislead the model?) found something again.

### Sprint 12, 2026-10-02: the crew has no logging, only events
- **Looked at:** the event model and `var/events` (Sponsor asked): 9,792 events, 24 kinds, 66 undefined detail keys, `note` a quarter of them, no levels, no stdlib `logging` anywhere. 12 modules read events, and two of them (`strain.py`, `revisit.py`) turn them into crew decisions.
- **Did:** filed crew#449, one shared logging library: levels, a common context, and a schema for records code reads (OpenTelemetry's events-are-log-records model).
- **Why:** not an incident; planned work for a crew iteration.
- **Gained:** the tick-watcher's false alarm traced to misleading event names (`tick.started` for passes).
- **Follow-up:** crew#449.

### Tick observed: 2026-10-02T00:52:11.028107Z–2026-10-02T01:26:24.074320Z

**What moved:**
- sprint-metrics#383 "Show retry count in the first-attempt-rate line in the markdown standup report" delivered, PR #423 (2026-10-02T01:02:20Z)
- sprint-metrics#386 "Add a Card Detail section at the end of the markdown report" delivered PR #425 at 01:11:44, returned from QA at 01:16:34 (2 unproven), re-delivered at 01:19:06, PR #425 merged (2026-10-02T01:25:20Z)
- Epic #64 closed (all 4 children done, 01:02:44)
- Epic #65 closed (all 3 children done, 01:26:08)
- Standup written (crew card #447, 01:26:33)

**Where a person was asked or a card stopped:**
- sprint-metrics#386 returned from QA at 2026-10-02T01:16:34Z; summary: "returned — 2 unproven"; unproven criteria: "Given a cards file with two cards: one completed (created 2024-01-01, started 2024-01-03, completed 2024-01-07) and one in-progress (created 2024-01-02, started 2024-01-04, no completed date) When I run the command with --markdown Then the output contains a line '## Card Detail' at a line index after '## Definitions', and the section body contains the strings '2024-01-01' and '2024-01-02', showing both cards are listed" and "Given an empty cards file (containing []) When I run the command with --markdown Then the output contains '## Definitions' but does NOT contain '## Card Detail', because there are no cards to list"; later accepted after re-delivery at 01:24:14.

**Failures:**
- None

**Operator and Sponsor steps:**
- None in this window.

**Possible incidents:**
- PR #425 returned for rebuild at 2026-10-02T01:02:55Z: "approved PR #425 conflicts with main — returned for a rebuild"

<!-- watched-to: 2026-10-02T01:26:24.074320Z -->

### Tick observed: 2026-10-02T01:26:33.406711Z–2026-10-02T01:32:32.740234Z

**What moved:**
- Goal card #48 closed: "all 4 children done — closing goal" (2026-10-02T01:29:14Z)

**Where a person was asked or a card stopped:**
- None

**Failures:**
- None

**Operator and Sponsor steps:**
- None in this window.

**Possible incidents:**
- None

<!-- watched-to: 2026-10-02T01:32:32.740234Z -->

### Sprint 12, 2026-10-02: tick 8, #386 rebuilt over a doc conflict
- **Looked at:** the watcher's one "possible incident": #386's PR #425 returned for a rebuild after QA accepted it. The conflict was in `docs/formats.md` (the rebuild note).
- **Did:** nothing live. A rebuild isn't an incident.
- **Why:** both stories edited the same doc section, so keeping both sides correctly didn't apply and the rebuild was the right fallback.
- **Gained:**
  - The watcher over-flags rebuilds: second run, second false alarm. The skill needs "rebuilds, blocks and failures are never possible incidents" (batch at sprint close).
  - Keeping both sides leaves no trace when it gives up, so I could only infer that it was tried. That goes into crew#449's logging.
- **Follow-up:** the skill edit at sprint close, and a note on crew#449.

## Evidence for the retro: Sprint 12

Window: events from 2026-10-01 (UTC timestamps; first event in window 2026-10-01T11:24:35Z, last in `tick.jsonl` 2026-10-02T01:32:32Z, last in `close.jsonl` 2026-10-02T01:45:59Z). Gathered 2026-10-01 (local), from `var/events/*.jsonl`, `gh pr list -R mqucifer/crew`, `gh issue view -R mqucifer/sprint-metrics`.

**Counts**
- 1. Interventions: 24 operator/sponsor events (19 operator, 5 sponsor); 14 crew PRs merged since 2026-10-01; 9 ticks (`tick.started` "pass 1").
- 2. Where a person was asked: 10 cards carried `needs:human` or `blocked` in the window (7 from label events in `tick.jsonl`: #184, #185, #187, #174, #414, #384, #383; 3 more carry `needs:human` now after the Sponsor's hold: #406, #407, #408); 2 further epics held by the Sponsor without those labels (#415, #416). 2 `card.blocked` events (#384, #383).
- 3. Repeats and cost: 4 rebuilds from main (#384 x2, #383, #386) plus #384's PR #405 closed past the rebuild limit; 1 QA return (#386); 0 code-review returns; 1 CI-failure return (#383); 0 stories returned to refinement (no `story.returned` events); 1 "kept both sides" (#383, PR #423); 7 failed model calls (14 `llm.failed` events, two per call) and 2 `task.failed`; 0 `llm.empty`; 4 distinct SCHEMA refusal messages (9 occurrences).

### 1. Interventions

**Operator and Sponsor events** (`var/events/operator.jsonl`, `var/events/sponsor.jsonl`, since 2026-10-01; `crew moves --people` not run by me):

| Time (UTC) | Source | Card | Summary |
|---|---|---|---|
| 2026-10-01T12:27:09Z | operator | #392 | card.moved Sprint Backlog > Ready: "back to Ready: epic #184 has no design note yet (crew#425)" |
| 2026-10-01T12:27:11Z | operator | #393 | card.moved Sprint Backlog > Ready: same summary |
| 2026-10-01T12:27:13Z | operator | #394 | card.moved Sprint Backlog > Ready: same summary |
| 2026-10-01T12:27:15Z | operator | #395 | card.moved Sprint Backlog > Ready: same summary |
| 2026-10-01T12:27:17Z | operator | #396 | card.moved Sprint Backlog > Ready: same summary |
| 2026-10-01T13:36:07Z | operator | #392 | note: "closed as not planned: Goal #174 revised, epics re-planned" |
| 2026-10-01T13:36:11Z | operator | #393 | note: same |
| 2026-10-01T13:36:14Z | operator | #394 | note: same |
| 2026-10-01T13:36:16Z | operator | #395 | note: same |
| 2026-10-01T13:36:20Z | operator | #396 | note: same |
| 2026-10-01T13:36:23Z | operator | #397 | note: same |
| 2026-10-01T13:36:26Z | operator | #398 | note: same |
| 2026-10-01T13:36:29Z | operator | #399 | note: same |
| 2026-10-01T13:36:31Z | operator | #400 | note: same |
| 2026-10-01T13:36:35Z | operator | #401 | note: same |
| 2026-10-01T13:36:39Z | operator | #402 | note: same |
| 2026-10-01T13:36:42Z | operator | #403 | note: same |
| 2026-10-01T19:59:04Z | sponsor | #415 | card.moved Needs Refinement > Inbox (Goals): "held by the Sponsor: waits for DevOps' note on this project's environments" |
| 2026-10-01T19:59:05Z | sponsor | #416 | card.moved Needs Refinement > Inbox (Goals): same summary |
| 2026-10-01T22:41:50Z | sponsor | #406 | card.moved Needs Refinement > Inbox (Goals): "held by the Sponsor: waits for the refinement panel (docs/plans/goal-174-service.md)" |
| 2026-10-01T22:41:53Z | sponsor | #407 | card.moved Needs Refinement > Inbox (Goals): same summary |
| 2026-10-01T22:41:53Z | sponsor | #408 | card.moved Needs Refinement > Inbox (Goals): same summary |
| 2026-10-01T22:47:54Z | operator | #384 | card.moved Blocked > Sprint Backlog: "unblocked: PR #405 closed past the rebuild limit; re-delivered from main (crew#436)" |
| 2026-10-01T23:09:09Z | operator | #383 | card.moved Blocked > Sprint Backlog: "unblocked: GitHub rejected the push server-side (passing); retried" |

Not in the table: the 2026-09-29 event in `operator.jsonl` (#307) and the earlier sponsor events, which fall before the window. The stopped or restarted ticks are the operator's to add.

**Crew PRs merged since 2026-10-01** (`gh pr list -R mqucifer/crew --state merged --search "merged:>=2026-10-01"`; 14). #423 and #424 merged at 02:17Z and 03:40Z on 2026-10-01, which is the evening of 2026-09-30 local time; the search includes them.
- #423 docs: the 2026-09-30 empty-answer arms, and what they rule out (#312)
- #424 exp: sprint-metrics#268 on Qwen3.8-Flash-Next against the 27B (#422)
- #426 fix: a design note that contradicts a story's criterion goes to the Product Owner (#425)
- #427 fix: a blocked design epic's stories still wait for its note (#425)
- #433 fix: a parent whose children were superseded isn't done (#432)
- #434 feat: a split whose criteria can't all pass goes to the Product Owner (#428)
- #435 fix: a doc criterion named as a test is told the doc proves it
- #438 fix: a design PR run by hand holds its project's epics (#437)
- #441 exp: the refinement panel test on Goal #174's first epics (design)
- #442 docs: the plan for running sprint-metrics as a service (Goal #174)
- #443 feat: two approved additions in one place are kept, not rebuilt (#436)
- #445 docs: the operator's retro runbook, and Sprint 12's log so far
- #446 chore: the operator-retro skill, with tick-watcher and retro-evidence agents
- #450 chore: rebuilds, blocks and failures are never the watcher's incidents

**Ticks:** 9 `tick.started` events with summary "pass 1" in `tick.jsonl`, at (UTC) 10-01 11:24, 12:27, 13:37, 13:45, 15:15, 22:44, 22:58; 10-02 00:52, 01:29. Whether each was stopped: not known from events.

### 2. Where a person was asked

Source for labels: `tick.jsonl` notes with `artifact: label`. Final state from `gh issue view -R mqucifer/sprint-metrics`, 2026-10-01 evening.

| Card | Label event(s) in window | Final state now | Last comment (first 300 chars) |
|---|---|---|---|
| #184 | `-needs:human` 11:34:32Z; `+blocked,needs:human` by Architect 12:10:10Z | CLOSED, not planned (13:38:07Z); labels: needs:design | "Superseded, not delivered: Goal #174 was revised and its epics are being planned again. Closed earlier as completed by mistake, because its stories had been closed as not planned." |
| #185 | `-needs:human` 11:41:40Z | CLOSED, not planned (13:38:12Z); no labels | same text as #184 |
| #187 | `-needs:human` 11:48:15Z | CLOSED, not planned (13:38:16Z); labels: needs:design | same text as #184 |
| #174 | `-needs:rework` 13:46:25Z; `-needs:human` 13:52:37Z | OPEN; label: goal | "<!-- crew:epic-proposal --> ## Proposed epics **Product Owner** decomposed *Goal: the tool is a service that keeps its history* into 3 epics, ordered so the most valuable is deliverable first. ### 1. Run sprint-metrics as a stateful service with durable event history — #406 **Outcome** — A user r" |
| #414 | `-needs:human` 22:57:12Z | CLOSED, completed (23:44:52Z); label: technical | "<!-- crew:story-split --> ## Stories **Business Analyst** split *Update pyproject.toml [project] dependencies and regenerate uv.lock.* into 1 stories totalling 1 points. - **[1]** Declare psycopg and OpenTelemetry as runtime dependencies and update installation docs — #420 ### Design **Not requi" |
| #384 | `card.blocked` 22:44:14Z "merge conflict on PR #405"; `+blocked,needs:human` 22:44:15Z | CLOSED, completed (23:35:17Z); no labels | "<!-- crew:qa --> <!-- crew:qa 0365452336a8 --> ## QA — accepted All six acceptance criteria for 'Move the Crew Performance Summary above the sprint sections in the markdown report' are proven. AC1, AC2, AC3, AC5, and AC6 are each proven by dedicated tests that directly exercise the ordering and cou" |
| #383 | `card.blocked` 23:08:20Z (Developer) "could not push `feat/383-show-retry-count-in-the-first-attempt-rate-line-in-the-…`"; `+blocked` 23:08:23Z | CLOSED, completed (2026-10-02T01:02:22Z); no labels | "<!-- crew:qa --> <!-- crew:qa 4bef23f783cf --> ## QA — accepted All 7 acceptance criteria are proven. The full test suite passes (all dots, no failures). Five behaviour criteria are each backed by a dedicated test that exercises the exact scenario described. The documentation criterion (4) is prove" |
| #406 | none in `tick.jsonl`; Sponsor move to Inbox (Goals) 22:41:50Z | OPEN; labels: needs:human, needs:ux | "Held by the Sponsor, 2026-10-01: back in Inbox (Goals) until the crew's refinement panel is built and tested (crew docs/plans/goal-174-service.md, part 5). This epic is still approved. It returns to Needs Refinement then." |
| #407 | same | OPEN; labels: needs:human, needs:ux | same text as #406 |
| #408 | same | OPEN; label: needs:human | same text as #406 |
| #415 | Sponsor move to Inbox (Goals) 19:59:04Z (no label event) | OPEN; label: technical | "Moved back to Inbox (Goals) by the Sponsor, 2026-10-01: held until DevOps has written its note on this project's environments. That note covers the settings in each environment, the test bed, and how service mode is proven. This epic will be reshaped by it." |
| #416 | same | OPEN (REOPENED); label: technical | same text as #415 |

Further facts:
- #184's `needs:human` comment at 12:10:10Z (Architect): "**No design note: this needs a person.** It contradicts a guideline or a story's criterion after a retry: The design states: "All three return 404 with the card_id in the body when the card does not exist in that sprint." ... contradicts #393 criterion 5 ..." A design note was written at 12:39:16Z (comment "<!-- crew:design-note -->"), after the operator's fix PRs #426 and #427 merged at 12:25Z and 12:29Z.
- #184, #185, #187 were closed as completed by the crew at 13:37:21Z–13:37:25Z ("all 5 children done — closing epic", "all 3 children done", "all 4 children done") after the operator closed their stories as not planned at 13:36Z. The comment now on each says they were "Closed earlier as completed by mistake". #433 (merged 13:44:54Z) is titled "a parent whose children were superseded isn't done".
- #174 was "split again at the Sponsor's request" (`epic.resplit` 13:46:25Z, `tick.jsonl`).
- Whether each person's answer was recorded where the crew reads it, and whether the question was the Sponsor's to answer: judgement, not mine.

### 3. Repeats and cost

**Rebuilds** (`rebuilt from main` notes, `returned for a rebuild` moves, `tick.jsonl`):
- #384: 14:07:03Z, 14:39:40Z ("approved PR #405 conflicts with main — returned for a rebuild"; both note `tests/test_crew_performance.py`); third approved attempt 15:26Z joined the merge queue; at 22:44:14Z `card.blocked` "merge conflict on PR #405"; operator note 22:47:54Z "PR #405 closed past the rebuild limit; re-delivered from main (crew#436)"; delivered as PR #421 at 23:14:54Z, merged. Deliveries of #384 in the window: PR #405 at 12:59, 14:15, 14:58 UTC, then PR #421 at 23:14.
- #383: 00:15:24Z "rebuilt from main: its branch conflicted in tests/test_crew_performance.py"; the card move at 00:13:27Z reads "PR #423: `tests` failed on its head, returned with the log". Delivered again 00:24:24Z. Earlier at 23:49:39Z "PR #423 kept both sides of tests/test_crew_performance.py".
- #386: 01:02:55Z "approved PR #425 conflicts with main — returned for a rebuild"; note 01:03:08Z "rebuilt from main: its branch conflicted in docs/formats.md".
- Time from each return to the next delivery (event timestamps): #384 14:07 to 14:15 (8 min), 14:39 to 14:58 (19 min); #386 01:02 to 01:11 (9 min); #383 00:13 to 00:24 (11 min).

**Gate round trips** (`card.moved` events):
- QA returned: #386 once, 01:16:34Z "returned — 2 unproven"; delivered again 01:19:06Z, accepted 01:24:14Z.
- Code review returned (Reviewing > In Progress): none.
- Merge/CI returns (Merging > In Progress): #384 x2, #383 x1 ("tests failed on its head"), #386 x1 (the rebuilds above).
- Deliveries per card ("delivered — PR"): #380 1, #381 1, #382 1, #383 2, #384 4 (see above), #385 1, #386 3, #420 1.
- Stories returned to refinement: 0 (no `story.returned` events). Operator moved #392 to #396 back to Ready on 2026-10-01 at 12:27Z (table above); these were not `story.returned` events.

**"Kept both sides" merges:** 1. #383, PR #423, `tests/test_crew_performance.py`, 23:49:39Z (`tick.jsonl` note "#383 PR #423 kept both sides of tests/test_crew_performance.py"). The later rebuild of #383 (00:15Z) and #386's rebuild over `docs/formats.md` (01:03Z) leave no "kept both sides" note.

**Failures, grouped by first line of the error.** Each failed model call writes two `llm.failed` events (same `call_id`), so counts are of calls.

| First line | Calls | Cards | Times (UTC) |
|---|---|---|---|
| OpenAI API call failed: 1 validation error for FirstOrDone | 1 | #381 | 10-01 14:17:42 |
| OpenAI API call failed: 2 validation errors for FirstOrDone | 1 | #381 | 10-01 14:19:22 |
| OpenAI API call failed: 1 validation error for Implementation | 5 | #385 (1), #386 (4) | 10-02 00:01:52 (#385); 00:33:53, 00:35:25, 00:37:07, 00:38:38 (#386) |

- `task.failed`: 2. #384 attempt 1, `for: deliver`, 14:49:56Z (its paired `escalation.decided` is a SCHEMA failure on "1 validation error for Rework"; no `llm.failed` event exists for it); #386 attempt 2, `for: deliver`, 00:37:07Z.
- `agent.failed`: 0.
- `escalation.decided` (14): VERIFY 9 (#392 x1, #420 x2, #383 x1, #385 x1, #386 x4), SCHEMA 2 (#384, #386), REGRESSION 2 (#386), EDIT 1 (#420: "'test_installation_states_python_312_and_third_party_packages' already exists — use replace, or choose another name"). One `escalation.sent` ("local repair exhausted"): #386, 00:48:13Z, VERIFY, "1/1 of sprint budget".
- #386 REGRESSION contracts (`escalation.decided` 00:41:47Z and 00:46:06Z): "tests/test_crew_performance.py::test_markdown_definitions_is_last_section_heading ... removed entirely; not a move: no other module defines `test_markdown_definitions_is_last_section_heading`, so this deletes it rather than moving it".

**`llm.empty`:** 0 events in the window.

**SCHEMA refusals by message** (second line of the validation error):
1. "'docs/formats.md' isn't a test file: name it tests/test_*.py": 3. #381 at 14:17:42Z and 14:19:22Z (llm.failed), #384 at 14:49:56Z (escalation.decided, "Rework").
2. "'docs/formats.md' is documentation. A criterion about what a doc says is proven by reading the doc, not by a test: leave it out of criteria_tests and make the doc change in new_files or text_edits": 2. #385 at 00:01:52Z, #386 at 00:35:25Z.
3. "the source for 'test_formats_worked_example_markdown_output' doesn't define it": 3. #386 at 00:33:53Z, 00:37:07Z, 00:38:38Z.
4. "replace needs source. Only delete may omit ..." (the second error in the 14:19:22Z call): 1. #381.
- Where a refusal repeated on the same card with the same message: #386 message 3 (three calls); #386 messages 2 then 3 alternate across calls. The doc-criterion message (2) fired after PR #435 merged at 14:51:01Z; the earlier "isn't a test file" message (1) was the one before it. Whether any message misled the model: judgement, not mine.

## Operator's retro: Sprint 12 (closed early 2026-10-02, crew#451)

**Ticks:** 9 started. I stopped 5: ticks 1, 3, 4 and 5, plus tick 6, whose mixed versions were my fault. Ticks 7, 8 and 9 ran to the end.

### 3. Drift between the Goal and the plan
- The Sponsor's decisions on Goal #174 weren't in the Goal: Postgres (crew#280), unblocked events, counting a card where it finishes, OTLP (crew#283), and versioning (the #186 decisions). Found only by the Sponsor's review. The Goal was revised and the epics re-planned. crew#431 (the stale-Goal forecast) is the process answer.
- A design note's risk ("a restart loses all events") contradicted the Goal and went nowhere. Covered by crew#430, and now by the refinement panel in docs/plans/goal-174-service.md.

### 5. The crew's own retro (crew#451) against this one
- **It saw:** the failure causes, linked to the right crew issues (#448, #444, #436). First-attempt rate 3/8. #383's push rejection.
- **It missed:** 6 of 9 ticks. It counts from standups, and stopped ticks write none, so it reported "three ticks (~43 min)". Also missed: the Goal #174 review and re-plan, 12 superseded stories, the epics wrongly closed, all 24 interventions, and 14 crew PRs.
- **It got wrong** in "Needs you":
  - "make the package public": the Sponsor decided private (crew#398)
  - "`infra` doesn't exist": deliberate for now
  - #406–#416 "awaiting approval": approved and held on purpose
- **"Defects filed: none":** I'd filed everything live (#425, #428, #429–#431, #432, #436, #437, #439, #440, #444, #448, #449).

### Results worth keeping
- crew#436's keep-both step merged #383's PR #423 live (tests/test_crew_performance.py) instead of a rebuild. #384, the same pattern before it, had cost two rebuilds and a block.
- The criteria check (crew#428) passed on its first in-tick split (#414 → #420).
- Misleading refusal messages: 3 messages, 9 occurrences, at least 5 wasted attempts, and they drove #386 to the sprint's one escalation. One is fixed (#435); #448 is open.

### 6. Proposed for the crew iteration (for the Sponsor to agree)
1. **The refinement panel:** run experiments/panel-174, then build crew#440 as the panel. It holds back #406–#408, which are Goal #174's next product work.
2. **crew#448:** the existing-test refusal names `proven_by_existing`. Small; it cost the sprint's escalation.
3. **New:** the crew's retro counts ticks from the tick events, not standups; reads the operator and Sponsor interventions; and leaves out of "Needs you" anything held on purpose or already decided. Small, and it makes much of this operator retro the crew's own.

**Deferred, with reasons:**
- crew#449 (logging): large; its own iteration
- crew#439 (technical-epic classification): needed before the next design PR
- crew#436 criterion 3, crew#444, crew#431, crew#429, crew#430: waiting for more evidence or for the panel
