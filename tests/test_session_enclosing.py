"""`search` and `view` say what each line sits in (crew#807).

The panel's QA read two hits in sprint-metrics' `tests.yml`, a postgres service and
`SPRINT_METRICS_DB`, as the `tests` job's. Both were in the `docker` job.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from crew_org.session.enclosing import enclosing
from crew_org.session.tools import SEARCH_HITS, Tools

# QA's case: the `tests` job has no database; the `docker` job has Postgres.
WORKFLOW = """\
name: tests

on:
  pull_request:
  push:
    branches: [main]

jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: uv sync --extra dev
      - run: uv run pytest -q

  docker:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
        options: >-
          --health-cmd="pg_isready"
    env:
      SPRINT_METRICS_DB: postgresql://postgres:postgres@localhost:5432/sprint_metrics
    steps:
      - uses: actions/checkout@v4
      - name: Smoke the service
        run: |
          set -euo pipefail
          docker run -e SPRINT_METRICS_DB="$SPRINT_METRICS_DB" smoke --service
"""

STORE = '''\
"""The store."""

import os

DB = os.environ.get("SPRINT_METRICS_DB")


class Store:
    """Events, by sprint."""

    def connect(self):
        return connect(DB)

    @staticmethod
    def schema():
        return "CREATE SCHEMA sprint_metrics"


def connect(url):
    return url
'''

SERVICE = """\
# The service

It needs a database.

## Settings

`SPRINT_METRICS_DB` names the database.

```sh
# not a heading: a comment in a fence
export SPRINT_METRICS_DB=postgresql://localhost/sprint_metrics
```

### Startup

