# Running the service (sprint-metrics Goal #174)

How the history-keeping service runs, gets tested and is looked after, and where DevOps and QA come into the work. Planned with the Sponsor on 2026-10-01.

**Note (2026-10-05):** parts 1, 2 and 4 (environments, settings and secrets, production on the shared Postgres) are infra's content: a product builds to its spec and proves it in CI, and infra owns where it runs ([ADR 0017](../decisions/0017-the-product-builds-to-its-spec-infra-owns-where-it-runs.md)). They stay here as the record until infra's own Goal takes them over.

**Status:** parts 1–5 agreed. Parts 6 (sequencing) and 7 (the Goals that follow) are still to plan. Nothing here is built yet, except where noted.

## 1. Environments

| Environment | What it is | Postgres |
|---|---|---|
| **Local checks** | The crew's sandbox: no network, runs lint and unit tests. | None. The store's tests skip. |
| **CI** | GitHub Actions, including a short-lived compose stack: the service and a throwaway Postgres, seeded, tested, torn down. Runners are amd64. | Throwaway, per run |
| **Production** | Compose on the Mac today. Later, dedicated Intel or AMD hardware, moved as-is. | The shared one |

- No persistent test bed. Short-lived stacks are cheap, and sprint-metrics is public, so Actions minutes are free.
- Nothing in production may depend on the Mac. Anything machine-specific, like the Spark's `.local` address, comes from settings.

## 2. Settings and secrets

| Setting | Local checks | CI | Production |
|---|---|---|---|
| `SPRINT_METRICS_DB` | Unset | The throwaway database's address, in the workflow. Not a secret. | **Secret**, in the host's `.env` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Unset (telemetry off) | Unset, or a collector in the stack | The crew's collector |
| `OTEL_SERVICE_NAME` | Default | Default | `sprint-metrics` |

- Production secrets stay in the host's `.env` for now.
- DevOps proposes settings and secrets by name, and may list which exist. It never reads a value.

## 3. Test bed and QA suite

