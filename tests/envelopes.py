"""Narrowing a ``Reply`` in a test, so an assertion says which envelope it wants.

``Reply`` is ``SuccessEnvelope | ErrorEnvelope`` and only one member of that
union has ``data`` - which is correct, and which ``mypy --strict`` enforces on
`tests/` as well as on `src/`. Rather than sprinkle ``assert isinstance`` at
every call site, each accessor here asserts the envelope *kind* on the way to
the field.

That turns a type-checker requirement into a better assertion. ``data(reply)``
fails with "expected a success envelope, got ok=False errors=[...]" - naming the
rule ids that caused it - where ``reply.data["blueprint"]`` would have failed
with an ``AttributeError`` and no explanation.

:func:`findings` accepts an ``ErrorEnvelope`` whether ``ok`` is true or false,
because ruling R-13 makes a warning-only validation result exactly that: ``ok:
true`` with warning-severity items in ``errors``.
"""

from __future__ import annotations

from typing import Any

from agentprops.models import ErrorEnvelope, RuleError, SuccessEnvelope, Warning
from agentprops.service import Reply

__all__ = ["codes", "data", "findings", "rules", "warnings_of"]


def data(reply: Reply) -> dict[str, Any]:
    """The success envelope's payload object."""
    assert isinstance(reply, SuccessEnvelope), f"expected a success envelope, got {reply!r}"
    return reply.data


def warnings_of(reply: Reply) -> list[Warning]:
    """The success envelope's warnings."""
    assert isinstance(reply, SuccessEnvelope), f"expected a success envelope, got {reply!r}"
    return reply.warnings


def findings(reply: Reply) -> list[RuleError]:
    """The error envelope's findings, whether ``ok`` is true or false."""
    assert isinstance(reply, ErrorEnvelope), f"expected an error envelope, got {reply!r}"
    return reply.errors


def rules(reply: Reply) -> list[str]:
    """Just the rule ids. What a test asserts on - never the message."""
    return [finding.rule for finding in findings(reply)]


def codes(reply: Reply) -> list[str]:
    """Just the warning codes."""
    return [item.code for item in warnings_of(reply)]
