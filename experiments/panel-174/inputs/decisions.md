# The Sponsor's recorded decisions

What the Sponsor wrote before 2026-10-01 on Goal #174's cards, or naming the Goal elsewhere.

## sprint-metrics#174 (comment 2026-09-27T00:48:56Z)

Follows #171: the service runs the image #171 releases. Record scope change: #173. The crew's side (running it, pushing to it, asking it) is operator work in mqucifer/crew#280, and its logs and traces go through mqucifer/crew#283.

## sprint-metrics#186 (body section)

## Sponsor decisions (2026-09-28)

- **Versioning.** The package version follows SemVer, separate from the JSON `api_version`:
  - removing an old API version is MAJOR
  - additions, including a new API version, are MINOR
  - fixes and docs are PATCH
  - **The first release is 1.0.0.** API v1 is already a promised contract.
- **Changelog.** `CHANGELOG.md` follows Keep a Changelog. Every user-visible pull request adds to `[Unreleased]` in the same pull request.
- **A release is a pull request.** It bumps `pyproject.toml` and dates `[Unreleased]` as `[X.Y.Z] - date`. When it merges, one workflow sees the new version, and using only `GITHUB_TOKEN`:
  - creates tag `vX.Y.Z`
  - creates a GitHub Release whose notes are that version's CHANGELOG section
  - pushes a `linux/amd64` + `linux/arm64` image to `ghcr.io/mqucifer/sprint-metrics`, tagged `X.Y.Z`, `X.Y` and `latest`, with build-provenance attestation
- **Everything is provable on a pull request.**
  - A `tests.yml` job builds the image and runs `--help` in it.
  - The release workflow has a dry-run path that builds the image and extracts the notes, and tags and pushes nothing.
  - Docker runs only in CI, never in the local checks. A criterion only CI can prove names the check that proves it.
- **Scope now.** The image ships today's tool: the CLI, and scrape mode serving `/metrics` and `/json`. What running as a service adds (a `serve` default, a volume for history, a healthcheck) follows #184, and isn't in this epic yet.
- **Order.** The Dockerfile and the image check in `tests.yml` come first, because the release workflow pushes the image they prove.
- **Built by the design's technical work, not here.** The design revision (#253) files technical epics that add the Dockerfile, the image job in `tests.yml` and the image push in `release.yml`. This epic's stories build on them and don't write them again. What's left here: what a user needs to run the image (pulling it by tag, running the CLI and scrape mode in it) in the user docs, proven by the image check.

---

Proposed by the Product Owner from #174 *(Goal: the tool is a service that keeps its history)*.

**Awaiting Sponsor approval.** Approve by moving this card out of `Inbox (Goals)` into `Needs Refinement`. Close it to reject.

## crew#280: The crew runs sprint-metrics as a service in its stack, pushes to it, and asks it (issue body)

The crew side of sprint-metrics#171 (the tool is released) and of the service Goal that follows it: "The first user is the crew itself." That's a change to the crew repository, outside `delivery.repos`, so it's operator work.

**Revised 2026-09-26 with the Sponsor.** Not a library the crew imports, but a **service**:
- sprint-metrics runs in its own container in the local compose stack, next to LiteLLM, from a **pinned image tag**, the artifact #171 releases.
- It's stateful: it keeps history in its own Postgres (its own container, not LiteLLM's `litellm-temp-db`).
- The crew **pushes** its input as work happens (cards started, blocked, done; escalations) through sprint-metrics' ingestion API.
- The crew's roles **ask** it through the versioned JSON API, for example the Scrum Master at standup and retro, whether as a tool call or through #231's loop.
- Its `/metrics` is scraped like LiteLLM's, and its logs and traces go through the collector in #283.

Released, reviewed software the crew wrote is ordinary software. It's containerised as deployment, not sandboxed as untrusted code.

## Who uses it, and for what (Sponsor, 2026-09-29)
The Sponsor wants two things from it: to **present** the data, and to **learn** from it. Each is a role asking the service for only the answer it needs. sprint-metrics does the computing, and no role reads raw events to work a number out.

