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
