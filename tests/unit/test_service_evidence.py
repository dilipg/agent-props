"""``run_evidence`` and ``run_export`` against a real store. The bundle's own contract.

`test_evidence_grading.py` owns the acceptance clause - the bundle graded with no
store and no session - and it reads a **committed** fixture. This file is what
makes that fixture evidence rather than decoration: it walks the same run through
a live service and asserts the bundle it gets is the one on disk. Without it, a
golden file could drift from the service for a milestone and the clause-2 suite
would keep passing, grading a document nothing emits any more.

That is M9's clause-1 lesson in a new costume, and it is worth restating because
it caught something real there: **a harness that cannot build a reply the service
sends manufactures agreement.** A fixture nobody checks against the service is
the same defect with the arrow reversed.

One field is masked, by name, on both sides
-------------------------------------------

``recorded_at`` is stamped by the **database** - ``func.now()`` on SQL - which
ruling R-09 permits ("a column default") and which the injected clock therefore
cannot freeze. So `tests/evidencewalk.py`'s ``normalised`` replaces it on both
sides of the comparison, and :func:`test_the_masked_field_is_a_real_timestamp`
asserts the live values separately: a real aware UTC stamp on every recorded
step, ``None`` on exactly the step that was never recorded. Masking a value
whose reproducibility a fixture cannot have is a compromise; masking a field
whose *presence* nothing then checks would be a hole.

``run_export`` is tested here without a collector
-------------------------------------------------

The clause-1 test needs a real collector and lives in
`tests/integration/test_otel_collector.py`, skipping with the endpoint named
when one is not running (ruling R-60's rule about a URL beside any count). What
belongs *here* is everything about ``run_export`` that a collector cannot tell
you: the argument vocabulary, the ``AP-008`` refusal when nothing is listening,
and that a refusal writes nothing to the run.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest

from agentprops.service import ServiceContext, runs
from agentprops.service import evidence as evidence_module
from agentprops.service.envelope import AP_ARGUMENT, AP_EXPORT_REFUSED
from agentprops.service.evidence import EXPORT_TARGETS, bundle, evidence, export
from conftest import FIXTURES_DIR
from envelopes import data, findings, rules
from evidencewalk import RUN_ID, UNRECORDED, normalised, seed, walk

FIXTURE: Final[Path] = FIXTURES_DIR / "evidence" / "priya-missing-docs-run.json"

#: A port nothing in this project publishes and nothing on a developer machine
#: conventionally holds, used to force the "no collector" branch. Not 4318 and
#: not the compose collector's 4418: ruling R-60's whole point is that a default
#: which *can* reach a foreign server is the defect, and a test that asserts a
#: refusal would silently invert if something answered.
DEAD_ENDPOINT: Final = "http://127.0.0.1:4319/v1/traces"


@pytest.fixture
def walked(context: ServiceContext) -> ServiceContext:
    """The golden blueprint, `priya-missing-docs`, and one completed run."""
    return walk(context)


def committed() -> dict[str, Any]:
    """The bundle `scripts/make_evidence_fixture.py` wrote."""
    document: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return document


# ------------------------------------------------------------- the drift guard


def test_the_live_bundle_equals_the_committed_fixture(walked: ServiceContext) -> None:
    """The fixture is the service's own output, or this fails naming the difference.

    Regenerate with ``uv run python scripts/make_evidence_fixture.py``, read the
    diff, and commit it - the point being that a change to the bundle's shape is
    a *reviewed* change rather than one the clause-2 suite absorbs silently.
    """
    reply = evidence(walked, RUN_ID)
    assert reply.ok, reply
    live = normalised(data(reply)["evidence"])
    assert live == committed(), (
        "the live bundle differs from tests/fixtures/evidence/; regenerate with "
        "`uv run python scripts/make_evidence_fixture.py`, read the diff, and commit it"
    )


def test_the_masked_field_is_a_real_timestamp(walked: ServiceContext) -> None:
    """What the mask hides is only the *value*. Presence and type are checked here.

    Otherwise a bundle that stopped carrying ``recorded_at`` at all would
    compare equal to the fixture and nothing would notice - the mask would have
    become the hole.
    """
    live = data(evidence(walked, RUN_ID))["evidence"]
    for node in live["nodes"]:
        if node["node_id"] == UNRECORDED:
            assert node["recorded_at"] is None
            continue
        stamped = datetime.fromisoformat(node["recorded_at"])
        assert stamped.tzinfo is not None, node
        assert stamped.utcoffset() == UTC.utcoffset(None), node


# ----------------------------------------------------------------- the contents


def test_every_documented_key_is_present(walked: ServiceContext) -> None:
    """The bundle's key set, from the milestone's own sentence.

    "the expectation, actual, per-node expected versus actual, traversed path
    against ``expected_path``, declared comparison mode, warnings" plus ruling
    R-82's ``outcome_schema``. Asserted as equality rather than containment, so
    a key added without a contract fails here too.
    """
    live = data(evidence(walked, RUN_ID))["evidence"]
    assert set(live) == {
        "run",
        "pin",
        "dataset",
        "comparison",
        "expected",
        "outcome_schema",
        "actual",
        "nodes",
        "path",
        "warnings",
    }


def test_the_bundle_reads_the_pinned_versions_not_the_latest(walked: ServiceContext) -> None:
    """A bundle is as reproducible as the run it describes (ground rule 5).

    The dataset is edited to version 2 *after* the run finished, and the bundle
    must still describe version 1 - the version the run pinned and served from.
    A bundle that followed the lineage's head would re-describe an old run
    against a world it never saw, which is the drift claim inverted.
    """
    before = data(evidence(walked, RUN_ID))["evidence"]
    stored = walked.store.get_dataset(before["pin"]["dataset_id"], None)
    assert stored is not None
    walked.store.put_dataset(
        stored.model_copy(update={"narrative": "rewritten after the run finished"})
    )
    after = data(evidence(walked, RUN_ID))["evidence"]
    assert after["pin"]["dataset_version"] == 1
    assert after["dataset"]["narrative"] == before["dataset"]["narrative"]


def test_the_warnings_the_run_accumulated_are_carried(walked: ServiceContext) -> None:
    """Warnings are one of the bundle's stated contents, and this run has one.

    The walk declares blueprint 1.1.0 against a pin of 1.0.0, so
    ``blueprint_version_mismatch`` is on the stored run - and a grader reading
    only ``expected`` and ``actual`` would never learn the agent thought it was
    running a different version.
    """
    live = data(evidence(walked, RUN_ID))["evidence"]
    assert [item["code"] for item in live["warnings"]] == ["blueprint_version_mismatch"]
    assert live["run"]["declared_blueprint_version"] == "1.1.0"
    assert live["pin"]["blueprint_version"] == "1.0.0"


def test_the_run_block_does_not_repeat_the_hoisted_keys(walked: ServiceContext) -> None:
    """One spelling per fact. ``steps``, ``path``, ``outcome``, ``warnings``, ``pin``
    are top-level under a grader's name for them, so the nested run omits them."""
    live = data(evidence(walked, RUN_ID))["evidence"]
    assert set(live["run"]).isdisjoint({"steps", "path", "outcome", "warnings", "pin"})
    assert live["run"]["id"] == RUN_ID


