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

And a third, for the five ``SK-*`` rules: :data:`PIPELINE_ONLY`. The corpus
mutates a *document*, and a skeleton rule's subject is the server-side fill
state a ``skeleton_id`` names - there is no document to patch, so no mutation
can reach one. Each entry names the test that covers the rule, in the same
shape and for the same reason as R-31's: an exemption is only worth what the
test it names is worth.

Scope is a parameter, not a constant (ruling R-12). At M2 the registry held
``BP-*`` and ``DS-*``; M5 widened :data:`IMPLEMENTED_PREFIXES` to include
``SK-*`` and inverted the test that pinned their absence. ``RT-E*`` never
enters: those are `fetch_step` response codes, and section 3.4 is not part of
the registry.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

from agentprops.models import RUNTIME_ERROR_CODES, RUNTIME_WARNING_CODES
from agentprops.service.blueprints import WARNING_BLUEPRINT_VERSION_MISSING
from agentprops.service.expansion import WARNING_EXPANSION_ADDED_NOTHING
from agentprops.service.runs import (
    WARNING_DATASET_SELECTION_AMBIGUOUS,
    WARNING_RUN_ALREADY_FINISHED,
    WARNING_RUN_START_MISMATCH,
)
from agentprops.validation import (
    FILL_RULES,
    RULE_REGISTRY,
    SUBMIT_RULES,
    TARGET_DATASET,
    TARGET_SKELETON,
    TARGETS,
)
from agentprops.validation.context import WARNING_RULES
from catalogue import parse_catalogue_ids, parse_runtime_codes, parse_runtime_warnings
from corpus import CASES, COVERED_RULE_IDS, RAW_TEXT_CASES

#: Resolved from this module, never from the CWD (ruling R-22): as written in
#: `worked-example.md` the snippet reads `parse_catalogue_ids("contracts.md")`,
#: which resolves to a nonexistent `./contracts.md` and would kill the one test
#: that prevents drift with a FileNotFoundError instead of comparing.
CATALOGUE_PATH: Final[Path] = Path(__file__).parents[2] / "docs" / "contracts.md"

#: The prefixes the registry implements. M2 had ``{"BP", "DS"}``; M5 added
#: ``"SK"`` with the skeleton pipeline, which is the widening ruling R-12
#: describes and the reason the scope is a parameter at all.
IMPLEMENTED_PREFIXES: Final[frozenset[str]] = frozenset({"BP", "DS", "SK"})

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

