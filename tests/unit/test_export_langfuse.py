"""The Langfuse linkage: their documented attribute names, on the spans that carry them.

Ruling R-83 decides the mechanism - "OTLP span and resource attributes that
Langfuse reads on ingestion, not ... a client library" - so what is left to test
is whether the attributes are actually there, spelled the way their contract
spells them, on the spans it says to put them on.

The probe this file is written against
--------------------------------------

R-79 requires a claim about a third party to arrive as a probe against the
documented contract rather than as an assertion. The probe is
https://langfuse.com/integrations/native/opentelemetry/experiments ("Ingest
experiment spans with OpenTelemetry"), and `DECISIONS.md`'s M10 entry quotes it
with the URLs. Its three levels are:

- **experiment context** - ``langfuse.experiment.id``,
  ``langfuse.experiment.name``, ``langfuse.experiment.dataset.id`` required;
- **experiment item root** - ``langfuse.observation.input``,
  ``langfuse.observation.output``, ``langfuse.experiment.item.id``,
  ``langfuse.experiment.item.root_observation_id`` required, the last of which
  "must equal the span's own ``spanId``";
- **item context**, propagated to the child spans, because trace-level
  attributes "must propagate to all spans for reliable filtering and aggregation
  across observations".

:data:`~agentprops.export.langfuse.LANGFUSE_REQUIRED_EXPERIMENT_ATTRS` and
:data:`~agentprops.export.langfuse.LANGFUSE_REQUIRED_ITEM_ROOT_ATTRS` are those
two required lists as data, and the tests below assert them against the realised
spans - so "the linkage is complete" is a measurement rather than an intention.

**What is not tested here, stated plainly.** No trace was sent to a Langfuse
instance: that needs a project and an API key, and ground rule 4 puts key
handling outside `src/`. So these tests establish that the attributes we emit
match the contract as documented and that they survive a real OTLP round trip
(`tests/integration/test_otel_collector.py`); they do not establish that
Langfuse's server accepts them. That distinction is the honest form R-79 asks
for and it belongs in the report as well as here.

**And no dependency.** :func:`test_no_langfuse_package_is_imported_anywhere`
measures the actual import graph of the whole service in a fresh interpreter,
because "we did not add an SDK" is the kind of claim a stray import makes false
without anything failing.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

import pytest
from opentelemetry.sdk.trace import ReadableSpan

from agentprops.export.langfuse import (
    DEFAULT_OBSERVATION_TYPE,
    LANGFUSE_ENVIRONMENT,
    LANGFUSE_EXPERIMENT_DATASET_ID,
    LANGFUSE_EXPERIMENT_DESCRIPTION,
    LANGFUSE_EXPERIMENT_ID,
    LANGFUSE_ITEM_EXPECTED_OUTPUT,
    LANGFUSE_ITEM_ID,
    LANGFUSE_ITEM_ROOT_OBSERVATION_ID,
    LANGFUSE_ITEM_VERSION,
    LANGFUSE_OBSERVATION_INPUT,
    LANGFUSE_OBSERVATION_OUTPUT,
    LANGFUSE_OBSERVATION_TYPE,
    LANGFUSE_REQUIRED_EXPERIMENT_ATTRS,
    LANGFUSE_REQUIRED_ITEM_ROOT_ATTRS,
    LANGFUSE_TRACE_NAME,
    LANGFUSE_TRACE_TAGS,
    OBSERVATION_TYPES,
    ROOT_OBSERVATION_TYPE,
    experiment_id,
    experiment_name,
    overlay,
)
from agentprops.export.otel import ATTR_PREFIX, encoded, readable_spans, trace_of
from conftest import FIXTURES_DIR
from evidencewalk import RUN_ID

FIXTURE: Final[Path] = FIXTURES_DIR / "evidence" / "priya-missing-docs-run.json"


@pytest.fixture
def bundle() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.fixture
def spans(bundle: dict[str, Any]) -> tuple[ReadableSpan, ...]:
    """The golden run's spans **with** the Langfuse overlay applied."""
    return readable_spans(overlay(trace_of(bundle), bundle))


