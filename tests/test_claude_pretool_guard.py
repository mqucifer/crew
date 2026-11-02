"""Claude's guard hook refuses what CLAUDE.md forbids, and nothing else (crew#458)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "claude_pretool_guard.py"


def run_guard(command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    return subprocess.run(
        [sys.executable, str(SCRIPT)], input=json.dumps(payload), capture_output=True, text=True
    )


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    git(
        path,
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
    )
    return path


@pytest.fixture
def guard(monkeypatch):
    spec = importlib.util.spec_from_file_location("claude_pretool_guard", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- pushes ----------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git push origin main",
        "git push origin HEAD:main",
        "git push -f origin +feature:refs/heads/main",
        "git push --all origin",
        "git push",
        "git push origin HEAD",
        "uv run pytest -q && git push origin main",
    ],
)
def test_a_push_to_main_is_refused(repo, command):
    done = run_guard(command, repo)
    assert done.returncode == 2
    assert "PR" in done.stderr


@pytest.mark.parametrize(
    "command", ["git push -u origin feat/458-claude-standards", "git push origin HEAD:feat/x"]
)
def test_a_push_of_a_branch_is_allowed(repo, command):
    assert run_guard(command, repo).returncode == 0


def test_a_bare_push_from_a_branch_is_allowed(repo):
    git(repo, "checkout", "-q", "-b", "feat/x")
    assert run_guard("git push", repo).returncode == 0


# --- pulls during a tick ---------------------------------------------------


def test_a_pull_in_the_main_checkout_during_a_tick_is_refused(guard, repo, monkeypatch):
    monkeypatch.setattr(guard, "tick_running", lambda: True)
    with pytest.raises(SystemExit) as stopped:
        guard.check_pull(["pull"], repo)
    assert stopped.value.code == 2


def test_merging_origin_counts_as_pulling(guard, repo, monkeypatch):
    monkeypatch.setattr(guard, "tick_running", lambda: True)
    with pytest.raises(SystemExit):
        guard.check_pull(["merge", "--ff-only", "origin/main"], repo)


def test_a_pull_with_no_tick_running_is_allowed(guard, repo, monkeypatch):
    monkeypatch.setattr(guard, "tick_running", lambda: False)
    guard.check_pull(["pull"], repo)


def test_a_pull_in_a_worktree_during_a_tick_is_allowed(guard, repo, tmp_path, monkeypatch):
    """The tick runs from the main checkout; a worktree is a separate directory."""
    worktree = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "feat/y", str(worktree))
    monkeypatch.setattr(guard, "tick_running", lambda: True)
    guard.check_pull(["pull"], worktree)


# --- bare issue numbers ----------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        'gh issue create --title "x" --body "see #12"',
        'gh pr create --title "fix: y (#458)" --body "Closes crew#458"',
        "gh pr comment 5 --body \"$(cat <<'EOF'\nFollows #301.\nEOF\n)\"",
    ],
)
def test_a_bare_issue_number_in_a_gh_body_is_refused(repo, command):
    done = run_guard(command, repo)
    assert done.returncode == 2
    assert "owner/repo#N" in done.stderr


@pytest.mark.parametrize(
    "command",
    [
        'gh issue create --title "x" --body "see crew#12 and mqucifer/sprint-metrics#3"',
        'gh pr create --body "https://github.com/mqucifer/crew/issues/12\n## Why"',
        "gh pr view 455 --json body",  # reading isn't posting
        'echo "#12"',
    ],
)
def test_scoped_references_and_other_commands_pass(repo, command):
    assert run_guard(command, repo).returncode == 0


def test_a_body_file_is_checked_too(repo):
    (repo / "body.md").write_text("Closes #9\n")
    assert run_guard("gh pr create --body-file body.md", repo).returncode == 2


# --- closing keywords (crew#951) -------------------------------------------
# GitHub closes an issue when a closing keyword comes right before its
# reference, wherever the pair sits. Only the PR's own closing line may say it.


@pytest.mark.parametrize(
    "body",
    [
        # The Sprint 34 case: merging mqucifer/crew#940 closed it.
        "Parked: couldn't close: mqucifer/crew-presentation#177",
        "this fixes mqucifer/crew#12's display",
        "It was RESOLVED https://github.com/mqucifer/crew/issues/12 already",
        "we **closed** mqucifer/crew#12 by hand",
        "the bug is fixed: _mqucifer/crew#12_",
        "see https://github.com/mqucifer/crew/pull/3 which closes mqucifer/crew#12",
        # At the start of a line, but prose follows the reference.
        "Fixes mqucifer/crew#12's display and the layout",
    ],
)
def test_a_closing_keyword_before_a_reference_in_prose_is_refused(repo, body):
    done = run_guard(f'gh pr create --title "fix: x" --body "{body}"', repo)
    assert done.returncode == 2
    assert "left open:" in done.stderr
    assert body.splitlines()[-1] in done.stderr


@pytest.mark.parametrize(
    "body",
    [
        "Closes mqucifer/crew#951",
        "closes: mqucifer/crew#951",
        "**Fixes** mqucifer/crew#951.",
        "Resolves https://github.com/mqucifer/crew/issues/951",
        "Closes mqucifer/crew#951\nCloses mqucifer/crew#952\n\n## Why\nIt closes the gap.",
        # A keyword with no reference after it closes nothing.
        "Left open: mqucifer/crew-presentation#177, couldn't close it here",
        "fix: the guard; crew#12 is plain text and closes nothing",
    ],
)
def test_the_pr_s_own_closing_line_and_plain_prose_pass(repo, body):
    assert run_guard(f'gh pr edit 5 --body "{body}"', repo).returncode == 0


def test_the_closing_line_ends_where_the_body_ends(repo):
    command = (
        'gh pr create --draft --title "fix: x" --body "Closes mqucifer/crew#951" '
        "--assignee mquarters && gh pr view --json url"
    )
    assert run_guard(command, repo).returncode == 0
    assert run_guard('gh pr edit 5 --body="Closes mqucifer/crew#951"', repo).returncode == 0


def test_a_body_with_both_is_refused_for_its_prose(repo):
    body = "Closes mqucifer/crew#951\n\n## Why\nParked: couldn't close: mqucifer/crew#940\n"
    (repo / "body.md").write_text(body)
    done = run_guard("gh pr create --body-file body.md", repo)
    assert done.returncode == 2
    assert "couldn't close: mqucifer/crew#940" in done.stderr
    assert "Closes mqucifer/crew#951" not in done.stderr


def test_a_heredoc_body_is_read_line_by_line(repo):
    command = (
        'gh pr create --title "fix: x" --body "$(cat <<\'EOF\'\n'
        "Closes mqucifer/crew#951\n\nParked: couldn't close: mqucifer/crew#940\nEOF\n)\""
    )
    assert run_guard(command, repo).returncode == 2
    command = command.replace("couldn't close", "left open")
    assert run_guard(command, repo).returncode == 0


def test_closing_keywords_in_comments_are_left_alone(repo):
    # Only a merged PR closes: an issue comment saying so changes nothing.
    command = 'gh issue comment 5 --body "This closes mqucifer/crew#12 once it merges"'
    assert run_guard(command, repo).returncode == 0


def test_input_it_cannot_read_is_allowed(repo):
    done = subprocess.run(
        [sys.executable, str(SCRIPT)], input="not json", capture_output=True, text=True
    )
    assert done.returncode == 0


# --- secrets ---------------------------------------------------------------
# Names and values here are made up. Token-shaped values are built at run time,
# so this file holds none for a scanner to flag.


def run_read(file_path: str) -> subprocess.CompletedProcess[str]:
    payload = {"tool_name": "Read", "tool_input": {"file_path": file_path}, "cwd": "."}
    return subprocess.run(
        [sys.executable, str(SCRIPT)], input=json.dumps(payload), capture_output=True, text=True
    )


@pytest.mark.parametrize(
    "command",
    [
        # The two real leaks: 2026-10-18 and 2026-10-23.
        'uv run python experiments/dry_deliver.py --repo x "$(gh auth token)"',
        'grep -rlF -- "$KEYLINE" var/',
        'curl -H "Authorization: Bearer ${LITELLM_API_KEY}" http://localhost:4000/v1/models',
        'gh api -H "Authorization: token `gh auth token`" /user',
        "uv run crew x --key=$api_key",
        'env FAKE_TOKEN="$OTHER_TOKEN" uv run crew doctor',
        'X=$(curl -H "k: $FAKE_API_KEY" localhost:4000)',
        'if true; then curl "$FAKE_TOKEN"; fi',
        "bash -c 'curl -H \"x: $FAKE_API_KEY\" localhost:4000'",
        "eval 'grep \"$KEYLINE\" notes.txt'",
    ],
)
def test_a_secret_in_a_process_s_arguments_is_refused(tmp_path, command):
    done = run_guard(command, tmp_path)
    assert done.returncode == 2
    assert "ps shows it" in done.stderr
    assert "load_env" in done.stderr


@pytest.mark.parametrize(
    "command",
    [
        'echo "$GH_TOKEN"',
        "printf '%s\\n' $FAKE_PASSWORD",
        'echo "$(gh auth token)"',
        "gh auth token",
        "gh auth status --show-token",
        "printenv",
        "printenv FAKE_SECRET",
        "env | grep LITELLM",
        "cat <<EOF\nkey=$FAKE_API_KEY\nEOF",
        "cat .env",
        "head -3 ~/Documents/git/crew/.env",
        "tail /srv/prod.env",
        "sed -n 1p .env",
        "awk -F= '{print $1}' .env",
        "grep LITELLM .env",
        "grep -n KEY < .env",
        "less .env",
        "more ../crew/.env",
        "rg -L KEY .env",
    ],
)
def test_printing_a_secret_is_refused(tmp_path, command):
    done = run_guard(command, tmp_path)
    assert done.returncode == 2
    assert "transcript" in done.stderr


@pytest.mark.parametrize(
    "literal",
    [
        "ghp_" + "x" * 36,
        "github_pat_" + "a1" * 20,
        "sk-" + "Ab3" * 8,
        "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
    ],
)
def test_a_literal_token_is_refused(tmp_path, literal):
    done = run_guard(f"uv run crew doctor --x '{literal}'", tmp_path)
    assert done.returncode == 2
    assert literal not in done.stderr


@pytest.mark.parametrize(
    "command",
    [
        # Assignments put the secret in the environment, not in argv.
        "export GH_TOKEN=$(gh auth token)",
        "GH_TOKEN=$(gh auth token) gh pr list",
        'FAKE_TOKEN="$OTHER_TOKEN"',
        'local key="$FAKE_API_KEY"',
        # The shell's own tests start no process.
        '[ -n "$FAKE_API_KEY" ] && echo set',
        "[[ -z $FAKE_TOKEN ]] || echo set",
        'test -n "$FAKE_SECRET"',
        ': "${FAKE_TOKEN:?unset}"',
        # Sourcing a file that isn't a .env.
        ". ~/.zshrc",
        "source .venv/bin/activate",
        # Never expanded, or not the value.
        "echo '$FAKE_TOKEN'",
        'echo "\\$FAKE_TOKEN"',
        'echo "${#FAKE_API_KEY}" "${FAKE_API_KEY:+set}"',
        'echo "$MAX_TOKENS"',
        'ls "$FAKE_KEY_PATH" "$CREDENTIALS_DIR"',
        # A pipe into a login reads the token from stdin.
        "gh auth token | docker login ghcr.io -u x --password-stdin",
        # Counting or listing doesn't print the file.
        "grep -c LITELLM .env",
        "grep -rlF FAKE .env",
        "grep -q KEY .env && echo ok",
        "rg -l KEY .env",
        "sed -i '' 's/a/b/' .env",
        "cat .env.example",
        "cat .env.local",
        "env FAKE=1 uv run crew doctor",
        "printenv HOME",
        # Can't parse it: a guard, not a gate.
        'echo "unclosed',
    ],
)
def test_allowed_forms_pass(tmp_path, command):
    done = run_guard(command, tmp_path)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize(
    "command",
    [
        # A heredoc body is the process's stdin. ps doesn't show it, and the
        # command record holds the literal $FAKE_API_KEY, not its value.
        'python3 - <<EOF\nimport os\nkey = "$FAKE_API_KEY"\nEOF',
        "python3 - <<'PY'\nimport os\nprint(os.environ['FAKE_KEY'] in open('a').read())\nPY",
        "cat > cfg.ini <<EOF\nkey=$FAKE_API_KEY\nEOF",
        "cat <<'EOF' > body.md\nreads $GH_TOKEN from the environment\nEOF",
        'uv run python - <<< "$FAKE_TOKEN"',
    ],
)
def test_a_heredoc_body_is_not_arguments(tmp_path, command):
    done = run_guard(command, tmp_path)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize(
    "command",
    [
        # What the tick operator runs every day.
        'grep -rn "llm.finished" var/events/',
        "grep -rh tokens var/events/*.jsonl | tail -5",
        "uv run crew doctor",
        "uv run crew tick --passes 1",
        "gh pr list --repo mqucifer/crew",
        "gh api repos/mqucifer/crew/pulls --jq '.[].title'",
        'gh pr create --draft --assignee mquarters --title "fix: x" --body-file body.md',
        'pgrep -f "crew tick"',
        'for f in var/events/*.jsonl; do wc -l "$f"; done',
        "curl -s localhost:4000/health/liveliness",
        "docker ps --format '{{.Names}}'",
        "uv run pytest -q && uv run ruff check .",
    ],
)
def test_normal_crew_commands_pass(tmp_path, command):
    (tmp_path / "body.md").write_text("Follows mqucifer/crew#458\n")
    done = run_guard(command, tmp_path)
    assert done.returncode == 0, done.stderr


def test_the_read_tool_on_a_env_file_is_refused():
    done = run_read("/Users/x/Documents/git/crew/.env")
    assert done.returncode == 2
    assert "Read tool" in done.stderr


@pytest.mark.parametrize("path", ["/x/.env.example", "/x/.env.local", "/x/crew/llm.py"])
def test_the_read_tool_on_other_files_is_allowed(path):
    assert run_read(path).returncode == 0


@pytest.mark.parametrize(
    "command",
    [
        "set -a; . ~/Documents/git/crew/.env; set +a",
        "source .env && uv run crew doctor",
        "(set -a; . /abs/path/.env; set +a; uv run python x.py)",
        "if true; then source ./.env; fi",
    ],
)
def test_sourcing_a_env_file_is_refused(tmp_path, command):
    done = run_guard(command, tmp_path)
    assert done.returncode == 2
    assert "runs its values as commands" in done.stderr
    assert "load_env" in done.stderr and "Never source a .env" in done.stderr


def test_the_route_the_guard_names_reads_a_value_the_shell_would_print(tmp_path):
    """crew#458, 2026-10-24: `GRAFANA_OTLP_AUTH=Basic glc_...` is valid dotenv. The shell
    sets `GRAFANA_OTLP_AUTH=Basic` for one command, `glc_...`, and its error prints it."""
    import sys

    env = tmp_path / ".env"
    env.write_text("FAKE_OTLP_AUTH=Basic fake_glc_value\n")
    sourced = subprocess.run(
        ["sh", "-c", f"set -a; . {env}; set +a"], capture_output=True, text=True
    )
    assert "fake_glc_value" in sourced.stdout + sourced.stderr, "the leak, as it happened"
    assert run_guard(f"set -a; . {env}; set +a", tmp_path).returncode == 2
    read = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from crew_org.config import load_env; "
            "v = load_env(__import__('pathlib').Path(sys.argv[1])); "
            "assert v['FAKE_OTLP_AUTH'] == 'Basic fake_glc_value'",
            str(env),
        ],
        capture_output=True,
        text=True,
    )
    assert read.returncode == 0, read.stderr
    assert "fake_glc_value" not in read.stdout + read.stderr, "parsed, never printed"


# --- running services (crew#997) --------------------------------------------
# Every command here is only read by the guard. None of them ever runs.

REACHING_A_SERVICE = [
    # What the builder ran, as recorded on mqucifer/crew#987.
    "docker exec litellm-temp-db pg_dump -U sprint_metrics -n sprint_metrics > dump.sql",
    "docker exec litellm-temp-db psql -U sprint_metrics -d sprint_metrics -At -c "
    "'select count(*) from board_events'",
    "docker exec litellm-temp-db psql -U postgres -l > /dev/null 2>&1",
    "docker inspect crew-sprint-metrics --format '{{range .Config.Env}}{{println .}}{{end}}'",
    "docker exec crew-sprint-metrics printenv SPRINT_METRICS_DB",
    # docker exec, however it's spelled.
    "docker exec -it crew-litellm sh",
    "docker exec litellm ls",
    "docker container exec crew-otelcol ls",
    "docker --context default exec crew-sprint-metrics ls",
    "docker exec -u root -e X=1 -w /tmp crew-sprint-metrics ls",
    "docker exec --user=root crew-sprint-metrics ls",
    # docker compose exec, by service name or by the project it's in.
    "docker compose exec litellm-temp-db psql -U postgres -l",
    "docker-compose exec crew-telemetry ls",
    "docker compose -p litellm exec litellm ls",
    "docker compose -p sprint-metrics exec sprint-metrics ls",
    "docker compose -f ~/git/infra/compose/mac/sprint-metrics/compose.yaml exec sprint-metrics ls",
    "cd ~/git/infra/compose/mac/sprint-metrics && docker compose exec sprint-metrics ls",
    # docker cp, either way.
    "docker cp litellm-temp-db:/var/lib/postgresql/data/pg_hba.conf .",
    "docker cp ./dump.sql litellm-temp-db:/tmp/dump.sql",
    "docker container cp crew-litellm:/app/config.yaml -",
    "docker compose cp litellm-temp-db:/tmp/x .",
    # docker inspect, whole or by a format that reaches past the safe fields.
    "docker inspect litellm-temp-db",
    "docker inspect crew-sprint-metrics",
    "docker container inspect crew-litellm",
    "docker inspect --type container litellm-temp-db",
    "docker inspect pg-997 crew-litellm",
    "docker inspect /crew-litellm",
    "docker inspect -f '{{.Config.Cmd}}' crew-sprint-metrics",
    "docker inspect -f 'x' crew-sprint-metrics",
    "docker inspect crew-sprint-metrics --format '{{.Config.Env}}'",
    "docker inspect crew-sprint-metrics --format '{{json .Config}}'",
    "docker inspect crew-sprint-metrics --format '{{json .}}'",
    "docker inspect --format='{{.Config.Env}}' crew-sprint-metrics",
    "docker inspect -f '{{$c := .Config}}{{$c.Env}}' crew-sprint-metrics",
    "docker inspect -f '{{index . \"Config\"}}' crew-sprint-metrics",
    # Wrapped, substituted or named indirectly.
    "timeout 30 docker exec litellm-temp-db psql -l",
    "sudo docker inspect crew-litellm",
    "env FOO=1 docker exec crew-litellm ls",
    "bash -c 'docker exec litellm-temp-db psql -l'",
    'sh -c "docker inspect crew-sprint-metrics"',
    "eval docker exec crew-litellm ls",
    "ssh mac 'docker exec litellm-temp-db psql -l'",
    "ssh -o BatchMode=yes mac docker exec litellm-temp-db psql -l",
    "X=$(docker inspect crew-sprint-metrics)",
    "echo $(docker exec crew-sprint-metrics ls)",
    'C=litellm-temp-db; docker exec "$C" psql -l',
    "docker exec $(docker ps -qf name=litellm-temp-db) psql -l",
    "docker ps -q --filter name=crew-litellm | xargs docker inspect",
    "docker ps -q --filter name=crew-litellm | xargs -I{} docker exec {} ls",
    "for c in crew-litellm litellm-temp-db; do docker exec $c ls; done",
]

REACHING_THE_SHARED_POSTGRES = [
    "docker run --rm --network litellm_default postgres:16-alpine "
    "psql -h litellm-temp-db -U sprint_metrics",
    "docker run --rm --network container:litellm-temp-db postgres:16-alpine psql -U postgres -l",
    "docker run --rm --network litellm_default postgres:16-alpine sh -c 'pg_dump -h $H x'",
    "docker exec pg-997 psql -h litellm-temp-db -U postgres -l",
    "docker exec pg-997 sh -c 'pg_dump -h litellm-temp-db sprint_metrics'",
    "psql postgresql://sprint_metrics@litellm-temp-db:5432/sprint_metrics",
    "PGHOST=litellm-temp-db psql -l",
    "pg_restore -h litellm-temp-db -d sprint_metrics dump.pgc",
    "pg_dumpall -h litellm-temp-db",
    'H=litellm-temp-db; psql -h "$H" -l',
]

READING_AN_ENVIRONMENT = [
    "docker exec crew-sprint-metrics env",
    "docker exec open-webui env",
    "docker exec open-webui printenv WEBUI_SECRET_KEY",
    "docker exec pg-997 sh -c 'env | grep DB'",
    "docker exec open-webui cat /proc/1/environ",
    "docker compose exec db printenv",
    "docker inspect open-webui --format '{{.Config.Env}}'",
    "cat /proc/1234/environ",
    "tr '\\0' '\\n' < /proc/1234/environ",
    "cat /proc/$(pgrep -f litellm)/environ",
    "strings /proc/self/environ",
    "ps eww -p 1234",
    "ps auxe",
    "ps -E -p 1234",
]


@pytest.mark.parametrize(
    "command", REACHING_A_SERVICE + REACHING_THE_SHARED_POSTGRES + READING_AN_ENVIRONMENT
)
def test_reaching_a_running_service_is_refused_and_says_what_to_do(tmp_path, command):
    done = run_guard(command, tmp_path)
    assert done.returncode == 2, command
    assert "HTTP API" in done.stderr
    assert "file the operator provides" in done.stderr
    assert "`!`" in done.stderr


# The commands in mqucifer/infra's runbooks/, as written there, that must still pass.
INFRA_RUNBOOKS = [
    "docker ps --format '{{.Names}}\\t{{.Status}}'",
    "docker ps -a --filter name=crew-litellm",
    "docker ps --filter name=litellm-temp-db",
    "docker ps --filter name=crew-telemetry --filter name=crew-otelcol",
    "docker logs --tail 50 crew-litellm",
    "docker logs --since 10m crew-litellm",
    "docker logs --since 5m crew-otelcol",
    "docker logs --since 1h crew-sprint-metrics",
    "docker compose --env-file ~/.config/infra/secrets/litellm.env up -d",
    "docker compose --env-file ~/.config/infra/secrets/open-webui.env up -d",
    "docker compose stop",
    "docker compose restart",
    "docker compose -f compose.yaml restart dcgm-exporter",
    "docker compose -f compose.yaml up -d",
    "docker compose -p spark-sglang -f - up -d",
    "docker compose -p spark-candidate -f - config -q",
    "cd ~/sglang-image && docker compose -p spark-image -f compose.yaml up -d",
    "docker inspect crew-litellm --format "
    "'{{index .Config.Labels \"com.docker.compose.project.working_dir\"}}'",
    "docker inspect crew-sprint-metrics --format '{{.Config.Image}}'",
    "docker inspect open-webui --format "
    "'{{index .Config.Labels \"com.docker.compose.project.working_dir\"}}'",
    "docker inspect open-webui --format '{{.HostConfig.RestartPolicy.Name}}'",
    "docker inspect qwen3.8-27b-sglang --format '{{.Config.Image}}'",
    "docker inspect qwen3.8-27b-sglang --format '{{join .Args \" \"}}' "
    "| tr ' ' '\\n' | grep -A1 speculative",
    "docker inspect -f '{{.State.StartedAt}}' dcgm-exporter",
    "docker inspect qwen3.8-27b-sglang",
    "docker image inspect -f '{{.Id}} {{.Architecture}}' tensorfold-spark:1.1.6",
    "{ printf 'WEBUI_SECRET_KEY='; docker cp open-webui:/app/backend/.webui_secret_key - "
    "| tar -xOf -; } >> settings.env",
    "docker rm open-webui",
    "docker rm -f image-trial",
    "docker start qwen3.8-27b-sglang",
    "docker stop spark-image",
    "docker volume ls --filter name=open-webui --format '{{.Name}}'",
    "docker run --rm --network none --pull never tensorfold-spark:1.1.6 capabilities --json",
    'ssh "$SPARK_ADDR" \'docker logs spark-candidate 2>&1 | grep -i "at once"\'',
    "ssh \"$SPARK_ADDR\" 'cd ~/node-exporter && docker compose -f compose.yaml up -d'",
    "ssh \"$SPARK_ADDR\" 'docker build -t tensorfold-spark:1.1.6 -' < Dockerfile",
    "python3 -I scripts/context_review.py sections ~/infra-results/context-review/epic-468",
]

A_THROWAWAY_OF_ONES_OWN = [
    "docker run --rm -d --name pg-997 -e POSTGRES_PASSWORD=throwaway -p 55432:5432 "
    "postgres:16-alpine",
    "docker rm -f pg-997",
    "docker exec pg-997 psql -U postgres -l",
    "docker exec pg-997 psql -U sprint_metrics -d sprint_metrics -c 'select 1'",
    "docker exec -i pg-997 pg_restore -U postgres -d scratch < dump.pgc",
    "docker exec pg-997 pg_dump -U postgres scratch > out.sql",
    "docker exec pg-997 ls /tmp",
    "docker cp ./crew-dump.sql pg-997:/tmp/dump.sql",
    "docker cp pg-997:/tmp/out.sql ./out.sql",
    "docker compose exec db psql -U postgres -l",
    "docker compose -p my-trial exec sprint-metrics ls",
    "docker inspect pg-997",
    "docker inspect ghcr.io/berriai/litellm:main-latest",
    # Holds no environment, on a service too: what sessions check day to day.
    "docker inspect crew-litellm --format '{{json .Mounts}} {{json .HostConfig.PortBindings}}'",
    "docker inspect crew-otelcol --format '{{range .Mounts}}{{.Source}} {{.Destination}}{{end}}'",
    "docker inspect crew-litellm --format '{{.Config.Image}} {{json .NetworkSettings.Networks}}'",
    "docker inspect crew-sprint-metrics --format '{{.State.Status}} {{.RestartCount}}'",
    "psql -h 127.0.0.1 -p 55432 -U postgres -d scratch -c 'select 1'",
    "PGPASSWORD=throwaway psql -h 127.0.0.1 -p 55432 -U postgres -l",
    "pg_dump postgresql://postgres@127.0.0.1:55432/scratch",
    "pg_restore -h 127.0.0.1 -p 55432 -U postgres -d scratch dump.pgc",
    'pg=pg-997; docker exec "$pg" psql -U postgres -l',
    "docker ps -q --filter name=pg-997 | xargs -I{} docker exec {} ls",
]

ONLY_TALKING_ABOUT_IT = [
    "git commit -m 'refuse docker exec litellm-temp-db psql'",
    "grep -n 'docker exec litellm-temp-db' docs/x.md",
    "rg 'psql -h litellm-temp-db' docs",
    'echo "docker inspect crew-litellm and /proc/1/environ"',
    "cat > notes.md <<'EOF'\ndocker exec litellm-temp-db psql -l\nEOF",
    "curl -s http://127.0.0.1:8080/health",
    "uv run pytest -q",
    "uv run python -m pytest tests/test_claude_pretool_guard.py",
    "ps aux",
    "ps -ef",
    "ps -p 1234 -o user",
    "printenv PATH",
    "env FOO=1 uv run pytest -q",
    "docker build -t crew-sandbox .",
]


@pytest.mark.parametrize(
    "command", INFRA_RUNBOOKS + A_THROWAWAY_OF_ONES_OWN + ONLY_TALKING_ABOUT_IT
)
def test_the_work_the_guard_must_not_get_in_the_way_of(tmp_path, command):
    done = run_guard(command, tmp_path)
    assert done.returncode == 0, f"{command}\n{done.stderr}"


def test_a_pr_body_describing_the_refused_commands_is_allowed(tmp_path):
    (tmp_path / "body.md").write_text("Refuses `docker exec litellm-temp-db psql -l`.\n")
    command = "gh pr create --title x --body-file body.md"
    assert run_guard(command, tmp_path).returncode == 0
    inline = "gh pr create --body \"$(cat <<'EOF'\ndocker inspect crew-litellm\nEOF\n)\""
    assert run_guard(inline, tmp_path).returncode == 0


# A script written and then run: the guard reads the ones it can name.

PYTHON_LIST = (
    "import subprocess\n"
    'subprocess.run(["docker", "inspect", "crew-sprint-metrics"], capture_output=True)\n'
)
PYTHON_LIST_LINES = (
    "import subprocess\n"
    "subprocess.run(\n"
    "    [\n"
    '        "docker",\n'
    '        "exec",\n'
    '        "litellm-temp-db",\n'
    '        "pg_dump",\n'
    "    ],\n"
    ")\n"
)
PYTHON_STRING = 'import os\nos.system("docker exec -i litellm-temp-db psql -l")\n'
PYTHON_PROC = 'print(open("/proc/1234/environ").read())\n'
SHELL_SCRIPT = "#!/bin/sh\nset -e\ndocker exec litellm-temp-db psql -U postgres -l\n"
SHELL_CLIENT = "#!/bin/sh\npsql -h litellm-temp-db -l\n"


@pytest.mark.parametrize(
    ("name", "text", "command"),
    [
        ("x.py", PYTHON_LIST, "python3 x.py"),
        ("x.py", PYTHON_LIST, "uv run python x.py"),
        ("x.py", PYTHON_LIST, "python3 -I x.py --flag"),
        ("x.py", PYTHON_LIST_LINES, "python3 x.py"),
        ("x.py", PYTHON_STRING, "cd sub/.. && python3 x.py"),
        ("x.py", PYTHON_PROC, "python x.py"),
        ("x.sh", SHELL_SCRIPT, "bash x.sh"),
        ("x.sh", SHELL_SCRIPT, "./x.sh"),
        ("x.sh", SHELL_CLIENT, "sh ./x.sh"),
    ],
)
def test_a_script_file_that_reaches_a_service_is_refused(tmp_path, name, text, command):
    (tmp_path / "sub").mkdir()
    (tmp_path / name).write_text(text)
    done = run_guard(command, tmp_path)
    assert done.returncode == 2, command
    assert "HTTP API" in done.stderr


@pytest.mark.parametrize(
    "command",
    [
        f"python3 -c '{PYTHON_LIST}'",
        f"python3 - <<'EOF'\n{PYTHON_LIST}EOF",
        f"cat > x.py <<'EOF'\n{PYTHON_LIST}EOF\npython3 x.py",
        f"bash <<'EOF'\n{SHELL_SCRIPT}EOF",
        f"cat > x.sh <<'EOF'\n{SHELL_SCRIPT}EOF\nbash x.sh",
    ],
)
def test_a_script_written_in_the_same_command_is_read_too(tmp_path, command):
    assert run_guard(command, tmp_path).returncode == 2, command


@pytest.mark.parametrize(
    ("name", "text", "command"),
    [
        # Its own container, and the commands the work needs.
        ("x.py", 'import os\nos.system("docker exec pg-997 psql -l")\n', "python3 x.py"),
        ("x.py", 'import os\nos.system("docker logs crew-litellm")\n', "python3 x.py"),
        ("x.sh", "docker ps\ndocker logs crew-litellm\n", "bash x.sh"),
        # A word about the service in a comment or a message isn't a reach.
        ("x.py", 'print("reads crew-litellm over HTTP")\n', "python3 x.py"),
        ("x.py", 'NOTE = "docker ps shows crew-litellm"\n', "python3 x.py"),
        # It holds the command as data, and has no way to shell out: a script that
        # edits a test or a doc, such as this file's own.
        ("x.py", 'TEXT = "docker inspect crew-litellm"\nprint(TEXT)\n', "python3 x.py"),
        ("x.py", "# docker exec litellm-temp-db psql -l\nimport subprocess\n", "python3 x.py"),
        # What the guard can't do: the name is held in a variable, as in infra's
        # scripts/context_review.py. The PR says so; the rule still holds it.
        (
            "x.py",
            'C = "litellm-temp-db"\nsubprocess.run(["docker", "exec", C, "psql"])\n',
            "python3 x.py",
        ),
    ],
)
def test_a_script_file_that_does_not_name_a_reach_is_allowed(tmp_path, name, text, command):
    (tmp_path / name).write_text(text)
    done = run_guard(command, tmp_path)
    assert done.returncode == 0, done.stderr


def test_a_script_the_guard_cannot_find_is_allowed(tmp_path):
    assert run_guard("python3 not-here.py", tmp_path).returncode == 0
