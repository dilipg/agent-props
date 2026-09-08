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

Scope is a parameter, not a constant (ruling R-12). At M2 the registry holds
``BP-*`` and ``DS-*``; ``SK-*`` is documented and unimplemented until M5, which
widens :data:`IMPLEMENTED_PREFIXES` by one entry. ``RT-E*`` never enters: those
are `fetch_step` response codes, and section 3.4 is not part of the registry.
"""

from __future__ import annotations

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