def attributes(span: ReadableSpan) -> dict[str, Any]:
    return dict(span.attributes or {})


# --------------------------------------------------------- the required contract


def test_every_required_experiment_attribute_is_on_every_span(
    spans: tuple[ReadableSpan, ...],
) -> None:
    """Their contract's experiment context, on all ten spans.

    "Trace-level attributes must propagate to all spans for reliable filtering
    and aggregation across observations." Their SDK does that with baggage; this
    module builds every span itself and sets them directly, which is the same
    end state (see `export/langfuse.py`'s docstring on baggage).

    Non-empty as well as present: an attribute holding ``""`` satisfies a
    key-set check and links nothing.
    """
    assert len(spans) == 10
    for span in spans:
        found = attributes(span)
        for name in sorted(LANGFUSE_REQUIRED_EXPERIMENT_ATTRS):
            assert name in found, f"{span.name} is missing {name}"
            assert found[name], f"{span.name} has an empty {name}"


def test_every_required_item_root_attribute_is_on_the_root_span(
    spans: tuple[ReadableSpan, ...],
) -> None:
    """Their contract's item root. "Langfuse needs a clear root span to identify
    the item trace and its data." """
    found = attributes(spans[0])
    for name in sorted(LANGFUSE_REQUIRED_ITEM_ROOT_ATTRS):
        assert name in found, f"the root span is missing {name}"
        assert found[name], f"the root span has an empty {name}"


def test_the_root_observation_id_equals_the_root_spans_own_span_id(
    spans: tuple[ReadableSpan, ...],
) -> None:
    """Their contract: it "must equal the span's own ``spanId``".

    Satisfied by construction rather than by inspection - both sides come from
    ``derived_span_id`` - which is why the child spans can carry the same value
    without the trace having been built first.
    """
    root = spans[0]
    assert root.context is not None
    expected = f"{root.context.span_id:016x}"
    for span in spans:
        assert attributes(span)[LANGFUSE_ITEM_ROOT_OBSERVATION_ID] == expected, span.name


def test_the_trace_is_one_item_with_no_enclosing_experiment_span(
    spans: tuple[ReadableSpan, ...],
) -> None:
    """Their contract: "Do not create an enclosing 'experiment' span."

    One trace per run, the root is the item, and nothing wraps it - which is the
    hierarchy `export/otel.py` chose on its own grounds, so the overlay needed
    no change to it.
    """
    assert spans[0].parent is None
    assert attributes(spans[0])[LANGFUSE_OBSERVATION_TYPE] == ROOT_OBSERVATION_TYPE
    assert all(span.parent is not None for span in spans[1:])


# ------------------------------------------------------------------ the mapping


def test_the_dataset_is_the_agent_and_the_item_is_the_pinned_dataset(
    bundle: dict[str, Any], spans: tuple[ReadableSpan, ...]
) -> None:
    """The noun mapping, which is the whole of "dataset-run linkage" here.

    A Langfuse *dataset* is the blueprint - the collection of scenarios for one
    agent - and a Langfuse dataset *item* is one agent-props dataset at the
    version the run pinned. Their optional ``item.version`` takes the lineage
    version directly, which is why the item id is the lineage id and not
    ``id@version``.
    """
    found = attributes(spans[0])
    assert found[LANGFUSE_EXPERIMENT_DATASET_ID] == bundle["run"]["agent_id"]
    assert found[LANGFUSE_ITEM_ID] == bundle["pin"]["dataset_id"]
    assert found[LANGFUSE_ITEM_VERSION] == str(bundle["pin"]["dataset_version"])


