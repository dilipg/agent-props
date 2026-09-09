"""One run of the worked example, walked the same way by every M10 consumer.

Three things need a *completed* run: the evidence-bundle test, the committed
bundle fixture that `tests/unit/test_evidence_grading.py` grades with no store,
and the collector test that watches the same run arrive as spans. If each built
its own run they would agree by coincidence, and the fixture in particular would
stop being evidence of what the service emits - which is M9's clause-1 lesson
("a harness that cannot build a reply the service sends manufactures
agreement") pointed at a golden file.

So the walk lives here, once. `scripts/make_evidence_fixture.py` regenerates the
fixture from this module and `tests/unit/test_service_evidence.py` asserts the
live bundle still equals it, so the two can only diverge by a *reviewed* edit.

What the walk does, and why each choice is in it
------------------------------------------------

`priya-missing-docs`, the loop-path golden dataset, walked in ``expected_path``
order with the one re-visit dropped - see below. Four details are deliberate,
because each one puts something in the bundle that would otherwise be absent
and untested:

- **``declared_blueprint_version`` is 1.1.0** against a pin of 1.0.0, so the run
  carries a real ``blueprint_version_mismatch`` warning. ``warnings`` is one of
  the bundle's required contents and an empty list would test nothing;
- **``run_class`` is ``eval`` and a model is declared**, so the model attributes
  and the Langfuse experiment identity have values to be derived from. A run
  with no model exercises the other branch and is built inline where that is the
  point;
- **``check_docs`` is fetched and never recorded**, because a decision node is
  exactly the step a real agent has no output to report at. That is the bundle's
  ``recorded: false`` and ``actual: null`` pair, which is the distinction
  contracts 2.2 draws when it says ``null`` is not absence;
- **``escalate`` is never fetched at all.** The dataset expects
  ``escalate: {"called": false}``, so a bundle that lists no ``escalate`` step is
  what satisfies that expectation - and a grader can only see it because the
  bundle is one entry per *served* step rather than one per blueprint node.

**The re-visit is dropped, and that is a finding rather than a shortcut.**
``expected_path`` visits ``check_docs`` twice, but ``fetch_step`` is idempotent
on ``(run_id, node_id, iteration)`` (M6's gate), so a second fetch of
``check_docs`` at iteration 0 records no second path entry. Walking it twice
would therefore produce the same run as walking it once. `service/evidence.py`
records the consequence: the reconstructed path can never equal this
``expected_path``, so the service must not compute a verdict about it.
"""

from __future__ import annotations

from typing import Any, Final

from agentprops.models import Dataset
from agentprops.service import ServiceContext, blueprints, runs
from agentprops.service.runs import STATUS_FINISHED
from conftest import BLUEPRINT_FIXTURE, DATASET_FIXTURE, load_document

AGENT: Final = "location-onboarding"

#: The run id, and it is fixed rather than generated: the trace id and every
#: span id are derived from it (`export/otel.py`), so a fixed run id is what
#: makes the committed bundle and the expected span ids reproducible. A real
#: client generates one with ``uuid4()``, which is ground rule 9's one
#: exemption and belongs in the client, not here.
RUN_ID: Final = "m10-priya-evidence"

#: `priya-missing-docs`, by explicit id. The same constant the M6 and M8 suites
#: use.
PRIYA: Final = "3f8c1a20-0000-4000-8000-000000000001"

#: What the run declares it is running, against a pin of 1.0.0. Produces the
#: ``blueprint_version_mismatch`` warning the bundle then carries.
DECLARED_VERSION: Final = "1.1.0"

#: The model under test. Recorded, never interpreted (contracts 2.3).
MODEL: Final[dict[str, str]] = {
    "provider": "anthropic",
    "name": "claude-opus-5",
    "version": "20260401",
}

RUN_CLASS: Final = "eval"

#: The steps, in order, as ``(node_id, iteration)``. Nine of them, so a trace of
#: this run has **ten** spans: one for the run and one per step.
STEPS: Final[tuple[tuple[str, int], ...]] = (
    ("receive_request", 0),
    ("fetch_store_profile", 0),
    ("check_docs", 0),
    ("request_docs", 0),
    ("request_docs", 1),
    ("recheck_store", 0),
    ("assign_training", 0),
    ("verify_compliance", 0),
    ("complete", 0),
)

