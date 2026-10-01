---
name: operator-retro
description: The operator's retro procedure. Record the facts of a finished tick in the sprint's working log, or gather a sprint's retro evidence at close. Facts only; never decides, files, moves or fixes anything.
argument-hint: "watch <sprint> | evidence <sprint> <start-date YYYY-MM-DD>"
---

# The operator's retro: the facts

The runbook is `docs/runbooks/operator-retro.md`. Read it first, every time. You start with no memory of earlier runs, so the runbook and the working log are your whole context.

**You gather facts. You decide nothing.**
- Never file an issue, comment, move a card, label, merge, stop a tick, or change code or config.
- Never call something an incident or a defect, and never say what caused it. You may write *possible incident* or *worth a look*, quoting the event that makes it so.
- Every fact names its source: an event (file, timestamp, kind), a card (`sprint-metrics#N`), a PR, or a command's output.
- Write only to `var/notes/operator-log-<sprint>.md`, and only by appending. Never touch the main checkout's branch: a tick may be running from it.
- Read GitHub with `gh` (view and list only), and the crew's events in `var/events/*.jsonl`.
- **Quote on one line:** collapse newlines in anything you quote to a space.
- **How the events are shaped:** one tick has several passes. Each pass emits `tick.started` events ("pass N", "reading board") and `tick.finished` events (refinement, admission). None of these mark a tick's start or end. Don't infer tick boundaries from them. Whether a tick stopped is known from its process, not from you.

## `watch <sprint>`: one finished tick into the working log

1. Read the working log `var/notes/operator-log-<sprint>.md`. Its last line `<!-- watched-to: <ISO timestamp> -->` says where the last run stopped. If there's none, start from the first `tick.started` today.
2. Read every event after that timestamp in `var/events/*.jsonl`.
3. Record, under a heading `### Tick observed: <first timestamp>–<last timestamp>`:
   - **What moved:** stories delivered, approved, accepted by QA, merged (card numbers, PRs).
   - **Where a person was asked or a card stopped:** each `card.blocked`, each label containing `needs:human`, each `story.returned`, each rebuild ("returned for a rebuild"), each "kept both sides". Give the card, the event's summary verbatim, and the card's last comment (first 300 characters, via `gh issue view`).
   - **Failures:** each `llm.failed`, `task.failed`, `agent.failed` and `llm.empty`: the card, the role, and the first line of the error. Group repeats of the same first line and give the count.
   - **The operator's and Sponsor's own steps:** every event in `operator.jsonl` and `sponsor.jsonl` in the window, listed here even if it also appears above.
   - **Possible incidents,** listed last, only when an event shows one of these:
     - a Python error in a phase (a summary containing "failed: " with an exception name, such as "deliver failed: ImportError")
     - a card closed as completed that had no merged PR
     - every story blocked
4. End with `<!-- watched-to: <last event's timestamp> -->`.
5. Your answer to whoever ran you is the list of possible incidents only, or "none". Nothing else.

## `evidence <sprint> <start-date>`: the sprint's retro evidence

Facts for the runbook's checklist, sections 1, 2 and 4. Sections 3, 5 and 6 (drift, the comparison with the crew's own retro, the proposal) are judgement, and not yours.

Append `## Evidence for the retro: <sprint>` to the working log, with:

1. **Interventions:**
   - every event in `operator.jsonl` and `sponsor.jsonl` since the start date, in a table: time, card, summary;
   - crew PRs merged since the start date (`gh pr list -R mqucifer/crew --state merged --search "merged:>=<start-date>"`): number and title;
   - ticks: count the `tick.started` events with summary "pass 1". Each one is a tick. (Whether a tick was stopped is the operator's to add.)
2. **Where a person was asked:** every card that carried `needs:human` or `blocked` in the window, with its final state now (`gh issue view`).
3. **Repeats and cost:**
   - rebuilds per card, gate round trips per card, stories returned to refinement, "kept both sides" merges;
   - failures by first line, with counts and cards;
   - `llm.empty` count;
   - SCHEMA refusals by message.
4. A one-line count per section at the top.

Your answer is the section's heading line and the counts. The detail stays in the log.
