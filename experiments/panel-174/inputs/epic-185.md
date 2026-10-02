# #185: Version and document the intake format as a published contract

**Outcome** — The event format the service accepts carries a version identifier, a published schema (served and in the README), and a backward-compatibility guarantee that a release never changes what an existing version accepts.

**Why** — The goal states 'What it's sent is as much a contract as what it returns' and 'a release never changes what an existing version accepts.' Today only the output has a schema endpoint and an api_version key. The input the user sends is undocumented beyond the code. Without a versioned, documented intake contract, a user cannot trust that an upgrade will not silently reject their events, and there is no way to discover the accepted format without reading the source. This is the 'documented the same way as its answers' half of the contract.

**Stands alone because** — A user reads the intake schema (served at an endpoint or in the README), validates their events against it before sending, and trusts that the v1 event format will be accepted by every future release. Even without the container or structured logging, the contract is explicit, discoverable, and enforceable.

---

Proposed by the Product Owner from #174 *(Goal: the tool is a service that keeps its history)*.

**Awaiting Sponsor approval.** Approve by moving this card out of `Inbox (Goals)` into `Needs Refinement`. Close it to reject.
