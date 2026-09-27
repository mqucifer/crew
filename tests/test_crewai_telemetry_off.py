"""CrewAI's own telemetry is off: nothing about the crew's work goes to a third party."""

from __future__ import annotations

import os
import subprocess
import sys


def test_importing_the_crew_turns_crewai_telemetry_off():
    env = {k: v for k, v in os.environ.items() if k != "CREWAI_DISABLE_TELEMETRY"}
    code = (
        "import crew_org\n"
        "from crewai.telemetry.telemetry import Telemetry\n"
        "print(Telemetry._is_telemetry_disabled())\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip().endswith("True")


def test_an_explicit_choice_still_wins():
    env = {**os.environ, "CREWAI_DISABLE_TELEMETRY": "false"}
    code = "import crew_org, os\nprint(os.environ['CREWAI_DISABLE_TELEMETRY'])\n"
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "false"
