"""Langfuse dataset-run linkage, as OTLP attributes. **No Langfuse SDK.**

Ruling R-83, which is what this module implements rather than interprets:

> Implement the linkage as OTLP span and resource attributes that Langfuse reads
> on ingestion, not by adding a client library. Langfuse accepts OTLP; the stack
> already emits it; and PRD design principle 7 is "interoperate rather than
> replace - emit formats other tools consume." A vendor SDK would make one
> consumer a build dependency, which is the opposite of that.

So this file adds *names*, not a dependency. It is a pure function from the
`run_evidence` bundle plus a vendor-neutral :class:`~agentprops.export.otel.
TraceSpec` to the same trace carrying ``langfuse.*`` attributes as well. Nothing
here opens a socket, and nothing here handles a credential: an API key reaches
Langfuse through the OTLP exporter's own ``OTEL_EXPORTER_OTLP_HEADERS``
convention, outside this package entirely (ground rule 4).

What was probed, and what was not
---------------------------------

R-83 requires that a claim about what Langfuse does or does not accept arrive as
a probe against its **documented ingestion contract**, quoted, rather than as an
assertion (ruling R-79). The probe is recorded in `DECISIONS.md` under M10 with
its URLs; the contract it found is
https://langfuse.com/integrations/native/opentelemetry/experiments, "Ingest
experiment spans with OpenTelemetry", which models three levels:

- **experiment context**, shared by every item trace of one experiment -
  ``langfuse.experiment.id``, ``langfuse.experiment.name`` and
  ``langfuse.experiment.dataset.id`` required, with
  ``langfuse.experiment.description``, ``langfuse.experiment.metadata.*`` and
  ``langfuse.environment`` optional;
- **the experiment item root** - ``langfuse.observation.input``,
  ``langfuse.observation.output``, ``langfuse.experiment.item.id`` and
  ``langfuse.experiment.item.root_observation_id`` required, with
  ``langfuse.experiment.item.expected_output``,
  ``langfuse.experiment.item.version`` and
  ``langfuse.experiment.item.metadata.*`` optional;
- **item context**, propagated to the child spans of one item trace.

Two sentences from that page shape the span tree, and both agree with what
`otel.py` already built for its own reasons: "Langfuse needs a clear root span
to identify the item trace and its data", and "**Do not create an enclosing
'experiment' span.**" One trace per run, root span is the run, steps are its
children - which is exactly `otel.py`'s hierarchy, so the linkage needed no
change to it.

**Baggage is a propagation mechanism, not an ingestion format.** Langfuse
describes the experiment and item contexts as travelling in baggage, and their
own guidance on the trace-level attributes is that they "must propagate to all
spans for reliable filtering and aggregation across observations". Baggage is
how a *distributed* instrumentation gets an attribute onto a span it does not
build. This module builds every span in the trace from one stored run, so it
sets the attributes directly on each of them and needs no propagator - which is
the same end state, reached without a context manager.

**What I did not probe, stated plainly.** No trace was sent to a Langfuse
instance. Doing so needs a project and an API key, and ground rule 4 puts key
handling outside `src/` - so the attribute *names and shapes* here are checked
against the documented contract above and against a real OTLP collector, and the
claim "Langfuse ingests this as a dataset run" rests on their documentation
rather than on an observation of their server. If it turns out to need something
more, that is a finding about the contract and not about this code.

The mapping, and the one place it is a judgement
------------------------------------------------

Langfuse's dataset/experiment model and agent-props' blueprint/dataset/run model
line up almost exactly, once you see which noun matches which:

===============================  =========================================
``langfuse.experiment.dataset``  the **blueprint** - the agent whose
                                 scenarios these are (``agent_id``)
``langfuse.experiment.item``     one agent-props **dataset**, at the
                                 version the run pinned. One authored world
                                 is one item; its ``version`` is the
                                 lineage version, which their optional
                                 ``item.version`` field takes directly
the **item trace**               one agent-props **run**
``item.expected_output``         ``expected.final``
``observation.output``           the run's recorded ``outcome``
===============================  =========================================

The judgement is ``langfuse.experiment.id``. Langfuse wants it "unique per
experiment" and shared across the item traces that belong to one, and
agent-props has **no experiment or suite id** on a run to hand it - the Run
aggregate (contracts 2.3) carries an agent, a pin, a model, a run class and a
status, and nothing that groups runs. So it is *derived* from the tuple that
makes two runs comparable: ``(agent_id, blueprint_version, run_class, model)``.

That is PRD design principle 2 as an identity - "a difference between two runs
must be attributable to the model, not the world" - so an experiment here holds
the model and the blueprint fixed and varies the dataset item, which is the unit
the whole drift claim is about. It also means the id is a function of the run,
so two runs of one batch land in one experiment with nothing to coordinate.

Its cost is recorded rather than hidden: **two batches run a week apart against
the same model collapse into one experiment**, because a derived key has no way
to tell them apart and the only field that could is a clock read, which would
make the id non-reproducible. If separating them matters, the fix is an explicit
experiment id on the run - an owner decision and a phase-2 field, not something
to invent here.
"""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import blake2b
from typing import Any, Final

