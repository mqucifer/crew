# 2. The board is the orchestrator

- **Date:** 2026-09-18
- **Status:** Accepted

## Context

The crew runs a full agile engineering organisation as agents. Earlier
attempts (custom profiles, hand-rolled agents, extensions) reinvented process
machinery that agile already solves, and left no shared state anyone could
inspect, so nothing felt orchestrated.

## Decision

- The GitHub Projects v2 board is the orchestrator: a shared state machine
  that the crew reconciles, not bespoke orchestration code.
- Deterministic routing is the crew's own Python (`flows/`). Bounded creative
  work is one model call per step, made by the crew's own call layer
  (ADR 0025).
- Hierarchical manager-agent crews are avoided: they were unreliable on a 27B
  local model.
- Process questions are answered by how working teams do it, not by inventing
  new mechanisms (Sponsor, 2026-10-01).

## Consequences

- Card movement and WIP limits are deterministic code, not an agent's choice.
- Anyone can read the state of the work on the board.
- Epics are the Product Owner agent's work, never written by the Sponsor or by
  Claude directly.

## Changelog

- **2026-10-10:** "CrewAI Flows sit on the outside for deterministic routing.
  Crews sit on the inside for bounded creative work" replaced. The flows were
  the crew's own Python from the start, and CrewAI's Crew was a one-task
  wrapper at the call sites; the crew now makes its own model calls (ADR 0025,
  Sponsor).
