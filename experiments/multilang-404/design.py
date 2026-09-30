"""The Architect's parts for the static site, from its code alone (#404, phase 2c).

A fresh design: the fixture's record without its design, and the repository as
design reads it. Checks the proposal names a javascript part with Playwright's
test files, and that the image it named pins to a real digest. Nothing is posted. Run
from the repository root: uv run python experiments/multilang-404/design.py
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import yaml

from crew_org.crews.design_crew import propose_design
from crew_org.flows.design import image_problems, pin_image, to_design
from crew_org.project import brief, read_record
from crew_org.tools.repo_context import repository_context

HERE = Path(__file__).parent
FIXTURE = HERE.parent.parent / "tests" / "fixtures" / "static-site"


def main() -> None:
    # A copy with no design recorded: the fixture's own record states the parts,
    # and the Architect is shown the repository, record file included.
    site = Path(tempfile.mkdtemp()) / "site"
    shutil.copytree(FIXTURE, site)
    record_file = site / ".crew" / "project.yaml"
    raw = yaml.safe_load(record_file.read_text())
    raw.pop("design", None)
    record_file.write_text(yaml.safe_dump(raw, sort_keys=False))
    record = read_record(site)
    proposal = propose_design(
        project=brief(record),
        repository=repository_context(site, editing=False),
    )
    named = proposal.sandbox_image.value if proposal.sandbox_image else None
    pinned, unpinnable = pin_image(proposal.sandbox_image)
    if pinned is not None:
        proposal.sandbox_image = pinned
    design = to_design(proposal)
    result = {
        "parts": [p.model_dump() for p in proposal.parts],
        "image_named": named,
        "sandbox_image": design.sandbox_image,
        "image_problems": unpinnable or image_problems(pinned),
        "setup": design.setup,
        "checks": design.checks,
    }
    (HERE / "design-result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
