"""The parts of the validator the corpus cannot reach from the golden fixtures.

The corpus proves each rule fires on a document that violates it, and the golden
fixtures prove every rule stays silent on a document that does not. What neither
covers is behaviour the `location-onboarding` blueprint happens not to contain,
and three of those are load-bearing rulings:

- **R-19's dotted `var` paths and `[path, default]` operand form.** Every
  condition in the golden blueprint is a flat top-level ``var``, so the corpus
  cannot tell a correct BP-010 from one that only handles the easy case. Ruling
  R-19 says the first author to write ``{"var": "store.status"}`` is who finds
  out; these tests are that author.
- **R-02's cyclic reachability.** DS-010 hinges on ``after_node`` being an
  ancestor "via at least one path", and the loop is what makes the two readings
  of the catalogue's wording differ. The graph is tested directly.
- **R-20's duplicate-key detection**, which only exists before parsing.

Plus the precedence decisions from R-18 and the ones this milestone had to make
itself: whichever rule *does not* fire is as much a part of the contract as the
one that does, because the gate asserts exact sets.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentprops.validation import validate_blueprint, validate_dataset
from agentprops.validation.blueprint import condition_var_paths
from agentprops.validation.graph import Graph
from agentprops.validation.jsonschemas import (
    canonical,
    declared_properties,
    entity_ref_sites,
    resolve_entity_refs,
    strip_entity_refs,
)
from agentprops.validation.pointers import escape_token, pointer, section_for_pointer
from agentprops.validation.rawjson import parse_with_duplicate_keys
from agentprops.validation.timeline import split_ref
from corpus import DATASET_RESOLVER, GOLDEN_BLUEPRINT, base_document, mutated

ENTITY_SCHEMAS = {entity["id"]: entity["schema"] for entity in GOLDEN_BLUEPRINT["entities"]}


def rules_for_blueprint(document: dict[str, Any]) -> set[str]:
    return {error.rule for error in validate_blueprint(document).errors}


def rules_for_dataset(document: dict[str, Any]) -> set[str]:
    return {error.rule for error in validate_dataset(document, DATASET_RESOLVER).errors}


# --------------------------------------------------------------------------- #
# BP-010, and ruling R-19's condition walk.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ({"==": [{"var": "documents_complete"}, False]}, ["documents_complete"]),
        ({"var": "store.status"}, ["store.status"]),
        ({"var": ["store.status", "pending_documents"]}, ["store.status"]),
        (
            {"and": [{"!": {"var": "a"}}, {"or": [{"var": "b.c"}, {"var": ["d", 1]}]}]},
            ["a", "b.c", "d"],
        ),
        ({"==": [1, 2]}, []),
        ({"var": ""}, [""]),
    ],
)
def test_condition_var_paths_walks_the_whole_tree(condition: Any, expected: list[str]) -> None:
    """Both operand forms, at any depth, without evaluating anything."""
    assert condition_var_paths(condition) == expected


def test_bp_010_accepts_a_dotted_path_that_resolves() -> None:
    """``documents_complete`` has no sub-properties, so use an entity-backed schema.

    ``fetch_store_profile.output_schema.store`` is ``entity:store``, which
    declares ``status`` - so ``store.status`` is a real path and BP-010 must
    accept it. Nothing in the golden blueprint exercises this.
    """
    document = base_document("blueprint")
    document["edges"][1]["condition"] = {"==": [{"var": "store.status"}, "active"]}
    assert "BP-010" not in rules_for_blueprint(document)


def test_bp_010_rejects_a_dotted_path_whose_tail_is_unknown() -> None:
    document = base_document("blueprint")
    document["edges"][1]["condition"] = {"==": [{"var": "store.stauts"}, "active"]}
    assert rules_for_blueprint(document) == {"BP-010"}


def test_bp_010_rejects_a_dotted_path_whose_head_is_unknown() -> None:
    document = base_document("blueprint")
    document["edges"][1]["condition"] = {"==": [{"var": "outlet.status"}, "active"]}
    assert rules_for_blueprint(document) == {"BP-010"}


def test_bp_010_accepts_the_default_operand_form() -> None:
    document = base_document("blueprint")
    document["edges"][2]["condition"] = {"==": [{"var": ["documents_complete", False]}, False]}
    assert "BP-010" not in rules_for_blueprint(document)


def test_bp_010_reports_once_per_bad_path_not_once_per_segment() -> None:
    document = base_document("blueprint")
    document["edges"][1]["condition"] = {"==": [{"var": "store.a.b.c"}, 1]}
    errors = [error for error in validate_blueprint(document).errors if error.rule == "BP-010"]
    assert len(errors) == 1
    assert errors[0].context["missing_segment"] == "a"


# --------------------------------------------------------------------------- #
# The graph, and ruling R-02's direction.
# --------------------------------------------------------------------------- #


def test_reachable_from_excludes_the_start_unless_a_cycle_returns_to_it() -> None:
    graph = Graph(["a", "b", "c"], [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}])
    assert graph.reachable_from("a", include_start=False) == frozenset({"b", "c"})
    assert graph.reachable_from("a", include_start=True) == frozenset({"a", "b", "c"})
    cyclic = Graph(["a", "b"], [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}])
    assert cyclic.reachable_from("a", include_start=False) == frozenset({"a", "b"})


def test_edges_naming_an_unknown_node_contribute_no_adjacency() -> None:
    """BP-004 reports them; letting them into the graph would fake reachability."""
    graph = Graph(["a"], [{"from": "a", "to": "ghost"}, {"from": "ghost", "to": "a"}])
    assert graph.successors("a") == frozenset()
    assert graph.predecessors("a") == frozenset()


def test_cycle_ignoring_finds_a_loop_free_cycle_and_nothing_else() -> None:
    edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}, {"from": "c", "to": "a"}]
    graph = Graph(["a", "b", "c"], edges)
    assert graph.cycle_ignoring(frozenset()) is not None
    assert graph.cycle_ignoring(frozenset({"b"})) is None


def test_ds_010_accepts_a_reference_from_inside_the_loop() -> None:
    """The golden graph's cycle is why ruling R-02's two readings differ.

    ``recheck_store`` observes ``store@after_docs``, whose ``after_node`` is
    ``request_docs``. ``request_docs`` is *also* downstream of
    ``recheck_store``, through the loop - so the catalogue's literal "downstream
    of it" wording would reject the golden fixture. R-02's equivalent form,
    "an ancestor via at least one path", accepts it.
    """
    document = base_document("dataset")
    assert document["nodes"]["recheck_store"]["entity_refs"] == ["store@after_docs"]
    assert "DS-010" not in rules_for_dataset(document)


def test_ds_010_rejects_a_reference_from_a_node_the_after_node_cannot_reach() -> None:
    document = base_document("dataset")
    document["nodes"]["receive_request"]["entity_refs"] = ["store@after_docs"]
    assert rules_for_dataset(document) == {"DS-010"}


# --------------------------------------------------------------------------- #
# Rule precedence. The rule that does *not* fire is part of the contract.
# --------------------------------------------------------------------------- #


def test_bp_009_alone_when_an_entity_ref_does_not_resolve() -> None:
    """Ruling R-18: BP-011 checks syntax with the refs stripped."""
    assert rules_for_blueprint(
        mutated(
            {
                "target": "blueprint",
                "mutate": [
                    {
                        "op": "replace",
                        "path": "/nodes/1/output_schema/properties/store/$ref",
                        "value": "entity:outlet",
                    }
                ],
            }
        )
    ) == {"BP-009"}


def test_ds_019_alone_when_a_pool_fixture_violates_its_schema() -> None:
    """Ruling R-18: pool fixtures are DS-019's territory, never DS-004's."""
    document = base_document("dataset")
    document["pools"]["request_docs"][0]["output"] = {"requested": "fssai", "received": []}
    assert rules_for_dataset(document) == {"DS-019"}


def test_ds_009_alone_when_the_revision_is_unknown() -> None:
    """Ruling R-02: DS-010 is skipped for a reference DS-009 has reported."""
    document = base_document("dataset")
    document["nodes"]["receive_request"]["entity_refs"] = ["store@never_declared"]
    assert rules_for_dataset(document) == {"DS-009"}


def test_ds_011_alone_when_a_revision_points_at_no_node() -> None:
    document = base_document("dataset")
    document["entities"]["store"]["revisions"]["after_docs"]["after_node"] = "ghost"
    assert rules_for_dataset(document) == {"DS-011"}


def test_bp_006_alone_when_the_entry_node_does_not_exist() -> None:
    """Reachability from a node that does not exist would report every node."""
    document = base_document("blueprint")
    document["entry_node"] = "ghost"
    assert rules_for_blueprint(document) == {"BP-006"}


def test_ds_004_skipped_entirely_when_a_fault_is_set() -> None:
    """Ruling R-07, both halves: no schema check, and `output` must be an object."""
    document = base_document("dataset")
    document["nodes"]["verify_compliance"]["fault"] = {"kind": "error", "code": "BOOM"}
    document["nodes"]["verify_compliance"]["output"] = {"anything": True}
    assert rules_for_dataset(document) == set()

    document["nodes"]["verify_compliance"]["output"] = "a string, not an object"
    assert rules_for_dataset(document) == {"DS-004"}


def test_ds_004_and_ds_005_skip_a_node_key_that_is_not_a_blueprint_node() -> None:
    """DS-003 owns the unknown key; validating it against nothing is not possible."""
    document = base_document("dataset")
    document["nodes"]["ghost"] = {"input": {"x": 1}, "output": {"y": 2}, "entity_refs": []}
    assert rules_for_dataset(document) == {"DS-003"}


def test_ds_003_when_a_pool_node_appears_in_both_nodes_and_pools() -> None:
    """Ruling R-01's second half."""
    document = base_document("dataset")
    document["nodes"]["request_docs"] = {
        "output": {"requested": [], "received": []},
        "entity_refs": [],
    }
    assert rules_for_dataset(document) == {"DS-003"}