**Present**
- **The Product Owner at the sprint review** (`crew sprint close`, the Sponsor's second gate). Reporting up to the Sponsor, it presents what was delivered beside the sprint's numbers: cycle time, first-try rate, escalation rate, and any threshold flags.
- **The presentation-site pilot** (next after sprint-metrics 1.0.0) shows the same data beyond the terminal, as visuals, read from sprint-metrics' versioned JSON API. That makes a second real consumer in another repo. Delivering across repos against a contract is the capability the pilot roadmap most needs proved, and #174's API and ingestion versioning is that contract.

**Learn**
- **The Scrum Master at the retro** asks for trends across sprints and for why work doesn't land first time (sprint-metrics' failure breakdown, Goal #87), in place of reading raw standups and retries. Pre-computed answers make a smaller context: the retro once spent its whole budget reasoning over raw material and wrote nothing.
- **The DevOps Engineer** has the service's `/metrics` scraped into Grafana beside the crew's telemetry (#283). Its `infra` research turns what it sees there into dashboards and alert rules (#335).
- **What's learned becomes defects:** process defects against the crew, or product defects against sprint-metrics (criterion 4).

**Decided with the Sponsor**
- sprint-metrics 1.0.0 may ship before the crew can feed it; it's a pilot. `crew export` writes stories and attempts with no dates, and sprint-metrics reads only a JSON list of dated cards. No interim adapter will be built: this issue and #174 are the path.
- This stays crew work, tracked by label, never on the delivery board.
- Until this lands, the close report and retro keep their current inputs.

## Acceptance criteria

1. **Given** a sprint-metrics release
   **When** the crew's stack runs
   **Then** `deploy/` runs that image by pinned tag, with its own database, and an upgrade is a PR that changes the tag and cites the release notes.
2. **Given** a card started, blocked, unblocked or done, or an escalation
   **When** the crew records it
   **Then** it's pushed to sprint-metrics' ingestion API at the pinned API version. A push that fails is retried later from `var/events` and never blocks a tick.
3. **Given** the Scrum Master writing a standup or retro
   **When** it wants a sprint's metrics or a trend
   **Then** it gets them from the service, and if the service is down, the standup or retro goes ahead and says so.
4. **Given** the crew finds a bug or a missing metric while using it
   **When** the retro sees it
   **Then** it's filed against sprint-metrics as a product defect, the crew being a real user of its own product.
5. **Given** a sprint closing
   **When** the Product Owner reports the sprint to the Sponsor
   **Then** the report carries the sprint's numbers and flags from the service. If the service is down, the report goes ahead and says so.
6. **Given** the service's `/metrics`
   **When** the telemetry collector runs
   **Then** it's scraped beside LiteLLM's and the exporter's, and it reaches Grafana with no prompt or free text.

Waits on #171's first release and the service Goal. Operator work in the crew repo, outside `delivery.repos`.

## crew#280 (comment 2026-09-27T00:26:24Z)

## How the crew uses it: as a tool (Sponsor, 2026-09-26)

The crew incorporates sprint-metrics as a **tool its roles call**, not only something the crew's code runs. For example, the Scrum Master asks for this sprint's cycle time or its trend while it writes the standup or the retro. sprint-metrics' record already says "the crew invokes the tool directly" and rules out an MCP server for now, so this is a tool that runs the pinned CLI (`--json`, `--schema`) and returns its JSON.

Two ways to wire it, to decide when this starts:
- **A native tool call:** a CrewAI tool on the role. Qwen3 with thinking on often plans a tool call and never emits it (see #231's notes), so this needs measuring first.
- **The loop #231 proposes:** the role names what it needs ("cycle time for Sprint 8, with the prior sprint"), and the flow runs the tool and hands back the JSON. Same outcome, and it works under thinking today.

Either way, the `--schema` output (sprint-metrics#147) is what describes the tool to the role, and the API version pins what it gets back.

## crew#280 (comment 2026-09-27T00:48:55Z)

The service Goal is sprint-metrics#174. Its record's scope change is sprint-metrics PR #173.
