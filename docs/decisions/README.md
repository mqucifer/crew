# Decisions

Architecture decision records for the crew's own design (ADR 0001). This README is
how they're kept: living documentation, changed by PR like any other doc, not by
another ADR, so the current process is always read here and nowhere else.

**What gets one:** a decision that changes how the crew works across roles, steps
or projects, or that would be costly to reverse. Examples: infra owns the deployed
runtime (0017), the Product Owner's leeway within the Goal (0018). A choice inside
one issue (which option meets a criterion, a retry count, a threshold, a wording)
goes in that issue and its PR instead.

**How:** the Sponsor's decisions are asked in chat, with the options and a
recommendation. When one meets the line above, Claude writes the ADR by PR in the
session it's made, and the Sponsor's merge is the approval. When it's unclear which
side of the line a decision falls, that's asked with the decision.

**An ADR says what's true now** (Sponsor, 2026-10-07). Reading one ADR is enough to
know the current decision; nobody has to follow a chain of superseding records.

**Changing one:** edit it in place, by PR. The Context, Decision and Consequences
are rewritten to read as the decision stands now. A dated line is added to the
`## Changelog` at its bottom saying what changed, why, and who decided, so the
earlier version stays findable there and in git. A decision dropped altogether
keeps its file, with Status `Withdrawn` and a changelog line saying why. A new ADR
is for a new decision, not a change to an existing one.

Use the format of `0001` (Date, Status, Context, Decision, Consequences,
Changelog), numbered in order, with a kebab-case title. The Date is when the
decision was first made.

| ADR | Decision | Date | Status |
|---|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | 2026-10-02 | Accepted (the process now lives in this README) |
| [0002](0002-the-board-is-the-orchestrator.md) | The board is the orchestrator | 2026-09-18 | Accepted |
| [0003](0003-the-sponsors-gates.md) | The Sponsor's gates | 2026-09-18 | Accepted |
| [0004](0004-escalation-runs-on-the-subscription.md) | Escalation runs on the subscription, never an API key | 2026-09-18 | Accepted (escalation retires with ADR 0032, not built) |
| [0005](0005-escalation-is-not-a-crutch.md) | Escalation is rationed, not a crutch | 2026-09-18 | Accepted (escalation retires with ADR 0032, not built) |
| [0006](0006-generated-code-runs-sandboxed.md) | Generated code runs sandboxed | 2026-09-18 | Accepted (the Developer's workspace per story in ADR 0032, not built) |
| [0007](0007-all-model-traffic-through-the-proxy.md) | All model traffic goes through the proxy | 2026-09-23 | Accepted |
| [0008](0008-the-crew-repo-is-off-the-board.md) | The crew repo is off the board | 2026-09-23 | Accepted |
| [0009](0009-ideas-live-in-discussions.md) | Ideas live in Discussions | 2026-09-26 | Accepted |
| [0010](0010-telemetry-never-carries-content.md) | Telemetry never carries content | 2026-09-26 | Accepted |
| [0011](0011-focused-passes-over-bigger-prompts.md) | Focused passes over bigger prompts | 2026-09-29 | Accepted |
| [0012](0012-no-approver-app-on-the-crew-repo.md) | No approver App on the crew repo | 2026-09-30 | Accepted |
| [0013](0013-claude-and-the-crew-are-independent.md) | Claude's standards and the crew's are independent | 2026-10-02 | Accepted |
| [0014](0014-logging-one-levelled-stream.md) | Logging: one levelled stream, named events for what code reads | 2026-10-02 | Accepted (built by crew#449) |
| [0015](0015-a-goals-decisions-are-the-sponsors-words-under-a-heading.md) | A Goal's decisions are the Sponsor's comments and headed sections | 2026-10-05 | Accepted |
| [0016](0016-what-the-panel-is-shown.md) | What the refinement panel is shown | 2026-10-05 | Accepted (built by crew#440 and crew#468) |
| [0017](0017-the-product-builds-to-its-spec-infra-owns-where-it-runs.md) | The product builds to its spec; infra owns where it runs | 2026-10-05 | Accepted (built by crew#440) |
| [0018](0018-the-product-owner-decides-within-the-goal-and-records-it.md) | The Product Owner decides within the Goal, and the Architect settles design before the split | 2026-10-05 | Accepted (the Architect's settle before the split to be built by crew#440) |
| [0019](0019-an-epic-is-the-smallest-unit-of-release.md) | The Sponsor decides when a release is cut | 2026-10-07 | Accepted (until crew#500 settles how releases happen) |
| [0020](0020-what-the-crew-publishes-about-itself.md) | What the crew publishes about itself | 2026-10-07 | Accepted (to be built by crew#521) |
| [0021](0021-contracts-between-projects-flow-from-their-source.md) | Contracts between projects flow from their source, and the consumer reviews what it receives | 2026-10-07 | Accepted (the package built by mqucifer/sprint-metrics#461 and crew#521; the consumer's review planned for M10) |
| [0022](0022-each-projects-part-in-the-delivery-history.md) | Each project's part in the delivery history | 2026-10-07 | Accepted (v1; to be built by mqucifer/sprint-metrics#461, mqucifer/sprint-metrics#462, crew#280 and crew#521) |
| [0023](0023-the-epics-record-is-the-crews-memory.md) | The epic's record is the crew's memory | 2026-10-10 | Accepted (to be built by crew#583, Part A of the record plan; the ruling's move to the gate in ADR 0032, not built) |
| [0024](0024-a-steps-code-comes-from-a-graph-then-by-asking.md) | A step's code comes from a graph, then by asking | 2026-10-10 | Accepted (to be built as Part C of the record plan; crew#584 lands in it; changed for the Developer by ADR 0032, not built) |
| [0025](0025-the-crew-makes-its-own-model-calls.md) | The crew makes its own model calls | 2026-10-10 | Accepted (staged; Part D of the record plan, the criteria check first; changed for the Developer by ADR 0032: its session's calls built, `calls.converse`) |
| [0026](0026-code-comes-back-in-blocks-structure-in-json.md) | Code comes back in blocks, its structure in JSON | 2026-10-11 | Accepted (built for the Developer, crew#626; changed for the Developer by ADR 0032, not built) |
| [0027](0027-collaborators-file-bugs.md) | Collaborators file bugs, and the Product Owner may propose a Goal | 2026-10-12 | Accepted (bugs built by crew#407; proposed Goals planned for M10) |
| [0028](0028-a-sprint-closes-itself-when-its-time-is-up.md) | A sprint closes itself when its time is up | 2026-10-12 | Accepted |
| [0029](0029-the-plan-lives-in-the-crew-roadmap.md) | The plan lives in the Crew roadmap project | 2026-10-12 | Accepted |
| [0030](0030-a-new-project-starts-from-a-walking-skeleton.md) | A new project starts from an alpha Claude builds | 2026-10-13 | Accepted (the skeleton path built by crew#690; the handover check to be built by crew#757) |
| [0031](0031-every-rule-a-role-meets-is-declared-once.md) | Every rule a role meets is declared once, by category | 2026-10-17 | Accepted (built by crew#756 part 3, answer_shape first; each other category in its cleanup; the session's sort in ADR 0032, not built) |
| [0032](0032-the-developer-works-in-a-session.md) | The Developer works in a session | 2026-10-17 | Accepted (the session built beside delivery, Stage C; delivery, retiring and measures to build, Stages D to F of `docs/plans/developer-session.md`) |
