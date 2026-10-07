# 21. Contracts between projects flow from their source

- **Date:** 2026-10-07
- **Status:** Accepted (to be built by a sprint-metrics Goal and the crew's replay feed)

## Context

crew-presentation is the first project built on others: it shows sprint-metrics'
answers and the crew's replay feed, styled by mqucifer/design-system. Each of those
links was written as a copy:

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
constantly ensure it's up to date with checking."

## Decision

- **The producer generates its contract from the code that produces the output,**
  and its tests prove the real output matches. A schema written by hand beside the
  code isn't a contract.
- **Each release publishes its contract at a fixed path under its tag.** The
  producer's README says how to use it, as the design system's "How projects use
  it" does.
- **Each data file names the schema it follows, by tag** (its `$schema`, such as
  `https://cdn.jsdelivr.net/gh/<owner>/<repo>@<tag>/<path>`). The consumer
  validates each file against the schema it names. It never keeps a copy, and its
  record pins no version.
- **The consumer's own check is which major versions it can display.** A file on a
  major version it can't display fails the pull request that brought it.
- **A dependency that isn't data,** such as a stylesheet, is pinned once, in the
  consumer's code, and bumped by pull request.
- **Records and Goals point to the source's usage notes.** They never restate a
  URL, a version or a shape.

## Consequences

- A producer's release reaches its consumers with no edit to them, as long as the
  major version is one they can display.
- A Goal whose contract isn't published yet waits in Inbox (Goals).
  crew-presentation's Goals wait for sprint-metrics' published schemas and the
  crew's replay feed.
- crew-presentation's record is rewritten to this, and its design is proposed again,
  because the merged one copies the schemas.
- The crew's own exports are producers too: the replay feed and the snapshot are
  generated from the crew's code and published by tag, with what they may carry set
  by ADR 0020.

## Changelog

- 2026-10-07: Accepted. The Sponsor decided it while planning crew-presentation's
  Goals (crew#518).
