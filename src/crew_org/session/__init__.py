"""The Developer's session: a conversation of tool calls over a workspace (crew#778).

The Developer sees, finds, changes and checks the code by calling tools, one model
call at a time, until it calls `done` or `blocked`, or the session is stopped. Each
model call is made by the call layer (`calls.converse`, ADR 0025); the code it runs
runs in the story's workspace container (decision 4b). The plan is
`docs/plans/developer-session.md`, and what the replay proved is
`var/notes/778/replay.md`.

- `container`: what the session needs of its workspace container.
- `tools`: the tools (decision 2), each reply a refusal by a declared rule or a result.
- `start`: what a session starts with (decision 4), each part measured.
- `signals`: what's recorded on every call, and the two ceilings that stop a session.
- `history`: the conversation as it's sent, and what it becomes if it's compacted.
- `conversation`: the session itself, its endings (decision 3) and its records (P5).

Refinement's steps work in the same session (crew#807, ADR 0033):

- `reading`: their tools, the Developer's less what edits or runs.
- `steps`: what each step starts with, its form as `done`, and the runner.
"""
