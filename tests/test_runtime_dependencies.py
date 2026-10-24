"""Every module the crew imports at run time is a run-time dependency (crew#889).

`packages/sprint_metrics_answers.py` imported jsonschema inside a function while
`pyproject.toml` declared it only in the `dev` extra. It worked until an exact
`uv sync` without the extra removed it, and no package was released for two
sprints. This reads the imports in `src/crew_org`, inside functions too, and
holds each to the `[project] dependencies` list.

An import name isn't a distribution name (`yaml` is pyyaml, `jwt` is pyjwt), so
`importlib.metadata.packages_distributions()` maps one to the other from what's
installed: the test environment, which has every extra. A namespace like
`opentelemetry` maps to several; one of them declared at run time is enough.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "crew_org"

# Imported by code that never runs in the crew's environment, with where it runs.
ELSEWHERE = {
    "playwright": "tools/look_page.py runs in its own image, which has playwright",
}


def imported(source: Path) -> dict[str, set[str]]:
    """Each top-level module imported under `source`, and the files that import it."""
    found: dict[str, set[str]] = {}
    for path in sorted(source.rglob("*.py")):
        # A walk, not the module's body: an import inside a function is the one that bit.
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                found.setdefault(name.split(".")[0], set()).add(str(path.relative_to(source)))
    return found


def third_party(found: dict[str, set[str]]) -> dict[str, set[str]]:
    own = {"__future__", SOURCE.name}
    return {
        name: files
        for name, files in found.items()
        if name not in sys.stdlib_module_names and name not in own
    }


def normalised(requirement: str) -> str:
    """A requirement's distribution name, normalised as PEP 503 does."""
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement.strip())
    assert match, requirement
    return re.sub(r"[-_.]+", "-", match.group()).lower()


def declared(pyproject: Path) -> tuple[set[str], set[str]]:
    """(run-time distributions, distributions declared only in an extra)."""
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    run = {normalised(r) for r in project.get("dependencies", [])}
    extras = {
        normalised(r) for group in project.get("optional-dependencies", {}).values() for r in group
    }
    return run, extras - run


def undeclared(
    found: dict[str, set[str]], *, run: set[str], extra_only: set[str], dists: dict[str, list[str]]
) -> list[str]:
    """A line for each import no run-time dependency provides."""
    problems = []
    for name, files in sorted(found.items()):
        if name in ELSEWHERE:
            continue
        provided = {normalised(d) for d in dists.get(name, [])}
        if provided & run:
            continue
        where = ", ".join(sorted(files))
        if provided & extra_only:
            problems.append(
                f"{name} ({', '.join(sorted(provided & extra_only))}) is declared only in "
                f"an extra, and imported by {where}"
            )
        else:
            problems.append(f"{name} is declared nowhere, and imported by {where}")
    return problems


def test_every_run_time_import_is_a_run_time_dependency():
    run, extra_only = declared(ROOT / "pyproject.toml")
    problems = undeclared(
        third_party(imported(SOURCE)),
        run=run,
        extra_only=extra_only,
        dists=packages_distributions(),
    )
    assert not problems, "\n".join(problems)


def test_an_import_inside_a_function_counts(tmp_path):
    (tmp_path / "late.py").write_text("def build():\n    import jsonschema\n")
    found = third_party(imported(tmp_path))
    assert undeclared(
        found, run=set(), extra_only={"jsonschema"}, dists={"jsonschema": ["jsonschema"]}
    ) == ["jsonschema (jsonschema) is declared only in an extra, and imported by late.py"]


def test_an_import_name_maps_to_its_distribution(tmp_path):
    (tmp_path / "config.py").write_text("import yaml\nfrom opentelemetry import trace\n")
    found = third_party(imported(tmp_path))
    dists = {"yaml": ["PyYAML"], "opentelemetry": ["opentelemetry-api", "opentelemetry-sdk"]}
    run = {"pyyaml", "opentelemetry-sdk"}
    assert undeclared(found, run=run, extra_only=set(), dists=dists) == []


def test_relative_and_standard_library_imports_are_not_dependencies(tmp_path):
    (tmp_path / "mod.py").write_text("import json\nfrom . import sibling\nfrom crew_org import x\n")
    assert third_party(imported(tmp_path)) == {}
