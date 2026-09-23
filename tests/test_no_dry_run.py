"""There is no dry mode, and nothing should quietly grow one back.

A dry run cost the same inference as a real one — `deliver_story` ran the model
call, the repair loop and the sandboxed test run, and stopped only at the
commit — so it produced nothing that could land. What it did produce was
movement: it claimed a card, did the work, and put the card back, which is
three of the five backward moves in the crew's own log and noise that
`crew capability` had to be taught to filter out of its self-knowledge.

Nine branches served it, plus a separate escalation ledger namespace and a
restore column. Each accommodation was reasonable alone, which is how they
accumulated.

What made landing frightening was having no way to undo it. The answer is a
revert (crew#83), not a rehearsal.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from crew_org.flows import delivery, loop, merge

SRC = Path(__file__).resolve().parent.parent / "src" / "crew_org"

# `crew sprint start --dry-run` stays. It shows what would be admitted and
# costs nothing — a real preview, and a different thing entirely.
ALLOWED = {"cli.py": "crew sprint start --dry-run shows the plan and spends nothing"}


def test_no_delivery_entry_point_takes_a_dry_run():
    for fn in (delivery.deliver, delivery.deliver_story, merge.merge_approved, loop.run):
        params = inspect.signature(fn).parameters
        assert "dry_run" not in params, f"{fn.__qualname__} still takes dry_run"


def test_no_flow_module_mentions_dry_run():
    """The nine branches, and the parameters that fed them."""
    offenders = {}
    for path in sorted(SRC.rglob("*.py")):
        if ALLOWED.get(path.name):
            continue
        if "dry_run" in path.read_text(encoding="utf-8"):
            offenders[str(path.relative_to(SRC))] = "mentions dry_run"
    assert not offenders, f"a dry mode is growing back: {offenders}"


def test_delivery_has_no_restore_column_or_would_land():
    """A run that never puts a card back needs neither."""
    source = (SRC / "flows" / "delivery.py").read_text(encoding="utf-8")
    for gone in ("restore_to", "would_land", "ledger_sprint"):
        assert gone not in source, f"{gone} outlived the dry run"


def test_the_tick_command_has_no_land_flag():
    """`--land` said the default was a rehearsal. There is no default to opt
    out of now."""
    cli = (SRC / "cli.py").read_text(encoding="utf-8")
    assert '"--land"' not in cli


def test_a_real_run_is_always_recorded():
    """`_sink` wrote nowhere on a dry tick, so the mode the crew ran in by
    default was the one mode `crew capability` could not see it in."""
    tree = ast.parse((SRC / "cli.py").read_text(encoding="utf-8"))
    sink = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_sink")
    args = [a.arg for a in sink.args.args]
    assert args == ["demo"], f"_sink is gated on {args}, not on the synthetic demo alone"
