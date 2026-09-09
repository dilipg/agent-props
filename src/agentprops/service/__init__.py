"""Orchestration layer. Storage-agnostic business logic.

Everything a tool needs and nothing a tool should do itself. `server/` imports
**only** this package - the ``Store`` Protocol and the ``Reply`` type are
re-exported here for that reason, so the layering rule in
`tests/unit/test_layering.py` holds for `server/` literally rather than with an
exemption.

The layout
----------

============================ =============================================
`clock.py`                   the one clock port (ruling R-09)
`resolver.py`                the real ``Resolver`` over a store (R-11)
`context.py`                 the two injected ports, together
`envelope.py`                the contracts section 1 envelopes, plus the
                             six ``AP-*`` boundary codes that are not rules
`limits.py`                  the integer range a store column can hold, the
                             clamp and predicate that keep values in it, and
                             the one page-defaulting helper both find tools use
`documents.py`               argument to document, and R-20's raw-text seam
`diff.py`                    ``blueprint_diff``'s pure computation
`blueprints.py`              the five blueprint tools
`datasets.py`                the five dataset tools M4 owns
`skeletons.py`               the authoring flow, and the one dataset write
`resolution.py`              contracts section 5, the step identity algorithm
`runs.py`                    the runtime read path, which writes only the run
`admin.py`                   the three admin reads
============================ =============================================

Three invariants this layer exists to hold
------------------------------------------

**It never gates** (ground rule 3). No function here returns a failure signal
for a policy problem. Mismatches come back as ``warnings`` on the success
envelope, ``blueprint_diff`` never fails, and ``dataset_get`` returns an
archived dataset.

**It never grades** (ground rule 2). There is no comparison logic anywhere in
this package. ``expected.comparison`` is stored and handed out; the three
comparison functions live in the Python client.

**Structured errors, never exceptions, for anything a user could cause.** Every
function returns a :data:`~agentprops.service.envelope.Reply`. The
``ValidationError`` a model can raise after the catalogue passed, and the two
programming-error guards an adapter can raise, are both translated into findings
here - which is the only place they *can* be translated, because `server/` may
not import `storage/` and would not know what it was catching.

Validate, then parse, then store (ruling R-23)
----------------------------------------------

The ordering is not a style preference: it is what makes ten rule ids reachable
at all. ``service/blueprints.py::upsert`` is the reference implementation and
its docstring walks the six steps.
"""

from agentprops.service.clock import Clock, FrozenClock, SystemClock
from agentprops.service.context import ServiceContext, context_from_url, sqlite_context
from agentprops.service.documents import Document, read_document
from agentprops.service.envelope import (
    AP_ARGUMENT,
    AP_DOCUMENT_SHAPE,
    AP_ID_SPACE_EXHAUSTED,
    AP_MALFORMED_JSON,
    AP_NOT_FOUND,
    AP_STORE_REFUSED,
    BOUNDARY_CODES,
    Reply,
    boundary,
    failure,
    field_pointer,
    success,
)
from agentprops.service.limits import MAX_STORED_INT, MIN_STORED_INT, clamp, storable
from agentprops.storage import Store

__all__ = [
    "AP_ARGUMENT",
    "AP_DOCUMENT_SHAPE",
    "AP_ID_SPACE_EXHAUSTED",
    "AP_MALFORMED_JSON",
    "AP_NOT_FOUND",
    "AP_STORE_REFUSED",
    "BOUNDARY_CODES",
    "MAX_STORED_INT",
    "MIN_STORED_INT",
    "Clock",
    "Document",
    "FrozenClock",
    "Reply",
    "ServiceContext",
    "Store",
    "SystemClock",
    "boundary",
    "clamp",
    "context_from_url",
    "failure",
    "field_pointer",
    "read_document",
    "sqlite_context",
    "storable",
    "success",
]