from agentprops.export.otel import (
    ATTR_PREFIX,
    AttributeValue,
    SpanSpec,
    TraceSpec,
    encoded,
    root_span_id_hex,
)

__all__ = [
    "LANGFUSE_ENVIRONMENT",
    "LANGFUSE_EXPERIMENT_DATASET_ID",
    "LANGFUSE_EXPERIMENT_DESCRIPTION",
    "LANGFUSE_EXPERIMENT_ID",
    "LANGFUSE_EXPERIMENT_NAME",
    "LANGFUSE_ITEM_EXPECTED_OUTPUT",
    "LANGFUSE_ITEM_ID",
    "LANGFUSE_ITEM_ROOT_OBSERVATION_ID",
    "LANGFUSE_ITEM_VERSION",
    "LANGFUSE_OBSERVATION_INPUT",
    "LANGFUSE_OBSERVATION_OUTPUT",
    "LANGFUSE_OBSERVATION_TYPE",
    "LANGFUSE_REQUIRED_EXPERIMENT_ATTRS",
    "LANGFUSE_REQUIRED_ITEM_ROOT_ATTRS",
    "LANGFUSE_TRACE_NAME",
    "LANGFUSE_TRACE_TAGS",
    "OBSERVATION_TYPES",
    "experiment_id",
    "experiment_name",
    "overlay",
]

#: Langfuse's experiment context. Spelled exactly as their documented ingestion
#: contract spells them, because a near-miss on an attribute name is a linkage
#: that silently does not happen.
LANGFUSE_EXPERIMENT_ID: Final = "langfuse.experiment.id"
LANGFUSE_EXPERIMENT_NAME: Final = "langfuse.experiment.name"
LANGFUSE_EXPERIMENT_DATASET_ID: Final = "langfuse.experiment.dataset.id"
LANGFUSE_EXPERIMENT_DESCRIPTION: Final = "langfuse.experiment.description"
LANGFUSE_ENVIRONMENT: Final = "langfuse.environment"

#: The experiment item root, which for agent-props is the run's root span.
LANGFUSE_ITEM_ID: Final = "langfuse.experiment.item.id"
LANGFUSE_ITEM_ROOT_OBSERVATION_ID: Final = "langfuse.experiment.item.root_observation_id"
LANGFUSE_ITEM_VERSION: Final = "langfuse.experiment.item.version"
LANGFUSE_ITEM_EXPECTED_OUTPUT: Final = "langfuse.experiment.item.expected_output"

#: Observation-level attributes, on every span.
LANGFUSE_OBSERVATION_TYPE: Final = "langfuse.observation.type"
LANGFUSE_OBSERVATION_INPUT: Final = "langfuse.observation.input"
LANGFUSE_OBSERVATION_OUTPUT: Final = "langfuse.observation.output"

#: Trace-level attributes, which their guidance says to set on every span of the
#: trace rather than on the root alone.
LANGFUSE_TRACE_NAME: Final = "langfuse.trace.name"
LANGFUSE_TRACE_TAGS: Final = "langfuse.trace.tags"

#: The attributes their contract marks **required** for an experiment context.
#: A frozenset rather than prose so `tests/unit/test_export_langfuse.py` can
#: assert every one is present on every span, which is the only way "the
#: linkage is complete" is a fact rather than an intention.
LANGFUSE_REQUIRED_EXPERIMENT_ATTRS: Final[frozenset[str]] = frozenset(
    {LANGFUSE_EXPERIMENT_ID, LANGFUSE_EXPERIMENT_NAME, LANGFUSE_EXPERIMENT_DATASET_ID}
)

#: The attributes their contract marks **required** on the item root span.
LANGFUSE_REQUIRED_ITEM_ROOT_ATTRS: Final[frozenset[str]] = frozenset(
    {
        LANGFUSE_OBSERVATION_INPUT,
        LANGFUSE_OBSERVATION_OUTPUT,
        LANGFUSE_ITEM_ID,
        LANGFUSE_ITEM_ROOT_OBSERVATION_ID,
    }
)

