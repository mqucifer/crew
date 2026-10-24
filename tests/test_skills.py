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
        (role_tasks.FIRST_WORKSPACE, "repository", ["workspace"]),
        (role_tasks.NEED, "postgres", ["workspace"]),
        # The gate's finding that the workspace doesn't run what CI runs is both.
        (role_tasks.FINDING, "workspace-0123456789ab", ["workspace", "ci"]),
        (role_tasks.CI_SERVICES, "repository", ["ci"]),
        (role_tasks.FINDING, "s12-devops-c0ffee", []),
        (role_tasks.RELEASE, "1.4.0", []),
    ],
)
def test_each_devops_task_is_offered_the_skills_its_kind_needs(tmp_path, kind, key, offered):
    found = skills.for_task(role_tasks.DEVOPS, body(kind, key), tmp_path, [])
    assert [s.name for s in found] == offered


def test_any_devops_task_whose_branch_changes_the_workspace_file_is_offered_it(tmp_path):
    changed = [".devcontainer/devcontainer.json"]
    found = skills.for_task(role_tasks.DEVOPS, body(role_tasks.RELEASE, "1.4.0"), tmp_path, changed)
    assert [s.name for s in found] == ["workspace"]


def test_any_devops_task_whose_branch_changes_a_workflow_is_offered_ci(tmp_path):
    changed = [".github/workflows/ci.yml"]
    for kind, key in ((role_tasks.RELEASE, "1.4.0"), (role_tasks.FIRST_WORKSPACE, "repository")):
        found = skills.for_task(role_tasks.DEVOPS, body(kind, key), tmp_path, changed)
        assert "ci" in [s.name for s in found], kind
    other = [".github/dependabot.yml", "CHANGELOG.md"]
    assert (
        skills.for_task(role_tasks.DEVOPS, body(role_tasks.RELEASE, "1.4.0"), tmp_path, other) == []
    )


def test_no_other_role_is_offered_either(tmp_path):
    workflow = [".github/workflows/ci.yml"]
    for kind in (role_tasks.FIRST_WORKSPACE, role_tasks.CI_SERVICES):
        assert skills.for_task(role_tasks.ARCHITECT, body(kind), tmp_path, workflow) == []
        assert skills.for_task(None, body(kind), tmp_path, workflow) == []


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


def test_a_ci_tasks_start_names_the_ci_skill(tmp_path):
    offered = skills.for_task(role_tasks.DEVOPS, body(role_tasks.CI_SERVICES), tmp_path, [])
    start = role_tasks.task_start(role_tasks.DEVOPS, "2026-10-24", offered)
    assert start.endswith(
        "Skills for this Task: `ci`, how GitHub Actions runs a job's services: reaching "
        "them, health checks, and logs. Read one with `skill` when you need it."
    )
    assert "skill" in names(role_tasks.done_specs(role_tasks.DEVOPS, offered))


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


def test_the_start_records_which_skills_were_offered(tmp_path, seen):  # noqa: F811
    (tmp_path / "a.py").write_text("x = 1\n")
    offered = [skills.SKILLS["workspace"], skills.SKILLS["ci"]]
    proxy = Proxy(said(("blocked", {"missing": "nothing", "kind": "fact"})))
    specs = role_tasks.done_specs(role_tasks.DEVOPS, offered)
    conversation.session(
        tmp_path, "# Task", Fake(), CHECKS, send=proxy, specs=specs, skills=("workspace", "ci")
    ).run()
    assert kinds(seen, EventKind.SESSION_STARTED)[0].detail["skills"] == ["workspace", "ci"]


def test_workspace_check_points_to_the_skill_when_its_offered(tmp_path):
    (tmp_path / ".devcontainer").mkdir()
    (tmp_path / ".devcontainer/devcontainer.json").write_text('{"image": "x"}')
    pointer = "The `workspace` skill says how the crew runs a workspace."
    with_it = Tools(tmp_path, Fake(), CHECKS, skills=("workspace",))
    assert pointer in with_it.call("workspace_check", {}).text
    assert pointer not in Tools(tmp_path, Fake(), CHECKS).call("workspace_check", {}).text
    only_ci = Tools(tmp_path, Fake(), CHECKS, skills=("ci",))
    assert pointer not in only_ci.call("workspace_check", {}).text


def test_a_failed_setup_at_the_start_points_to_the_skill():
    from crew_org.flows import session_end

    assert "The `workspace` skill" in session_end.setup_failed_at_start("$ x\n(exit 1)\n")


def test_a_ci_tasks_own_compose_file_doesnt_offer_the_workspace_skill(tmp_path):
    found = skills.for_task(
        role_tasks.DEVOPS, body(role_tasks.CI_SERVICES), tmp_path, ["ci/docker-compose.yml"]
    )
    assert [s.name for s in found] == ["ci"]


def test_the_skill_says_a_failed_setup_isnt_run_again_until_done():
    """The review of mqucifer/crew#880: resync follows only a setup that passed
    (`Held.synced`), exactly the case part 2's start points to the skill in."""
    text = skills.workspace()
    assert "It runs once the session's container has started" in text
    assert "Once a setup has passed, it runs again" in text
    assert "If the start's setup failed, nothing runs it again in the session: only `done`" in text


def test_each_flag_the_skill_reads_is_in_the_argv_it_reads_it_from():
    """The review of mqucifer/crew#880: the text says what runs only while each is there."""
    argv = skills.containers()
    for name, flags in skills.READS.items():
        for flag in flags:
            assert flag in argv[name], (name, flag)


def test_a_flag_taken_out_changes_the_text_never_breaks_the_tool(tmp_path, monkeypatch):
    real = container.service_command

    def bare(*a, **k):
        return [f for f in real(*a, **k) if not f.startswith("--tmpfs") and "nosuid" not in f]

    monkeypatch.setattr(container, "service_command", bare)
    read = Tools(tmp_path, Fake(), CHECKS).call("skill", {"name": "workspace"})
    assert read.kind == "ok" and "not set" in read.text


def test_the_text_is_the_same_on_any_machine():
    import os

    assert f"{os.getuid()}:{os.getgid()}" not in skills.workspace()
    assert "the host's user, never root" in skills.workspace()


# --- the ci skill: GitHub's own pages, each point linked ----------------------------------


def test_the_ci_skill_says_how_a_job_reaches_its_services_by_where_it_runs():
    text = skills.ci()
    assert "`localhost:<port>`" in text and "`ports:`" in text
    assert "`jobs.<job_id>.container`" in text
    assert "`POSTGRES_HOST: localhost`" in text and "`POSTGRES_HOST: postgres`" in text
    assert '"The `--network` option is not supported."' in text
    assert "`--health-cmd`" in text
    assert "`docker logs ${{ job.services.<service_id>.id }}`" in text


def test_each_github_point_links_the_page_it_comes_from():
    """Written from GitHub's documentation: every bullet but the crew's own part links a
    docs.github.com page."""
    github, _, crew = skills.ci().partition("## What the crew shows")
    bullets = [b for b in github.split("\n- ")[1:]]
    assert len(bullets) >= 10
    for bullet in bullets:
        assert "(https://docs.github.com/en/" in bullet, bullet[:80]
    assert "Not GitHub's: the crew's." in crew


def test_the_ci_skill_says_how_many_lines_of_a_services_log_the_crew_shows(monkeypatch):
    from crew_org.flows import ci

    assert "the last 10 lines of its own log" in skills.ci()
    monkeypatch.setattr(ci, "SERVICE_LOG_LINES", 4)
    assert "the last 4 lines of its own log" in skills.ci()


def test_skill_reads_the_ci_skill(tmp_path):
    read = Tools(tmp_path, Fake(), CHECKS).call("skill", {"name": "ci"})
    assert read.kind == "ok" and read.text == skills.ci()
