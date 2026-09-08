"""``blueprint_diff``'s computation: all four categories, and the two properties.

The two properties the acceptance criteria name are asserted here rather than
only through the tool, because they are properties of the *computation*:

- **never a failure signal.** There is no input to
  :func:`diff_blueprints` that produces one - not a missing version, not a
  malformed document, not two blueprints with nothing in common.
- **byte-identical output for identical input.** Asserted by comparing
  ``json.dumps`` of two runs, not by comparing lengths, and over a *mutated*
  pair as well as an unchanged one so the sorted branches are actually
  exercised.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from agentprops.service.diff import NODE_SCALAR_FIELDS, SCHEMA_FIELDS, diff_blueprints
from conftest import BLUEPRINT_FIXTURE, load_document


def golden() -> dict[str, Any]:
    return load_document(BLUEPRINT_FIXTURE)


def diff(before: Any, after: Any) -> dict[str, Any]:
    return diff_blueprints("location-onboarding", "1.0.0", before, "1.1.0", after)


def test_a_blueprint_against_itself_reports_nothing_changed() -> None:
    computed = diff(golden(), golden())
    assert computed["nodes"] == {"added": [], "removed": [], "changed": []}
    assert computed["edges"] == {"added": [], "removed": []}
    assert computed["schemas"] == []
    assert computed["labels"] == {
        "dimensions_added": [],
        "dimensions_removed": [],
        "values_added": [],
        "values_removed": [],
    }


def test_both_sides_report_their_version_status_and_presence() -> None:
    computed = diff(golden(), golden())
    assert computed["from"] == {"version": "1.0.0", "present": True, "status": "published"}
    assert computed["to"] == {"version": "1.1.0", "present": True, "status": "published"}
    assert computed["agent_id"] == "location-onboarding"


def test_an_added_and_a_removed_node_are_reported_by_id_and_sorted() -> None:
    after = golden()
    after["nodes"] = [node for node in after["nodes"] if node["id"] != "escalate"]
    after["nodes"].append(
        {
            "id": "zzz_new",
            "kind": "terminal",
            "pool": False,
            "input_schema": {},
            "output_schema": {},
        }
    )
    after["nodes"].append(
        {
            "id": "aaa_new",
            "kind": "terminal",
            "pool": False,
            "input_schema": {},
            "output_schema": {},
        }
    )
    computed = diff(golden(), after)
    assert computed["nodes"]["added"] == ["aaa_new", "zzz_new"]
    assert computed["nodes"]["removed"] == ["escalate"]


def test_a_changed_scalar_field_is_reported_with_its_before_and_after() -> None:
    after = golden()
    after["nodes"][0]["kind"] = "llm"
    after["nodes"][0]["notes"] = None
    changed = diff(golden(), after)["nodes"]["changed"]
    assert len(changed) == 1
    assert changed[0]["node_id"] == golden()["nodes"][0]["id"]
    fields = {entry["field"]: (entry["from"], entry["to"]) for entry in changed[0]["changes"]}
    assert fields["kind"][1] == "llm"
    assert fields["notes"][1] is None


def test_a_node_whose_only_change_is_a_schema_appears_in_schemas_not_in_changed() -> None:
    """The documented partition: the two categories never overlap."""
    after = golden()
    node_id = after["nodes"][0]["id"]
    after["nodes"][0]["output_schema"] = {"type": "object", "properties": {"new": {}}}
    computed = diff(golden(), after)
    assert computed["nodes"]["changed"] == []
    assert [(entry["node_id"], entry["field"]) for entry in computed["schemas"]] == [
        (node_id, "output_schema")
    ]
    assert computed["schemas"][0]["to"]["properties"] == {"new": {}}


def test_the_scalar_and_schema_field_sets_are_disjoint() -> None:
    """The property the partition rests on, asserted rather than assumed."""
    assert set(NODE_SCALAR_FIELDS) & set(SCHEMA_FIELDS) == set()


def test_a_changed_edge_condition_is_one_removal_and_one_addition() -> None:
    """Edge identity includes the condition, so no change is invisible."""
    after = golden()
    target = next(edge for edge in after["edges"] if edge.get("condition"))
    original = copy.deepcopy(target)
    assert original["condition"] != {"!=": [{"var": "documents_complete"}, "unset"]}
    target["condition"] = {"!=": [{"var": "documents_complete"}, "unset"]}
    computed = diff(golden(), after)
    assert original in computed["edges"]["removed"]
    assert {"from": target["from"], "to": target["to"], "condition": target["condition"]} in (
        computed["edges"]["added"]
    )


def test_an_added_and_a_removed_edge_are_reported() -> None:
    after = golden()
    dropped = after["edges"].pop(0)
    after["edges"].append({"from": "complete", "to": "escalate", "condition": None})
    computed = diff(golden(), after)
    assert {key: dropped.get(key) for key in ("from", "to", "condition")} in (
        computed["edges"]["removed"]
    )
    assert {"from": "complete", "to": "escalate", "condition": None} in computed["edges"]["added"]


def test_label_vocabulary_changes_cover_dimensions_and_values() -> None:
    after = golden()
    dimensions = after["label_schema"]["dimensions"]
    dimensions["region"] = ["south"]
    del dimensions["edge_case"]
    dimensions["tier"] = [*dimensions["tier"], "national"]
    dimensions["outcome"] = dimensions["outcome"][1:]
    computed = diff(golden(), after)["labels"]
    assert computed["dimensions_added"] == ["region"]
    assert computed["dimensions_removed"] == ["edge_case"]
    assert computed["values_added"] == [{"dimension": "tier", "values": ["national"]}]
    assert computed["values_removed"] == [
        {"dimension": "outcome", "values": [golden()["label_schema"]["dimensions"]["outcome"][0]]}
    ]


def test_a_missing_side_is_reported_as_absent_rather_than_as_an_error() -> None:
    computed = diff(golden(), None)
    assert computed["to"] == {"version": "1.1.0", "present": False, "status": None}
    assert computed["nodes"]["removed"] == sorted(node["id"] for node in golden()["nodes"])
    assert computed["nodes"]["added"] == []
    assert computed["labels"]["dimensions_removed"] == sorted(
        golden()["label_schema"]["dimensions"]
    )


def test_both_sides_missing_is_still_a_diff() -> None:
    computed = diff(None, None)
    assert computed["from"]["present"] is False
    assert computed["to"]["present"] is False
    assert computed["nodes"] == {"added": [], "removed": [], "changed": []}


def test_a_malformed_document_does_not_raise() -> None:
    """Total over anything a store can hand back, including a draft mid-edit."""
    computed = diff({"nodes": "not a list", "edges": 7, "label_schema": None}, golden())
    assert computed["nodes"]["added"] == sorted(node["id"] for node in golden()["nodes"])
    computed = diff({"nodes": [{"no_id": 1}, "string", 5]}, {"nodes": []})
    assert computed["nodes"] == {"added": [], "removed": [], "changed": []}


def test_repeated_identical_input_gives_byte_identical_output() -> None:
    """The determinism criterion, on the computation. Bytes, not row counts."""
    after = golden()
    after["nodes"][0]["kind"] = "llm"
    after["label_schema"]["dimensions"]["region"] = ["south", "north"]
    after["edges"].append({"from": "complete", "to": "escalate", "condition": None})
    first = json.dumps(diff(golden(), after), sort_keys=False)
    second = json.dumps(diff(golden(), after), sort_keys=False)
    assert first == second


def test_key_reordering_is_not_a_change() -> None:
    """Canonical comparison, the same one DS-008 and BP-016 use."""
    after = golden()
    schema = after["nodes"][0]["output_schema"]
    after["nodes"][0]["output_schema"] = dict(reversed(list(schema.items())))
    computed = diff(golden(), after)
    assert computed["schemas"] == []
    assert computed["nodes"]["changed"] == []