#: The one step that is fetched and never recorded. See the module docstring.
UNRECORDED: Final = "check_docs"


def expected_final() -> dict[str, Any]:
    """`priya-missing-docs`'s ``expected.final``, read off the fixture.

    Read rather than copied, so a fixture edit cannot leave this module quietly
    asserting the old answer - the practice `test_service_run_writes.py`
    established.
    """
    document: dict[str, Any] = load_document(DATASET_FIXTURE)["expected"]["final"]
    return document


def seed(context: ServiceContext) -> ServiceContext:
    """Publish the golden blueprint and store `priya-missing-docs`. No run yet."""
    published = blueprints.upsert(context, load_document(BLUEPRINT_FIXTURE), publish=True)
    assert published.ok, published
    context.store.put_dataset(Dataset.model_validate(load_document(DATASET_FIXTURE)))
    return context


def walk(context: ServiceContext, run_id: str = RUN_ID) -> ServiceContext:
    """Seed, start, walk every step, record all but one, and finish. Asserts as it goes.

    Every reply is asserted ``ok`` here rather than in the callers: a walk that
    silently half-happened would produce a *shorter* bundle, and three suites
    would then agree about the wrong document.

    ``run_id`` defaults to :data:`RUN_ID` and the committed fixture depends on
    that default. The collector suite overrides it **per test**, and the reason
    is a real consequence of deriving the trace id from the run id: two exports
    of one run share a trace id, and the collector's output file appends, so a
    second test reading "the spans for this trace" would find the first test's
    as well. A distinct run id per test gives a distinct trace id, which makes
    the filter exact.
    """
    seed(context)
    started = runs.start(
        context,
        run_id,
        AGENT,
        {"dataset_id": PRIYA},
        DECLARED_VERSION,
        model=MODEL,
        run_class=RUN_CLASS,
    )
    assert started.ok, started
    for node_id, iteration in STEPS:
        served = runs.fetch_step(context, run_id, node_id=node_id, iteration=iteration)
        assert served.ok, served
        if node_id == UNRECORDED:
            continue
        fixture = served.data["step"]["fixture"]  # type: ignore[union-attr]
        recorded = runs.record_step(
            context, run_id, fixture["output"], node_id=node_id, iteration=iteration
        )
        assert recorded.ok, recorded
    finished = runs.finish(context, run_id, expected_final(), STATUS_FINISHED)
    assert finished.ok, finished
    return context


#: What :func:`normalised` puts in place of a database-stamped ``recorded_at``.
#:
#: A **valid** timestamp rather than a sentinel like ``"<stamped>"``, and that
#: is a considered trade. A sentinel would be unmistakable, but it would also
#: make the committed fixture a document the service can never emit - and
#: `test_export_otel.py` reads that fixture to build a trace, where an
#: unparseable stamp is not a bundle at all. So the mask is one second after
#: :data:`~conftest.FROZEN_NOW`, which is recognisable on sight because it is
#: the only stamp in the fixture that is not ``12:00:00``, and is a real instant
#: everywhere it is read.
STAMPED_BY_DATABASE: Final = "2026-09-08T12:00:01Z"


def normalised(bundle: dict[str, Any]) -> dict[str, Any]:
    """``bundle`` with the one non-reproducible field masked, and only that one.

    ``recorded_at`` is stamped by the **database** - ``func.now()`` on SQL,
    ``$$NOW`` on Mongo - which ruling R-09 sanctions ("a column default") and
    which the injected ``Clock`` therefore cannot freeze. So the committed
    fixture cannot be byte-stable on it, and both sides of the drift comparison
    mask it here rather than in either one of them.

    Masking is the compromise and it is a *named* one. The risk it creates is
    that a bundle which stopped carrying ``recorded_at`` at all would compare
    equal, so `tests/unit/test_service_evidence.py` asserts the live values
    separately: a real aware UTC timestamp on every recorded step, and ``None``
    on exactly the step that was never recorded. Nothing about the field is
    hidden - only its value, whose reproducibility is the property the field
    does not have.
    """
    return {
        **bundle,
        "nodes": [
            {**node, "recorded_at": None if node["recorded_at"] is None else STAMPED_BY_DATABASE}
            for node in bundle["nodes"]
        ],
    }
