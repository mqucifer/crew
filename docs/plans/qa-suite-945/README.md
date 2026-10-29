# qa-suite's side of a dispute (crew#945)

These two files are qa-suite's `.github/workflows/suite.yml` and `qa.yml`, changed for a
dispute's test to be checked against the disputed pull request, not the project's main.
They start from qa-suite's main at `316187d`. **The operator opens them as a pull request in
mqucifer/qa-suite**: the crew's App can't push workflow files there
(`workflows_not_permitted`). Once that merges, delete this folder.

## What changes

- **`suite.yml`, QA's own pull request.** When its body carries the crew's dispute marker,
  `<!-- crew:qa-dispute repo=<project> story=<n> pull=<n> head=<sha> -->`, that project's
  folder is built at the disputed pull request's head, not at its main. The crew writes the
  marker into QA's dispute pull request (`qa_tasks.dispute_line`). Only a pull request from
  qa-suite's own branches is read that way, never a fork's.
- **`qa.yml`, the check a project's pull request runs (`qa-suite / qa`).** It first looks for
  an open qa-suite pull request, from qa-suite's own branches, whose dispute marker names the
  caller's pull request. If one exists, it runs that pull request's tests instead of main's.
  In a merge queue, it reads the pull request's number from the queue's branch name.
  Otherwise it runs main, as before. `build-ref` is the new input `suite.yml` uses to pass
  the disputed head.

## Why both

The crew lands the story's pull request first and QA's straight after, as the Sponsor
decided (ADR 0006 in mqucifer/project-print). The story's own `qa-suite / qa` check is
required, and against main's old test it fails. That's the dispute. With `qa.yml`'s change
it runs QA's changed test, which already passed against the story's head in QA's own check.

## The window between the two merges

From the moment the story lands until QA's pull request does, qa-suite's main still has the
old test, and the project's main has the new behaviour. A `qa-suite / qa` check another pull
request of that project starts in that window fails on the old test. The crew keeps the
window short:

- The story lands only when QA's pull request is approved and its checks have passed
  (`merge._qa_beside`). QA's never lands before the story (`qa_tasks.waits_for_story`).
- With no merge queue, both merge in the same call, one straight after the other: the window
  is seconds.
- With a merge queue (sprint-metrics), the queue merges the story, and the next pass of the
  tick lands QA's. The window is up to one pass.

A check that failed in the window comes back to its Developer as CI's verdict, like any
other red check. A new commit, or the crew's empty-commit rerun, runs it again on the fixed
main.

## How to check it once it's open

Open the qa-suite pull request from a branch of qa-suite itself. Its `suite` run has no
dispute marker in its body, so it changes nothing for its own run. To see the dispute path,
re-run mqucifer/qa-suite#10's check after this merges, with #10's body edited to carry the
marker for mqucifer/crew-presentation#200 (already merged, so its head still exists).
