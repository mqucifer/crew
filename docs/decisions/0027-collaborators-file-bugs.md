# 27. Collaborators file bugs, and the Product Owner may propose a Goal

- **Date:** 2026-10-12
- **Status:** Accepted (bugs' path through the flow built by crew#407; proposed Goals built in M7 as the Product Owner's "the Goal points elsewhere" by crew#846, and with ADR 0021's consumer review in M15)

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

**How a bug flows** (built by crew#407): refined if needed, sized 1–5, admitted to a sprint, fixed with a test that reproduces it and then passes, reviewed, and proven by QA. The test runs where the project's tests can: in the default branch's workspace, with its services, when it declares one (crew#839). A bug the crew filed from the tests a workspace turned on is already reproduced: the crew's own record, made at merge, names the tests that fail on main. Its fix makes each of them pass in the workspace, and a hung file runs to its end.

**A bug is any defect, a test's too** (the Sponsor, mqucifer/crew#1005, 2026-11-02). One whose fix is only in tests is still worked as a bug and can reach Done: its defect is a test, as mqucifer/sprint-metrics#868's stale one was, or its failure shows only off main, as mqucifer/crew-presentation#300's did on a pin's branch. Nothing it does can fail on main, so it's proven another way. Its named tests run, with nothing else changed, and pass. Each merged test it keeps still makes every check it made: every `assert` that can fail, and each call that checks, is still there, as it was or rewritten. A rewrite is paired with the most alike check left, at difflib's close-match cutoff of 0.6, two checks of one shape compared by their values, and never with one that checks the reverse (`not in` for `in`). So it can't pass by dropping or blanking a check, and whether a rewritten one still checks what it did is the Code Reviewer's. A merged test it removes whole is a declared change, ruled on as any other merged test's is. It has to change a merged test in a named test's file, and the Code Reviewer is asked to judge that the defect is the tests', not the code's. A branch that changes the code still has to fail on main first.

## Consequences

- People have a way to report a defect besides writing a Goal, and a bug isn't dressed up as one.
- Bugs don't hold the critical path (the Sponsor, 2026-10-12).
- **The crew files a bug from its own finding where a decision says so,** rather than as a retro finding: so far, the Developer's bug for the merged tests a workspace turns on that fail (the Sponsor's decision on mqucifer/crew#839). Its App isn't a collaborator, so triage reads org.yaml's `trust.crew` for it, and those bugs are triaged like any other. Anyone else without access still waits on a person.
- **Work a consumer finds can become a Goal without the Sponsor writing it.** It still waits on the Sponsor's label, and what the Sponsor decided stays distinguishable from what the Product Owner proposed.

## Changelog

- **2026-10-12:** decided by the Sponsor in chat, while crew#407 was raised to P1.
- **2026-10-17:** the Product Owner may propose a Goal, which the Sponsor approves by applying the `goal` label; its rows stay the Product Owner's. "A Goal stays the Sponsor's alone" no longer holds. Decided by the Sponsor in the M6 planning session (crew#755).
- **2026-10-21:** a proposed Goal is also how the Product Owner says "the Goal points elsewhere" (ADR 0018), so it's built in M7 (crew#846) rather than waiting for the consumer review. Decided by the Sponsor in the overhaul's planning session.
- **2026-10-23:** a bug the crew's own App files (org.yaml's `trust.crew`) is triaged like a collaborator's. The first, mqucifer/sprint-metrics#742, was refused as "not a collaborator"; mqucifer/crew#839 had already decided the crew files it.
- **2026-10-23:** a bug's test runs in the default branch's workspace, not the networkless sandbox, where a database test could only skip. A bug the crew filed from turned-on tests is reproduced by its own record, read only from the crew's comment at the merged head. mqucifer/sprint-metrics#742 was refused three rounds running for a database test the sandbox skipped (crew#839).
- **2026-10-29:** a bug whose defect and fix are in the project's tests is triaged as a story, since the reproduction check can't be met by one (mqucifer/crew#949). mqucifer/sprint-metrics#868 was sent back 4 times before the operator re-filed it by hand.
- **2026-11-02:** replaced by the Sponsor's call on mqucifer/crew#1005: a bug whose fix is only in tests stays a bug, and the reproduction check takes the proof above. Triage no longer makes one a story, and the gate's final finding for it is gone. mqucifer/crew-presentation#300 had no `**Where:**` line, stayed a bug, and was blocked by that finding for a person, holding the 3.0.0 pin.
