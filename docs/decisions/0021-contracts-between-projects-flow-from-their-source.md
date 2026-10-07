# 21. Contracts between projects flow from their source

- **Date:** 2026-10-07
- **Status:** Accepted (to be built by mqucifer/sprint-metrics#461 and crew#521)

## Context

crew-presentation is the first project built on others: it shows the crew's delivery
history (a replay of its work, and sprint-metrics' answers about each sprint), styled
by mqucifer/design-system. Each of those links was first written as a copy:

- sprint-metrics' answer schemas are written by hand in `schema.py`. No test checks
  the real answers against them, `/trend` has none, and only the event intake's is
  served.
- The replay feed has no schema yet.
- crew-presentation's design copies the schemas into its own `schemas/`, from a
  published source that doesn't exist.
- crew-presentation's record restates the design system's URL and version, which
  the design system's README already gives.

A copy drifts silently, and an undefined link gets guessed. An earlier design for
crew-presentation invented an npm package for the design system because how to get
it wasn't written down.

The Sponsor, 2026-10-07: "This should flow from the sources and not require us to
constantly ensure it's up to date with checking." And on how the data arrives: "I'm
expecting crew to just generate its data, call sprint metrics, package this up and
release it by tag and has a defined schema we can test with (its own schema which is
going to inherently contain sprint metrics data)."

## Decision

- **The producer generates its contract from the code that produces the output,**
  and its tests prove the real output matches. A schema written by hand beside the
  code isn't a contract.
- **Each release carries its contract at a fixed path under its tag.** The
  producer's README says how to use it, as the design system's "How projects use
  it" does.
- **A consumer depends on one producer.** A package that carries another project's
  data includes that project's schema in its own, generated from that project's
  release. crew-presentation depends on the crew, never on sprint-metrics.
- **Data is delivered as a package, like a report.** A package has a name for what
  it holds (`delivery-history` is the first), and each release is dated, not
  versioned: `<name>-<date>`. Its archive is the Release's asset, and its manifest
  names the schema version it follows. It carries everything up to its date, so a
  consumer needs only the latest.
- **The schema's version lives in the producer's code:** MAJOR when a consumer
  could break on the change, MINOR when it only adds. A schema change without a
  version bump fails the producer's tests.
- **A release reaches its consumer as a pull request** the producer opens, which
  changes the one line naming the package's tag. The consumer's CI fetches the
  package and validates it against the schema it names. Merging deploys.
- **The consumer's own check is which major versions it can display.** A package on
  a major version it can't display fails the pull request that brought it. It
  keeps no copy of a schema.
- **A dependency that isn't data,** such as a stylesheet, is pinned once, in the
  consumer's code, and bumped by pull request.
- **Records and Goals point to the source's usage notes.** They never restate a
  URL, a version or a shape.

## Consequences

- A producer's release reaches its consumer as a pull request it can check. No one
  edits the consumer by hand to keep up.
- A consumer's history lives in the producer's releases, not in the consumer's
  repository.
- A Goal whose contract isn't published yet waits in Inbox (Goals).
  crew-presentation's Goals wait for `delivery-history`, which waits for
  sprint-metrics' published schemas.
- crew-presentation's record is rewritten to this, and its design is proposed again,
  because the merged one copies the schemas.
- More packages follow the same pattern under their own names, tags and schema
  versions, with what they may carry set by ADR 0020.

## Changelog

- 2026-10-07: Accepted. The Sponsor decided it while planning crew-presentation's
  Goals (crew#518).
- 2026-10-07: Data arrives as a dated package from one producer, by a pull request
  that bumps its tag, in place of data files that each name a schema. The Sponsor
  decided the package, its name and its delivery (crew#521).
