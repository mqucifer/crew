# Crew backlog inventory, for the Sprint 17 planning session

The input to the planning session at the close of Sprint 17 ([crew#491](https://github.com/mqucifer/crew/issues/491)). It lists every open crew issue and discussion, grouped by theme, with each one's P-label, its state and what it depends on. **Facts only:** what to do with them is the session's to decide.

As of 2026-10-06, Sprint 17 under way. It's refreshed at sprint close, so the sprint's new findings are in it.

## At a glance

| | Count |
|---|---|
| Open issues | 38: 5 P1, 14 P2, 14 P3, 5 unlabelled (3 retros, 1 retro finding, 1 standup) |
| Open discussions | 11, all ideas |
| Closed during Sprint 17 so far | [crew#489](https://github.com/mqucifer/crew/issues/489) (the repair form's message) |

## The question the session starts from

The Sponsor, 2026-10-06: when does the QA-owned test suite come in, compared to the infra project and DevOps owning more?

| | What it needs first | Where that's recorded |
|---|---|---|
| **QA-owned test suite** ([D261](https://github.com/mqucifer/crew/discussions/261)) | QA can write to a repo, which it can't today. CI runs a short-lived stack: the service plus a throwaway Postgres, seeded by QA. DevOps makes CI right for what the tests need. | [D261](https://github.com/mqucifer/crew/discussions/261), entry of 2026-10-01 |
| **DevOps owns more** ([crew#335](https://github.com/mqucifer/crew/issues/335)) | The role's next phase waits for `infra` to be onboarded, after the presentation pilot (Sponsor, 2026-09-30). | [crew#335](https://github.com/mqucifer/crew/issues/335), last comment |
| **Onboard infra** ([crew#475](https://github.com/mqucifer/crew/issues/475)) | Its first Goals start from the "For infra" items product epics leave in their conclusions (ADR 0017). | [crew#475](https://github.com/mqucifer/crew/issues/475) |

Evidence from Sprint 17 for the QA suite is on [D261](https://github.com/mqucifer/crew/discussions/261) (2026-10-06). On mqucifer/sprint-metrics#406's stories, three gaps reached a green CI:
- the intake schema and the table disagree on `card.created`;
- a store never committed;
- CI's tests job has no database.

## By theme

### Refinement and the Product Owner

| Item | P | State | Depends on / relates to |
|---|---|---|---|
| [crew#440](https://github.com/mqucifer/crew/issues/440) Refinement panel and conclusion before the split | P1 | Steps 3–8 merged. Step 9, the proof, ran in Sprint 17: mqucifer/sprint-metrics#406 split into 8 stories, all admitted. | [crew#468](https://github.com/mqucifer/crew/issues/468), [crew#475](https://github.com/mqucifer/crew/issues/475) |
| [crew#468](https://github.com/mqucifer/crew/issues/468) The project's decision log, written by the Product Owner | P1 | Part 1 merged: the panel and the split are shown the log. Criterion 3 reworded by ADR 0018. | [crew#431](https://github.com/mqucifer/crew/issues/431), [crew#190](https://github.com/mqucifer/crew/issues/190) |
| [crew#429](https://github.com/mqucifer/crew/issues/429) A Goal gains an epic without superseding the epics under way | P1 | Not started. From Sprint 12. | [crew#430](https://github.com/mqucifer/crew/issues/430), [crew#431](https://github.com/mqucifer/crew/issues/431) |
| [crew#431](https://github.com/mqucifer/crew/issues/431) The Product Owner forecasts which Goals have gone stale | P1 | Not started. From Sprint 12. | [crew#468](https://github.com/mqucifer/crew/issues/468), [crew#429](https://github.com/mqucifer/crew/issues/429), [crew#291](https://github.com/mqucifer/crew/issues/291) |
| [crew#439](https://github.com/mqucifer/crew/issues/439) A design's declared changes become technical epics even when they aren't | P1 | Not started. Technical work blocking epic work is intended (Sponsor, 2026-10-06); the issue is what gets classed as technical. | [crew#430](https://github.com/mqucifer/crew/issues/430) |
| [crew#430](https://github.com/mqucifer/crew/issues/430) A design note's risks are checked against the Goal | P2 | Not started. | [crew#429](https://github.com/mqucifer/crew/issues/429) |
| [crew#394](https://github.com/mqucifer/crew/issues/394) Design and presentation notes ask for the context they need | P2 | Not started. | [crew#457](https://github.com/mqucifer/crew/issues/457) |
| [crew#457](https://github.com/mqucifer/crew/issues/457) Every role's hand-off ends in a concise conclusion table | P2 | Not started. | [crew#394](https://github.com/mqucifer/crew/issues/394) |
| [crew#291](https://github.com/mqucifer/crew/issues/291) The Product Owner is shown a Goal's comments | P3 | Not started. | [crew#431](https://github.com/mqucifer/crew/issues/431) |
| [crew#190](https://github.com/mqucifer/crew/issues/190) Decisions are recorded where they reach | P3 | Not started. Overlaps the decision log ([crew#468](https://github.com/mqucifer/crew/issues/468)) and the ADR process in `docs/decisions/README.md`. | [crew#468](https://github.com/mqucifer/crew/issues/468) |
| [crew#408](https://github.com/mqucifer/crew/issues/408) A presentation note is checked against its stories' criteria | P3 | Not started. | [D261](https://github.com/mqucifer/crew/discussions/261) |

### Delivery

| Item | P | State | Depends on / relates to |
|---|---|---|---|
| [crew#276](https://github.com/mqucifer/crew/issues/276) A failed one-shot attempt's retry runs in steps | P2 | Design options recorded (2026-09-28). Not started. | [crew#299](https://github.com/mqucifer/crew/issues/299) |
| [crew#299](https://github.com/mqucifer/crew/issues/299) Run independent work in parallel | P2 | Per-role plan from the panel test ([D359](https://github.com/mqucifer/crew/discussions/359)). Not started. | [D359](https://github.com/mqucifer/crew/discussions/359), [crew#276](https://github.com/mqucifer/crew/issues/276) |
| [crew#383](https://github.com/mqucifer/crew/issues/383) Dependency update PRs the crew didn't open | P2 | Not started. | [crew#335](https://github.com/mqucifer/crew/issues/335) |
| [crew#444](https://github.com/mqucifer/crew/issues/444) A push GitHub rejects is retried, not blocked | P2 | Not started. | [crew#414](https://github.com/mqucifer/crew/issues/414) |
| [crew#407](https://github.com/mqucifer/crew/issues/407) Bugs get a path through the flow | P3 | Not started. | |
| [crew#414](https://github.com/mqucifer/crew/issues/414) A failed phase records where | P3 | Not started. Partly met by the logging library ([crew#449](https://github.com/mqucifer/crew/issues/449)), which logs each phase's failure with its pass and phase. | [crew#449](https://github.com/mqucifer/crew/issues/449) |
| [crew#354](https://github.com/mqucifer/crew/issues/354) A story's criteria name CI runs as unit tests | P3 | Triage (2026-09-29): mostly covered; what's left is upstream, in what the Business Analyst is shown. | [crew#419](https://github.com/mqucifer/crew/issues/419), [crew#370](https://github.com/mqucifer/crew/issues/370) |
| [crew#370](https://github.com/mqucifer/crew/issues/370) A pin-by-digest story's criteria omit the value's source | P3 | Triage: the mechanism is fixed; a criteria-wording point remains. | [crew#354](https://github.com/mqucifer/crew/issues/354) |
| [crew#419](https://github.com/mqucifer/crew/issues/419) Criteria naming internals break when a sibling refactors them | none | Retro finding, Sprint 11. Untriaged. | [crew#354](https://github.com/mqucifer/crew/issues/354) |

### QA suite and more than Python

| Item | P | State | Depends on / relates to |
|---|---|---|---|
| [D261](https://github.com/mqucifer/crew/discussions/261) QA-owned test suite | idea | Shape agreed 2026-09-26 and 2026-10-01. Evidence added 2026-10-06. | [crew#335](https://github.com/mqucifer/crew/issues/335), [crew#405](https://github.com/mqucifer/crew/issues/405) |
| [crew#376](https://github.com/mqucifer/crew/issues/376) A toolchain profile per project part | P2 | Plan of 2026-09-30, in phases. | [crew#405](https://github.com/mqucifer/crew/issues/405), [D260](https://github.com/mqucifer/crew/discussions/260) |
| [crew#405](https://github.com/mqucifer/crew/issues/405) Phase 3: web QA, and contract tests between projects | P2 | Not started. Builds on phase 2. | [crew#376](https://github.com/mqucifer/crew/issues/376), [D261](https://github.com/mqucifer/crew/discussions/261) |

### DevOps and infra

| Item | P | State | Depends on / relates to |
|---|---|---|---|
| [crew#335](https://github.com/mqucifer/crew/issues/335) A DevOps Engineer owns infra, CI/CD, releases and the stack | P2 | Tracks the role's phases. The next waits for infra's onboarding. DevOps already sits on every refinement panel ([crew#440](https://github.com/mqucifer/crew/issues/440)). | [crew#475](https://github.com/mqucifer/crew/issues/475), [D261](https://github.com/mqucifer/crew/discussions/261) |
| [crew#475](https://github.com/mqucifer/crew/issues/475) Onboard infra from the notes product epics left | P2 | Not started. mqucifer/sprint-metrics#406 left one item for infra: the orchestrator's probe configuration. | [crew#335](https://github.com/mqucifer/crew/issues/335), [crew#440](https://github.com/mqucifer/crew/issues/440) |
| [crew#280](https://github.com/mqucifer/crew/issues/280) The crew runs sprint-metrics as a service and asks it | P3 | Waits on mqucifer/sprint-metrics Goal 174, being delivered in Sprint 17. One Postgres shared across the stack (Sponsor, 2026-10-01). | [crew#475](https://github.com/mqucifer/crew/issues/475), [crew#283](https://github.com/mqucifer/crew/issues/283) |
| [crew#398](https://github.com/mqucifer/crew/issues/398) A private image is the Sponsor's choice | P2 | Not started. Until it lands, every tick holds on "make the package public" (from crew#386, closed). | |
| [D328](https://github.com/mqucifer/crew/discussions/328) Tailscale and remote development | idea | Notes on how `infra` holds specifics (2026-09-28). | [crew#335](https://github.com/mqucifer/crew/issues/335) |
| [D401](https://github.com/mqucifer/crew/discussions/401) Backing up the crew's history | idea | Not scheduled. | [D262](https://github.com/mqucifer/crew/discussions/262) |

### Logging and telemetry

| Item | P | State | Depends on / relates to |
|---|---|---|---|
| [crew#449](https://github.com/mqucifer/crew/issues/449) One shared logging library | P2 | Part 1 merged (ADR 0014): a levelled stream, `crew log`, and OTLP to Grafana with trace links. Parts 2–4 open. Follow-ups noted from Sprint 17: `tick.finished` has no trace link; `crew log --last 0` replays the whole log; a retried form refusal is logged as a warning, and twice. | [crew#283](https://github.com/mqucifer/crew/issues/283), [crew#414](https://github.com/mqucifer/crew/issues/414) |
| [crew#283](https://github.com/mqucifer/crew/issues/283) The crew's events exported as OpenTelemetry | P3 | Criteria 1, 3, 4 and 5 met (2026-09-29). Left: criterion 2, LiteLLM's own spans nested under the crew's. | [crew#449](https://github.com/mqucifer/crew/issues/449) |
| [crew#80](https://github.com/mqucifer/crew/issues/80) Spike: what reads back what a tick records about the model | P3 | Answered 2026-09-23: became the bridge fix, crew#117, since closed. | [crew#283](https://github.com/mqucifer/crew/issues/283) |
| [D402](https://github.com/mqucifer/crew/discussions/402) Trusting GitHub less | idea | Not scheduled. | |

### Sprints and capacity

| Item | P | State | Depends on / relates to |
|---|---|---|---|
| [crew#26](https://github.com/mqucifer/crew/issues/26) A sprint ends when its work is done | P3 | To be decided with [crew#114](https://github.com/mqucifer/crew/issues/114). | [crew#114](https://github.com/mqucifer/crew/issues/114) |
| [crew#114](https://github.com/mqucifer/crew/issues/114) Spike: sprints that keep running without a person adding them | P3 | Not started. Board iterations are changed only by the Sponsor, in the UI. | [crew#26](https://github.com/mqucifer/crew/issues/26), [crew#88](https://github.com/mqucifer/crew/issues/88) |
| [crew#88](https://github.com/mqucifer/crew/issues/88) Capacity learned, not typed | P3 | Not started. | [crew#26](https://github.com/mqucifer/crew/issues/26) |
| [crew#379](https://github.com/mqucifer/crew/issues/379) `sprint start --dry-run` plans from stale inputs | P2 (bug) | Not started. | |

### What the crew learns

| Item | P | State | Depends on / relates to |
|---|---|---|---|
| [crew#112](https://github.com/mqucifer/crew/issues/112) The crew writes what it learns into the project's record | P3 | Not started. | [crew#468](https://github.com/mqucifer/crew/issues/468) |
| [D453](https://github.com/mqucifer/crew/discussions/453) The retro sees the whole sprint | idea | Below P3 (Sponsor, 2026-10-02). | |

### Pilots and the north star

| Item | State |
|---|---|
| [D263](https://github.com/mqucifer/crew/discussions/263) North star: an autonomous 3D-printing project | The pilots are steps toward it. |
| [D260](https://github.com/mqucifer/crew/discussions/260) Presentation site: replays and metrics on GitHub Pages | Comes after sprint-metrics' release. The design reference is set. |
| [D262](https://github.com/mqucifer/crew/discussions/262) Model performance project | Groundwork only: keep the raw data stable and versioned. |
| [D282](https://github.com/mqucifer/crew/discussions/282) DORA metrics proper | Waits on releases. |
| [D359](https://github.com/mqucifer/crew/discussions/359) Panels and teams | Results recorded (2026-09-29). The refinement panel ([crew#440](https://github.com/mqucifer/crew/issues/440)) used them. |
| [D422](https://github.com/mqucifer/crew/discussions/422) Try other models on the crew's work | Ornith-1.5 and Qwen3.8-Flash-Next tried. Not scheduled. |

## Housekeeping

These are facts the session may want to act on:
- **Retro issues still open:** [crew#373](https://github.com/mqucifer/crew/issues/373) (Sprint 10), [crew#420](https://github.com/mqucifer/crew/issues/420) (Sprint 11) and [crew#451](https://github.com/mqucifer/crew/issues/451) (Sprint 12).
- **Possibly met:** [crew#440](https://github.com/mqucifer/crew/issues/440), whose proof ran in Sprint 17; and [crew#80](https://github.com/mqucifer/crew/issues/80), whose answer was the bridge fix, now closed.
- **Overlaps:** [crew#190](https://github.com/mqucifer/crew/issues/190) with [crew#468](https://github.com/mqucifer/crew/issues/468) and the ADR process; [crew#414](https://github.com/mqucifer/crew/issues/414) partly with [crew#449](https://github.com/mqucifer/crew/issues/449).
- **Untriaged:** [crew#419](https://github.com/mqucifer/crew/issues/419) has no P-label.
- **A hold on every tick** ("make the package public") ends with [crew#398](https://github.com/mqucifer/crew/issues/398).
