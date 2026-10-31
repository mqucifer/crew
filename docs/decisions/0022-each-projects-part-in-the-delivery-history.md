# 22. Each project's part in the delivery history

- **Date:** 2026-10-07
- **Status:** Accepted (v1; to be built by mqucifer/sprint-metrics#461, mqucifer/sprint-metrics#462, crew#280 and crew#521)

## Context

The crew's delivery history passes through three projects: sprint-metrics, the crew
and crew-presentation. What each one does was never written down, so each guessed.
- sprint-metrics took a sprint to be a calendar month, because no one told it what
  the crew sends.
- The planning for crew-presentation drifted between the site reading sprint-metrics
  directly, the crew passing sprint-metrics' presented findings through, and the
  site writing its own prose.

ADR 0021 settles how data moves between projects. This settles what each one does
with it.

The Sponsor, 2026-10-07: "Crew wants sprint-metrics to keep and return general data
for multiple sprints in some desired sprint format. Crew will then take back the
computed data, add its OWN events and analysis and produce a data report package
that presentation then unwraps and displays. So let's be real clear about the scope
in each piece."

## Decision

| Project | Its part | Not its part |
|---|---|---|
| **sprint-metrics** | **Keeps and computes.** It takes the board events the crew sends, with each sprint as the crew defines it (a name and its dates), keeps that history, and returns computed data for one sprint or several. | The board, the crew's other events, analysis or prose for the crew, and crew-presentation, which it doesn't know exists. |
| **The crew** | **Adds and packages.** It sends sprint-metrics its events and sprints, takes back the computed data, adds its own events and cards, and releases the `delivery-history` package (ADR 0021) with only what ADR 0020 allows. | Computing the metrics sprint-metrics computes. |
| **crew-presentation** | **Unwraps and displays.** It validates the package against its schema and shows it, built to its design reference. | Computing, analysis, data of its own, or knowing sprint-metrics exists. |

- **A Goal for one of them says what it's sent and what's wanted back.** It doesn't
  design the project's API or model: how to do it is that project's crew's job.
- **The package carries what happened, and sprint-metrics' answers.** Events, cards and
  sprints are the crew's. A count, a sum, a verdict or a sentence worked out from those
  isn't the crew's to send: if a reader wants one, sprint-metrics calculates it, by a Goal
  there ("I don't want crew sending duplicate data anymore. If we want something we'll make
  sprint metrics calculate it": the Sponsor, 2026-10-31, crew#963). What first try means is
  sprint-metrics' to say, from the events it's sent (the Sponsor, 2026-10-30, crew#963). The
  crew's internal counts for its own stops and blocks and its local `crew export` report
  stay, since neither is sent to a reader.

## Consequences

- sprint-metrics' Goals ask for data and leave presentation to its users.
  mqucifer/sprint-metrics#462 is the first written this way.
- The crew's package carries both sprint-metrics' computed data and the crew's
  events and cards, so crew-presentation's Goals only point to the package for what
  they show.
- **v1.** This is the split for the first delivery history. A later need, such as
  another consumer or analysis that belongs in sprint-metrics, changes this ADR by
  pull request.

## Changelog

- 2026-10-07: Accepted. The Sponsor set the scope of each piece while planning the
  path to crew-presentation (crew#521).
- 2026-10-30: A story's pattern leaves the crew's analysis, with delivery-history
  3.0.0: the crew logs what happened, and sprint-metrics decides first try (the
  Sponsor, crew#963).
- 2026-10-31: The crew's analysis leaves the package whole, with delivery-history 3.0.0,
  and its code with it: the crew sends no duplicate data, and what a reader wants worked
  out, sprint-metrics calculates (the Sponsor, crew#963).
