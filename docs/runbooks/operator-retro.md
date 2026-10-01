# Runbook: the operator's retro

**Started:** 2026-10-01, Sprint 12, agreed with the Sponsor. **Log:** [operator-retro-log.md](operator-retro-log.md).

## Why this exists

The crew's retro reads the sprint's board and standups and files a defect or two. It doesn't see what was done *around* the crew: the operator's fixes, stopped ticks, cards moved by hand, the Sponsor's decisions. On 2026-10-01 that was most of the signal. Nine crew PRs merged mid-sprint, the tick was stopped five times, and almost every defect was found by watching and then patched live.

So, for a few sprints:
- **During a sprint, the operator watches and files.** Only incidents are fixed live.
- **At sprint close, the operator works through this checklist** as if running the retro, and logs what was looked at, what was done, why, and what it gained.
- **After a few sprints,** the log shows which checks earn their place. The cheap ones become part of the crew's own process, such as the retro reading interventions or a focused pass proposing next actions. A reminder skill comes only if this gets forgotten.

The point is evidence: what was actually found, and what it changed. Impressions don't count.

## During the sprint: incident or file?

**An incident** is fixed live:
- a tick that can't run, or runs wrong code (mixed versions)
- the board being corrupted: wrong closes, superseded work counted as delivered
- every story being blocked

**Everything else is filed:** an issue labelled with its priority, and a line in the working log. That includes repeated rebuilds, a misleading message, or a gap in a check.

**Before calling something a routing or ordering bug, read what the role said** against the Goal, the sibling epics and the Sponsor's recorded decisions. A block is usually content.

**Never update the crew checkout while a tick runs.** Phases import lazily, and the tick runs two versions at once.

The working log during a sprint is `var/notes/operator-log-<sprint>.md` (not committed). It goes into the repo log at sprint close.

## At sprint close: the checklist

Work through each item. Log a finding only when there's evidence: a card, an event, a number.

### 1. Interventions
- [ ] Every manual step: `var/events/operator.jsonl`, `var/events/sponsor.jsonl`, `crew moves --people`. For each one: why was a person needed, and could the crew have done it?
- [ ] Ticks stopped or restarted, and why.
- [ ] Crew PRs merged during the sprint: incident fixes, or things that could have waited?

### 2. Where a person was asked
- [ ] Every `needs:human` and `blocked`. Was it content (the work was wrong) or mechanism (the crew couldn't proceed)? Was the person's answer recorded where the crew reads it?
- [ ] Every question that reached the Sponsor. Was it theirs to answer?

### 3. Drift between the Goal and the plan
- [ ] Sponsor decisions made during the sprint (issue comments, chat, crew issues). Is each one in the Goal, the record, or an epic? Anything that isn't is a stale-Goal signal (crew#431).
- [ ] Design notes' risks and deferrals. Does any of them leave part of a Goal undelivered (crew#430)?

### 4. Repeats and cost
- [ ] Failure causes by fingerprint (the retro's "why work didn't land first time"). Has any repeated across sprints?
- [ ] Rebuilds, gate round trips, stories returned to refinement: count them, and estimate the time each cost.
- [ ] Model failures by class (`llm.failed`, `llm.empty`, SCHEMA refusals). Did any message mislead the model (as the doc-criterion refusal did)?

### 5. The crew's own retro
- [ ] Compare it with this checklist. What did it see that I didn't, and the other way round?
- [ ] Its filed defects. Duplicates of mine, or new?

### 6. The proposal
- [ ] From the above, the **few** crew changes for the next crew iteration, ranked, for the Sponsor to agree. Each one names its evidence.
- [ ] Any check on this list that found nothing for two sprints: candidate for removal.
- [ ] Any check that found something every sprint: candidate for the crew to do itself.

## Log entry format

```
### <sprint>, <date>: <short title>
- **Looked at:** what was read, with links (cards, events, PRs)
- **Did:** the action, or "filed #N", or "nothing"
- **Why:** the reason, incident or not
- **Gained:** the evidence it produced: a number, a defect found, time saved or lost
- **Follow-up:** issue, process candidate, or none
```