#: Which Langfuse observation type a blueprint node kind becomes.
#:
#: Their vocabulary is ``span, generation, event, embedding, agent, tool, chain,
#: retriever, guardrail, evaluator``; contracts 2.1's node kinds are
#: ``tool_call, llm, decision, loop, terminal``. Two map onto something
#: meaningful - a ``tool_call`` is a ``tool`` and an ``llm`` node is a
#: ``generation``, which is what makes token and model views work - and the
#: other three do **not**: a ``decision`` is not a ``chain``, a ``loop`` is not
#: an ``agent``, and a ``terminal`` node is not an ``event``. Those three stay
#: ``span``, the neutral type, because mapping them onto a nearby word would
#: make a Langfuse view claim something about the agent's structure that the
#: blueprint never said. The run's own root span is the ``agent``.
OBSERVATION_TYPES: Final[Mapping[str, str]] = {
    "tool_call": "tool",
    "llm": "generation",
    "decision": "span",
    "loop": "span",
    "terminal": "span",
}

#: What an unmapped or absent node kind becomes. Their neutral type.
DEFAULT_OBSERVATION_TYPE: Final = "span"

#: The root span's observation type. A run *is* an agent execution.
ROOT_OBSERVATION_TYPE: Final = "agent"

#: How the derived experiment id is spelled: a short digest of the comparability
#: tuple, prefixed so it is recognisable in a Langfuse UI as one of ours rather
#: than a random string.
_EXPERIMENT_ID_PREFIX: Final = "agentprops"
_EXPERIMENT_ID_BYTES: Final = 8


def experiment_id(evidence: Mapping[str, Any]) -> str:
    """The Langfuse experiment id for this run: derived, stable, shared by a batch.

    A digest of ``(agent_id, blueprint_version, run_class, provider, model,
    model version)`` - the tuple that makes two runs comparable. See the module
    docstring for why it is derived rather than carried, and for the cost.
    """
    material = "\x00".join(_comparability(evidence))
    digest = blake2b(material.encode(), digest_size=_EXPERIMENT_ID_BYTES).hexdigest()
    return f"{_EXPERIMENT_ID_PREFIX}-{digest}"


def experiment_name(evidence: Mapping[str, Any]) -> str:
    """A human-readable name for the same experiment, from the same tuple.

    Their contract wants the name unique per experiment as well as the id, so it
    is built from the identical inputs rather than from a subset - two
    experiments that differ only by model must not share a name, or a Langfuse
    reader comparing them sees one.
    """
    agent, blueprint, run_class, provider, model, model_version = _comparability(evidence)
    model_part = "/".join(part for part in (provider, model, model_version) if part) or "no-model"
    return f"{agent}@{blueprint} {run_class} {model_part}"


def overlay(spec: TraceSpec, evidence: Mapping[str, Any]) -> TraceSpec:
    """``spec`` with Langfuse's attributes added. The vendor-neutral ones survive.

    Additive on purpose: an OTLP consumer that has never heard of Langfuse still
    reads every ``agentprops.*`` attribute `otel.py` wrote, and the same export
    is legible to both. Langfuse deletes the ``langfuse.*`` keys it recognises
    on ingestion and keeps the rest, so nothing is lost in either direction.
    """
    shared = _shared_attributes(evidence)
    return spec.with_root(
        spec.root.with_attributes({**shared, **_root_attributes(spec, evidence)}).with_children(
            tuple(
                child.with_attributes({**shared, **_step_attributes(child)})
                for child in spec.root.children
            )
        )
    )


# ------------------------------------------------------------------- internals


def _comparability(evidence: Mapping[str, Any]) -> tuple[str, str, str, str, str, str]:
    """The six values an experiment's identity is made of, in a fixed order."""
    run = _mapping(evidence.get("run"))
    model = _mapping(run.get("model"))
    return (
        _text(run.get("agent_id")),
        _text(_mapping(evidence.get("pin")).get("blueprint_version")),
        _text(run.get("run_class")),
        _text(model.get("provider")),
        _text(model.get("name")),
        _text(model.get("version")),
    )


