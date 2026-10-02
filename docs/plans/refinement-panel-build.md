# Building the refinement panel

**Bottom line:** build the panel and its settle step into refinement, then prove them on sprint-metrics #406. Success means #406 splits without the contradictions and missed decisions of 2026-10-01.

**Status:** planned with the Sponsor on 2026-10-02. Nothing is built yet. The decisions behind this plan are in part 5 of [`goal-174-service.md`](goal-174-service.md), and the evidence for them is in [`experiments/panel-174/README.md`](../../experiments/panel-174/README.md).

## Where things stand (2026-10-02)

| Item | State | Where |
|---|---|---|
| Panel test, 108 calls | Done, scored | `experiments/panel-174/`, crew PR #455 |
| Part 5 decisions | Agreed, on PR #455 | `docs/plans/goal-174-service.md` |
| Goal #174 decision log | Posted, 12 rows | sprint-metrics #174 body, "Sponsor decisions" |
| Concise hand-offs, crew-wide | Filed, P2 | crew#457 |
| No bare issue numbers | Filed, P2 | crew#456 |
| The panel's own issue | To reshape (step 2) | crew#440 |
| Epics #406–#408 | Approved, held until the panel exists | sprint-metrics board, Inbox |
| Sprint 13 | Not started; needs the Sponsor | Board |

## The agreed design

| ID | Context | Decision | Consequences |
|---|---|---|---|
| A1 | Who | Architect, UX Designer, QA, DevOps; "nothing to add" allowed | Replaces labels as the trigger for these roles |
| A2 | When | After Sponsor approval, before the split; one epic per pass | Clashes between epics are left to the criteria check and the PO |
| A3 | How | The four calls in parallel | About 5 minutes per epic; crew#299's first case |
| A4 | Context | Goal (with decision log), record, epic, sibling bodies, decisions by rule | The same context for every member |
| A5 | Focus | Each reviews the epic in front of it | How it fits a sibling: raise it. Wholly inside a sibling, or a delivered epic: don't |
| A6 | Blind spots | DevOps checks environments; Architect checks SemVer and sibling dependencies | Covers findings 10, 5 and 9 from the test |
| A7 | Discussion | Full notes are kept as one comment | The audit trail; never passed on |
| A8 | Settle | The PO writes the conclusion; one question to the Sponsor if the sources can't answer | The epic waits for the reply, as story problems do |
| A9 | Conclusion | In the epic body, under its own header, with the approved text untouched | BLUF line, ADR-field rows, open questions apart |
| A10 | Format | Short cells enforced by schema; no cap on rows; links with words | The same form as the Goal's decision log |
| A11 | Down the chain | Stories name the rows they follow | Later steps get those rows only; a coverage check confirms every row is used |
| A12 | References | No bare issue numbers in anything posted | crew#456 |

## Build steps

Each step is one PR. Anything that calls a model or GitHub is proven with one real call before its PR, because mocks hid the crash in crew#294. Nothing is pulled while a tick runs.

| Step | What | Done when |
|---|---|---|
| 1 | Merge PR #455; delete the `exp/panel-174` branch | Part 5 and the experiment are on main |
| 2 | Reshape crew#440 into the panel's issue: this plan's design table as its criteria | crew#440 describes the panel, not a DevOps-only note |
| 3 | **Decisions by rule in the crew:** move `experiments/panel-174/collect_decisions.py`'s rule into `src/crew_org` | The Goal's decision log and the decisions naming it are collected for any Goal, with tests |
| 4 | **The panel:** `crews/panel_crew.py`, the four members from `build_agent`, run in parallel | Notes posted as one marked comment, each tagged with who settles it; a real run on #406 |
| 5 | **The settle step:** a PO call that writes the conclusion into the epic body, with the question path reused from `product_step` | The conclusion's schema refuses long cells; the Sponsor question works; a real run on #406 |
| 6 | **The split reads it:** in `refine_epics` (`flows/board_flow.py`), panel then settle then split; stories carry "Follows the epic's R…" | The BA may not contradict a row or settle an open question; the coverage check passes |
| 7 | **The criteria check reads it:** a criterion against a row is a conflict (crew#428) | A conflicting criterion is caught before stories exist |
| 8 | **Later steps get only their rows:** the Developer, Code Reviewer, QA and deploy review get the rows their story names | Like `story_note` today; no whole conclusion passed down |
| 9 | **The proof on #406** in a tick, then #407 and #408 | The success test below |
| 10 | **The design note's conclusion** (crew#457), then the other hand-offs, one at a time | Each ends in the same table |

## The success test (step 9)

| Check | 2026-10-01 | Success |
|---|---|---|
| Stories returned for contradicting criteria | 4 (#393, #394, #395, #402) | None |
| Goal decisions missing from the plan | Postgres, counting, OTLP, MINOR | All in the conclusion, cited |
| Notes that became unneeded work | sprint-metrics #419 | None |
| Sponsor questions | Not counted | At most one or two per epic |

## What to reuse from the test

| Piece | File | Use |
|---|---|---|
| Members' focus lines | `experiments/panel-174/run_panel.py`, `MEMBERS` | Starting point; add the A5 and A6 lines |
| Answer model | `run_panel.py`, `PanelAnswer` | Add the "settled by" field |
| Decisions rule | `experiments/panel-174/collect_decisions.py` | Step 3 |
| Replay inputs | `experiments/panel-174/inputs/` | Test the A5 and A6 lines before step 4's PR: the four missed findings should appear, and the unneeded notes should go |

## Open questions

| ID | Question | Settled by |
|---|---|---|
| Q1 | Does Sprint 13 start before or after the panel is built? (#406–#408 wait for it) | Sponsor |
| Q2 | Who writes the QA suite's stories, and how its growth is triggered | Sponsor, from how real teams do it |
| Q3 | The design note's conclusion in detail | With step 10 |
