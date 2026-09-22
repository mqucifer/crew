# The Crew at Final State

What a finished crew does, capability by capability, and what "finished" means
for each.

**This document holds intent only.** Nothing in it should be falsifiable by a
commit. It says what we are building toward, not what exists — so it goes out
of date when we change our minds, not when someone changes the code.

That division is deliberate, and it is the reason this file exists separately:

| Document | Answers | Goes stale when |
|---|---|---|
| [`ways-of-working.md`](ways-of-working.md) | What are the rules? | we change the rules |
| [`running-the-crew.md`](running-the-crew.md) | How do I operate it today? | the code changes |
| **this** | What are we building? | we change our minds |
| `crew capability` | How far along are we? | never — it is computed |

**Where we are against this is not written down anywhere, on purpose.** Every
status claim we have written in prose has gone stale silently: a permission
that changed and a page that did not, a capability scored *Partial* for a role
that could not do the thing at all, an event count that was wrong by half. Ask
`crew capability`, or read a test. If a gap cannot be computed or asserted, say
so out loud rather than freezing it in a paragraph here.

---

## The North Star

**An organisation that improves itself, where the Sponsor's only job is
deciding what is worth building.**

Not "a crew that ships stories" — that exists. The thing worth aiming at is the
loop closing on itself: the crew measures its own delivery, notices where it is
failing, proposes the fix as a card, and that card goes through the same board
as everything else.

That reframes what scale means. If the crew runs continuously, the binding
constraint becomes **goal supply** — how fast a person can decide what is worth
building — and the Sponsor's job collapses to authoring goals and reading
reviews.

**The measure of arrival is intervention.** Every human touch that is not
authoring a goal or accepting an increment is a capability gap. A finished crew
is one where that number is near zero and stays there while the work continues.

---

## Two questions this document cannot settle

Both are live contradictions between what is built and what was sketched in the
workflow this document absorbs. Neither should be settled as a side effect of
writing documentation.

### 1. Who is the human?

| | Calls the human | Gives *Product Owner* to |
|---|---|---|
| Built, and in the constitution | **Product Sponsor** | an agent that proposes epics |
| The absorbed sketch | **Product Owner** | the human |

**The case for Sponsor.** It is what §1 says, what the code implements, and it
keeps *Product Owner* meaning the thing that writes epics — which an agent
does. It also names the job accurately: this person funds and accepts work
rather than owning a backlog day to day.

**The case for Product Owner.** It is the word every agile practitioner
already knows, and inventing a role name is a tax on everyone who reads this
later. The agent could be called something else.

**What changes either way:** the constitution, `agents.yaml`, and every prompt
that names the role. Not large, but it touches everything.

> **Ruling: the human is the Product Owner.** It is the word every agile
> practitioner already knows, and inventing a role name taxes everyone who
> reads this later. *Product Sponsor* goes.

**The knock-on, settled with it: the agent becomes the Product Manager.** It
turns Goals into Epics and may not write acceptance criteria; the Business
Analyst writes stories from what it produces. Both names are ones a
practitioner already holds, and the altitude each works at is the standard
one — which matters more here than elsewhere, because §1 makes role boundaries
structural precisely on the grounds that granularity is what a small model
blurs.

**One oddity, named rather than discovered.** In most organisations a Product
Manager sits *above* a Product Owner, and here the agent sits below the human.
The hierarchy reads inverted. It is accepted knowingly: the alternative is a
coined name that nobody arrives already knowing.

### 2. What orchestrates — the board, or a manager?

| | Work is handed on by | Order comes from |
|---|---|---|
| Built | moving a card between columns | the board's queues |
| The absorbed sketch | a Manager Agent delegating tasks | a hierarchical process |

**The case for the board.** A card's column *is* the queue a phase reads from,
so the process is visible to a person without running anything, survives a
crash, and is restartable — the loop is a reconciliation, not a script. There
is no single agent whose failure stops the organisation.

**The case for a manager.** It is CrewAI's native shape, it makes delegation
and parallelism explicit, and a manager can hold context across a sprint that
the board cannot express.

**They are not entirely exclusive.** A manager could plan *within* a column
while the board still carries hand-offs between them. That hybrid is probably
the real answer and is worth naming rather than defaulting into.

**What changes either way:** everything about how phases are invoked. This is
the largest open decision in the project.

> **Ruling:** _unset._

---

## The capabilities, and what finished means

A capability is one question: **can the crew do this unattended, reliably,
without the card sitting there?**

The first ten are the ladder `crew capability` already scores. The last two are
proposed additions this document brings in from the absorbed sketch.

### Refinement
Goals become epics; epics become stories a test can be written from.

**Finished when** a Goal the Sponsor writes becomes stories that pass Definition
of Ready without a person editing them, and an epic too large to split says so
and parks itself rather than failing repeatedly.

### Planning
What enters a sprint, and when a sprint ends.

**Finished when** capacity is learned from measured velocity rather than fixed,
a sprint ends when its work is done rather than when a clock elapses, and
nothing enters a sprint that cannot be finished in it.

### Implementation
A story becomes code the crew can defend.

**Finished when** a story is implemented, repaired against its own failures, and
delivered without a person reading the diff first — and when a story that comes
back from a gate is re-worked carrying everything the gate said.

