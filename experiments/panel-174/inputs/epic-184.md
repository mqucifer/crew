# #184: Accept events and answer queries from retained history

**Outcome** — A user sends the service events as work happens (card created, started, blocked, completed, escalated) and queries any sprint, range, or trend without re-supplying the data that was already sent.

**Why** — This is the core shift the goal demands. Today the tool is stateless: every query requires the full card set to be handed to it again, and it forgets everything the moment the process exits. The Sponsor's words are 'it reports on what it's handed and forgets it.' This epic makes the service remember what it is told and answer from its own store. Without this, no other part of the goal is reachable: there is no history to version, nothing for a container to persist, and no long-running process to log and trace.

**Stands alone because** — A user starts the service, POSTs events to an intake endpoint as work happens, and then GETs a sprint report (table, JSON, markdown, or Prometheus) that reflects everything sent so far. They never re-supply the past. No container, no formal schema document for the intake, no structured logs — the service retains and answers.

---

Proposed by the Product Owner from #174 *(Goal: the tool is a service that keeps its history)*.

**Awaiting Sponsor approval.** Approve by moving this card out of `Inbox (Goals)` into `Needs Refinement`. Close it to reject.
