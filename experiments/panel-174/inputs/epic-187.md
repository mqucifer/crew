# #187: Emit structured logs and traces in the user's expected format

**Outcome** — The service emits structured logs (JSON) and distributed traces that ship to wherever the user's other services send theirs — their log aggregator, their tracing backend — without custom collectors or format shims.

**Why** — The goal says 'it fits into how its users already watch their systems' and 'its logs and traces go wherever the user's other services send theirs.' Today the service prints human-readable text to stdout. A production service running in a container needs structured JSON logs that a pipeline (Fluentd, Vector, CloudWatch) can parse and route, and traces that a backend (Jaeger, Datadog, Tempo) can correlate with the user's other services. This is what makes the service a known quantity in the user's observability stack rather than a black box that only exposes /metrics.

**Stands alone because** — A user running the service (in a container or from source) points their log pipeline at its stdout and sees structured, queryable log entries; they point their tracing backend at the service's exporter and see request traces correlated with event intake and query processing. Even without the container, the logs and traces are in the format their infrastructure already ingests.

---

Proposed by the Product Owner from #174 *(Goal: the tool is a service that keeps its history)*.

**Awaiting Sponsor approval.** Approve by moving this card out of `Inbox (Goals)` into `Needs Refinement`. Close it to reject.