The schema is created at startup.
"""


@pytest.fixture
def root(tmp_path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "tests.yml").write_text(WORKFLOW)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "store.py").write_text(STORE)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "service.md").write_text(SERVICE)
    return tmp_path


def tools(root):
    return Tools(root, None, SimpleNamespace(verify=()))


def search(root, pattern, path=None):
    args = {"pattern": pattern, **({"path": path} if path else {})}
    return tools(root).call("search", args).text


def test_each_workflow_hit_names_its_job_as_qa_needed(root):
    reply = search(root, "postgres|SPRINT_METRICS_DB", ".github")
    shown = reply.splitlines()[2:]
    assert shown[:2] == [
        "  19 [jobs.docker.services]: postgres:",
        "  20 [jobs.docker.services.postgres]: image: postgres:16",
    ]
    # Lines in a row with one name are put under it, once.
    assert shown[2:5] == [
        "  [jobs.docker.services.postgres.env]",
        "    22: POSTGRES_USER: postgres",
        "    23: POSTGRES_PASSWORD: postgres",
    ]
    assert "  27 [jobs.docker.env]: SPRINT_METRICS_DB:" in reply
    assert "  33 [jobs.docker.steps[1].run]: docker run" in reply
    # Every hit names the docker job, and none the tests job.
    names = [line for line in shown if "[" in line]
    assert len(names) == 5 and all("[jobs.docker." in line for line in names)
    assert "jobs.tests" not in reply


def test_a_key_path_counts_items_from_0_and_a_flow_list_is_named_by_its_key(root):
    named = enclosing("tests.yml", WORKFLOW)
    assert named is not None
    lines = WORKFLOW.splitlines()
    by_line = {line.strip(): named.name(n) for n, line in enumerate(lines, 1)}
    assert by_line["- run: uv run pytest -q"] == "jobs.tests.steps[2]"
    assert by_line["branches: [main]"] == "on.push"
    assert by_line["docker:"] == "jobs"
    assert by_line["name: tests"] == "top level"


def test_a_python_hit_names_its_method_function_or_module_level(root):
    reply = search(root, "connect|DB|schema|staticmethod", "src")
    assert "  5 [module level]: DB = os.environ.get(" in reply
    assert "  [Store.connect]\n    11: def connect(self):\n    12: return connect(DB)" in reply
    # A decorator is its definition's: `@requires_db` names the test it marks.
    assert "  [Store.schema]\n    14: @staticmethod\n    15: def schema():" in reply
    assert "  19 [connect]: def connect(url):" in reply


def test_a_constant_over_several_lines_names_its_lines():
    named = enclosing("schema.py", 'X = 1\nSCHEMA = {\n    "maximum": 100,\n}\n')
    assert named is not None
    assert [named.name(n) for n in (1, 3)] == ["module level", "SCHEMA"]


def test_a_markdown_hit_names_its_heading_and_a_fence_holds_none(root):
    reply = search(root, "SPRINT_METRICS_DB|schema|database", "docs")
    assert "  3 [# The service]: It needs a database." in reply
    assert "  [## Settings]\n    7: `SPRINT_METRICS_DB` names" in reply
    assert "    11: export SPRINT_METRICS_DB" in reply
    assert "  16 [### Startup]: The schema is created at startup." in reply
    named = enclosing("service.md", SERVICE)
    assert named is not None
    # A heading is under the one above it of a higher level.
    assert [named.name(n) for n in (1, 5, 14)] == ["top of file", "# The service", "## Settings"]


def test_a_file_that_doesnt_parse_shows_plain_lines_and_says_so(root):
    (root / "broken.yml").write_text("jobs:\n  tests: [postgres\n  docker: postgres\n")
    (root / "broken.json").write_text('{"services": {"postgres": 1}\n')
    reply = search(root, "postgres")
    assert (
        "broken.yml (it doesn't parse as YAML, so what each line is in isn't shown):\n"
        "  2: tests: [postgres\n"
        "  3: docker: postgres\n"
    ) in reply
    assert "broken.json (it doesn't parse as JSON, so what each line is in isn't shown):" in reply
    view = tools(root).call("view", {"path": "broken.yml", "from_line": 2}).text
    assert "It doesn't parse as YAML, so what line 2 is in isn't shown." in view


def test_a_json_hit_names_its_key_path_comments_and_trailing_commas_too(root):
    (root / ".devcontainer.json").write_text(
        '{\n  // the service\'s database\n  "services": {\n    "postgres": {\n'
        '      "image": "postgres:16",\n      "ports": [\n        5432,\n      ],\n'
        "    },\n  },\n}\n"
    )
    reply = search(root, "postgres|5432", ".devcontainer.json")
    assert '  4 [services]: "postgres": {' in reply
    assert '  5 [services.postgres]: "image": "postgres:16",' in reply
    assert "  7 [services.postgres.ports[0]]: 5432," in reply


def test_any_other_file_is_unchanged(root):
    (root / "Dockerfile").write_text("FROM python:3.12\nENV SPRINT_METRICS_DB=\n")
    assert "Dockerfile:\n  2: ENV SPRINT_METRICS_DB=" in search(
        root, "SPRINT_METRICS_DB", "Dockerfile"
    )


def test_a_view_of_a_range_names_what_its_first_line_is_in(root):
    t = tools(root)
    window = t.call("view", {"path": ".github/workflows/tests.yml", "from_line": 21, "to_line": 24})
    assert window.text.startswith(
        "`.github/workflows/tests.yml lines 21-24` The file has 33 lines. "
        "Line 21 is in [jobs.docker.services.postgres].\n"
    )
    method = t.call("view", {"path": "src/store.py", "from_line": 12}).text
    assert "Line 12 is in [Store.connect]." in method.splitlines()[0]
    # A definition's view names itself already.
    assert "is in" not in t.call("view", {"path": "src/store.py", "definition": "Store"}).text


def test_past_the_cap_each_count_names_its_parts_when_theyre_few(root):
    (root / "src" / "many.py").write_text(
        "def big():\n" + "    hit = 1\n" * SEARCH_HITS + "\n\ndef other():\n    hit = 2\n"
    )
    (root / "src" / "spread.py").write_text(
        "".join(f"def f{i}():\n    hit = {i}\n\n\n" for i in range(5))
    )
    reply = search(root, "hit", "src")
    assert "in 2 files. Too many to show" in reply
    assert f"src/many.py: {SEARCH_HITS + 1} lines, in [big], [other]" in reply
    assert "src/spread.py: 5 lines\n" in reply + "\n"  # five parts: too many to name
