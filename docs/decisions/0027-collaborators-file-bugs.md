# 27. Collaborators file bugs, and the Product Owner may propose a Goal

- **Date:** 2026-10-12
- **Status:** Accepted (bugs' path through the flow built by crew#407; proposed Goals to be built with ADR 0021's consumer review, planned for M10)

## Context

The taxonomy has a **Bug**: "observed behaviour contradicting accepted acceptance criteria", 1–5 points (ways-of-working, "Work item taxonomy"). But it also called a Goal "the only artifact the human authors", and nothing in the flow works a bug: sprint admission takes only stories. A defect a person found had no proper way in:
- On 2026-10-07 a defect in sprint-metrics' answer schemas went in as a Goal instead, because there was no path for a bug (crew#407).
- On 2026-10-11, seeding sprint-metrics 1.2.0 with the crew's history (crew#280) found four. Claude's account filed them as `bug` issues on sprint-metrics (mqucifer/sprint-metrics#593 to mqucifer/sprint-metrics#596), and nothing in the crew picks them up.

Whether a person may file a bug at all was the Sponsor's to decide, since it changes what people author.

A Goal was the Sponsor's alone. Then a consumer's review of what it receives (ADR 0021) can find work that no Goal covers: a use worth building, or a producer's request outside every Goal. The Sponsor, 2026-10-15: they approve Goals the crew proposes, not only ones they author.

## Decision

**Collaborators in the organization file bugs** (the Sponsor, 2026-10-12). Today that is the Sponsor and Claude's account, `quarters-claude`. A bug is an issue on a delivery repository, labelled `bug`, saying:
- what happens;
- what was expected;
- the evidence.

A bug reports behaviour that contradicts what was accepted. It doesn't set direction: new behaviour, or a change to what the numbers mean, is a Goal (for example mqucifer/sprint-metrics#597).

**A Goal is the Sponsor's to approve.** The Sponsor writes Goals, and the crew may propose one (the Sponsor, 2026-10-17):
- **Only a project's Product Owner proposes a Goal,** from a use review or from a request outside every Goal (ADR 0021). The Architect keeps its technical epics, and anyone files bugs.
- **The gate is the one a drafted Goal already has** (ADR 0003). A proposed Goal is filed as a draft in `Inbox (Goals)`, without the `goal` label. The Sponsor approves it by applying the label, edits it first, or closes it. Approving one is no new step.
- **A proposed Goal's decision rows stay the Product Owner's,** binding under ADR 0018. Only what the Sponsor adds under their own heading is the Sponsor's (ADR 0015), so the crew's words never count as the Sponsor's.

**How a bug flows** (built by crew#407): refined if needed, sized 1–5, admitted to a sprint, fixed with a test that reproduces it and then passes, reviewed, and proven by QA.

## Consequences

- People have a way to report a defect besides writing a Goal, and a bug isn't dressed up as one.
- Bugs don't hold the critical path (the Sponsor, 2026-10-12).
- Whether a role's own finding is filed as a bug, rather than as a retro finding, isn't decided here. crew#407 proposes it.
- **Work a consumer finds can become a Goal without the Sponsor writing it.** It still waits on the Sponsor's label, and what the Sponsor decided stays distinguishable from what the Product Owner proposed.

## Changelog

- **2026-10-12:** decided by the Sponsor in chat, while crew#407 was raised to P1.
- **2026-10-17:** the Product Owner may propose a Goal, which the Sponsor approves by applying the `goal` label; its rows stay the Product Owner's. "A Goal stays the Sponsor's alone" no longer holds. Decided by the Sponsor in the M6 planning session (crew#755).
