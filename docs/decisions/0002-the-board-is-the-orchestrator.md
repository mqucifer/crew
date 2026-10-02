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
- CrewAI Flows sit on the outside for deterministic routing. Crews sit on the
  inside for bounded creative work.
- Hierarchical manager-agent crews are avoided: they were unreliable on a 27B
  local model.
- Process questions are answered by how working teams do it, not by inventing
  new mechanisms (Sponsor, 2026-10-01).

## Consequences

- Card movement and WIP limits are deterministic code, not an agent's choice.
- Anyone can read the state of the work on the board.
- Epics are the Product Owner agent's work, never written by the Sponsor or by
  Claude directly.