def test_ds_002_exempts_a_pool_node_from_nodes() -> None:
    """Ruling R-01's first half, stated positively: the golden shape is legal."""
    document = base_document("dataset")
    assert "request_docs" not in document["nodes"]
    assert "request_docs" in document["pools"]
    assert rules_for_dataset(document) == set()


# --------------------------------------------------------------------------- #
# DS-008's mechanism (ruling R-14 asked for one; see DECISIONS.md).
# --------------------------------------------------------------------------- #


def test_entity_ref_sites_locates_state_through_the_schema() -> None:
    schema = {
        "type": "object",
        "properties": {
            "store": {"$ref": "entity:store"},
            "history": {"type": "array", "items": {"$ref": "entity:store"}},
            "count": {"type": "integer"},
        },
    }
    instance = {"store": {"id": "ST-1"}, "history": [{"id": "ST-2"}, {"id": "ST-3"}], "count": 2}
    assert entity_ref_sites(schema, instance, "store") == [
        (("store",), {"id": "ST-1"}),
        (("history", 0), {"id": "ST-2"}),
        (("history", 1), {"id": "ST-3"}),
    ]


def test_entity_ref_sites_is_empty_when_a_node_embeds_no_state() -> None:
    """`verify_compliance` references `store` and carries only a `store_id`.

    This is why DS-008 cannot simply compare every fixture that names an entity:
    most fixtures do not carry one.
    """
    node = next(n for n in GOLDEN_BLUEPRINT["nodes"] if n["id"] == "verify_compliance")
    document = base_document("dataset")
    fixture = document["nodes"]["verify_compliance"]
    assert entity_ref_sites(node["output_schema"], fixture["output"], "store") == []
    assert entity_ref_sites(node["input_schema"], fixture["input"], "store") == []


