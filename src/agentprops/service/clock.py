"""The single clock the service is allowed to read (ruling R-09).

Ground rule 9 bans `datetime.now()` from generation and expansion paths, and
ruling R-09 names the four legitimate sources a timestamp may come from - a DB
column default, the client, authored content in the document, or
``Seeded.timestamp()`` - plus one addition, which is this module: **one injected
clock, used only in `service/`, for ``validated_at`` and the run timestamps, and
frozen in tests.**

Two things make that a port rather than a bare function call.

**It is injected, so tests freeze it.** :class:`FrozenClock` ships here rather
than in the test tree because four milestones need it (M4's determinism gate,
M5's ``validated_at``, M6's and M8's run timestamps) and a helper each suite
reinvents is a helper each suite gets subtly differently.

**It is the only clock, and a test enforces that.**
``tests/unit/test_layering.py::test_only_the_clock_module_reads_a_clock``
asserts that no other module in `service/` or `server/` calls
``datetime.now()``. Without that assertion "a single injected clock" is a
sentence in a docstring, which is worth nothing.

M4 stamps nothing
-----------------

Worth stating plainly, because a port with no caller looks like an oversight.
``validated_at`` is a *dataset* field and the dataset write path is
``dataset_submit``, which is M5's. M4's writes are ``blueprint_upsert`` (the
``Blueprint`` model carries no timestamp) and ``dataset_archive`` /
``dataset_restore`` (a flag, not a new version). So M4 wires the clock, tests
it, and stamps nothing with it.

M4 also deliberately puts **no** clock reading into any response.
``blueprint_diff`` was the obvious candidate for a ``generated_at`` field and
does not have one: M4's acceptance criterion is byte-identical output across
repeated identical calls, and a timestamp in a response is exactly the thing
that breaks it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

__all__ = ["Clock", "FrozenClock", "SystemClock"]


class Clock(Protocol):
    """One method: the current instant, timezone-aware."""

    def now(self) -> datetime:
        """The current instant as a timezone-aware UTC ``datetime``.

        Timezone-aware is part of the contract, not an implementation detail:
        ``UtcDateTime`` in `storage/sql.py` exists because SQLite silently
        discards ``tzinfo``, and a naive instant reaching a column is
        unrecoverable afterwards.
        """
        ...


class SystemClock:
    """The real clock. The one sanctioned ``datetime.now()`` call in `src/`."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True)
class FrozenClock:
    """A clock that always answers ``instant``.

    What a test injects. Frozen and hashable so it can be a module constant in
    a suite without any risk of one test mutating another's clock.
    """

    instant: datetime

    def now(self) -> datetime:
        return self.instant
