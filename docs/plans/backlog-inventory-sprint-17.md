# Crew backlog inventory, for the Sprint 17 planning session

The input to the planning session at the close of Sprint 17 ([crew#491](https://github.com/mqucifer/crew/issues/491)). It lists every open crew issue and discussion, grouped by theme, with each one's P-label, its state and what it depends on. **Facts only:** what to do with them is the session's to decide.

As of 2026-10-07, after Sprint 17's close. mqucifer/sprint-metrics#406 is delivered and released as v1.1.0; the crew's retro is [crew#505](https://github.com/mqucifer/crew/issues/505), and the operator's is in `docs/runbooks/operator-retro-log.md`.

## At a glance

| | Count |
|---|---|
| Open issues | 42: 5 P1, 14 P2, 14 P3, 9 unlabelled (4 retros, 4 retro findings, and [crew#500](https://github.com/mqucifer/crew/issues/500)) |
| Open discussions | 12, all ideas |
| Closed during Sprint 17 | [crew#436](https://github.com/mqucifer/crew/issues/436) (stories in different epics editing the same files), [crew#456](https://github.com/mqucifer/crew/issues/456) (no bare issue numbers), [crew#489](https://github.com/mqucifer/crew/issues/489) (the repair form's message), [crew#398](https://github.com/mqucifer/crew/issues/398) (a private image by choice), [crew#491](https://github.com/mqucifer/crew/issues/491) (this inventory), [crew#494](https://github.com/mqucifer/crew/issues/494) (container logs on failure), [crew#497](https://github.com/mqucifer/crew/issues/497) (added definitions before `__main__`) |

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
| [crew#440](https://github.com/mqucifer/crew/issues/440) Refinement panel and conclusion before the split | P1 | Steps 3–8 merged. Step 9, the proof, ran in Sprint 17: mqucifer/sprint-metrics#406 split into 8 stories, then a release story; all 9 delivered and released as v1.1.0. | [crew#468](https://github.com/mqucifer/crew/issues/468), [crew#475](https://github.com/mqucifer/crew/issues/475) |
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
| [crew#280](https://github.com/mqucifer/crew/issues/280) The crew runs sprint-metrics as a service and asks it | P3 | The service it needs shipped in mqucifer/sprint-metrics v1.1.0 (mqucifer/sprint-metrics#406); Goal 174's mqucifer/sprint-metrics#407 and mqucifer/sprint-metrics#408 are still held. One Postgres shared across the stack (Sponsor, 2026-10-01). | [crew#475](https://github.com/mqucifer/crew/issues/475), [crew#283](https://github.com/mqucifer/crew/issues/283) |
| [crew#500](https://github.com/mqucifer/crew/issues/500) DevOps proposes a release when a product epic completes | none | Not started. Builds ADR 0019. Also: release stories get the real date, and declare the tests that pin the version. | [crew#335](https://github.com/mqucifer/crew/issues/335) |
| [D328](https://github.com/mqucifer/crew/discussions/328) Tailscale and remote development | idea | Notes on how `infra` holds specifics (2026-09-28). | [crew#335](https://github.com/mqucifer/crew/issues/335) |
| [D401](https://github.com/mqucifer/crew/discussions/401) Backing up the crew's history | idea | Not scheduled. | [D262](https://github.com/mqucifer/crew/discussions/262) |

### Logging and telemetry

| Item | P | State | Depends on / relates to |
|---|---|---|---|
| [crew#449](https://github.com/mqucifer/crew/issues/449) One shared logging library | P2 | Part 1 merged (ADR 0014): a levelled stream, `crew log`, and OTLP to Grafana with trace links. Parts 2–4 open. Follow-ups noted from Sprint 17: `tick.finished` has no trace link; `crew log --last 0` replays the whole log; a retried form refusal is logged as a warning, and twice (every `llm.failed` event is written twice); verification output is kept to 600 characters. | [crew#283](https://github.com/mqucifer/crew/issues/283), [crew#414](https://github.com/mqucifer/crew/issues/414) |
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
| [D453](https://github.com/mqucifer/crew/discussions/453) The retro sees the whole sprint | idea | Below P3 (Sponsor, 2026-10-02). Sprint 17's retro again listed held epics under "Needs you" and called refinement hours idle. | [crew#506](https://github.com/mqucifer/crew/issues/506) |
| [crew#506](https://github.com/mqucifer/crew/issues/506) The retro links a delivery repo's cards to the crew issue with the same number | P2 | Filed at Sprint 17's close. | [crew#456](https://github.com/mqucifer/crew/issues/456) |
| [crew#502](https://github.com/mqucifer/crew/issues/502) A story spanning two layers loses import context | none | Retro finding, Sprint 17. The operator's evidence points at the misleading refusal instead (crew#489, fixed). | [crew#504](https://github.com/mqucifer/crew/issues/504) |
| [crew#503](https://github.com/mqucifer/crew/issues/503) A documentation story's criteria don't say what the tests check | none | Retro finding, Sprint 17. Explained by [crew#497](https://github.com/mqucifer/crew/issues/497), fixed; the operator suggests closing it. | |
| [crew#504](https://github.com/mqucifer/crew/issues/504) Recurring lint F821: an undefined name | none | Retro finding, Sprint 17: 2 cards. | [crew#502](https://github.com/mqucifer/crew/issues/502) |

### Pilots and the north star

| Item | State |
|---|---|
| [D263](https://github.com/mqucifer/crew/discussions/263) North star: an autonomous 3D-printing project | The pilots are steps toward it. |
| [D260](https://github.com/mqucifer/crew/discussions/260) Presentation site: replays and metrics on GitHub Pages | Comes after sprint-metrics' release. The design reference is set. |
| [D262](https://github.com/mqucifer/crew/discussions/262) Model performance project | Groundwork only: keep the raw data stable and versioned. |
| [D282](https://github.com/mqucifer/crew/discussions/282) DORA metrics proper | Waits on releases. |
| [D359](https://github.com/mqucifer/crew/discussions/359) Panels and teams | Results recorded (2026-09-29). The refinement panel ([crew#440](https://github.com/mqucifer/crew/issues/440)) used them. |
| [D422](https://github.com/mqucifer/crew/discussions/422) Try other models on the crew's work | Ornith-1.5 and Qwen3.8-Flash-Next tried. Not scheduled. |

## Raised during Sprint 17

### An idea, parked as a discussion

**A decision model that pre-loads what a step needs** ([D499](https://github.com/mqucifer/crew/discussions/499)). The Sponsor raised it on 2026-10-07 while exploring. It belongs to the model-performance project ([D262](https://github.com/mqucifer/crew/discussions/262)), not the backlog. Its first test is an offline replay: 11 of the Developer's 35 calls in Sprint 17 only asked to see files first.

### Generator gaps seen in Sprint 17

These are from mqucifer/sprint-metrics#406's delivery. They are evidence for the session, not yet triaged:
- **Contract, storage and runtime**, put on [D261](https://github.com/mqucifer/crew/discussions/261) as evidence for the QA suite:
  - `card.created` is optional in the intake schema but `NOT NULL` in the table;
  - a store never committed;
  - CI's tests job has no database;
  - the service exits at startup when the database is unreachable, against the Product Owner's decision to keep running and return 503.
- **The deploy review runs only for Dockerfile and workflow changes.** A new dependency that needs a system library (`psycopg` needs `libpq`) reached a slim image unreviewed, and surfaced only when mqucifer/sprint-metrics#433 first ran the image as a service.
- **The Code Reviewer and QA passed a change whose CI was red.** The delivery gate caught it, and returned it with the log.
- **Container logs on failure:** the Developer added `docker logs` on its own after two blind failures, and then found the cause. It's now §19 rule 8 ([crew#494](https://github.com/mqucifer/crew/issues/494)).
- **Text edits for Python definitions:** the Developer reached for a text edit to add a definition three times across mqucifer/sprint-metrics#434 and mqucifer/sprint-metrics#443. The refusal names `edits` as the way.
- **mqucifer/sprint-metrics#443 was blocked** once the sprint's single escalation was spent on mqucifer/sprint-metrics#427. The cause was the edit tool: an added definition landed after the module's `__main__` block ([crew#497](https://github.com/mqucifer/crew/issues/497), fixed by PR 498), and it landed on its next delivery. Its prompts were 24,865 to 55,137 tokens, not the cause.
- **The release check re-reads a verified release on every pass:** it remembers only versions it filed an epic for.
- **Nothing files a release when an epic completes:** mqucifer/sprint-metrics#406's release story was filed by hand, and went back to refinement once over tests pinning the version. Now ADR 0019, built by [crew#500](https://github.com/mqucifer/crew/issues/500).
- **Misleading refusal messages drove the sprint's escalation, two sprints running** (crew#448 in Sprint 12, [crew#489](https://github.com/mqucifer/crew/issues/489) in Sprint 17). The Business Analyst was also refused 3 times for row IDs like "I1" where the form wants "R3".
- **`crew moves --people` shows nothing after 2026-09-27**, so the board's record of hand moves is incomplete. Not yet filed.

## Housekeeping

These are facts the session may want to act on:
- **Retro issues still open:** [crew#373](https://github.com/mqucifer/crew/issues/373) (Sprint 10), [crew#420](https://github.com/mqucifer/crew/issues/420) (Sprint 11), [crew#451](https://github.com/mqucifer/crew/issues/451) (Sprint 12) and [crew#505](https://github.com/mqucifer/crew/issues/505) (Sprint 17).
- **Possibly met:** [crew#440](https://github.com/mqucifer/crew/issues/440), whose proof delivered and released mqucifer/sprint-metrics#406 in Sprint 17; and [crew#80](https://github.com/mqucifer/crew/issues/80), whose answer was the bridge fix, now closed.
- **Overlaps:** [crew#190](https://github.com/mqucifer/crew/issues/190) with [crew#468](https://github.com/mqucifer/crew/issues/468) and the ADR process; [crew#414](https://github.com/mqucifer/crew/issues/414) partly with [crew#449](https://github.com/mqucifer/crew/issues/449).
- **Untriaged, no P-label:** [crew#419](https://github.com/mqucifer/crew/issues/419), [crew#500](https://github.com/mqucifer/crew/issues/500), [crew#502](https://github.com/mqucifer/crew/issues/502), [crew#503](https://github.com/mqucifer/crew/issues/503) and [crew#504](https://github.com/mqucifer/crew/issues/504).
- **The changelog's 1.0.x dates** in mqucifer/sprint-metrics say 2025; they were 2026-09-30. To fix in the next release.
