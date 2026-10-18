# Session replay (crew#778, Stage R)

Does a tool-driven Developer session converge, for the crew's local model, on stories
that really failed, and at what model time and tokens against the attempts those
stories took? The plan is `docs/plans/developer-session.md`; the results are in
`var/notes/778/replay.md`.

    uv run python experiments/session_replay/replay.py sprint-metrics-662
    uv run python experiments/session_replay/replay.py        # every case, one at a time

Needs Docker and LiteLLM on :4000. Writes to `runs/<case>/<run>/`, which git ignores:
`start.md` (what the session started with), `transcript.jsonl` (each call and tool
reply), `diff.patch` (the branch against its base) and `result.json`. Nothing reaches
the board, GitHub or `var/events`.

- `cases.py`: the stories, the commit each first attempt started from, and the story
  text as it stood then, from the issue's edit history.
- `container.py`: one workspace container per session (decision 4b), with the crew's
  sandbox hardening from `Sandbox.command`. Setup runs with the network, which is then
  disconnected; everything after runs with none. Removed at the end; any left by a
  crash is swept by its label.
- `tools.py`: decision 2's tools. Files are read and written on the host, in the
  worktree only; `run`, and the lint each edit reports, run in the container.
- `start.py`: decision 4's start, each part measured.
- `session.py`: the loop and decision 3's endings: `done`, `blocked`, no progress (the
  same first error in the same test after 3 runs with edits between), the 2-hour
  backstop of model time, and 3 replies in a row that call no tool.
- `history.py`: the story's real delivery from the crew's event log, read-only.
- `replay.py`: one case end to end, and at `done` the crew's gate (autofix, then every
  check), recorded apart from the Developer's own last run.
