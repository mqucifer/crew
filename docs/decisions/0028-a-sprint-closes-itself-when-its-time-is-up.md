# 28. A sprint closes itself when its time is up

- **Date:** 2026-10-12
- **Status:** Accepted

## Context

A sprint was closed by hand with `crew sprint close`. Until someone ran it, the old sprint stayed open, and the crew's numbers came out differently depending on who counted:

- Sprint 22's day ended at midnight on 2026-10-11, and Sprint 23's first tick still found it open.
- Cards committed to Sprint 21 were finished on Sprint 22's day: two by a tick that started before midnight, three by Sprint 22's ticks while the board still had them in Sprint 21.
- The close counts by the board's Sprint field, and sprint-metrics counts by the day work finished (crew#280). The close said Sprint 22 delivered 3 stories and 7 points, and sprint-metrics said 8 cards and 21 points.

Sprints are one day long, so a close left for a person to run falls behind every day.

## Decision

**A sprint closes itself once its time is up, at a tick's start or end** (the Sponsor, 2026-10-12). Never in the middle of a tick: a tick works the sprint it started in.

- **At a tick's start,** the sprint that ended most recently is closed if nothing has closed it yet.
- **At a tick's end,** the sprint the tick worked is closed if its last day passed while the tick ran.

A close is due for a sprint whose last day is past, that has cards, and whose retro isn't recorded. Whatever closes it first records the retro, so it closes once. That can be a tick, or `crew sprint close`, which stays for closing early or by hand.

**The close is the one `crew sprint close` runs:**
- it merges what the Sponsor approved;
- it moves approved, unmerged stories to the next sprint;
- it records the retro and files its findings.

A close that fails is said, and doesn't stop the tick. The next tick's start tries again, because the sprint still has no retro.

**No card changes column when a sprint closes.** What was already true stays true:
- a story never started goes back to Ready at the next admission, and is planned again with its siblings by priority and capacity (crew#353);
- started work (In Progress, Reviewing, QAing, Blocked) carries on whatever sprint it's in, because review, QA and rework don't ask which sprint a card is in.

## Consequences

- The board and sprint-metrics agree on what a sprint delivered, except for cards finished by a tick that runs across midnight. Those count in the sprint whose day they finished on for sprint-metrics, and in the sprint the tick worked for the close.
- The Sponsor's sprint-end review (ADR 0003) reads a report the crew recorded at the date change, rather than starting the close.
- A tick that starts after a sprint ended spends its first minutes on the close, including the retro's model calls.
- A sprint skipped entirely, with no ticks and no cards, has nothing to close and gets no retro.

## Changelog

- **2026-10-12:** decided by the Sponsor in chat, after the Sprint 22 counts disagreed (crew#280).