def test_the_expectation_and_the_outcome_are_side_by_side_with_no_verdict(
    bundle: dict[str, Any], spans: tuple[ReadableSpan, ...]
) -> None:
    """``item.expected_output`` is ``expected.final``; ``observation.output`` is the
    recorded outcome.

    Their optional expected-output field is an exact fit for an authored
    expectation, and putting the two next to each other in a Langfuse UI with
    **nothing between them** is ground rule 2 surviving the trip: the service
    hands over both documents and grades neither.
    """
    found = attributes(spans[0])
    assert found[LANGFUSE_ITEM_EXPECTED_OUTPUT] == encoded(bundle["expected"]["final"])
    assert found[LANGFUSE_OBSERVATION_OUTPUT] == encoded(bundle["actual"])

    # The golden run produced exactly what was expected, so the two attributes
    # hold the same string - which on its own could not tell one field from the
    # other. Perturbing the outcome separates them, and confirms that the
    # expectation is not derived from the actual.
    diverged = copy.deepcopy(bundle)
    diverged["actual"] = {"onboarding_status": "escalated", "outstanding_tasks": 4}
    moved = attributes(readable_spans(overlay(trace_of(diverged), diverged))[0])
    assert moved[LANGFUSE_ITEM_EXPECTED_OUTPUT] == found[LANGFUSE_ITEM_EXPECTED_OUTPUT]
    assert moved[LANGFUSE_OBSERVATION_OUTPUT] != found[LANGFUSE_OBSERVATION_OUTPUT]


def test_the_item_input_is_the_authored_world(
    bundle: dict[str, Any], spans: tuple[ReadableSpan, ...]
) -> None:
    """The narrative and the labels: what varies between the items of one experiment."""
    found = json.loads(attributes(spans[0])[LANGFUSE_OBSERVATION_INPUT])
    assert found["narrative"] == bundle["dataset"]["narrative"]
    assert found["labels"] == bundle["dataset"]["labels"]


def test_a_steps_input_and_output_are_the_fixture_and_the_actual(
    spans: tuple[ReadableSpan, ...],
) -> None:
    """Read off the vendor-neutral attributes, so the two cannot disagree.

    The served fixture is what the agent was handed, and the recorded ``actual``
    is what it produced - so a Langfuse observation shows the same pair the
    bundle's ``nodes`` entry does.
    """
    for span in spans[1:]:
        found = attributes(span)
        assert found[LANGFUSE_OBSERVATION_INPUT] == found[f"{ATTR_PREFIX}.step.served"]
        assert found[LANGFUSE_OBSERVATION_OUTPUT] == found[f"{ATTR_PREFIX}.step.actual"]


def test_the_node_kind_becomes_a_langfuse_observation_type(
    spans: tuple[ReadableSpan, ...],
) -> None:
    """``tool_call`` is a ``tool`` and an ``llm`` node is a ``generation``.

    The other three kinds stay ``span``, deliberately: a ``decision`` is not a
    ``chain`` and a ``terminal`` node is not an ``event``, and mapping them onto
    a nearby word would make a Langfuse view claim something about the agent's
    structure the blueprint never said.
    """
    types = {span.name: attributes(span)[LANGFUSE_OBSERVATION_TYPE] for span in spans[1:]}
    assert types["step fetch_store_profile"] == "tool"
    assert types["step check_docs"] == DEFAULT_OBSERVATION_TYPE
    assert types["step complete"] == DEFAULT_OBSERVATION_TYPE
    assert OBSERVATION_TYPES["llm"] == "generation"
    assert set(OBSERVATION_TYPES) == {"tool_call", "llm", "decision", "loop", "terminal"}


def test_an_unknown_node_kind_falls_back_to_the_neutral_type(bundle: dict[str, Any]) -> None:
    """A kind this mapping has never heard of must not become a guess.

    contracts 2.1's vocabulary is closed today; a future kind arriving as
    ``span`` is legible, and arriving as ``agent`` or ``guardrail`` would be a
    fabrication.
    """
    for node in bundle["nodes"]:
        node["kind"] = "sorcery"
    spans = readable_spans(overlay(trace_of(bundle), bundle))
    assert {attributes(span)[LANGFUSE_OBSERVATION_TYPE] for span in spans[1:]} == {
        DEFAULT_OBSERVATION_TYPE
    }


