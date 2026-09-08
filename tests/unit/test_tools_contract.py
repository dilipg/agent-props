"""M4's gate: an in-memory ``Client`` against the server object, every tool.

Ruling R-16 verified that ``mcp.Client`` accepts an ``MCPServer`` instance
directly, so this whole suite runs in-process. `docs/build-handoff.md`'s testing
strategy calls this layer "tool contract tests: assert shapes and error
envelopes, not prose", and CLAUDE.md adds "no subprocesses in unit tests". The
two transports get separate evidence in `tests/integration/test_transports.py`,
because "the tools work in memory" and "the transports start" are different
claims.

Sessions come from `tests/toolclient.py` rather than from a fixture, for the
pytest-asyncio/anyio reason recorded there.

What is asserted here, and what is not
--------------------------------------

Here: the **envelope** each tool returns over a real MCP round trip, the
warnings it attaches, and the determinism criterion. Rule ids and shapes, never
messages.

Not here: the behaviour behind the tools. That is
`test_service_blueprints.py`, `test_service_datasets.py`,
`test_service_admin.py` and `test_service_diff.py`, which can assert on a
monkeypatched store failure or a malformed document without a client in the way.
Duplicating those through the client would double the suite and test the SDK's
serialisation thirteen more times.

`test_tool_surface.py` is what makes "every tool" mechanical rather than a list
kept by hand here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agentprops.models import Dataset
from agentprops.service import ServiceContext, blueprints
from conftest import DATASET_FIXTURE, load_text
from toolclient import attempt, connected, invoke


@pytest.fixture
def seeded(
    context: ServiceContext,
    blueprint_document: dict[str, Any],
    dataset_document: dict[str, Any],
    other_dataset_document: dict[str, Any],
) -> ServiceContext:
    """The published blueprint and both golden datasets, in the client's store.

    Datasets are written through ``Store.put_dataset`` because M4 has no dataset
    write tool - ``dataset_submit`` is M5's, and building it early to test a read
    would be building M5 early.
    """
    blueprints.upsert(context, blueprint_document, publish=True)
    context.store.put_dataset(Dataset.model_validate(dataset_document))
    context.store.put_dataset(Dataset.model_validate(other_dataset_document))
    return context


def rules(envelope: dict[str, Any]) -> list[str]:
    return [finding["rule"] for finding in envelope["errors"]]


def codes(envelope: dict[str, Any]) -> list[str]:
    return [item["code"] for item in envelope["warnings"]]


def revised(document: dict[str, Any]) -> dict[str, Any]:
    """A ``1.1.0`` that differs in all four diff categories and still validates.

    "Still validates" is the part that needs saying. An earlier version of this
    helper removed a node and added an edge into a terminal node, so the
    ``blueprint_upsert`` that was supposed to set up the diff was silently
    rejected by BP-004 and BP-007 - and the diff test then compared against a
    version that did not exist and passed anyway, asserting nothing. Every
    caller asserts the upsert succeeded, and this graph keeps BP-004, BP-005,
    BP-007, BP-014 and BP-018 satisfied: ``fetch_store_profile ->
    recheck_store`` adds no new cycle and no outbound edge on a terminal node.
    """
    newer: dict[str, Any] = json.loads(json.dumps(document))
    newer["version"] = "1.1.0"
    newer["nodes"][0]["kind"] = "llm"
    newer["nodes"][1]["output_schema"] = {"type": "object"}
    newer["label_schema"]["dimensions"]["region"] = ["south", "north"]
    newer["edges"].append({"from": "fetch_store_profile", "to": "recheck_store", "condition": None})
    return newer


# ------------------------------------------------------------------- handshake


async def test_the_server_reports_its_identity_and_instructions(
    context: ServiceContext,
) -> None:
    async with connected(context) as client:
        assert client.server_info is not None
        assert client.server_info.name == "agent-props"
        assert client.instructions is not None
        assert '"ok"' in client.instructions, "the envelope contract belongs in the instructions"


async def test_the_client_lists_the_tools_over_the_protocol(context: ServiceContext) -> None:
    async with connected(context) as client:
        listed = await client.list_tools()
    assert {tool.name for tool in listed.tools} >= {"blueprint_upsert", "store_status"}


async def test_one_session_serves_many_calls(seeded: ServiceContext) -> None:
    """Session reuse, so the per-call helper is not the only path with coverage."""
    async with connected(seeded) as client:
        first = await invoke(client, "store_status")
        second = await invoke(client, "agent_list")
        third = await invoke(client, "blueprint_list")
    assert first["ok"] and second["ok"] and third["ok"]


# ------------------------------------------------------------------- blueprint


async def test_blueprint_upsert_stores_and_returns_the_blueprint(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    envelope = await invoke(context, "blueprint_upsert", blueprint=blueprint_document, publish=True)
    assert envelope["ok"] is True
    assert envelope["data"]["blueprint"]["status"] == "published"
    assert envelope["warnings"] == []


async def test_blueprint_upsert_is_idempotent_for_an_identical_document(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """Ruling R-29 over the wire: a CI pipeline republishing is not an error."""
    first = await invoke(context, "blueprint_upsert", blueprint=blueprint_document, publish=True)
    second = await invoke(context, "blueprint_upsert", blueprint=blueprint_document, publish=True)
    assert second["ok"] is True
    assert second["data"] == first["data"]


async def test_blueprint_upsert_reports_bp_016_for_a_modified_published_version(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    await invoke(context, "blueprint_upsert", blueprint=blueprint_document, publish=True)
    modified = {**blueprint_document, "description": "reworded after publishing"}
    envelope = await invoke(context, "blueprint_upsert", blueprint=modified, publish=True)
    assert envelope["ok"] is False
    assert rules(envelope) == ["BP-016"]


async def test_blueprint_upsert_carries_a_warning_on_a_successful_write(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    """Ruling R-13's shape on the wire: ``warnings``, not ``errors``."""
    document = json.loads(json.dumps(blueprint_document))
    del document["nodes"][0]["notes"]
    envelope = await invoke(context, "blueprint_upsert", blueprint=document, publish=True)
    assert envelope["ok"] is True
    assert codes(envelope) == ["BP-019"]
    assert envelope["warnings"][0]["detail"]["pointer"].startswith("/nodes/")