def _shared_attributes(evidence: Mapping[str, Any]) -> dict[str, AttributeValue]:
    """The experiment and item context: on **every** span, root and children.

    Their guidance is that trace-level attributes "must propagate to all spans
    for reliable filtering and aggregation across observations", and the
    experiment and item contexts are the two they describe as travelling in
    baggage for exactly that purpose. Setting them here is that requirement
    satisfied directly - see the module docstring on baggage.
    """
    run = _mapping(evidence.get("run"))
    pin = _mapping(evidence.get("pin"))
    dataset = _mapping(evidence.get("dataset"))
    found: dict[str, AttributeValue] = {
        LANGFUSE_EXPERIMENT_ID: experiment_id(evidence),
        LANGFUSE_EXPERIMENT_NAME: experiment_name(evidence),
        LANGFUSE_EXPERIMENT_DATASET_ID: _text(run.get("agent_id")),
        LANGFUSE_ITEM_ID: _text(pin.get("dataset_id")),
        LANGFUSE_ITEM_ROOT_OBSERVATION_ID: root_span_id_hex(_text(run.get("id"))),
        LANGFUSE_ITEM_VERSION: str(_integer(pin.get("dataset_version"))),
        LANGFUSE_TRACE_NAME: _text(dataset.get("title")) or _text(run.get("agent_id")),
        LANGFUSE_TRACE_TAGS: _tags(evidence),
        LANGFUSE_ENVIRONMENT: _text(run.get("run_class")),
    }
    if intent := _text(dataset.get("intent")):
        found[LANGFUSE_EXPERIMENT_DESCRIPTION] = intent
    return found


def _root_attributes(spec: TraceSpec, evidence: Mapping[str, Any]) -> dict[str, AttributeValue]:
    """The item root's own four required attributes, plus the expected output.

    ``root_observation_id`` "must equal the span's own ``spanId``", and it does
    by construction: `otel.py` derives the root span id from the run id, so both
    sides of that equality come from :func:`~agentprops.export.otel.
    derived_span_id` and `tests/unit/test_export_langfuse.py` asserts they
    agree.

    The item's **input** is the authored world the agent was given - the
    narrative and the labels - because that is what varies between the items of
    one agent-props experiment. The **output** is the recorded ``outcome``,
    verbatim, and ``expected_output`` is ``expected.final``. Note what that
    puts side by side in a Langfuse UI: the expectation an author wrote and the
    outcome an agent produced, with no verdict between them. Grading stays in
    the client (ground rule 2).
    """
    dataset = _mapping(evidence.get("dataset"))
    return {
        LANGFUSE_OBSERVATION_TYPE: ROOT_OBSERVATION_TYPE,
        LANGFUSE_ITEM_ROOT_OBSERVATION_ID: spec.root.span_id_hex,
        LANGFUSE_OBSERVATION_INPUT: encoded(
            {
                "narrative": _text(dataset.get("narrative")),
                "labels": _mapping(dataset.get("labels")),
            }
        ),
        LANGFUSE_OBSERVATION_OUTPUT: encoded(evidence.get("actual")),
        LANGFUSE_ITEM_EXPECTED_OUTPUT: encoded(
            _mapping(evidence.get("expected")).get("final"),
        ),
    }


def _step_attributes(child: SpanSpec) -> dict[str, AttributeValue]:
    """One step's observation type, input and output, read off its own span.

    Read from the vendor-neutral attributes rather than from the bundle a second
    time, which is the same reason `otel.py` takes the bundle: two projections
    of one fact can disagree, one cannot. The served fixture is the step's
    input - it is what the agent was handed - and the recorded ``actual`` is its
    output.
    """
    kind = str(child.attributes.get(f"{ATTR_PREFIX}.node.kind", ""))
    return {
        LANGFUSE_OBSERVATION_TYPE: OBSERVATION_TYPES.get(kind, DEFAULT_OBSERVATION_TYPE),
        LANGFUSE_OBSERVATION_INPUT: child.attributes.get(f"{ATTR_PREFIX}.step.served", ""),
        LANGFUSE_OBSERVATION_OUTPUT: child.attributes.get(f"{ATTR_PREFIX}.step.actual", ""),
    }


def _tags(evidence: Mapping[str, Any]) -> tuple[str, ...]:
    """``dimension:value`` for every label on the dataset, sorted.

    The dataset's labels are what a suite is sliced by (PRD 4), so they are the
    tags worth filtering a Langfuse view on. Sorted, because an attribute that
    reorders between two exports of one run makes a diff of two traces
    unreadable.
    """
    labels = _mapping(_mapping(evidence.get("dataset")).get("labels"))
    return tuple(sorted(f"{key}:{_text(value)}" for key, value in labels.items()))


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _integer(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
