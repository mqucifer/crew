# Decisions

Architecture decision records for the crew's own design (ADR 0001).

**What gets one:** every decision the Sponsor makes or approves about the crew,
and any technical choice of Claude's that would be costly to reverse.

**How:** Claude writes it by PR in the session the decision is made. The
Sponsor's merge is the approval.

**Changing one:** write a new ADR that supersedes the old one, and set the old
one's Status to `Superseded by NNNN`. The old body isn't edited.

Use the format of `0001` (Date, Status, Context, Decision, Consequences),
numbered in order, with a kebab-case title.

| ADR | Decision | Date | Status |
|---|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | 2026-10-02 | Accepted |
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
