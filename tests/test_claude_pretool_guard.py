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