def test_ds_008_ignores_an_entity_that_declares_revisions() -> None:
    """A revised entity is *expected* to differ; DS-010 owns which state goes where."""
    document = base_document("dataset")
    document["nodes"]["fetch_store_profile"]["output"]["store"]["name"] = "Somewhere else"
    assert "DS-008" not in rules_for_dataset(document)


def test_ds_008_fires_on_a_constant_entity_that_drifts_in_an_input() -> None:
    """The mechanism reads `input` as well as `output`: `check_docs` embeds a store."""
    document = base_document("dataset")
    document["entities"]["store"].pop("revisions")
    document["nodes"]["check_docs"]["input"]["store"]["city"] = "Mysuru"
    reported = {
        (error.rule, error.pointer) for error in validate_dataset(document, DATASET_RESOLVER).errors
    }
    assert ("DS-008", "/nodes/check_docs/input/store") in reported


@pytest.mark.parametrize(
    ("left", "right", "identical"),
    [
        ({"a": 1, "b": 2}, {"b": 2, "a": 1}, True),
        ({"a": 1}, {"a": 1.0}, False),
        ({"a": 1}, {"a": True}, False),
        ({"a": [1, 2]}, {"a": [2, 1]}, False),
    ],
)
def test_canonical_treats_key_order_as_equal_and_json_type_as_different(
    left: Any, right: Any, identical: bool
) -> None:
    """DS-008's "byte-identical", made precise. See DECISIONS.md."""
    assert (canonical(left) == canonical(right)) is identical


# --------------------------------------------------------------------------- #
# Schema helpers.
# --------------------------------------------------------------------------- #


def test_resolve_entity_refs_substitutes_and_keeps_siblings() -> None:
    resolved = resolve_entity_refs(
        {"$ref": "entity:store", "description": "the outlet"}, ENTITY_SCHEMAS
    )
    assert resolved["type"] == "object"
    assert resolved["description"] == "the outlet"
    assert "$ref" not in resolved


def test_resolve_entity_refs_drops_an_unknown_ref_rather_than_leaving_it() -> None:
    """An unresolvable ref left in place would make `jsonschema` raise inside DS-004."""
    assert resolve_entity_refs({"$ref": "entity:ghost"}, ENTITY_SCHEMAS) == {}
    assert strip_entity_refs({"properties": {"a": {"$ref": "entity:store"}}}) == {
        "properties": {"a": {}}
    }


