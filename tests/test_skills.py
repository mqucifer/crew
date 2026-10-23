"""DevOps's workspace skill: know-how its workspace Tasks read on demand (the Sponsor,
2026-10-23, crew#839).

mqucifer/sprint-metrics#739 spent its gate rounds learning by trial how the crew runs a
workspace. The skill says it, generated from what the crew runs, and only the work
that needs it is offered it.
"""

from __future__ import annotations

import json

import pytest

from crew_org.events import EventKind
from crew_org.flows import role_tasks
from crew_org.session import conversation, skills
from crew_org.session.tools import Tools
from crew_org.tools import container, devcontainer, services, workspace
from tests.test_session import CHECKS, Fake, Proxy, kinds, said, seen  # noqa: F401


def body(kind: str, key: str = "repository") -> str:
    return role_tasks.render(
        role_tasks.DEVOPS, kind=kind, key=key, why="w", work="w", done_when=["d"]
    )


# --- the text is the crew's own, so it can't drift -------------------------------------


def test_the_workspace_skill_says_what_the_crew_reads_and_runs():
    text = skills.workspace()
    for name in (
        *devcontainer.SETUP_FIELDS,
        *devcontainer.NOT_HONOURED,
        *services.HONOURED,
        *container.MANIFESTS,
    ):
        assert f"`{name}`" in text, name
    assert " ".join(workspace.SETUP) in text  # the default an own image replaces
    assert "no `apt-get`" in text and "--cap-drop ALL" in text and "--network none" in text
    assert "`/var/run/postgresql`" in text  # a database's socket directory is a tmpfs


def test_a_changed_constant_changes_the_skill(monkeypatch):
    monkeypatch.setattr(devcontainer, "SETUP_FIELDS", ("onCreateCommand",))
    assert "exactly one of `onCreateCommand`, as an array" in skills.workspace()


def test_the_services_limits_are_read_from_the_flags_they_start_with(monkeypatch):
    real = container.service_command

    def loosened(*a, **k):
        return [f for f in real(*a, **k) if f != "--read-only"]

    assert "Each starts as its own `user`, read-only," in skills.workspace()
    monkeypatch.setattr(container, "service_command", loosened)
    assert "Each starts as its own `user`, read-only," not in skills.workspace()


# --- only a workspace Task is offered it -----------------------------------------------


@pytest.mark.parametrize(
    ("kind", "key", "offered"),
    [
        (role_tasks.FIRST_WORKSPACE, "repository", True),
        (role_tasks.NEED, "postgres", True),
        (role_tasks.FINDING, "workspace-0123456789ab", True),
        (role_tasks.CI_SERVICES, "repository", False),
        (role_tasks.RELEASE, "1.4.0", False),
    ],
)
def test_devops_workspace_tasks_are_offered_the_skill_and_its_others_not(
    tmp_path, kind, key, offered
):
    found = skills.for_task(role_tasks.DEVOPS, body(kind, key), tmp_path, [])
    assert [s.name for s in found] == (["workspace"] if offered else [])


def test_any_devops_task_whose_branch_changes_the_workspace_file_is_offered_it(tmp_path):
    changed = [".devcontainer/devcontainer.json"]
    found = skills.for_task(role_tasks.DEVOPS, body(role_tasks.CI_SERVICES), tmp_path, changed)
    assert [s.name for s in found] == ["workspace"]


def test_no_other_role_is_offered_it(tmp_path):
    first = body(role_tasks.FIRST_WORKSPACE)
    assert skills.for_task(role_tasks.ARCHITECT, first, tmp_path, []) == []
    assert skills.for_task(None, first, tmp_path, []) == []


def names(specs):
    return [s["function"]["name"] for s in specs]


def test_the_tool_and_the_starts_line_come_with_the_skill_and_not_without():
    offered = [skills.SKILLS["workspace"]]
    with_it = names(role_tasks.done_specs(role_tasks.DEVOPS, offered))
    assert "skill" in with_it and with_it.index("skill") < with_it.index("done")
    assert "skill" not in names(role_tasks.done_specs(role_tasks.DEVOPS))
    start = role_tasks.task_start(role_tasks.DEVOPS, "2026-10-23", offered)
    assert start.endswith(
        "Skills for this Task: `workspace`, how the crew runs a workspace: setup, the network, "
        "services, and what the checks refuse. Read one with `skill` when you need it."
    )
    assert "Skills" not in role_tasks.task_start(role_tasks.DEVOPS, "2026-10-23")


# --- read on demand, and counted --------------------------------------------------------


def test_skill_returns_the_text_and_refuses_a_name_there_isnt(tmp_path):
    t = Tools(tmp_path, Fake(), CHECKS)
    read = t.call("skill", {"name": "workspace"})
    assert read.kind == "ok" and read.text == skills.workspace()
    refused = t.call("skill", {"name": "nope"})
    assert refused.kind == "refused" and refused.rule == "session.skill"
    assert "`workspace`" in refused.text


def test_its_event_records_the_name_asked_for_never_the_text(tmp_path, seen):  # noqa: F811
    """M11's evidence of whether skills are read: one event per call, by name."""
    (tmp_path / "a.py").write_text("x = 1\n")
    proxy = Proxy(
        said(("skill", {"name": "workspace"})),
        said(("skill", {"name": "nope"})),
        said(("blocked", {"missing": "nothing", "kind": "fact"})),
    )
    specs = role_tasks.done_specs(role_tasks.DEVOPS, [skills.SKILLS["workspace"]])
    conversation.session(tmp_path, "# Task", Fake(), CHECKS, send=proxy, specs=specs).run()
    assert "skill" in names(proxy.sent[0]["tools"])
    finished = [
        e.detail for e in kinds(seen, EventKind.TOOL_FINISHED) if e.detail["tool"] == "skill"
    ]
    assert [(e["target"], e["result"], e.get("rule")) for e in finished] == [
        ("workspace", "ok", None),
        ("nope", "refused", "session.skill"),
    ]
    assert all("How the crew runs" not in json.dumps(e) for e in finished)
    assert kinds(seen, EventKind.SESSION_STARTED)[0].detail["skill_offered"] is True


def test_workspace_check_points_to_the_skill_when_its_offered(tmp_path):
    (tmp_path / ".devcontainer").mkdir()
    (tmp_path / ".devcontainer/devcontainer.json").write_text('{"image": "x"}')
    pointer = "The `workspace` skill says how the crew runs a workspace."
    assert pointer in Tools(tmp_path, Fake(), CHECKS, skills=True).call("workspace_check", {}).text
    assert pointer not in Tools(tmp_path, Fake(), CHECKS).call("workspace_check", {}).text


def test_a_failed_setup_at_the_start_points_to_the_skill():
    from crew_org.flows import session_end

    assert "The `workspace` skill" in session_end.setup_failed_at_start("$ x\n(exit 1)\n")


def test_a_ci_tasks_own_compose_file_doesnt_offer_the_workspace_skill(tmp_path):
    found = skills.for_task(
        role_tasks.DEVOPS, body(role_tasks.CI_SERVICES), tmp_path, ["ci/docker-compose.yml"]
    )
    assert found == []