def test_the_trace_name_and_tags_are_what_a_reader_filters_on(
    bundle: dict[str, Any], spans: tuple[ReadableSpan, ...]
) -> None:
    """The dataset's title names the trace; its labels become sorted tags.

    Sorted, because an attribute that reorders between two exports of one run
    makes a diff of two traces unreadable.
    """
    found = attributes(spans[0])
    assert found[LANGFUSE_TRACE_NAME] == bundle["dataset"]["title"]
    assert found[LANGFUSE_TRACE_TAGS] == tuple(
        sorted(f"{key}:{value}" for key, value in bundle["dataset"]["labels"].items())
    )
    assert found[LANGFUSE_ENVIRONMENT] == bundle["run"]["run_class"]
    assert found[LANGFUSE_EXPERIMENT_DESCRIPTION] == bundle["dataset"]["intent"]


# ------------------------------------------------------- the derived experiment


def test_the_experiment_id_is_shared_by_runs_that_differ_only_by_dataset(
    bundle: dict[str, Any],
) -> None:
    """The property their contract needs: "unique per experiment", shared by its items.

    An experiment here holds the agent, the blueprint version, the run class and
    the model fixed and varies the **dataset item** - PRD design principle 2 as
    an identity, since that is the arrangement in which a difference between two
    runs is attributable to the model rather than to the world.
    """
    other = copy.deepcopy(bundle)
    other["pin"]["dataset_id"] = "11111111-0000-4000-8000-000000000002"
    other["run"]["id"] = "m10-other-run"
    assert experiment_id(other) == experiment_id(bundle)
    assert experiment_name(other) == experiment_name(bundle)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        pytest.param(("run", "agent_id"), "other-agent", id="agent"),
        pytest.param(("pin", "blueprint_version"), "2.0.0", id="blueprint-version"),
        pytest.param(("run", "run_class"), "load", id="run-class"),
    ],
)
def test_the_experiment_id_and_name_change_with_every_part_of_the_tuple(
    bundle: dict[str, Any], path: tuple[str, str], value: str
) -> None:
    """Every input, not just the ones that were convenient to test.

    The name has to change too, because their contract wants it unique per
    experiment as well - two experiments differing only by model must not share
    a name, or a reader comparing them sees one.
    """
    other = copy.deepcopy(bundle)
    other[path[0]][path[1]] = value
    assert experiment_id(other) != experiment_id(bundle)
    assert experiment_name(other) != experiment_name(bundle)


def test_the_model_is_part_of_the_experiment_identity(bundle: dict[str, Any]) -> None:
    """The whole point of the grouping: same world, different model, different experiment."""
    other = copy.deepcopy(bundle)
    other["run"]["model"] = {"provider": "anthropic", "name": "claude-sonnet-5", "version": "1"}
    assert experiment_id(other) != experiment_id(bundle)
    assert "claude-sonnet-5" in experiment_name(other)


def test_a_run_with_no_model_still_gets_an_experiment(bundle: dict[str, Any]) -> None:
    """The other end of the boundary. ``model`` is optional on a run.

    An experiment id is required by their contract, so a modelless run cannot be
    left without one; the name says ``no-model`` rather than trailing an empty
    segment, because a name is read by a person.
    """
    bundle["run"]["model"] = None
    assert experiment_id(bundle)
    assert experiment_name(bundle).endswith("no-model")
    spans = readable_spans(overlay(trace_of(bundle), bundle))
    for span in spans:
        assert attributes(span)[LANGFUSE_EXPERIMENT_ID]


def test_the_experiment_id_is_recognisably_ours(bundle: dict[str, Any]) -> None:
    """Prefixed, so it reads as one of ours in a Langfuse UI rather than as noise."""
    assert experiment_id(bundle).startswith("agentprops-")
    assert len(experiment_id(bundle)) == len("agentprops-") + 16


# ------------------------------------------------------------------- the overlay


