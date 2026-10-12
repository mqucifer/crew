# 27. Collaborators file bugs

- **Date:** 2026-10-12
- **Status:** Accepted (the path through the flow to be built by crew#407)

## Context

The taxonomy has a **Bug**: "observed behaviour contradicting accepted acceptance criteria", 1–5 points (ways-of-working, "Work item taxonomy"). But it also called a Goal "the only artifact the human authors", and nothing in the flow works a bug: sprint admission takes only stories. A defect a person found had no proper way in:
- On 2026-10-07 a defect in sprint-metrics' answer schemas went in as a Goal instead, because there was no path for a bug (crew#407).
- On 2026-10-11, seeding sprint-metrics 1.2.0 with the crew's history (crew#280) found four. Claude's account filed them as `bug` issues on sprint-metrics (mqucifer/sprint-metrics#593 to mqucifer/sprint-metrics#596), and nothing in the crew picks them up.

Whether a person may file a bug at all was the Sponsor's to decide, since it changes what people author.

## Decision

**Collaborators in the organization file bugs** (the Sponsor, 2026-10-12). Today that is the Sponsor and Claude's account, `quarters-claude`. A bug is an issue on a delivery repository, labelled `bug`, saying:
- what happens;
- what was expected;
- the evidence.

**A Goal stays the Sponsor's alone.** A bug reports behaviour that contradicts what was accepted. It doesn't set direction: new behaviour, or a change to what the numbers mean, is still a Goal (for example mqucifer/sprint-metrics#597).

**How a bug flows** is crew#407's to build: refined if needed, sized 1–5, admitted to a sprint, fixed with a test that reproduces it and then passes, reviewed, and proven by QA.

## Consequences

- People have a way to report a defect besides writing a Goal, and a bug isn't dressed up as one.
- Until crew#407 is built, a filed bug waits on its repository. Bugs don't hold the critical path: crew#407 is P1, beside it (the Sponsor, 2026-10-12).
- Whether a role's own finding is filed as a bug, rather than as a retro finding, isn't decided here. crew#407 proposes it.

## Changelog

- **2026-10-12:** decided by the Sponsor in chat, while crew#407 was raised to P1.
