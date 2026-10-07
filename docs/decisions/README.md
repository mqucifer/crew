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
| [0004](0004-escalation-runs-on-the-subscription.md) | Escalation runs on the subscription, never an API key | 2026-09-18 | Accepted |
| [0005](0005-escalation-is-not-a-crutch.md) | Escalation is rationed, not a crutch | 2026-09-18 | Accepted |
| [0006](0006-generated-code-runs-sandboxed.md) | Generated code runs sandboxed | 2026-09-18 | Accepted |
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
| [0019](0019-an-epic-is-the-smallest-unit-of-release.md) | An epic is the smallest unit of release | 2026-10-07 | Accepted (to be built by crew#500) |
| [0020](0020-what-the-crew-publishes-about-itself.md) | What the crew publishes about itself | 2026-10-07 | Accepted (to be built by crew#521) |
| [0021](0021-contracts-between-projects-flow-from-their-source.md) | Contracts between projects flow from their source | 2026-10-07 | Accepted (to be built by mqucifer/sprint-metrics#461 and crew#521) |
