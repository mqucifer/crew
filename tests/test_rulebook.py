"""Every rule a role meets is declared once, and the code and the catalog agree (crew#756).

The catalog says what was decided about each rule; the code says where it's
checked and told. A rule added in the code without a declaration, or a
declaration the code no longer has, fails here, and so does either policy broken.
"""

from __future__ import annotations

from crew_org import rule_list, rulebook


def declared() -> dict[str, rulebook.RuleSpec]:
    return rulebook.catalog()


def test_every_rule_in_the_code_is_declared():
    """Run `crew rules --declare` to propose its declaration, then decide it."""
    missing = sorted(e.id for e in rule_list.entries() if e.id not in declared())
    assert not missing, missing


def test_every_declaration_is_a_rule_the_code_has():
    """A rule taken out of the code goes from the catalog in the same change."""
    found = {e.id for e in rule_list.entries()}
    stale = sorted(rule for rule in declared() if rule not in found)
    assert not stale, stale


def test_each_category_is_one_file_of_the_eight():
    for spec in declared().values():
        assert spec.category in {"", *rulebook.CATEGORIES}


def test_a_told_rule_is_checked_or_labelled_guidance():
    """The first policy: told and checked by nothing named is guidance, or it's cut."""
    unlabelled = [
        e.id
        for e in rule_list.entries()
        if e.group == "told"
        and not any(not c.startswith("maybe ") for c in e.checked)
        and not (e.declared and e.declared.guidance)
    ]
    assert not unlabelled, unlabelled


def test_only_a_line_a_role_is_told_is_guidance():
    checks = [e.id for e in rule_list.entries() if e.group != "told" and e.declared.guidance]
    assert not checks, "a rule the code checks isn't guidance"


def test_each_role_keeps_within_its_guidance_budget():
    budget = rulebook.budget()
    over = {
        role: (n, budget.get(role, 0))
        for role, n in rule_list.guidance_counts().items()
        if n > budget.get(role, 0)
    }
    assert not over, f"guidance over budget (told, budget): {over}"


def test_a_migrated_categorys_checks_are_told_in_their_form():
    """The second policy, for each category moved to `refuse()`. The call layer's
    checks are exempt: the model doesn't choose an empty answer or a cut field. So is
    a repair that never refuses: there's no refusal for its telling to come from."""
    untold = [
        e.id
        for e in rule_list.entries()
        if e.category in rulebook.MIGRATED
        and e.group in ("form", "check")
        and not e.repairs
        and not e.declared.exempt
        and not (e.declared.description and e.declared.told_in)
    ]
    assert not untold, untold


def test_a_form_is_told_its_migrated_rules_by_field(monkeypatch):
    specs = [
        rulebook.RuleSpec("a", "answer_shape", description="Named by its ID.", told_in="Row.id"),
        rulebook.RuleSpec("b", "answer_shape", description="One or the other.", told_in="Row"),
        rulebook.RuleSpec("c", "scope", description="Not migrated.", told_in="Row.id"),
    ]
    monkeypatch.setattr(rulebook, "catalog", lambda: {s.id: s for s in specs})
    monkeypatch.setattr(rulebook, "MIGRATED", frozenset({"answer_shape"}))
    assert rulebook.told("Row") == {"id": ["Named by its ID."], "": ["One or the other."]}


def test_declaring_again_keeps_every_decision(tmp_path, monkeypatch):
    """`crew rules --declare` once rewrote the catalog from the code alone, and lost the
    messages declared that day: what's declared is read before anything is rewritten."""
    for path in rulebook.CATALOG.glob("*.yaml"):
        (tmp_path / path.name).write_text(path.read_text("utf-8"), encoding="utf-8")
    monkeypatch.setattr(rulebook, "CATALOG", tmp_path)
    monkeypatch.setattr(rulebook, "BUDGET", tmp_path / "budget.yaml")
    rulebook.catalog.cache_clear()
    before = rulebook.catalog(tmp_path)
    rule_list.write_declarations()
    rulebook.catalog.cache_clear()
    assert rulebook.catalog(tmp_path) == before
    rulebook.catalog.cache_clear()


def test_a_migrated_rule_refuses_by_its_own_declaration():
    """A migrated check's code names its rule, so its message is the declared one."""
    loose = [
        e.id
        for e in rule_list.entries()
        if e.category in rulebook.MIGRATED
        and e.group in ("form", "check", "call")
        and not e.repairs
        and not (e.declared and e.declared.exempt)
        and e.id != "call.schema"
        and e.id not in e.named
    ]
    assert not loose, loose


def test_every_refusal_names_a_declared_message():
    import ast

    wrong = []
    for source in rule_list.sources():
        for node in ast.walk(source.tree):
            rule = rule_list._refused_by(node)
            if rule is None:
                continue
            assert isinstance(node, ast.Call)
            which = node.args[1] if len(node.args) > 1 else None
            name = which.value if isinstance(which, ast.Constant) else "default"
            spec = declared().get(rule)
            if spec is None or name not in dict(spec.messages):
                wrong.append(f"{source.path}:{node.lineno} {rule} {name}")
    assert not wrong, wrong


def test_a_form_tells_the_rules_that_check_it():
    """The second policy, as the role reads it: the form shows each rule's description."""
    from crew_org import calls
    from crew_org.crews.refinement_crew import ProductAnswer

    shown = calls.form_text(ProductAnswer.model_json_schema())
    product = declared()["form.ProductAnswer.one_or_the_other"]
    row_id = declared()["form.AnswerRow.an_id"]
    assert f"**ProductAnswer**: {product.description}" in shown
    replaces = next(line for line in shown.splitlines() if line.startswith("- `replaces`"))
    assert replaces.endswith(row_id.description)


def test_each_case_of_a_merged_rule_is_one_the_code_has():
    """crew#756: a case is a merged rule's name in the code; one the code lost goes."""
    found = {case for e in rule_list.entries() for case in e.cases}
    for spec in declared().values():
        assert not set(spec.cases) - found, (spec.id, sorted(set(spec.cases) - found))
