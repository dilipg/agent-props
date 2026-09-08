"""Argument coercion, and why every tool parameter is annotated ``object``.

A tool function "parses, delegates, shapes the response, and stays under 20
lines" (CLAUDE.md). This module is the *parses* half, factored out so that each
tool is one line per argument and so that "a malformed argument comes back as an
envelope" is true everywhere rather than remembered case by case.

The permissive annotation, and what it buys
-------------------------------------------

Every tool parameter is annotated ``object`` and carries its real JSON type in
``json_schema_extra``. That looks backwards, and the reason it is not is
mechanical: ``MCPServer`` builds a Pydantic model from the tool's signature and
validates the arguments against it *before* calling the function. With
``agent_id: str``, a caller sending ``agent_id: 123`` gets

    isError: true, "Error executing tool blueprint_get: 1 validation error ..."

- an MCP protocol error carrying a Pydantic message. That is not the section 1
  envelope, and "a malformed argument" is on the list of things CLAUDE.md
  requires an envelope for. With ``object``, the SDK accepts the value, the tool
  receives it, and :class:`ArgReader` reports ``AP-001`` with a pointer at the
  argument name.

``json_schema_extra`` is what keeps the published input schema honest for the
LLM callers this surface exists for. Verified against `mcp` 2.2.0:
``Annotated[object, Field(json_schema_extra={"type": "string"})]`` publishes
``{"type": "string", "title": "Agent Id"}`` while accepting anything at
runtime. The advertised contract is therefore *stricter* than the
implementation, which is the safe direction: a caller that follows the schema
never sees ``AP-001``, and a caller that does not gets a structured answer
instead of a stack trace.

One parameter cannot use this, and it is ``dataset_json``
---------------------------------------------------------

``MCPServer`` pre-parses any string argument whose annotation is not literally
``str`` (``func_metadata.pre_parse_json``), which destroys the duplicate keys
DS-013 exists to find. So ``dataset_json`` is annotated ``str`` - the one
parameter on this surface where a wrong-typed value is rejected by the SDK
rather than by an envelope. `service/documents.py` carries the full evidence for
why that trade has to be made.

Booleans are not integers here
------------------------------

:meth:`ArgReader.number` rejects ``True``. Python makes ``isinstance(True, int)``
true, and the same trap already cost a rule: ``validation.context.as_int``
excludes ``bool`` explicitly so DS-020 does not accept ``"seed": true``. A
``limit`` of ``true`` silently becoming ``1`` is the same class of bug one layer
out.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import Field

from agentprops.models import RuleError
from agentprops.service import AP_ARGUMENT, Reply, boundary, field_pointer, storable

__all__ = [
    "ArgReader",
    "BoolArg",
    "IntArg",
    "ObjectArg",
    "OptionalObjectArg",
    "OptionalTextArg",
    "RequiredIntArg",
    "TextArg",
    "reply",
]

#: A required string, advertised as one.
TextArg = Annotated[object, Field(json_schema_extra={"type": "string"})]

#: An optional string. ``None`` means "not given".
OptionalTextArg = Annotated[object, Field(json_schema_extra={"type": ["string", "null"]})]

#: A JSON object.
ObjectArg = Annotated[object, Field(json_schema_extra={"type": "object"})]

#: An optional JSON object.
OptionalObjectArg = Annotated[object, Field(json_schema_extra={"type": ["object", "null"]})]

#: A boolean.
BoolArg = Annotated[object, Field(json_schema_extra={"type": "boolean"})]

#: An optional integer.
IntArg = Annotated[object, Field(json_schema_extra={"type": ["integer", "null"]})]

#: A required integer, advertised as one. Separate from :data:`IntArg` because
#: the published schema is what an LLM caller reads: ``dataset_skeleton``'s
#: ``seed`` has no default and admitting ``null`` in its type would advertise
#: an option the tool then rejects.
RequiredIntArg = Annotated[object, Field(json_schema_extra={"type": "integer"})]


def reply(envelope: Reply) -> dict[str, Any]:
    """The wire form of an envelope: ``model_dump(mode="json")``.

    The whole of a tool function's "shapes the response" step. Key order is the
    model's field order, and no value is a clock reading, so repeated identical
    calls serialise byte-identically - which is M4's determinism criterion.
    """
    payload: dict[str, Any] = envelope.model_dump(mode="json")
    return payload


class ArgReader:
    """Coerces tool arguments, collecting findings instead of raising.

    Usage is always the same three steps, and the middle one is why the readers
    return a usable value even on failure - so that a tool reads its arguments
    linearly and checks once::

        args = ArgReader()
        agent_id = args.text("agent_id", agent_id)
        version = args.optional_text("version", version)
        if args.errors:
            return reply(failure(args.errors))

    A reader that rejected by returning ``None`` would push a ``| None`` into
    every downstream signature for a value the early return has already made
    unreachable.
    """

    def __init__(self) -> None:
        self.errors: list[RuleError] = []

    def text(self, name: str, value: object) -> str:
        """A required string."""
        if isinstance(value, str):
            return value
        self._reject(name, value, "a string")
        return ""

    def optional_text(self, name: str, value: object) -> str | None:
        """A string, or ``None`` when the argument was omitted."""
        if value is None or isinstance(value, str):
            return value
        self._reject(name, value, "a string or null")
        return None

    def flag(self, name: str, value: object, *, default: bool = False) -> bool:
        """A boolean. An omitted argument takes ``default``."""
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        self._reject(name, value, "a boolean")
        return default

    def number(self, name: str, value: object) -> int | None:
        """An integer, or ``None`` when omitted. ``true`` is not an integer."""
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            self._reject(name, value, "an integer or null")
            return None
        return value

    def integer(self, name: str, value: object) -> int:
        """A required integer, inside the range a store column can hold.

        Separate from :meth:`number`, which returns ``None`` for an omitted
        optional argument. A required argument that is absent has to be
        ``AP-001``, and ``0`` is a legitimate ``seed``, so "absent" and "zero"
        cannot share a return value.

        ``true`` is not an integer here, for the reason the module docstring
        gives.

        **The range check is ruling R-50, and it is not optional.** Python
        integers are unbounded and every backend's integer column is signed
        64-bit, so an out-of-range value reaches the driver and raises -
        ``OverflowError`` from pysqlite, which the SDK turns into a protocol
        error rather than the section 1 envelope. That is the M4 blocker
        (`limit`, `offset`, `version`) repeated at M5 on `seed`, which is why
        R-50 asks for a guard as well as a fix:
        `test_bounded_integers.py` enumerates every integer parameter on the
        **registered surface** and asserts each one answers an envelope at both
        ends of the range.

        An out-of-range value is **rejected**, not clamped, and that asymmetry
        with :func:`~agentprops.service.limits.clamp` is the point.
        ``clamp`` is for a *quantity* - a `limit` of ``2**64`` means the same as
        the largest representable one. A `seed` is neither a quantity nor an
        identifier of an existing row: it is an authored value that every
        derived id depends on, so silently substituting a nearby one would
        author a dataset the caller did not ask for.
        """
        if isinstance(value, bool) or not isinstance(value, int):
            self._reject(name, value, "an integer")
            return 0
        if not storable(value):
            self._reject(name, value, f"an integer no wider than 64 bits (got {value})")
            return 0
        return value

    def mapping(self, name: str, value: object) -> dict[str, Any]:
        """A required JSON object, as a plain ``dict``.

        Distinct from :meth:`document`, which passes a *document* argument
        through untouched so that `service/documents.py` can resolve it against
        ruling R-20's raw-text seam. ``dataset_fill_part``'s ``content`` is a
        section fragment rather than a whole document: no rule reads its raw
        text, and DS-013 cannot apply to it because ``labels`` is not a fillable
        section.
        """
        if isinstance(value, Mapping):
            return dict(value)
        self._reject(name, value, "an object")
        return {}

    def required_labels(self, name: str, value: object) -> dict[str, str]:
        """A required ``{dimension: value}`` object of strings.

        ``labels`` on ``dataset_skeleton`` is required, and an empty object is a
        *meaningful* value there - it is a dataset with no labels, which DS-024
        rejects at submit with a rule id rather than a boundary code. So absence
        and emptiness are different answers and only absence is ``AP-001``.
        """
        if value is None:
            self._reject(name, value, "an object of string label values")
            return {}
        return self.labels(name, value) or {}

    def labels(self, name: str, value: object) -> dict[str, str] | None:
        """A ``{dimension: value}`` object of strings, or ``None`` when omitted.

        A non-string *value* is rejected with a pointer at that dimension
        (``/labels/tier``) rather than at the whole argument, because that is the
        one thing the caller has to change.
        """
        if value is None:
            return None
        if not isinstance(value, Mapping):
            self._reject(name, value, "an object of string label values or null")
            return None
        coerced: dict[str, str] = {}
        for dimension, label in value.items():
            if isinstance(dimension, str) and isinstance(label, str):
                coerced[dimension] = label
            else:
                self._reject(name, label, "a string", target=field_pointer(name, str(dimension)))
        return coerced

    def document(self, name: str, value: object) -> object:
        """Passed through untouched.

        Document arguments are resolved by `service/documents.py`, not here:
        ruling R-20's duplicate-key detector lives in `validation/`, which
        `server/` may not import. This method exists so that a tool reads *all*
        of its arguments through one object, and so the asymmetry is visible at
        the call site instead of being a silently missing step.
        """
        return value

    def _reject(
        self, name: str, value: object, expected: str, *, target: str | None = None
    ) -> None:
        self.errors.append(
            boundary(
                AP_ARGUMENT,
                target if target is not None else field_pointer(name),
                f"{name} must be {expected}.",
                argument=name,
                given_type=type(value).__name__,
            )
        )