def test_resolve_entity_refs_survives_a_self_referential_entity() -> None:
    """No rule owns this case, so the permissive answer is the only safe one."""
    resolved = resolve_entity_refs(
        {"$ref": "entity:knot"}, {"knot": {"properties": {"self": {"$ref": "entity:knot"}}}}
    )
    assert "properties" in resolved


def test_declared_properties_follows_entity_refs_and_branches() -> None:
    schema = {
        "allOf": [
            {"properties": {"a": {"type": "string"}}},
            {"$ref": "entity:franchisee"},
        ]
    }
    assert set(declared_properties(schema, ENTITY_SCHEMAS)) == {
        "a",
        "id",
        "name",
        "existing_locations",
    }


# --------------------------------------------------------------------------- #
# Pointers, sections, and the entity_refs grammar.
# --------------------------------------------------------------------------- #


def test_pointer_escapes_reference_tokens() -> None:
    assert pointer("nodes", "a/b", 0) == "/nodes/a~1b/0"
    assert escape_token("~/") == "~0~1"


@pytest.mark.parametrize(
    ("target", "section"),
    [
        ("/provenance/title", "provenance"),
        ("/narrative", "provenance"),
        ("/entities/store/base", "entities"),
        ("/nodes/check_docs/output", "nodes.core"),
        ("/nodes/request_docs/output", "nodes.branches"),
        ("/pools/request_docs/0/output", "nodes.branches"),
        ("/expected/final", "expected"),
        ("/labels/persona", None),
        ("/seed", None),
        ("/blueprint", None),
        ("", None),
    ],
)
def test_section_for_pointer(target: str, section: str | None) -> None:
    """Ruling R-06's five sections, plus the three fields that have none."""
    assert section_for_pointer(target, pool_nodes=frozenset({"request_docs"})) == section


def test_blueprint_findings_carry_no_section() -> None:
    document = base_document("blueprint")
    document["agent_id"] = "Nope"
    assert [error.section for error in validate_blueprint(document).errors] == [None]


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("store", ("store", None)),
        ("store@after_docs", ("store", "after_docs")),
        ("store@", ("store", "")),
    ],
)
def test_split_ref(ref: str, expected: tuple[str, str | None]) -> None:
    assert split_ref(ref) == expected


# --------------------------------------------------------------------------- #
# Ruling R-20: duplicate keys, which only exist in the raw text.
# --------------------------------------------------------------------------- #


def test_parse_with_duplicate_keys_reports_pointers_and_parses_normally() -> None:
    document, duplicates = parse_with_duplicate_keys(
        '{"labels": {"persona": "a", "persona": "b"}, "nodes": [{"x": 1, "x": 2}]}'
    )
    assert document == {"labels": {"persona": "b"}, "nodes": [{"x": 2}]}
    assert duplicates == ["/labels/persona", "/nodes/0/x"]


def test_parse_with_duplicate_keys_is_silent_on_clean_text() -> None:
    document, duplicates = parse_with_duplicate_keys('{"a": {"b": 1}, "c": [1, 2]}')
    assert duplicates == []
    assert document == {"a": {"b": 1}, "c": [1, 2]}


def test_ds_013_only_reports_duplicates_under_labels() -> None:
    """The rule is "no label dimension appears twice", not "no key anywhere"."""
    document = base_document("dataset")
    reported = {
        error.rule
        for error in validate_dataset(
            document, DATASET_RESOLVER, duplicate_keys=["/nodes/0/x"]
        ).errors
    }
    assert reported == set()


# --------------------------------------------------------------------------- #
# The validator never raises, whatever it is handed.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"nodes": "not a list", "edges": 7, "entities": None, "label_schema": []},
        {"nodes": [None, 3, {"id": None}], "edges": [None, {"from": 1, "to": 2}]},
        {"agent_id": None, "version": 1.0, "entry_node": [], "outcome_schema": "nope"},
    ],
)
def test_validate_blueprint_never_raises(document: dict[str, Any]) -> None:
    envelope = validate_blueprint(document)
    assert envelope.ok is False
    assert envelope.errors


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"blueprint": "not a mapping", "nodes": 3, "pools": None, "labels": "x"},
        {"blueprint": {"agent_id": "location-onboarding", "version": "1.0.0"}, "nodes": []},
        {
            "blueprint": {"agent_id": "location-onboarding", "version": "1.0.0"},
            "entities": {"store": None},
            "nodes": {"complete": None},
            "pools": {"request_docs": "not a list"},
            "expected": [],
        },
    ],
)
def test_validate_dataset_never_raises(document: dict[str, Any]) -> None:
    envelope = validate_dataset(document, DATASET_RESOLVER)
    assert envelope.ok is False
    assert envelope.errors


def test_a_clean_document_reports_an_ok_envelope_with_no_findings() -> None:
    envelope = validate_dataset(base_document("dataset"), DATASET_RESOLVER)
    assert envelope.ok is True
    assert envelope.errors == []
