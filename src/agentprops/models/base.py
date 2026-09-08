"""The shared Pydantic configuration every model in this package inherits.

One base class rather than a repeated ``model_config`` because three settings
here are load-bearing for the M1 acceptance criterion and must not drift
between models:

``extra="forbid"``
    Unknown keys are a shape error. Ruling R-04 permits this "only where no
    rule owns it", and no ``BP-*``/``DS-*`` rule owns unknown keys on a model:
    the rules that police unknown keys (DS-003 on ``nodes``, DS-012 on
    ``labels``, DS-018 on ``pools``) all police keys of a ``dict`` field, which
    Pydantic's ``extra`` policy never sees. The one exception is
    :class:`~agentprops.models.dataset.FaultSpec`, whose conformance DS-022
    owns; it overrides this to ``extra="allow"``.

``validate_by_name`` / ``validate_by_alias``
    Two wire field names are not legal Python identifiers or are already taken
    on ``BaseModel``: ``Edge.from`` and ``EntitySchema.schema``. They are
    carried as aliases, and accepting both the alias and the Python name keeps
    in-process construction (``Edge(from_node=..., to=...)``) ergonomic. The
    emitted JSON Schemas are generated ``by_alias``, so the *wire* contract
    stays exactly the alias plus ``additionalProperties: false``.

``serialize_by_alias``
    So that the round-trip criterion in ruling R-08 -
    ``model_validate(raw).model_dump(mode="json", exclude_unset=True) == raw``
    - holds verbatim, with no ``by_alias=True`` argument the caller could
    forget to pass.

Nothing here constrains a *value*. Per ruling R-04 the models carry fields and
the validation catalogue enforces constraints, so no model in this package
declares ``min_length``, ``max_length``, ``pattern``, ``ge``/``gt``, a
``Literal`` enum, or a cross-field validator.

Strict numbers and booleans (ruling R-23)
-----------------------------------------

Every ``int`` and ``bool`` field in this package is spelled ``StrictInt`` or
``StrictBool``, never bare ``int``/``bool``. That is a *coercion* policy, not a
value constraint, and it exists because Pydantic's lax mode silently rewrites
values: ``seed: "42"`` parses as ``42`` and ``seed: true`` parses as ``1``, so
the document parses and then fails R-08's round-trip criterion - strictly worse
than either accepting or rejecting it. Verified under strict typing,
``"42"``, ``true``, ``3.7`` and ``3.0`` all raise ``int_type``, and ``"no"``
and ``0`` raise ``bool_type``.

``strict`` is deliberately *not* set on the model config, which would apply to
every field: in strict mode a ``datetime`` field accepts only ``datetime``
objects and a ``UUID`` field only ``UUID`` objects, so a JSON document would
stop parsing entirely. It is applied per field, through the annotation.

This does not take a check away from the catalogue. Ruling R-23 settles that
the validator operates on the **raw document** - the ``dict`` off
``json.loads`` - and that models are constructed only after validation passes.
So DS-020 ("``seed`` is an integer") and BP-008's "is an integer" half are
implemented against the raw dict at M2 and get real corpus cases; the strict
annotations here are the second, defence-in-depth layer R-23 asks for.
"""

from pydantic import BaseModel, ConfigDict

__all__ = ["StrictModel"]


class StrictModel(BaseModel):
    """Base for every agent-props model. See the module docstring."""

    model_config = ConfigDict(
        extra="forbid",
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )
