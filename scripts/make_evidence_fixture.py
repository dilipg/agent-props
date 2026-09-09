"""Regenerate the committed `run_evidence` bundle in `tests/fixtures/evidence/`.

That fixture exists for one reason: M10's second acceptance clause is that
`run_evidence`'s output is "sufficient for the client's comparison helpers to
grade the run with no further server calls", and the only test that proves it is
one with **no store and no session** - so it needs a bundle on disk.

A golden file is only evidence if it is provably the service's own output, so
this script and `tests/unit/test_service_evidence.py` walk the *same* run,
through `tests/evidencewalk.py`, and the test asserts the live bundle still
equals what is committed here. Regenerating is therefore a reviewed edit: run
this, read the diff, commit it.

One field is masked and one only: ``recorded_at`` is stamped by the database,
so no injected clock can freeze it. `tests/evidencewalk.py`'s ``normalised``
does the masking for both sides, and the drift test asserts the live values are
real timestamps separately - see that function's docstring.

Usage:  uv run python scripts/make_evidence_fixture.py

The walk lives under `tests/` rather than here because two test suites take it
as well, and one definition that a script has to reach for beats three copies
that agree by coincidence. Hence the `sys.path` insert below - the only unusual
line in this file.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from agentprops.service import FrozenClock, sqlite_context  # noqa: E402
from agentprops.service.evidence import evidence  # noqa: E402
from agentprops.storage import SqlStore  # noqa: E402
from evidencewalk import RUN_ID, normalised, walk  # noqa: E402

#: The same frozen instant `tests/conftest.py` uses. Every timestamp in the
#: bundle comes from the injected clock (ruling R-09), so freezing it is what
#: makes the fixture byte-stable rather than a diff on every run.
FROZEN_NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)

TARGET = ROOT / "tests" / "fixtures" / "evidence" / "priya-missing-docs-run.json"


def main() -> int:
    with tempfile.TemporaryDirectory() as scratch:
        context = sqlite_context(Path(scratch) / "agentprops.db", clock=FrozenClock(FROZEN_NOW))
        try:
            walk(context)
            reply = evidence(context, RUN_ID)
            assert reply.ok, reply
            bundle = normalised(reply.data["evidence"])  # type: ignore[union-attr]
        finally:
            if isinstance(context.store, SqlStore):
                context.store.dispose()
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}: {len(bundle['nodes'])} steps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