def test_the_overlay_is_additive(bundle: dict[str, Any]) -> None:
    """Every vendor-neutral attribute survives, so one export serves both readers.

    Langfuse deletes the ``langfuse.*`` keys it recognises on ingestion and keeps
    the rest, and a Jaeger or Tempo reader has never heard of either - so the
    same trace has to be legible to both or the two targets would need two
    exports.
    """
    plain = trace_of(bundle)
    linked = overlay(plain, bundle)
    for before, after in zip(plain.spans(), linked.spans(), strict=True):
        assert set(before.attributes) <= set(after.attributes), before.name
        for key, value in before.attributes.items():
            assert after.attributes[key] == value, f"{before.name}: {key}"
        assert after.attributes.keys() - before.attributes.keys(), before.name


def test_the_overlay_does_not_mutate_the_neutral_trace(bundle: dict[str, Any]) -> None:
    """``SpanSpec`` is frozen; the overlay copies. Otherwise a caller exporting to
    both targets would send Langfuse attributes to a vendor-neutral collector."""
    plain = trace_of(bundle)
    before = [dict(span.attributes) for span in plain.spans()]
    overlay(plain, bundle)
    assert [dict(span.attributes) for span in plain.spans()] == before


def test_the_overlay_changes_no_id_no_name_and_no_window(bundle: dict[str, Any]) -> None:
    """A linked export and a neutral one are the same trace, differently annotated.

    So a caller can export the same run to both targets and get one trace in
    each backend rather than two traces that cannot be lined up.
    """
    plain, linked = trace_of(bundle), overlay(trace_of(bundle), bundle)
    assert plain.trace_id == linked.trace_id
    assert dict(plain.resource) == dict(linked.resource)
    for before, after in zip(plain.spans(), linked.spans(), strict=True):
        assert (before.name, before.span_id, before.start_ns, before.end_ns) == (
            after.name,
            after.span_id,
            after.start_ns,
            after.end_ns,
        )


def test_no_langfuse_attribute_is_dropped_by_the_sdk(bundle: dict[str, Any]) -> None:
    """The same silent-loss hazard `test_export_otel.py` guards, on the overlay.

    An attribute whose value is not a scalar or a homogeneous sequence is
    discarded with nothing but a log line - and every ``langfuse.*`` value here
    is a JSON *string* precisely because of that, so this is the assertion that
    the encoding actually happened.
    """
    spec = overlay(trace_of(bundle), bundle)
    for wanted, span in zip(spec.spans(), readable_spans(spec), strict=True):
        lost = set(wanted.attributes) - set(attributes(span))
        assert not lost, f"{wanted.name} lost {sorted(lost)}"


# ------------------------------------------------------------- and no dependency


def test_no_langfuse_package_is_imported_anywhere() -> None:
    """Ruling R-83's constraint, measured on the import graph rather than promised.

    A fresh interpreter, the whole export package imported, and no module whose
    name mentions Langfuse or Braintrust in ``sys.modules``. Reading
    `pyproject.toml` would be weaker: a transitive dependency could pull one in
    without ever appearing there.
    """
    probe = (
        "import sys, json\n"
        "import agentprops.export.langfuse\n"
        "import agentprops.export.otel\n"
        "json.dump(sorted(sys.modules), sys.stdout)\n"
    )
    finished = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )
    assert finished.returncode == 0, finished.stderr
    loaded = json.loads(finished.stdout)
    vendors = [name for name in loaded if "langfuse" in name.lower() and name.startswith("l")]
    assert vendors == [], vendors
    assert [name for name in loaded if "braintrust" in name.lower()] == []
    assert "agentprops.export.langfuse" in loaded, "the probe imported nothing"


def test_the_langfuse_module_reaches_only_the_neutral_one(bundle: dict[str, Any]) -> None:
    """`langfuse.py` -> `otel.py` -> nothing of this package. Asserted here as a use.

    `test_layering.py` owns the mechanical form over the whole package; this is
    the same fact from the caller's side, and it is what lets the overlay be a
    pure function of a bundle and a spec.
    """
    assert overlay(trace_of(bundle), bundle) is not None
    assert experiment_id({}) and experiment_name({}), "an empty bundle must still answer"
    assert RUN_ID not in experiment_id(bundle), "the run id must not enter the experiment key"
