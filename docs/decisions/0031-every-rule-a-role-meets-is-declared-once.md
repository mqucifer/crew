# 31. Every rule a role meets is declared once, by category

- **Date:** 2026-10-17
- **Status:** Accepted (built by crew#756 part 3, with the answer-shape category moved first; each other category moves in its own cleanup)

## Context

The crew's roles meet several kinds of rule: forms that refuse an answer, checks whose reason is sent back, guards on a change, edits that can't be applied, gates, holds, and lines in their prompts. Each was added one incident at a time, where the incident happened. By 2026-10-17 nobody could list them.

crew#756's list, generated from the code, found 372. Of the rules that refuse something, 67 of 113 were never told to the role they refuse, and 180 of 224 told lines had no check found. Some rules were said in several places with different wording. The defects found since crew#583 were of the same kinds: keys without their repository (crew#752), two readers of one fact (crew#746), one path refusing what another accepts (crew#744).

The Sponsor wanted a structure, not a one-off review: "the code itself is organized in such a way that new rules/changes are put in and maintained in a way that they are much easier to trace … This is not a 1 time effort or problem."

Linters face the same problem at a larger scale, with hundreds of rules contributed one at a time, and settled it the same way. In ruff and ESLint each rule has an id, a category, documentation, and one declaration that the code, the docs and the configuration read. In ESLint, a rule's messages are declared in its `meta.messages` and reported by `messageId`, so the text a user sees comes from one place. Rules are enabled, reviewed and retired by category.

## Decision

- **Each rule has one declaration.** The catalog is `src/crew_org/config/rules/`, one file per category, keyed by the rule's id. A declaration holds what a person decides about the rule:
  - its category;
  - its owner, the role it serves;
  - its origin, the incidents it came from;
  - whether it's guidance;
  - for a rule that refuses through `rulebook.refuse()`, its message and how its form tells it.

  Where a rule is checked and where it's told are read from the code by `rule_list`, never written twice.
- **The code and the catalog are held together by tests:**
  - every rule in the code is declared, and every declaration is a rule the code has;
  - `docs/reference/rules.md`, generated from both, matches what's committed.

  `crew rules --declare` proposes the declaration of a new rule. Its category is proposed by a placement table in code (by kind, module, form, failure class, the words of its name, or the site a told line sits at), and a person decides it.
- **Eight categories, by what a rule protects** (the Sponsor, 2026-10-17):

  | Category | Protects |
  |---|---|
  | answer_shape | the answer parses and is complete |
  | scope | the work stays in the story |
  | edits | a change applies cleanly |
  | evidence | tests prove the criteria |
  | guards | safety |
  | flow | when a card moves |
  | contracts | cross-project contracts (the reading of this one is the Sponsor's to confirm: cross-project only, or everything other work relies on) |
  | guidance | writing guidance |
- **Two policies** (the Sponsor, 2026-10-17):
  1. **A rule that's told is checked, or labelled guidance.** A told-only rule gets a check where its condition is clear and testable. Otherwise it's labelled guidance (kept in the prompt, not enforced, counted against its role's budget) or cut. A literal check that refuses legitimate answers is worse than guidance (crew#761).
  2. **A rule that's checked is told,** in the description of the form it checks and only to the role that fills that form. The told text comes from the same declaration as its refusal message. Checks on the model call itself (an empty answer, a field cut off) are exempt, because the model doesn't choose them. So are the crew's own decisions (`rules.Rule`), which check no form.
- **A guidance budget per role** (the operator, 2026-10-17): each role starts at its count that day (`config/rules/budget.yaml`, 266 lines in all), so it can only go down. A test fails when a role goes over.
- **Retirement is flagged at the retro, never automatic** (the operator, 2026-10-17). A rule that can fire and hasn't in the last 10 sprints is a candidate to retire, and the five that fire most are shown as "a missing tool?". Both come from `crew rules --events`.
- **The structure lands in stages** (the operator, 2026-10-17). Part 3 adds the declaration, the catalog, the tests both ways, the budget, and `refuse()` with generated form descriptions, and moves answer_shape end to end. Each other category moves to `refuse()` in its own cleanup PR, where its messages are reviewed anyway. `rulebook.MIGRATED` names the categories that have moved; the second policy is tested for those.
- **New rules are frozen while part 3 is built** (the Sponsor, 2026-10-17). No PR adds a rule, except a fix for a live incident that the operator OKs. The freeze ends when part 3 merges.

## Consequences

- **Adding a rule is one change in one place:**
  1. a declaration in its category's file;
  2. `refuse("<id>", ...)` where it's checked.

  The form's description, the refusal, the list, the counts and the docs follow from them. A rule added any other way fails the tests.
- **Refusals name their rule.** A refusal raised through `refuse()` carries its id, and the event log records it. `crew rules --events` counts those by name, where today most refusals are matched by their text.
- **The Sponsor reviews by category, not by row.** Each category's summary covers: rules by kind, both policy gaps, fires, refused hours and roles.
- **The cleanup is category by category,** each one deciding check, guidance or cut for its told-only rules and giving each rule one owner.
- **Some lines are unplaced.** 24 told lines fit no one category: the onboarding interview, the Developer's instructions for reading files, and §16 of the ways of working. They're declared in `unplaced.yaml` until a cleanup places them.

## Changelog

- **2026-10-17:** decided. The Sponsor decided the shape, the categories, the two policies and the freeze in chat, recorded on crew#756. The operator decided the staging, the 10-sprint retirement flag and the starting budgets.