async def test_blueprint_upsert_reports_a_rule_id_for_a_bad_document(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    envelope = await invoke(
        context, "blueprint_upsert", blueprint={**blueprint_document, "agent_id": "BAD"}
    )
    assert envelope["ok"] is False
    assert "BP-001" in rules(envelope)
    assert envelope["errors"][0]["pointer"] == "/agent_id"


async def test_blueprint_upsert_reports_a_boundary_code_for_a_malformed_argument(
    context: ServiceContext,
) -> None:
    """A malformed argument is an envelope, not an MCP protocol error."""
    not_an_object = await invoke(context, "blueprint_upsert", blueprint=["a", "list"])
    assert rules(not_an_object) == ["AP-001"]
    bad_flag = await invoke(context, "blueprint_upsert", blueprint={}, publish="yes please")
    assert rules(bad_flag) == ["AP-001"]
    assert bad_flag["errors"][0]["pointer"] == "/publish"


async def test_blueprint_get_returns_the_stored_document(
    seeded: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    envelope = await invoke(seeded, "blueprint_get", agent_id="location-onboarding")
    assert envelope["data"]["blueprint"] == blueprint_document


async def test_blueprint_get_reports_ap_004_for_an_unknown_agent(
    context: ServiceContext,
) -> None:
    envelope = await invoke(context, "blueprint_get", agent_id="no-such-agent")
    assert rules(envelope) == ["AP-004"]


async def test_blueprint_list_returns_summaries(seeded: ServiceContext) -> None:
    envelope = await invoke(seeded, "blueprint_list")
    rows = envelope["data"]["blueprints"]
    assert [row["agent_id"] for row in rows] == ["location-onboarding"]
    assert set(rows[0]) == {"agent_id", "version", "status", "description"}


async def test_blueprint_list_filters_by_status(seeded: ServiceContext) -> None:
    drafts = await invoke(seeded, "blueprint_list", status="draft")
    published = await invoke(seeded, "blueprint_list", status="published")
    assert drafts["data"]["blueprints"] == []
    assert len(published["data"]["blueprints"]) == 1


async def test_blueprint_validate_returns_the_validation_envelope(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    envelope = await invoke(context, "blueprint_validate", blueprint=blueprint_document)
    assert envelope == {"ok": True, "errors": []}


async def test_blueprint_validate_stores_nothing(
    context: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    await invoke(context, "blueprint_validate", blueprint=blueprint_document)
    listed = await invoke(context, "blueprint_list")
    assert listed["data"]["blueprints"] == []


async def test_blueprint_diff_returns_a_structured_diff(
    seeded: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    published = await invoke(
        seeded, "blueprint_upsert", blueprint=revised(blueprint_document), publish=True
    )
    assert published["ok"] is True, published
    envelope = await invoke(
        seeded,
        "blueprint_diff",
        agent_id="location-onboarding",
        from_version="1.0.0",
        to_version="1.1.0",
    )
    diff = envelope["data"]["diff"]
    assert set(diff) == {"agent_id", "from", "to", "nodes", "edges", "schemas", "labels"}
    assert diff["from"]["present"] is True and diff["to"]["present"] is True
    assert diff["nodes"]["changed"] == [
        {
            "node_id": "receive_request",
            "changes": [{"field": "kind", "from": "tool_call", "to": "llm"}],
        }
    ]
    assert [entry["node_id"] for entry in diff["schemas"]] == ["fetch_store_profile"]
    assert [entry["field"] for entry in diff["schemas"]] == ["output_schema"]
    assert diff["labels"]["dimensions_added"] == ["region"]
    assert diff["edges"]["added"] == [
        {"from": "fetch_store_profile", "to": "recheck_store", "condition": None}
    ]
    assert diff["edges"]["removed"] == []


async def test_blueprint_diff_is_never_a_failure_signal(seeded: ServiceContext) -> None:
    """The acceptance criterion, in the three shapes that would tempt a failure."""
    both_missing = await invoke(
        seeded, "blueprint_diff", agent_id="ghost", from_version="1.0.0", to_version="2.0.0"
    )
    one_missing = await invoke(
        seeded,
        "blueprint_diff",
        agent_id="location-onboarding",
        from_version="1.0.0",
        to_version="9.9.9",
    )
    identical = await invoke(
        seeded,
        "blueprint_diff",
        agent_id="location-onboarding",
        from_version="1.0.0",
        to_version="1.0.0",
    )
    for envelope in (both_missing, one_missing, identical):
        assert envelope["ok"] is True, envelope
        assert "errors" not in envelope
    assert codes(one_missing) == ["blueprint_version_missing"]
    assert codes(both_missing) == ["blueprint_version_missing"] * 2
    assert codes(identical) == []


async def test_blueprint_diff_output_is_byte_identical_across_repeated_calls(
    seeded: ServiceContext, blueprint_document: dict[str, Any]
) -> None:
    published = await invoke(
        seeded, "blueprint_upsert", blueprint=revised(blueprint_document), publish=True
    )
    assert published["ok"] is True, published
    arguments = {
        "agent_id": "location-onboarding",
        "from_version": "1.0.0",
        "to_version": "1.1.0",
    }
    first = json.dumps(await invoke(seeded, "blueprint_diff", **arguments))
    second = json.dumps(await invoke(seeded, "blueprint_diff", **arguments))
    assert first == second


# --------------------------------------------------------------------- dataset


async def test_dataset_find_returns_summaries_in_a_total_order(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    envelope = await invoke(seeded, "dataset_find")
    rows = envelope["data"]["datasets"]
    assert len(rows) == 2
    assert rows[0]["id"] == dataset_document["id"], "ordered by (created_at, id)"
    assert "narrative_excerpt" in rows[0]
    assert "nodes" not in rows[0], "a summary never carries a fixture"


async def test_dataset_find_filters_and_pages(seeded: ServiceContext) -> None:
    labelled = await invoke(seeded, "dataset_find", labels={"tier": "regional"})
    paged = await invoke(seeded, "dataset_find", limit=1, offset=1)
    searched = await invoke(seeded, "dataset_find", q="fssai")
    assert len(labelled["data"]["datasets"]) == 1
    assert len(paged["data"]["datasets"]) == 1
    assert searched["ok"] is True


async def test_dataset_find_label_queries_are_byte_identical_across_calls(
    seeded: ServiceContext,
) -> None:
    """The acceptance criterion, on the query it names. Bytes, not row counts."""
    arguments = {"labels": {"scenario": "missing-documents", "tier": "regional"}}
    first = json.dumps(await invoke(seeded, "dataset_find", **arguments))
    second = json.dumps(await invoke(seeded, "dataset_find", **arguments))
    assert first == second
    unfiltered_first = json.dumps(await invoke(seeded, "dataset_find"))
    unfiltered_second = json.dumps(await invoke(seeded, "dataset_find"))
    assert unfiltered_first == unfiltered_second


async def test_dataset_find_reports_a_boundary_code_for_a_bad_label_value(
    seeded: ServiceContext,
) -> None:
    envelope = await invoke(seeded, "dataset_find", labels={"tier": 7})
    assert rules(envelope) == ["AP-001"]
    assert envelope["errors"][0]["pointer"] == "/labels/tier"


async def test_dataset_find_rejects_a_boolean_limit_rather_than_reading_it_as_one(
    seeded: ServiceContext,
) -> None:
    """``isinstance(True, int)`` is the trap; DS-020 already paid for it once."""
    envelope = await invoke(seeded, "dataset_find", limit=True)
    assert rules(envelope) == ["AP-001"]
    assert envelope["errors"][0]["pointer"] == "/limit"


async def test_dataset_get_returns_the_full_dataset(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    envelope = await invoke(seeded, "dataset_get", dataset_id=dataset_document["id"])
    assert envelope["data"]["dataset"] == dataset_document
    assert envelope["warnings"] == []


async def test_dataset_get_reports_ap_004_for_an_unknown_id(seeded: ServiceContext) -> None:
    envelope = await invoke(seeded, "dataset_get", dataset_id="not-a-uuid")
    assert rules(envelope) == ["AP-004"]


async def test_dataset_archive_hides_it_from_find_but_not_from_get(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    """Ground rule 3 and ruling R-34, over the wire."""
    archived = await invoke(seeded, "dataset_archive", dataset_id=dataset_document["id"])
    assert archived["data"]["summary"]["archived"] is True

    found = await invoke(seeded, "dataset_find")
    assert dataset_document["id"] not in {row["id"] for row in found["data"]["datasets"]}

    fetched = await invoke(seeded, "dataset_get", dataset_id=dataset_document["id"])
    assert fetched["ok"] is True
    assert codes(fetched) == ["dataset_archived"]


async def test_dataset_restore_puts_it_back(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    await invoke(seeded, "dataset_archive", dataset_id=dataset_document["id"])
    restored = await invoke(seeded, "dataset_restore", dataset_id=dataset_document["id"])
    assert restored["data"]["summary"]["archived"] is False
    found = await invoke(seeded, "dataset_find")
    assert len(found["data"]["datasets"]) == 2


async def test_dataset_archive_reports_ap_004_for_an_unknown_id(
    seeded: ServiceContext,
) -> None:
    envelope = await invoke(
        seeded, "dataset_archive", dataset_id="3f8c1a20-0000-4000-8000-0000000000ff"
    )
    assert rules(envelope) == ["AP-004"]


async def test_dataset_validate_returns_the_validation_envelope(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    envelope = await invoke(seeded, "dataset_validate", dataset=dataset_document)
    assert envelope == {"ok": True, "errors": []}


async def test_dataset_validate_reports_ds_013_through_the_raw_text_argument(
    seeded: ServiceContext,
) -> None:
    """Ruling R-20 over a real MCP round trip - the boundary, as the ruling asks.

    The same document sent as a parsed object reports nothing, because
    ``json.loads`` collapsed the duplicate before the request existed. That is
    the finding recorded in `service/documents.py`, demonstrated end to end.
    """
    text = load_text(DATASET_FIXTURE).replace(
        '"labels": {', '"labels": {"persona": "corporate-admin",', 1
    )
    from_text = await invoke(seeded, "dataset_validate", dataset_json=text)
    assert rules(from_text) == ["DS-013"]
    assert from_text["errors"][0]["pointer"] == "/labels/persona"

    from_object = await invoke(seeded, "dataset_validate", dataset=json.loads(text))
    assert from_object["ok"] is True


async def test_dataset_validate_refuses_both_argument_forms_at_once(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    envelope = await invoke(seeded, "dataset_validate", dataset=dataset_document, dataset_json="{}")
    assert rules(envelope) == ["AP-001"]
    assert envelope["errors"][0]["pointer"] == "/dataset_json"


async def test_dataset_validate_reports_malformed_raw_text_as_ap_002(
    seeded: ServiceContext,
) -> None:
    envelope = await invoke(seeded, "dataset_validate", dataset_json="{oops")
    assert rules(envelope) == ["AP-002"]


async def test_dataset_validate_reports_a_dataset_rule_with_its_section(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    """The ``section`` field, which is what makes a partial fill repairable (R-06)."""
    broken = json.loads(json.dumps(dataset_document))
    broken["provenance"]["title"] = "   "
    envelope = await invoke(seeded, "dataset_validate", dataset=broken)
    assert rules(envelope) == ["DS-025"]
    assert envelope["errors"][0]["section"] == "provenance"


# ----------------------------------------------------------------------- admin


async def test_store_status_reports_the_backend_and_counts(seeded: ServiceContext) -> None:
    status = (await invoke(seeded, "store_status"))["data"]["status"]
    assert status["backend"] == "sqlite"
    assert status["healthy"] is True
    assert status["counts"] == {"blueprints": 1, "datasets": 2, "runs": 0}


async def test_label_vocabulary_returns_the_schema_and_counts(seeded: ServiceContext) -> None:
    envelope = await invoke(seeded, "label_vocabulary", agent_id="location-onboarding")
    vocabulary = envelope["data"]["vocabulary"]
    assert vocabulary["counts"]["persona"]["multi-unit-operator"] == 1
    assert vocabulary["counts"]["persona"]["corporate-admin"] == 0
    assert vocabulary["dataset_count"] == 2


async def test_label_vocabulary_output_is_byte_identical_across_repeated_calls(
    seeded: ServiceContext,
) -> None:
    """The acceptance criterion's "label queries", on the label tool itself."""
    first = json.dumps(await invoke(seeded, "label_vocabulary", agent_id="location-onboarding"))
    second = json.dumps(await invoke(seeded, "label_vocabulary", agent_id="location-onboarding"))
    assert first == second


async def test_label_vocabulary_reports_ap_004_for_an_unknown_agent(
    context: ServiceContext,
) -> None:
    envelope = await invoke(context, "label_vocabulary", agent_id="ghost")
    assert rules(envelope) == ["AP-004"]


async def test_agent_list_reports_versions_and_dataset_counts(seeded: ServiceContext) -> None:
    agents = (await invoke(seeded, "agent_list"))["data"]["agents"]
    assert agents == [
        {"agent_id": "location-onboarding", "versions": ["1.0.0"], "dataset_count": 2}
    ]


async def test_agent_list_on_an_empty_store_is_an_empty_list(context: ServiceContext) -> None:
    assert (await invoke(context, "agent_list"))["data"]["agents"] == []


# ------------------------------------------------------------------ the envelope


async def test_every_success_envelope_puts_its_payload_under_one_named_key(
    seeded: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    """The ``data`` convention, checked across tools rather than described once."""
    expected: list[tuple[str, dict[str, Any], str]] = [
        ("blueprint_get", {"agent_id": "location-onboarding"}, "blueprint"),
        ("blueprint_list", {}, "blueprints"),
        ("dataset_find", {}, "datasets"),
        ("dataset_get", {"dataset_id": dataset_document["id"]}, "dataset"),
        ("store_status", {}, "status"),
        ("agent_list", {}, "agents"),
    ]
    for name, arguments, key in expected:
        envelope = await invoke(seeded, name, **arguments)
        assert list(envelope["data"]) == [key], name


async def test_an_unknown_tool_is_an_error_from_the_sdk_not_from_a_tool(
    context: ServiceContext,
) -> None:
    """The one failure that is not an envelope, pinned so the boundary is clear.

    An unknown *tool* is a protocol-level error - there is no tool function to
    return an envelope from. Every failure a real tool can produce is an
    envelope; this is the edge of that claim.
    """
    result = await attempt(context, "no_such_tool")
    assert result.is_error is True
