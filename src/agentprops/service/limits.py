"""The integer range a store can hold, and the clamp that keeps it in range.

Why this exists
---------------

`ArgReader.number` accepts any Python ``int``, and Python integers are
unbounded. Every backend's integer column is not: SQLite's ``INTEGER`` is a
signed 64-bit value, Postgres's ``bigint`` is the same, and BSON's ``long`` is
the same. Handing pysqlite a value outside that range raises ``OverflowError``
from the driver - which the MCP SDK turns into ``UnexpectedToolError``, so the
caller gets a protocol error instead of the section 1 envelope CLAUDE.md
promises for "anything a user could cause".

**Reproduced, and the boundary is exact.** Against the in-memory client:
``dataset_find`` with ``limit = 2**63 - 1`` returns an envelope;
``limit = 2**63`` escapes. Same for ``offset``, and for ``dataset_get``'s
``version``.

Why the bound is representability and not policy
------------------------------------------------

:data:`MAX_STORED_INT` is the widest integer a row can carry, on every backend.
Clamping to it is therefore **not** the service gating (ground rule 3): nothing
is refused, and no value that could ever have been stored is altered. A `limit`
of ``2**63 - 1`` already means "every row there will ever be", so a caller
asking for more is asking for the same thing.

An out-of-range *identifier* is different and is handled at the call site rather
than here: a version outside the range names no stored row, so
``dataset_get`` reports ``AP-004`` ("no such version") rather than clamping to
the nearest representable one. Clamping an id would answer a question the caller
did not ask.

What M5 and M6 inherit
----------------------

The same trap is one argument away in both: ``fetch_step``'s ``iteration`` and
``dataset_expand``'s ``count`` are caller-supplied integers that reach a column.
Use :func:`clamp` for a bound that is a *quantity* and :func:`storable` for one
that is an *identifier*.
"""

from __future__ import annotations

from typing import Final

__all__ = ["DEFAULT_PAGE_LIMIT", "MAX_STORED_INT", "MIN_STORED_INT", "clamp", "page", "storable"]

#: The largest integer any backend's integer column can hold: signed 64-bit.
#: SQLite ``INTEGER``, Postgres ``bigint`` and BSON ``long`` are all this.
MAX_STORED_INT: Final = 2**63 - 1

#: The other end of the same range. Nothing in the domain is negative, so this
#: is here for completeness and for the symmetry :func:`clamp` needs.
MIN_STORED_INT: Final = -(2**63)


def clamp(value: int, *, low: int = 0, high: int = MAX_STORED_INT) -> int:
    """``value`` brought inside ``[low, high]``.

    For a *quantity* - a limit, an offset, a count - where every value outside
    the range means the same thing as the nearest value inside it.
    """
    return min(max(value, low), high)


#: The page size a list-returning tool uses when the caller names no ``limit``.
#: A page size, not a cap: an explicit ``limit`` of 5000 is honoured.
DEFAULT_PAGE_LIMIT: Final = 50


def page(limit: int | None, offset: int | None) -> tuple[int, int]:
    """``(limit, offset)`` defaulted and clamped into ``[0, MAX_STORED_INT]``.

    One implementation for every paging tool. ``dataset_find`` had this inline
    at M4 and ``run_find`` needs exactly the same two decisions - the default
    page size, and both ends of the clamp - so it lives here rather than being
    written a second time. That is ruling R-50's own lesson applied one layer
    up: the defect sits in the one place a working pattern was not reused.
    """
    return clamp(DEFAULT_PAGE_LIMIT if limit is None else limit), clamp(offset or 0)


def storable(value: int) -> bool:
    """Whether ``value`` could name a stored row at all.

    For an *identifier* - a version, an iteration - where a value outside the
    range is not a large version, it is no version. The caller reports a miss.
    """
    return MIN_STORED_INT <= value <= MAX_STORED_INT