### Review
The diff is judged.

**Finished when** every revision is judged, a verdict belongs to the commit it
judged, and the Reviewer never re-litigates a point it has settled or raises
late one it could have raised first.

### Acceptance
The behaviour is judged against the criteria.

**Finished when** every criterion is proven by a named test, a verdict refuses
rather than guesses when the evidence does not fit, and QA is consistent across
attempts on the same card.

### Release
Approved work reaches the default branch.

**Finished when** approved work lands without a person, a conflict blocks for a
human decision rather than being worked around, and **a merged change can be
reverted through the same board as every other change.** That last clause is
the one that makes landing safe enough not to rehearse.

### Flow metrics
The crew can measure its own delivery.

**Finished when** time in each column, exercise, intervention and rework are all
computed from the crew's own record, and a person moving a card by hand is
visible rather than inferred — today intervention is a floor, not a count.

### Retrospective
The crew learns from a sprint it has finished.

**Finished when** a retro's findings become cards on the same board as
everything else, and the retro survives the terminal it was printed in.

### Self-diagnosis
The crew finds its own defects without a person reading the code.

**Finished when** a role reads the crew's own source and reports what is wrong
with it, that role cannot write, and its accepted findings become cards. The
test of arrival: it finds something a person would otherwise have found.

### Audit trail
What happened, who did it, and why — legible without reading the code.

**Finished when** every transition carries its actor and reason, what the model
did and cost is on the record, and the Sponsor can answer "why did this go the
way it did" without opening a terminal.

### Security *(proposed)*
Generated code is checked for what tests do not catch.

**Finished when** dependency and static analysis run against every change
alongside acceptance, and a finding routes back to the Developer as a structured
summary rather than raw output.

**Why proposed rather than assumed:** nothing scores this today and no card
carries it. Adding a capability is a claim that we intend to build it, so it
needs a decision, not an assumption.

### Test planning *(proposed)*
Tests are designed from acceptance criteria before code exists.

**Finished when** a story's criteria become a test plan at the moment the sprint
is agreed, that plan lands in a suite that outlives any single change, and the
suite is what catches regressions across stories.

**The tension worth naming:** this splits test ownership — the Developer owns
narrow tests beside the code, QA owns the durable suite. It also means QA
produces work before the Developer starts, which the current column order does
not allow for.

---

## The roles

Seven agents and one person today. The absorbed sketch proposes nine agents.

| Role | Exists | At final state |
|---|---|---|
| **Product Owner** (the human) | yes | Authors goals, approves epics, accepts increments. Nothing else. |
| Product Manager | agent | Goals into epics |
| Business Analyst | agent | Epics into stories, criteria, estimates |
| Architect | agent | Design notes, rationed by complexity |
| Developer | agent | Code, tests, pull requests |
| QA Engineer | agent | Behaviour verdicts — and test plans, if *Test planning* is adopted |
| Code Reviewer | agent | Diff verdicts |
| Scrum Master | agent | Standups, retros, defects in the process or the product |
| Senior Engineer | **no** | Diagnoses the crew's own code, and never writes |
| Security | **no** | Proposed with *Security* above |
| Release / DevOps | **no** | Proposed: merge, deploy, promote — beyond a merged pull request |
| Manager | **no** | Only if question 2 resolves toward a manager |

**A role is added by adding a capability, never the other way round.** A role
with nothing behind it is a declaration that documents something false — which
has happened, in the enum and in an allow-list both.

---

## The ceremonies

Each produces something, and each is triggered by something. A ceremony no
trigger fires is a capability that exists on paper.

| Ceremony | Produces | Triggered by |
|---|---|---|
| Refinement | stories that satisfy Definition of Ready | a goal or epic arriving |
| Planning | a sprint filled to capacity | a sprint starting |
| Standup | what moved, what is blocked, what waits on a person | to be decided — it has no trigger today |
| Review | a verdict per revision | a pull request, or a new head on one |
| Acceptance | a verdict per criterion | a diff that passed review |
| Sprint review | an accepted increment | the sprint ending |
| Retrospective | cards, not prose | the sprint ending |

---

## What we deliberately do not build

- **A second place the crew can be measured from.** The crew computes its own
  metrics; the pilot may present them. Making the crew's self-knowledge depend
  on the repository it practises on breaks it exactly when it is most needed.
- **Escalation as a substitute for task design.** It stays budgeted,
  classified, and forbidden as an answer to a poorly written story.
- **A rehearsal of the real process.** Work lands and is reverted; it is not
  previewed. Anything that cannot be safely landed is a missing capability, not
  a reason for a dry mode.
- **Multi-project machinery, yet.** A file-backed ledger is right until there
  is a second delivery project. When there is, a card's identity is already
  repository-scoped and the same seam applies to the logs.

---

## How we know where we are

Not from this document.

- `crew capability` — the investment ledger, and time in column, exercise,
  intervention and rework, computed from the board and the move log
- the test suite — claims about the code that would otherwise rot in prose, such
  as whether anything can still reach a given function
- the backlog — what is carded against each capability above

**A gap that can be computed should be computed; a gap that can be asserted
should be a test; only a gap that is neither belongs in prose, and then with a
date and the evidence beside it.**
