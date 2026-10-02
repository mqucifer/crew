# #186: Ship the service as a container image from a released tag

**Outcome** — A user pulls a container image tagged to a release and runs the service in their infrastructure (Kubernetes, Docker Compose, ECS) without installing Python, managing dependencies, or building anything.

**Why** — The goal says 'something a user runs as a container from a released image.' Today the release is a version tag the crew installs as a git dependency in its own tooling. A container image is the deployment artifact that the user's orchestrator expects. Without it, the user must provision a Python 3.12 runtime, install the package, and manage its lifecycle — none of which is 'runs as a service' in the sense the Sponsor means. This epic makes the service a first-class citizen of container-based infrastructure.

**Stands alone because** — A user deploys the container to their orchestrator and has a running, persistent service they can send events to and query. They can scrape /metrics, POST events, and GET reports. Even without structured logging or traces, the service is deployable and operational in the user's existing infrastructure.

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


