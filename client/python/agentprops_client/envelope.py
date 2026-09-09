"""The two envelopes contracts section 1 defines, as types a caller can read.

Pure: this module knows the *shape* of a response and nothing about how one
arrives. `run.py` builds requests and reads them through here; `session.py` is
the only module that opens anything.

Why an exception, in a product whose ethos is "structured errors, never
exceptions"
-------------------------------------------------------------------------------

That ethos is about the **service**: no tool may raise for anything a user can
cause, because an exception cannot cross a protocol boundary as an answer. It
has already been honoured by the time a response reaches this module - the
failure arrived *as data*, with a rule id and a pointer.

What a Python caller then wants is different. :class:`ToolError` carries the
findings and is raised by the typed methods on the run client, so that
``fetch_step`` can be annotated as returning a :class:`Step` and mean it. The
alternative - every method returning a union a caller must narrow - moves the
check to every call site and makes the common path noisy, which is how a caller
ends up not checking at all.

The escape hatch is deliberate and load-bearing: :meth:`RunClient.call` returns
the parsed :class:`Envelope` and raises nothing, so a caller who wants to look
at ``AP-001`` or ``SK-002`` as data still can. Every test in this repo that
asserts on a rule id uses it.

**Warnings never raise.** Ground rule 3 is that a mismatch is a warning
attached to the response, and a client that turned one into an exception would
be re-introducing the gate the service refuses to be. They ride on
:attr:`Envelope.warnings` and on every typed result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

__all__ = ["Envelope", "Finding", "ToolError", "Warning", "read_envelope"]

#: The two envelope shapes, as their key sets. contracts section 1:
#: ``{ok, data, warnings}`` on success and ``{ok, errors}`` on a validation or
#: resolution failure.
_SUCCESS_KEYS: Final[frozenset[str]] = frozenset({"ok", "data", "warnings"})
_FAILURE_KEYS: Final[frozenset[str]] = frozenset({"ok", "errors"})


@dataclass(frozen=True, slots=True)
class Finding:
    """One entry of ``errors``. contracts section 1's shape, field for field.

    ``rule`` is what to branch on - a catalogue id (``DS-008``), a runtime code
    (``RT-E01``) or a boundary code (``AP-004``). ``message`` is for humans and
    for logs; the repo's own testing rule is "assert on rule ids and envelope
    shapes, never on human-readable messages", and it applies to a caller's
    assertions just as well.
    """

    rule: str
    severity: str
    pointer: str
    message: str
    section: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Warning:
    """One entry of ``warnings``: a code and a free-form detail.

    The vocabulary is **open** (ruling R-22), so a caller must not switch
    exhaustively on ``code`` and assume it has seen them all. contracts 3.4
    tabulates the three that are part of the model and describes each tool-local
    addition beside it.
    """

    code: str
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Envelope:
    """A parsed response. Exactly one of ``data``/``errors`` is meaningful.

    ``ok`` is read from the payload rather than inferred from which key is
    present, because the two validate tools legitimately answer ``ok: true`` on
    an ``{ok, errors}`` envelope for a warning-only document (ruling R-13).
    """

    ok: bool
    data: Mapping[str, Any]
    warnings: tuple[Warning, ...]
    errors: tuple[Finding, ...]

    def __bool__(self) -> bool:
        return self.ok

    def payload(self, key: str) -> Mapping[str, Any]:
        """``data[key]``, or :class:`ToolError` if this is a failure.

        ``data`` always carries the payload under one named key (contracts
        section 1), so a caller naming the key it expects gets either that
        payload or an exception carrying the findings - never a ``KeyError`` on
        a response that was an error all along.

        A **successful** envelope missing the named key is a different fault and
        gets a different exception: the server answered, so there are no
        findings to raise, and a ``ToolError`` with an empty finding list
        would say "the tool failed" when the truth is "this client asked for the
        wrong key".
        """
        if not self.ok:
            raise ToolError(self)
        if key not in self.data:
            raise ValueError(f"the response carries no {key!r} payload: {sorted(self.data)}")
        return dict(self.data[key])

    def codes(self) -> tuple[str, ...]:
        """Just the warning codes, for a caller that logs or asserts on them."""
        return tuple(item.code for item in self.warnings)

    def rules(self) -> tuple[str, ...]:
        """Just the rule ids. What to branch on, and what to assert on."""
        return tuple(item.rule for item in self.errors)


class ToolError(Exception):
    """A tool answered ``{ok: false, errors: [...]}``.

    Carries the whole :class:`Envelope`, so a handler can read every finding's
    ``rule``, ``pointer``, ``section`` and ``context`` rather than parsing the
    message this builds for the traceback.
    """

    def __init__(self, envelope: Envelope, request: str = "") -> None:
        self.envelope = envelope
        self.findings = envelope.errors
        named = f"{request}: " if request else ""
        detail = ", ".join(f"{item.rule} at {item.pointer}" for item in envelope.errors)
        super().__init__(f"{named}{detail or 'no findings reported'}")

    def rules(self) -> tuple[str, ...]:
        return tuple(item.rule for item in self.findings)


def read_envelope(payload: Mapping[str, Any] | None) -> Envelope:
    """Parse one tool response. Strict about the envelope, tolerant inside it.

    **The tolerance is real only *inside* ``data``, ``warnings`` and
    ``errors``**, and that distinction matters enough to state precisely,
    because the looser claim would be false: a finding carrying a field this
    client has never heard of is read for the fields it knows and the rest is
    dropped, and a payload under a named key is handed over whole. But an
    unknown **top-level** key is rejected, and
    `tests/unit/test_client_run.py::test_a_protocol_level_error_is_not_mistaken_for_a_failure_envelope`
    pins that.

    Which is the right way round. contracts section 1 fixes the envelope at two
    shapes and every tool in this product answers one of them, so a third
    top-level key means the transport handed back something that is not an
    agent-props response - and saying so immediately beats a ``KeyError`` three
    frames later. The *contents* are where a server one version ahead of this
    client legitimately adds a field, so that is where nothing is rejected.

    The cost, stated: a future envelope-level addition needs this client
    updated, rather than being ignored. That is a deliberate trade and not an
    oversight - the alternative accepts a response from anything.
    """
    if payload is None:
        raise ValueError("the tool returned no structured content")
    if not isinstance(payload.get("ok"), bool):
        raise ValueError(f"not an agent-props envelope: {sorted(payload)}")
    shape = set(payload)
    if not (shape <= _SUCCESS_KEYS or shape <= _FAILURE_KEYS):
        raise ValueError(f"not an agent-props envelope: {sorted(shape)}")
    return Envelope(
        ok=bool(payload["ok"]),
        data=dict(payload.get("data") or {}),
        warnings=tuple(_warning(item) for item in _sequence(payload.get("warnings"))),
        errors=tuple(_finding(item) for item in _sequence(payload.get("errors"))),
    )


def _sequence(value: Any) -> Sequence[Mapping[str, Any]]:
    return list(value) if isinstance(value, Sequence) and not isinstance(value, str) else []


def _warning(item: Mapping[str, Any]) -> Warning:
    return Warning(code=str(item.get("code", "")), detail=dict(item.get("detail") or {}))


def _finding(item: Mapping[str, Any]) -> Finding:
    return Finding(
        rule=str(item.get("rule", "")),
        severity=str(item.get("severity", "error")),
        pointer=str(item.get("pointer", "")),
        message=str(item.get("message", "")),
        section=item.get("section"),
        context=dict(item.get("context") or {}),
    )
