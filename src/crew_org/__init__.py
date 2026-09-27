"""The crew: an agile team of agents, orchestrated by a GitHub Projects board."""

import os

# CrewAI's own anonymous telemetry is on by default and goes to CrewAI's
# servers. The Sponsor turned it off (2026-09-27): nothing about the crew's
# work leaves for a third party. Set here, before anything imports CrewAI,
# because it reads this once as it starts, and the crew reads .env into a dict,
# not into the environment. `setdefault`, so an explicit choice still wins.
# Not OTEL_SDK_DISABLED, which would switch off the crew's own tracing too.
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