#: The rules the mutation corpus cannot reach *by construction*, each naming
#: the test that covers it instead. The five ``SK-*`` rules are all of them.
#:
#: This is not the same exemption as :data:`MUTATION_UNREACHABLE`, which is for
#: a rule whose violation cannot survive being parsed, and whose coverage
#: therefore comes from a raw-text case. A skeleton rule has no document at
#: all: its subject is the ``parts``/``manifest``/``submitted_as`` state of a
#: skeleton row, so `broken/manifest.json` - a list of JSON Patches against two
#: golden *documents* - has nothing to patch. Extending the corpus to carry
#: skeleton fixtures would mean a second, differently-shaped manifest for five
#: rules, and the state those rules read is exactly what the service builds
#: anyway.
#:
#: Keyed by rule id, valued by the name of the covering test, so deleting that
#: test fails :func:`test_every_pipeline_exemption_names_a_test_that_exists`
#: rather than silently dropping the only coverage the rule has. Ruling R-12
#: asks for "corpus coverage or a **named** exemption"; this is the named half.
PIPELINE_ONLY: Final[dict[str, str]] = {
    "SK-001": "test_sk_001_rejects_a_section_that_is_not_in_the_manifest",
    "SK-002": "test_sk_002_rejects_a_section_filled_before_an_earlier_one",
    "SK-003": "test_sk_003_rejects_a_node_section_referencing_an_undeclared_entity",
    "SK-004": "test_sk_004_reports_every_unfilled_required_section",
    "SK-005": "test_sk_005_rejects_an_already_submitted_skeleton",
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

#: Where the covering tests named by :data:`RULE_HALF_EXEMPTIONS` and
#: :data:`PIPELINE_ONLY` are looked for.
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
    uncovered = catalogue - COVERED_RULE_IDS - set(MUTATION_UNREACHABLE) - set(PIPELINE_ONLY)
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


def test_the_runtime_codes_match_the_documented_table() -> None:
    """M6's drift guard, in the shape M4 put on the ``AP-*`` codes.

    ``RT-*`` is a *closed* set - four codes, tabulated in section 3.4 and
    exported from `models/errors.py` - so equality is the right assertion, and
    it is what pins ruling R-03's deletion of RT-E05 from **both** sides at
    once: a re-added constant fails here, and so does a re-added table row.
    """
    assert parse_runtime_codes(CATALOGUE_PATH) == RUNTIME_ERROR_CODES, (
        f"documented but not defined: "
        f"{sorted(parse_runtime_codes(CATALOGUE_PATH) - RUNTIME_ERROR_CODES)}; "
        f"defined but not documented: "
        f"{sorted(RUNTIME_ERROR_CODES - parse_runtime_codes(CATALOGUE_PATH))}"
    )
    assert "RT-E05" not in RUNTIME_ERROR_CODES


def test_the_warning_vocabulary_is_the_documented_three_plus_named_additions() -> None:
    """Ruling R-22, both halves - and they pull in opposite directions.

    The *vocabulary* is open: ``Warning.code`` is a plain string so a tool may
    add a code without a model change. The *table* is closed: contracts 3.4
    tabulates three codes and :data:`RUNTIME_WARNING_CODES` must be exactly
    those, so a fourth cannot be smuggled into the shared constant.

    An addition therefore lives in the module that attaches it, and the three
    there are asserted by name so this test says what the arrangement is rather
    than only what it forbids: ``blueprint_version_missing`` on
    ``blueprint_diff`` (M4), and ``dataset_selection_ambiguous`` and
    ``run_start_mismatch`` on ``run_start`` (M6, rulings R-54(a) and R-53).
    ``expansion_added_nothing`` is M7's, on ``dataset_expand``: a ``count`` of
    zero is a well-formed request that adds nothing, and the service does not
    refuse well-formed requests. M8's **one** is ``run_already_finished``
    (ruling R-54(c), ratified by R-67(a)), on the read path: a finished run
    still serves and says so.

    M8 shipped two more and lost both, which is worth knowing so neither is
    reinvented. ``step_actual_conflict`` and ``run_finish_mismatch`` became
    ``AP-007`` on an ``ok: false`` envelope, because a re-write whose value
    *differs* did not happen (R-65). And ``step_actual_already_recorded``,
    added for the *identical* half when R-65's first draft asked for "a no-op
    success with a warning", went when that was amended: a retry is the
    expected outcome, and a vocabulary that fires on expected outcomes trains
    callers to ignore it. PRD 5.4's ``unresolved_step`` is **not** among
    them: R-22 records that it was superseded by RT-E01/RT-E02 rather than
    dropped by accident.

    And each addition has to be *documented*, which the table guard cannot see:
    a code attached by a tool and described nowhere is a code the next
    milestone invents a second time.
    """
    assert parse_runtime_warnings(CATALOGUE_PATH) == RUNTIME_WARNING_CODES
    additions = {
        WARNING_BLUEPRINT_VERSION_MISSING,
        WARNING_DATASET_SELECTION_AMBIGUOUS,
        WARNING_EXPANSION_ADDED_NOTHING,
        WARNING_RUN_ALREADY_FINISHED,
        WARNING_RUN_START_MISMATCH,
    }
    assert additions & RUNTIME_WARNING_CODES == set(), (
        "a tool-local warning code must not also be in the shared constant"
    )
    assert "unresolved_step" not in RUNTIME_WARNING_CODES | additions

    prose = CATALOGUE_PATH.read_text(encoding="utf-8")
    undocumented = {code for code in additions if f"`{code}`" not in prose}
    assert not undocumented, (
        f"these warning codes are attached by a tool and documented nowhere in "
        f"docs/contracts.md: {sorted(undocumented)}"
    )


def test_the_skeleton_rules_are_documented_and_implemented() -> None:
    """M5's widening, and the inversion of the test that pinned its absence.

    Until M5 this test asserted the opposite - that ``SK-*`` was documented and
    *not* registered - so that widening ``IMPLEMENTED_PREFIXES`` had to be a
    deliberate edit rather than something a new registry entry did quietly. The
    five rules are now implemented, so the assertion is inverted rather than
    deleted: the count is what a sixth skeleton rule would have to change.
    """
    documented = parse_catalogue_ids(CATALOGUE_PATH)
    skeleton = {rule for rule in documented if rule.startswith("SK-")}
    assert skeleton == {"SK-001", "SK-002", "SK-003", "SK-004", "SK-005"}
    assert skeleton <= set(RULE_REGISTRY), "SK-* is M5's, and M5 has landed"


def test_every_skeleton_rule_runs_in_exactly_one_phase() -> None:
    """A rule the runners never call is a rule that cannot fire.

    ``SK-001`` to ``SK-003`` read a section name and its content, which a submit
    does not have; ``SK-004`` only means anything at submit; ``SK-005`` guards
    both. So the two lists overlap on SK-005 by design, and their **union** has
    to be the whole skeleton registry - otherwise a sixth rule could be added,
    documented, registered, and never run.
    """
    registered = {rule for rule in RULE_REGISTRY if rule.startswith("SK-")}
    assert set(FILL_RULES) | set(SUBMIT_RULES) == registered
    assert set(FILL_RULES) & set(SUBMIT_RULES) == {"SK-005"}
    for rule in FILL_RULES + SUBMIT_RULES:
        assert RULE_REGISTRY[rule].target == TARGET_SKELETON


def test_every_pipeline_exemption_names_a_test_that_exists() -> None:
    """The assertion that gives :data:`PIPELINE_ONLY` teeth.

    Same reasoning as ruling R-31's rule-half exemptions: an exemption whose
    covering test has been renamed or deleted is cover for a gap, and the whole
    point of the coverage gate is that a rule cannot be registered without
    something exercising it.
    """
    defined = module_level_test_names()
    missing = {
        f"{rule} -> {test_name}"
        for rule, test_name in PIPELINE_ONLY.items()
        if test_name not in defined
    }
    assert not missing, (
        f"a pipeline-only exemption names a test that does not exist in {UNIT_TEST_DIR}: "
        f"{sorted(missing)}. Restore the test or drop the exemption - the rule has no other "
        "coverage."
    )


def test_no_pipeline_exemption_is_stale() -> None:
    """A skeleton rule the mutation manifest reaches after all would be news."""
    unknown = set(PIPELINE_ONLY) - set(RULE_REGISTRY)
    assert not unknown, f"PIPELINE_ONLY names unregistered rules: {sorted(unknown)}"
    reached = set(PIPELINE_ONLY) & COVERED_RULE_IDS
    assert not reached, f"these rules are exempted but a corpus case covers them: {sorted(reached)}"
    overlap = set(PIPELINE_ONLY) & set(MUTATION_UNREACHABLE)
    assert not overlap, f"these rules are exempted twice: {sorted(overlap)}"


def test_registry_metadata_is_consistent() -> None:
    """Severity comes from one table, and every rule states its own check."""
    for rule, spec in RULE_REGISTRY.items():
        assert spec.rule == rule
        assert spec.target in TARGETS
        assert spec.severity == ("warning" if rule in WARNING_RULES else "error")
        assert spec.summary, f"{rule} has no docstring summary"
        assert not (spec.target != TARGET_DATASET and spec.needs_blueprint), (
            f"{rule}: needs_blueprint is meaningless outside a dataset rule"
        )


def test_the_registry_is_in_catalogue_order() -> None:
    """Findings are emitted in registry order, so the error list is deterministic."""
    ids = list(RULE_REGISTRY)
    assert ids == sorted(ids)
