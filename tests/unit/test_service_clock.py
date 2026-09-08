"""The ``Clock`` port. Small, and tested so it is not untested code.

M4 stamps nothing with the clock - ``validated_at`` is a dataset field and
dataset writes land at M5 - so without these the port would be three classes
nobody had ever run. The other half of ruling R-09's enforcement is in
`test_layering.py`, which asserts that no module in `service/` or `server/`
other than `clock.py` reads a clock at all.
"""

from __future__ import annotations

from datetime import UTC, datetime

from agentprops.service import Clock, FrozenClock, ServiceContext, SystemClock
from conftest import FROZEN_NOW


def test_the_system_clock_answers_a_timezone_aware_utc_instant() -> None:
    """Aware is part of the contract: ``UtcDateTime`` exists because SQLite drops tzinfo."""
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == UTC.utcoffset(None)


def test_the_frozen_clock_answers_the_same_instant_every_time() -> None:
    clock = FrozenClock(FROZEN_NOW)
    assert clock.now() == FROZEN_NOW
    assert clock.now() == clock.now()


def test_the_frozen_clock_is_hashable_so_a_suite_can_hold_one_as_a_constant() -> None:
    assert FrozenClock(FROZEN_NOW) == FrozenClock(FROZEN_NOW)
    assert len({FrozenClock(FROZEN_NOW), FrozenClock(FROZEN_NOW)}) == 1


def test_both_clocks_satisfy_the_protocol() -> None:
    clocks: list[Clock] = [SystemClock(), FrozenClock(FROZEN_NOW)]
    assert all(isinstance(clock.now(), datetime) for clock in clocks)


def test_the_context_carries_the_injected_clock(context: ServiceContext) -> None:
    """The fixture freezes it, which is ruling R-09's "frozen in tests" half."""
    assert context.clock.now() == FROZEN_NOW


def test_the_context_defaults_to_the_system_clock(context: ServiceContext) -> None:
    """An omitted clock is the real one, so production needs no wiring step."""
    assert isinstance(ServiceContext(store=context.store).clock, SystemClock)
