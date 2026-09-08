"""The two drift tests: the catalogue, the registry and the corpus must agree.

`docs/worked-example.md` section 6 names both, and they are the reason M2's work
cannot rot quietly:

``test_every_rule_has_an_implementation``
    Every id documented in `docs/contracts.md` sections 3.1-3.3 has an entry in
    ``RULE_REGISTRY``, and every registry entry is documented. Add a rule to the
    document without implementing it, or implement one without documenting it,
    and this fails.

``test_every_rule_has_a_broken_fixture``
    Every registry entry is exercised by at least one corpus case, modulo the
    *named* exemptions in :data:`MUTATION_UNREACHABLE` - and each exemption has
    to be justified by a raw-text case, so an exemption is never a silent gap
    (ruling R-20).

There is a second, narrower exemption shape for a rule *half* that no exact-set
case can isolate: :data:`RULE_HALF_EXEMPTIONS` names the unit test that covers
the half, and a test asserts that test still exists (ruling R-31).

Scope is a parameter, not a constant (ruling R-12). At M2 the registry holds
``BP-*`` and ``DS-*``; ``SK-*`` is documented and unimplemented until M5, which
widens :data:`IMPLEMENTED_PREFIXES` by one entry. ``RT-E*`` never enters: those
are `fetch_step` response codes, and section 3.4 is not part of the registry.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from agentprops.validation import RULE_REGISTRY
from agentprops.validation.context import WARNING_RULES
from catalogue import parse_catalogue_ids, parse_runtime_codes
from corpus import CASES, COVERED_RULE_IDS, RAW_TEXT_CASES

#: Resolved from this module, never from the CWD (ruling R-22): as written in
#: `worked-example.md` the snippet reads `parse_catalogue_ids("contracts.md")`,
#: which resolves to a nonexistent `./contracts.md` and would kill the one test
#: that prevents drift with a FileNotFoundError instead of comparing.
CATALOGUE_PATH: Final[Path] = Path(__file__).parents[2] / "docs" / "contracts.md"

#: The prefixes M2 implements. M5 adds "SK".
IMPLEMENTED_PREFIXES: Final[frozenset[str]] = frozenset({"BP", "DS"})

#: Rules no JSON Patch against a golden fixture can reach, with the reason.
#: Ruling R-20 requires this to be a named exemption rather than a silent gap,
#: and :func:`test_every_exemption_is_covered_by_a_raw_text_case` requires each
#: one to be exercised some other way.
MUTATION_UNREACHABLE: Final[dict[str, str]] = {
    "DS-013": (
        "duplicate object keys collapse at parse time and a dict cannot hold one key twice, "
        "so the violation only exists in the raw text; covered by a raw_text_cases entry and "
        "detected at the tool boundary via json.loads(object_pairs_hook=...)"
    ),
}

#: Rule *halves* that no exact-set corpus case can isolate, each naming the unit
#: test that covers it. Ruling R-31.
#:
#: This is a different shape from :data:`MUTATION_UNREACHABLE`, and it has to
#: be: the rules here **do** have corpus cases, for their other half, so
#: registering one in the whole-rule map would trip
#: :func:`test_no_exemption_is_stale` and the exemption would be a lie. Keyed by
#: ``(rule, half)`` and valued by the *name* of the covering test, so that
#: deleting that test fails :func:`test_every_rule_half_exemption_names_a_test_that_exists`
#: rather than silently dropping the only coverage the half has.
#:
#: BP-006's inbound-edge half is the only entry, and it is unreachable
#: structurally rather than incidentally: any blueprint with an edge into
#: ``entry_node`` must also carry either a cycle (BP-018) or a predecessor
#: unreachable from the entry (BP-005), so a mutation always reports a second
#: id. R-31 therefore asks for a *membership* test, not an exact-set case.
RULE_HALF_EXEMPTIONS: Final[dict[tuple[str, str], str]] = {
    ("BP-006", "inbound-edge"): "test_bp_006_reports_an_inbound_edge_into_the_entry_node",
}

#: Where :data:`RULE_HALF_EXEMPTIONS`' covering tests are looked for.
UNIT_TEST_DIR: Final[Path] = Path(__file__).parent


def module_level_test_names() -> set[str]:
    """Every module-level function defined in `tests/unit/*.py`, by AST.

    Parsed rather than imported: a name is checked against the source, so a
    test that exists but is skipped, renamed or commented out is all visible,
    and no import order matters.
    """
    names: set[str] = set()
    for path in sorted(UNIT_TEST_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names.update(
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        )
    return names


def in_scope(rule: str) -> bool:
    return rule.split("-")[0] in IMPLEMENTED_PREFIXES


def test_every_rule_has_an_implementation() -> None:
    """The prose catalogue and the registry are the same set, within scope."""
    documented = {rule for rule in parse_catalogue_ids(CATALOGUE_PATH) if in_scope(rule)}
    implemented = set(RULE_REGISTRY)
    assert documented == implemented, (
        f"documented but not implemented: {sorted(documented - implemented)}; "
        f"implemented but not documented: {sorted(implemented - documented)}"
    )


def test_every_rule_has_a_broken_fixture() -> None:
    """Every registry id is exercised by a corpus case, modulo named exemptions."""
    catalogue = set(RULE_REGISTRY)
    uncovered = catalogue - COVERED_RULE_IDS - set(MUTATION_UNREACHABLE)
    assert uncovered == set(), f"rules with no broken fixture: {sorted(uncovered)}"


def test_every_exemption_is_covered_by_a_raw_text_case() -> None:
    """An exemption from the mutation manifest is not an exemption from testing."""
    raw_text_covered = {rule for case in RAW_TEXT_CASES for rule in case["expect"]}
    missing = set(MUTATION_UNREACHABLE) - raw_text_covered
    assert not missing, (
        f"these rules are exempted from the mutation manifest but nothing else exercises "
        f"them: {sorted(missing)}"
    )


def test_no_exemption_is_stale() -> None:
    """An exemption for a rule the mutation manifest *does* reach is a lie.

    If someone finds a JSON Patch that violates DS-013 - and the ruling register
    says there is none - the exemption must go, not linger as cover.
    """
    mutation_covered = {rule for case in CASES for rule in case["expect"]}
    stale = mutation_covered & set(MUTATION_UNREACHABLE)
    assert not stale, f"these rules are exempted but reachable by mutation after all: {stale}"


def test_every_rule_half_exemption_names_a_test_that_exists() -> None:
    """Ruling R-31: a rule-half exemption is only as good as the test it names.

    This is the assertion that gives the exemption teeth. Before it, the
    exemption for BP-006's inbound-edge half was prose in `manifest.json` and
    `DECISIONS.md` that protected nothing: R-31 recorded that a unit test
    covered the half, no such test existed, and a version of `bp_006` that
    never reported the inbound case would have passed the whole suite.
    """
    defined = module_level_test_names()
    missing = {
        f"{rule} ({half}) -> {test_name}"
        for (rule, half), test_name in RULE_HALF_EXEMPTIONS.items()
        if test_name not in defined
    }
    assert not missing, (
        f"a rule-half exemption names a test that does not exist in {UNIT_TEST_DIR}: "
        f"{sorted(missing)}. Restore the test or drop the exemption - the half has no other "
        "coverage."
    )


def test_every_rule_half_exemption_names_a_registered_rule() -> None:
    unknown = {rule for rule, _ in RULE_HALF_EXEMPTIONS if rule not in RULE_REGISTRY}
    assert not unknown, f"rule-half exemptions name unregistered rules: {sorted(unknown)}"


def test_a_rule_half_exemption_is_not_a_whole_rule_exemption() -> None:
    """The two exemption maps must stay distinct, or one of them is wrong.

    A rule with a *half* exemption still has a corpus case, for its other half -
    which is exactly why it cannot live in `MUTATION_UNREACHABLE`, where
    `test_no_exemption_is_stale` would reject it.
    """
    for rule, _ in RULE_HALF_EXEMPTIONS:
        assert rule in COVERED_RULE_IDS, (
            f"{rule} has a rule-half exemption but no corpus case at all; it belongs in "
            "MUTATION_UNREACHABLE instead"
        )
        assert rule not in MUTATION_UNREACHABLE, (
            f"{rule} is exempted twice, as a whole rule and as a half"
        )


def test_the_catalogue_parser_reads_the_document() -> None:
    """Guard against a parser that silently finds nothing.

    Equality in the first test would still fail loudly on an empty parse, but it
    would blame the registry. This blames the parser.
    """
    documented = parse_catalogue_ids(CATALOGUE_PATH)
    assert len(documented) >= 50, f"parsed only {len(documented)} rule ids from {CATALOGUE_PATH}"
    assert {"BP-001", "DS-001", "DS-033", "SK-001"} <= documented


def test_the_parser_excludes_the_response_code_table() -> None:
    """Section 3.4 is not part of the registry (ruling R-12), and RT-E05 is gone (R-03)."""
    documented = parse_catalogue_ids(CATALOGUE_PATH)
    assert not any(rule.startswith("RT-") for rule in documented)
    codes = parse_runtime_codes(CATALOGUE_PATH)
    assert {"RT-E01", "RT-E02", "RT-E03", "RT-E04"} <= codes
    assert "RT-E05" not in codes, (
        "ruling R-03 deletes RT-E05: a loop drawing past its pool is a pool_exhausted warning, "
        "never an error, because the service never gates"
    )


def test_skeleton_rules_are_documented_and_not_yet_implemented() -> None:
    """M5's widening, pinned so it is a deliberate change rather than a surprise.

    SK-* is in the catalogue and out of the registry. When M5 implements the
    skeleton pipeline it adds "SK" to IMPLEMENTED_PREFIXES and deletes this
    test, and the equality test above starts covering the five rules.
    """
    documented = parse_catalogue_ids(CATALOGUE_PATH)
    skeleton = {rule for rule in documented if rule.startswith("SK-")}
    assert skeleton == {"SK-001", "SK-002", "SK-003", "SK-004", "SK-005"}
    assert not skeleton & set(RULE_REGISTRY), "SK-* is M5's; M2 must not implement it"


def test_registry_metadata_is_consistent() -> None:
    """Severity comes from one table, and every rule states its own check."""
    for rule, spec in RULE_REGISTRY.items():
        assert spec.rule == rule
        assert spec.target in {"blueprint", "dataset"}
        assert spec.severity == ("warning" if rule in WARNING_RULES else "error")
        assert spec.summary, f"{rule} has no docstring summary"
        assert not (spec.target == "blueprint" and spec.needs_blueprint), (
            f"{rule}: needs_blueprint is meaningless for a blueprint rule"
        )


def test_the_registry_is_in_catalogue_order() -> None:
    """Findings are emitted in registry order, so the error list is deterministic."""
    ids = list(RULE_REGISTRY)
    assert ids == sorted(ids)