- **CI only, for now.** DevOps makes sure CI is right for what the tests need.
- **QA creates the test data,** and owns acceptance tests that run against the short-lived stack. This is the first concrete step of the QA-owned suite (Discussion #261).
- **The suite is its own epic in sprint-metrics,** worked in the normal cycle. It doesn't hold features back: QA starts from what's built, then grows the suite as each feature arrives.

## 4. Production on the shared Postgres

- **Every service is a good tenant:** its own schema and its own user, in one database, with access to nothing else. The Sponsor maintains the users.
- **LiteLLM is dropped and recreated** into its own schema with its own user. It's a transient gateway with one user. Anything truly needed is extracted first, as an experiment or runbook.
- **Each service migrates its own schema** at startup.
- **Setup SQL** (users, schemas, permissions, the database's name) is proposed by DevOps, applied by the Sponsor, and rehearsed by CI on every run. The admin password becomes a real secret.
- **Backups, later:** a one-time setup of an encrypted dump to the Sponsor's iCloud backup, with short retention.
- For DevOps to check: that LiteLLM can use a non-default schema through its connection string.

## 5. DevOps and QA in the process

Modelled on how working teams refine: everyone with a stake is in the room, and people speak only when it matters to them.

- **A refinement panel, before each epic is split.** The Architect, UX Designer, QA and DevOps each take their own focused pass on the epic. "Nothing to add" is a first-class answer. It replaces labels, so no role is skipped because an epic wasn't labelled.
- **Every member sees the same context:** the Goal, the project record, the sibling epics, and the Sponsor's recorded decisions, collected by rule. The misses on 2026-10-01 came from that context being missing, not from a missing role.
- **The Architect's design note stays after the split,** on the stories. The panel is the conversation first; the design comes once the work is sliced.
- **Each note is checked before it's acted on.** A model asked for input tends to produce some, and an unneeded note shouldn't become work, as sprint-metrics#419 did.
- **A settle step ends the panel, before the split** (agreed 2026-10-02). The Product Owner turns the panel's notes into the epic's conclusion. Each point is answered from the Goal, the record or a recorded decision, or becomes one question to the Sponsor, whose answer becomes a line.
- **Discussion may be long; what's handed on is short.** As in a meeting: an hour of discussion, then a concise summary with clear actions for the next team. The panel's full notes stay as one comment, the audit trail, and aren't passed on. Only the conclusion is.
- **The conclusion lives in the epic body,** under its own header, below the text the Sponsor approved, which stays as it was. Nobody has to read ten comments to find out what was decided. Its format follows established standards, not one of the crew's own (agreed 2026-10-02):
  - **BLUF** (bottom line up front): the first line says whether the epic is ready to split.
  - **Nygard's ADR fields** (ID, Status, Context, Decision, Consequences) as table columns, short fragments with no prose, and a source on each row. **MADR status values:** accepted, or "superseded by" another row.
  - **Example Mapping's question cards:** what can't be settled yet is listed apart, with who settles it.

  ```
  ## Refinement conclusion
  Ready to split: 7 decided, 2 open for the design note.

  | ID | Status   | Context       | Decision                                  | Consequences                         | Source           |
  |----|----------|---------------|-------------------------------------------|--------------------------------------|------------------|
  | R1 | accepted | History store | Postgres, own schema on the shared server | Postgres client; CI needs throwaway DB | Goal decision D1 |
  | R2 | accepted | Counting      | A card counts in the sprint it merges     | Sprint queries group by merge date   | Goal decision D3 |
  ...

  | ID | Open question                   | Impact                  | Settled by  |
  |----|---------------------------------|-------------------------|-------------|
  | Q1 | Intake api_version: same track? | Every sender encodes it | Design note |
  ```

  - **It's the same format as the Goal's decision log,** so one way of writing a decision runs from the Goal down. An epic row can cite a Goal decision by its ID.
  - **Every decision gets a row,** with no cap on the number of rows. Each cell is held short by schema limits, because asking a model to be brief doesn't hold.
  - **A source is an explicit link with words,** never a bare issue number (see below).
- **Stories point back to the rows they rely on** ("Follows the epic's R1, R4") rather than restating them. A story's epic is its parent, so no number is needed. Whatever reads a story pulls only the rows it names. A long conclusion therefore doesn't bloat a story's context, and nothing it relies on goes missing. That's the start of each step getting the context it needs and no more (to design: what each later step pulls, and how).
- **No bare issue numbers in anything the crew posts to GitHub: issues, pull requests and comments.** GitHub turns `#123` (and `GH-123`) into a link to that number in the current repository, and adds a "mentioned this" back-link on that issue. Whenever the reference meant another project, both point at the wrong issue. A reference is an explicit link with descriptive words. Files in the repository aren't autolinked. This applies across the crew, not only here.
- **Serial first, then parallel.** Prove the notes are right, then make it crew#299's first parallel case. The calls are independent, read-only, and share a long prefix.
- Refinement is the slowest step and carries the most risk. The extra calls are accepted for that reason.

**The build plan:** [`refinement-panel-build.md`](refinement-panel-build.md): ten steps, then the proof on #406.

**The test before it's built:** [`experiments/panel-174/README.md`](../../experiments/panel-174/README.md) (crew PR #441; run 2026-10-02, PR #455). Its recommendation is to build the panel, in parallel, with three changes: record decisions where a rule finds them; give DevOps and the Architect checks for their blind spots; and have each member review the epic in front of it.

**Still open in this part:**
- Who writes the QA suite's stories, and how its growth is triggered with each feature. To be answered from how working teams do it. A starting point: QA writes a suite story inside each feature epic, and a Developer builds it in the same cycle.
- The design note's own conclusion. Agreed on 2026-10-02: each step hands down only the relevant conclusions. The design note, the largest thing a Developer is given today, ends in the same table, answering the epic's open questions as rows, and stories name its rows. A coverage check confirms every settled row is named by a story or marked not for stories. Still to plan in detail.

**Agreed 2026-10-02: every role's hand-off is concise in the same way.** The design note, the presentation note, reviews, QA verdicts, diagnoses, the Product Owner's answers, standups and retros: each ends in a conclusion table in the same standard form: the bottom line first, then one row per decision in ADR fields (Context, Decision, Consequences, Source), with open questions apart. The discussion behind it may be long; what the next step reads is the table. Crew-wide work, tracked in crew#457.

**Agreed 2026-10-02: the Sponsor's decisions for a Goal live in the Goal's body,** in a "Sponsor decisions" decision log in the same ADR fields, with MADR status values. Goal #174 has one since 2026-10-02. The decisions rule already reads a section headed "Sponsor decisions" on the Goal's cards, so it finds them with no change. A decision made elsewhere is added there when it's made.

**Agreed 2026-10-02: one epic per pass.** A clash between two epics' splits is caught later by steps that exist today. The criteria check reads the stories other epics have planned (crew#428). A story problem, or a design note at odds with the stories, goes back to the Product Owner to settle (crew#189, crew#425). A panel also sees what earlier sibling epics settled, because their conclusions are in their bodies.

## 6. Sequencing Goal #174 (to plan)

What exists today:
- #414 (dependencies) is open.
- #415 and #416 are held in Inbox for DevOps.
- #406, #407 and #408 are approved and held.
- The QA suite epic doesn't exist yet.

## 7. The Goals that follow (to plan)

Written last, from parts 1–4.

## Crew work this implies

| Issue | What | Change from this plan |
|---|---|---|
| crew#440 | DevOps' environment note | Becomes DevOps' seat on the panel instead of a label trigger |
| crew#299 | Parallel work | The panel is its first case, after the serial run |
| crew#430 | Design risks read against the Goal | Covered by the panel seeing the Goal |
| crew#439 | Which design changes become technical epics | Unchanged |
| crew#431 | Stale-Goal forecast | Unchanged; it would read the panel's notes |
| Discussion #261 | QA-owned suite | The first step is part 3. QA can't write to a repo yet |
