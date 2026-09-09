"""Outward publishing: OpenTelemetry spans and Langfuse linkage.

PRD design principle 7 is this package's whole thesis - "**interoperate rather
than replace.** Emit formats other tools consume" - and M10 is where it becomes
code. Nothing here replaces an eval platform: `otel.py` turns a recorded run
into an OTLP trace, and `langfuse.py` adds the attribute names that make that
trace arrive at Langfuse as a **dataset run** rather than as an anonymous
trace.

Two properties hold across both modules, and both are asserted rather than
described.

**This package imports no other layer of agent-props.** Not `models/`, not
`service/`, not `storage/`. Its input is the ``run_evidence`` bundle - a plain
mapping - so a trace cannot disagree with the evidence a grader read, because
they are the same document. `tests/unit/test_layering.py` enumerates the
forbidden imports from the package list rather than from a literal, the way it
does for `expansion/`.

**No vendor client, and no credential.** Ruling R-83: the Langfuse linkage
"rides OTLP; do not add a Langfuse dependency", so `langfuse.py` contributes
attribute *names* and not a package. An API key reaches a collector through the
OpenTelemetry SDK's own ``OTEL_EXPORTER_OTLP_HEADERS`` convention, which no
module in `src/` reads (ground rule 4). The one function here that opens a
socket is :func:`~agentprops.export.otel.emit`; everything else is pure.
"""

from agentprops.export import langfuse, otel

__all__ = ["langfuse", "otel"]