def test_one_entry_per_served_step_including_both_pool_iterations(
    walked: ServiceContext,
) -> None:
    """``nodes`` is keyed by ``(node_id, iteration)``, not by blueprint node.

    ``request_docs`` was drawn twice, so it contributes two entries with
    different served fixtures - which is what makes a loop legible in the bundle
    and, one layer out, what makes "one span per step" mean per *step* rather
    than per node.
    """
    live = data(evidence(walked, RUN_ID))["evidence"]
    keys = [(node["node_id"], node["iteration"]) for node in live["nodes"]]
    assert keys.count(("request_docs", 0)) == 1
    assert keys.count(("request_docs", 1)) == 1
    drawn = [node["expected"] for node in live["nodes"] if node["node_id"] == "request_docs"]
    assert drawn[0] != drawn[1], "the two pool entries served the same fixture"
    assert len(keys) == len(set(keys)), keys


def test_the_node_kind_and_tool_name_come_from_the_blueprint(walked: ServiceContext) -> None:
    """Each step carries what the blueprint says the node *is*.

    A grader looking at a `tool_call` and a `decision` treats them differently,
    and reading `kind` off the blueprint is the only way the bundle can say
    which - the run records a node id and nothing about its nature.
    """
    live = data(evidence(walked, RUN_ID))["evidence"]
    kinds = {node["node_id"]: (node["kind"], node["tool_name"]) for node in live["nodes"]}
    assert kinds["check_docs"] == ("decision", None)
    assert kinds["fetch_store_profile"] == ("tool_call", "delightree.stores.get")
    assert kinds["complete"] == ("terminal", None)


def test_an_unknown_run_is_the_same_finding_run_get_gives(context: ServiceContext) -> None:
    """One spelling of "no such run" across every run-addressed tool.

    `no_run` went public at M10 for exactly this: two codes for one condition
    would make a caller branch on which tool it asked.
    """
    missing = evidence(context, "no-such-run-id")
    assert not missing.ok
    assert rules(missing) == rules(runs.get(context, "no-such-run-id"))


