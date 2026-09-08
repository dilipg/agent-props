"""Turning a request argument into a document - and DS-013's raw text (ruling R-20).

Two jobs, both of which have to happen before a rule can run.

Resolving the payload
---------------------

A document reaches the tool surface either as a JSON **object** or as raw JSON
**text**, and :func:`read_document` normalises both into one
:class:`Document`. Anything else - a list, a number, a missing argument - is a
boundary finding, never an exception.

Where R-20's duplicate-key detection actually lives, and what that cost
----------------------------------------------------------------------

Ruling R-20 keeps DS-013 ("no label dimension appears twice") and says to detect
it "at the **tool boundary**, where the raw text still exists, via
``json.loads(..., object_pairs_hook=...)``", because "the MCP boundary genuinely
does receive raw text".

**With the documented ``dataset: object`` signature, it does not.** Verified
against the installed `mcp` 2.2.0, three separate layers destroy a duplicate key
before any agentprops code runs:

1. the stdio transport parses each line with
   ``jsonrpc_message_adapter.validate_json`` (`mcp/server/stdio.py:189`), and the
   streamable-HTTP transport calls ``json.loads(body)``
   (`mcp/server/_streamable_http_modern.py:402`) - both plain parses, no hook,
   no interception point exposed;
2. the in-memory ``Client`` used by the tool-contract suite never has raw text
   at all - it passes Python objects straight through;
3. and if a caller sends the document as a JSON *string*, ``MCPServer`` itself
   pre-parses it with a bare ``json.loads``
   (`mcp/server/mcpserver/utilities/func_metadata.py:256`, ``pre_parse_json``),
   which runs for every parameter whose annotation is not literally ``str``.

That third layer is what closes the loophole and is worth spelling out, because
it is the one that looks like it should work. A tool parameter annotated
``object`` accepting the string ``'{"labels": {"persona": "a", "persona": "b"}}'``
receives ``{'labels': {'persona': 'b'}}`` - the SDK collapsed it.

So the raw text reaches a tool function **only** through a parameter whose
annotation is exactly ``str``, which ``pre_parse_json`` skips by construction.
``dataset_validate`` therefore takes an additional optional ``dataset_json:
str`` argument, and that argument is the R-20 boundary. It is additive to
contracts section 4 and optional, in the same class of refinement as R-05's six
missing types and R-32's missing column: a caller that has the raw text hands it
over and gets DS-013; a caller that has a parsed object cannot violate DS-013
anyway, because a ``dict`` cannot hold one key twice.

The alternative R-20 itself offers - "if the boundary hook proves awkward,
retire DS-013" - was not taken. The rule protects something real (a submitted
dataset silently losing a label value), the raw-text path is the one M7's
``dataset_import`` and M9's web app will both use, and one optional argument is
cheaper than removing a rule and its registry entry.

Constructing the model, after validation
----------------------------------------

:func:`parse` is the second half of ruling R-23's ordering: validate the raw
document, *then* construct the model. It exists as a function rather than a
``model_validate`` call at each site because the failure has to become findings
(see :func:`~agentprops.service.envelope.findings_from_validation_error`), and a
write path that forgot to catch ``ValidationError`` would be exactly the defect
R-07's second amendment recorded.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from agentprops.models import RuleError
from agentprops.service.envelope import (
    AP_ARGUMENT,
    AP_MALFORMED_JSON,
    boundary,
    field_pointer,
    findings_from_validation_error,
)
from agentprops.validation import parse_with_duplicate_keys

__all__ = ["Document", "parse", "read_document"]


@dataclass(frozen=True)
class Document:
    """A resolved document argument, plus whatever the raw text revealed.

    ``duplicate_keys`` is always empty when the payload arrived already parsed,
    and that is a fact about JSON rather than a limitation: a ``dict`` cannot
    hold one key twice, so there is nothing to find.
    """

    value: Mapping[str, Any]
    duplicate_keys: tuple[str, ...] = ()
    findings: tuple[RuleError, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.findings


def read_document(
    field_name: str, payload: object, *, text_field: str = "", text: str = ""
) -> Document:
    """Resolve a document argument into a :class:`Document`, or into findings.

    Exactly one of ``payload`` and ``text`` supplies the document. ``text`` wins
    nothing by being first or second - giving both is a boundary finding, so a
    caller is never left guessing which one the service read.

    ``text`` is parsed with
    :func:`~agentprops.validation.rawjson.parse_with_duplicate_keys`, which is
    the R-20 detector. Malformed text is :data:`AP_MALFORMED_JSON`, not a
    catalogue violation: a request that never became a document.
    """
    has_payload = payload is not None
    has_text = bool(text)
    if has_payload and has_text:
        return Document(
            value={},
            findings=(
                boundary(
                    AP_ARGUMENT,
                    field_pointer(text_field or field_name),
                    f"give either {field_name} or {text_field}, not both.",
                    given=[field_name, text_field],
                ),
            ),
        )
    if has_text:
        return _from_text(text_field or field_name, text)
    if isinstance(payload, Mapping):
        return Document(value=dict(payload))
    return Document(
        value={},
        findings=(
            boundary(
                AP_ARGUMENT,
                field_pointer(field_name),
                f"{field_name} must be a JSON object"
                + (f", or supply {text_field} as JSON text." if text_field else "."),
                given_type=_json_type(payload),
            ),
        ),
    )


def _from_text(field_name: str, text: str) -> Document:
    try:
        parsed, duplicates = parse_with_duplicate_keys(text)
    except json.JSONDecodeError as exc:
        return Document(
            value={},
            findings=(
                boundary(
                    AP_MALFORMED_JSON,
                    field_pointer(field_name),
                    f"{field_name} is not valid JSON: {exc.msg} at line {exc.lineno} "
                    f"column {exc.colno}.",
                    line=exc.lineno,
                    column=exc.colno,
                ),
            ),
        )
    if not isinstance(parsed, Mapping):
        return Document(
            value={},
            findings=(
                boundary(
                    AP_ARGUMENT,
                    field_pointer(field_name),
                    f"{field_name} must be a JSON object.",
                    given_type=_json_type(parsed),
                ),
            ),
        )
    return Document(value=dict(parsed), duplicate_keys=tuple(duplicates))


def parse[ModelT: BaseModel](
    model: type[ModelT], document: Mapping[str, Any], *, pool_nodes: frozenset[str] = frozenset()
) -> tuple[ModelT | None, list[RuleError]]:
    """Construct ``model`` from a validated document. Never raises.

    Ruling R-23's second step. Returns ``(instance, [])`` on success and
    ``(None, findings)`` otherwise, so the caller cannot forget to check: there
    is no instance to use until the findings are known to be empty.
    """
    try:
        return model.model_validate(document), []
    except ValidationError as exc:
        return None, findings_from_validation_error(exc, pool_nodes=pool_nodes)


def _json_type(value: object) -> str:
    """The JSON type name of ``value``, for a boundary finding's context."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list | tuple):
        return "array"
    return type(value).__name__
