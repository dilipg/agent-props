"""Pure Pydantic domain models. No I/O.

Every shape in `docs/contracts.md` section 2, plus the six types section 6's
storage ``Protocol`` names but never defines (ruling R-05), the five-section
skeleton manifest (R-06) and the fault shape (R-07).

Two properties of this package are enforced by `tests/unit/test_layering.py`
and are worth stating up front, because everything from M2 onward depends on
them:

**Models are shapes; the rule catalogue is policy** (ruling R-04). No model
here declares ``min_length``, ``max_length``, ``pattern``, ``ge``/``gt``, a
``Literal`` enum, or a cross-field validator. Encoding a catalogue constraint
here would make Pydantic raise where the validator must return a structured
rule id, and ten corpus cases would stop reporting one. Required-vs-optional
*is* structural and is used.

**Nothing here reads a clock, a random source or the filesystem** (rulings R-09
and R-10). Timestamp fields are carried, never defaulted; ``uuid.UUID`` is
imported as a type and a parser, and ``uuid4()`` is never called.

The JSON Schemas the web app validates against are emitted from
:class:`Blueprint` and :class:`Dataset` by ``python -m
agentprops.schema_export`` into `schemas/`. That emitter lives outside this
package because it writes files.
"""

from agentprops.models.base import StrictModel
from agentprops.models.blueprint import (
    Blueprint,
    BlueprintRef,
    BlueprintSummary,
    Edge,
    EntitySchema,
    Node,
)
from agentprops.models.dataset import (
    Author,
    Dataset,
    DatasetQuery,
    DatasetSummary,
    Entity,
    EntityRevision,
    ExpectedOutcome,
    FaultSpec,
    NodeExpectation,
    NodeFixture,
    Provenance,
)
from agentprops.models.errors import (
    ERROR_ENVELOPE_SEVERITIES,
    RT_E01,
    RT_E02,
    RT_E03,
    RT_E04,
    RUNTIME_ERROR_CODES,
    RUNTIME_WARNING_CODES,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    WARNING_BLUEPRINT_VERSION_MISMATCH,
    WARNING_DATASET_ARCHIVED,
    WARNING_POOL_EXHAUSTED,
    ErrorEnvelope,
    RuleError,
    SuccessEnvelope,
    Warning,
)
from agentprops.models.labels import Labels, LabelSchema
from agentprops.models.run import (
    ModelInfo,
    PathStep,
    Run,
    RunPin,
    RunQuery,
    RunSummary,
    StepRecord,
)
from agentprops.models.skeleton import SECTION_IDS, Section, Skeleton
from agentprops.models.storage import StoreCounts, StoreHealth

__all__ = [
    "ERROR_ENVELOPE_SEVERITIES",
    "RT_E01",
    "RT_E02",
    "RT_E03",
    "RT_E04",
    "RUNTIME_ERROR_CODES",
    "RUNTIME_WARNING_CODES",
    "SECTION_IDS",
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "WARNING_BLUEPRINT_VERSION_MISMATCH",
    "WARNING_DATASET_ARCHIVED",
    "WARNING_POOL_EXHAUSTED",
    "Author",
    "Blueprint",
    "BlueprintRef",
    "BlueprintSummary",
    "Dataset",
    "DatasetQuery",
    "DatasetSummary",
    "Edge",
    "Entity",
    "EntityRevision",
    "EntitySchema",
    "ErrorEnvelope",
    "ExpectedOutcome",
    "FaultSpec",
    "LabelSchema",
    "Labels",
    "ModelInfo",
    "Node",
    "NodeExpectation",
    "NodeFixture",
    "PathStep",
    "Provenance",
    "RuleError",
    "Run",
    "RunPin",
    "RunQuery",
    "RunSummary",
    "Section",
    "Skeleton",
    "StepRecord",
    "StoreCounts",
    "StoreHealth",
    "StrictModel",
    "SuccessEnvelope",
    "Warning",
]