def test_a_run_with_no_steps_and_no_outcome_still_yields_a_bundle(
    context: ServiceContext,
) -> None:
    """The other end of the range: a run that started and did nothing.

    A DECISIONS entry reasoning about a boundary means testing both ends of it,
    and the empty end is where an assembler that assumed a finished run would
    raise instead of answering. ``actual`` is ``null``, ``nodes`` is empty, and
    the expectation and schema are there regardless - so a grader is told the
    agent produced nothing rather than being handed a truncated document.
    """
    seed(context)
    started = runs.start(context, "m10-empty-run", "location-onboarding", {"labels": {}})
    assert started.ok, started
    live = data(evidence(context, "m10-empty-run"))["evidence"]
    assert live["nodes"] == []
    assert live["actual"] is None
    assert live["path"]["actual"] == []
    assert live["expected"]["final"] and live["outcome_schema"]


def test_bundle_is_the_one_assembler_both_tools_use(walked: ServiceContext) -> None:
    """``run_export`` sends what ``run_evidence`` returns, because it is one function.

    A second assembler would be a second document to keep in step, and the trace
    would then be free to disagree with the evidence a grader read.
    """
    document, unresolved = bundle(walked, RUN_ID)
    assert unresolved == []
    assert document == data(evidence(walked, RUN_ID))["evidence"]


# -------------------------------------------------------------------- run_export


def test_an_unknown_target_is_ap_001_naming_the_vocabulary(walked: ServiceContext) -> None:
    """The closed vocabulary, reported the way ``run_class`` and ``status`` are."""
    refused = export(walked, RUN_ID, "braintrust")
    assert not refused.ok
    assert rules(refused) == [AP_ARGUMENT]
    assert findings(refused)[0].context["allowed"] == sorted(EXPORT_TARGETS)


def test_the_target_vocabulary_is_exactly_the_two_this_milestone_built() -> None:
    """Two, and Braintrust is deliberately not among them.

    PRD 5.4 names three destinations. A probe of Braintrust's documented OTLP
    contract found that its project and experiment linkage rides an
    ``x-bt-parent`` **header**, not a span attribute - so adding it is a
    credential-handling decision for the owner rather than an attribute overlay
    (`DECISIONS.md` M10 carries the probe). Asserted so that a third target is a
    visible edit here as well as in `evidence.py`.
    """
    assert {"otel", "langfuse"} == EXPORT_TARGETS


def test_an_unreachable_collector_is_ap_008_with_the_endpoint(
    walked: ServiceContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing listening means nothing published, and ``ok: false`` says so.

    ``ok: true`` would claim a publication the transport declined to give -
    ruling R-65's rule pointed outward. The endpoint is asserted to be *in* the
    finding because it is the only actionable thing about this failure (ruling
    R-60: the URL beside the count).
    """
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", DEAD_ENDPOINT)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "1")
    refused = export(walked, RUN_ID, "otel")
    assert not refused.ok
    assert rules(refused) == [AP_EXPORT_REFUSED]
    assert findings(refused)[0].context["endpoint"] == DEAD_ENDPOINT


def test_a_refused_export_writes_nothing_to_the_run(
    walked: ServiceContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run is untouched either way, and ``external_refs`` stays empty.

    ``run_export`` stores nothing at all - not on success and not on failure -
    because the trace id is derived from the run id and storing it would store a
    value the run already implies. This asserts the *failing* side, which is the
    one where a well-meaning "record that we tried" would be most tempting.
    """
    before = data(runs.get(walked, RUN_ID))["run"]
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", DEAD_ENDPOINT)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "1")
    assert not export(walked, RUN_ID, "otel").ok
    assert data(runs.get(walked, RUN_ID))["run"] == before
    assert before["external_refs"] == {}


def test_export_of_an_unknown_run_reports_the_run_before_the_collector(
    context: ServiceContext,
) -> None:
    """A missing run is answered without an export being attempted.

    Order matters: dialling a collector to publish a run that does not exist
    would report ``AP-008`` for a problem that is ``RT-E03``, and would do it
    after a network timeout.
    """
    refused = export(context, "no-such-run-id", "otel")
    assert not refused.ok
    assert rules(refused) == rules(runs.get(context, "no-such-run-id"))


def test_the_module_is_reachable_from_the_service_package() -> None:
    """`server/` imports only `service/`, so the module has to be exported there."""
    assert evidence_module.evidence is evidence
    assert evidence_module.export is export
