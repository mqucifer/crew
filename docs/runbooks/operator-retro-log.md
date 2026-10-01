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
