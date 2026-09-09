"""Acceptance clause 4: the same seed gives byte-identical output in two processes.

"``dataset_expand`` with the same seed produces byte-identical output across two
processes." The last three words are the whole test, and they are why this is a
subprocess test rather than two calls in one interpreter.

**A single-process test cannot see the failure it is guarding against.**
Python's built-in ``hash()`` is randomised per process by ``PYTHONHASHSEED``, so
a generator derived from it returns one sequence for the life of a process and a
different one tomorrow - and two calls inside one test would agree perfectly
every time. `tests/unit/test_seeded.py` established this at the source for
``Seeded`` itself; this establishes it for the whole ``dataset_expand`` path,
which also runs a validator, constructs a model, stamps a clock and writes a
row.

Three subprocesses, with ``PYTHONHASHSEED`` set to ``0``, ``1`` and ``random``
- and ``random`` is the value that actually varies, so it is the one that would
catch a regression. Each gets its **own** SQLite database, because sharing one
would let the second process expand version 2 rather than version 1 and the test
would be comparing two different requests.

The comparison is on the raw bytes of the envelope, **not** on a parsed and
re-sorted structure. ``json.dumps`` with ``sort_keys=True`` would hide a key
order that varied between processes, which is a real thing dict ordering can do
and a real thing an export consumer can depend on.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Final

import pytest

from conftest import FIXTURES_DIR

pytestmark = pytest.mark.integration

#: The pool node the golden blueprint declares ``pool: true``, with two authored
#: entries and ``max_iterations: 3`` - so ``count=1`` is the largest expansion
#: that satisfies DS-023, and ``count=2`` is the smallest that does not.
POOL_NODE: Final = "request_docs"

#: The script the subprocesses run. A file rather than ``python -c`` so a
#: failure has line numbers, and it takes every input as an argument so nothing
#: about it depends on the environment the test happened to run in - which is
#: the property under test.
SCRIPT: Final = '''\
"""Expand the golden dataset once, in a fresh store, and print the envelope."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from agentprops.models import Blueprint, Dataset
from agentprops.service import FrozenClock, expansion, sqlite_context

fixtures = Path(sys.argv[1])
database = sys.argv[2]
node_id = sys.argv[3]
count = int(sys.argv[4])

# The clock is frozen to the value tests/conftest.py uses, so `validated_at` is
# a fixed string rather than the one field that could never be byte-identical.
context = sqlite_context(database, clock=FrozenClock(datetime(2026, 9, 8, 12, 0, tzinfo=UTC)))
blueprint = json.loads(
    (fixtures / "blueprints" / "location-onboarding-1.0.0.json").read_text(encoding="utf-8")
)
dataset = json.loads(
    (fixtures / "datasets" / "priya-missing-docs.json").read_text(encoding="utf-8")
)
context.store.put_blueprint(Blueprint.model_validate(blueprint), publish=True)
context.store.put_dataset(Dataset.model_validate(dataset))

reply = expansion.expand(context, dataset["id"], node_id, count)
sys.stdout.write(
    json.dumps(reply.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False)
)
'''

#: ``PYTHONHASHSEED`` values. ``random`` is the one that varies per process and
#: therefore the one that would catch a ``hash()``-derived implementation; the
#: two literals are there so a failure says whether the value mattered at all.
HASH_SEEDS: Final[tuple[str, ...]] = ("0", "1", "random")


@pytest.fixture
def script(tmp_path: Path) -> Path:
    written = tmp_path / "expand_once.py"
    written.write_text(SCRIPT, encoding="utf-8")
    return written


def expand_in_a_subprocess(script: Path, tmp_path: Path, hash_seed: str, count: int) -> str:
    """One expansion, in its own process, its own store and its own hash seed."""
    database = tmp_path / f"store-{hash_seed}-{count}.db"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(FIXTURES_DIR),
            str(database),
            POOL_NODE,
            str(count),
        ],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
    )
    return result.stdout


def test_expansion_is_byte_identical_across_processes(script: Path, tmp_path: Path) -> None:
    """Acceptance clause 4, as literally as it can be tested.

    Three processes, three hash seeds, three fresh databases, one expansion
    each. The bytes have to match exactly - not the parsed structures, and not
    the structures after a key sort.
    """
    outputs = {
        hash_seed: expand_in_a_subprocess(script, tmp_path, hash_seed, 1)
        for hash_seed in HASH_SEEDS
    }
    distinct = set(outputs.values())
    assert len(distinct) == 1, (
        "dataset_expand is process-dependent. Per PYTHONHASHSEED: "
        + ", ".join(f"{seed}={len(text)}b" for seed, text in outputs.items())
    )


def test_the_subprocess_expansion_actually_succeeded(script: Path, tmp_path: Path) -> None:
    """The non-vacuity half, and it is not decoration.

    Three processes that all failed the same way would produce three identical
    error envelopes and pass the test above. So this asserts the envelope is a
    *success* and that the pool actually grew - which is also the only thing
    that proves the script is exercising the expansion path rather than an
    ``AP-004`` two lines in.
    """
    envelope: dict[str, Any] = json.loads(expand_in_a_subprocess(script, tmp_path, "0", 1))
    assert envelope["ok"] is True, envelope
    dataset = envelope["data"]["dataset"]
    assert dataset["version"] == 2, "the expansion was not stored as a new version"
    assert len(dataset["pools"][POOL_NODE]) == 3, "the pool did not grow"


def test_the_expansion_and_the_source_process_agree(script: Path, tmp_path: Path) -> None:
    """The subprocess and this process produce the same expanded pool.

    Which is the claim clause 4 is really making - a bundle a colleague exported
    is reproducible here - and it is a different claim from "three subprocesses
    agree with each other". Compared as the pool rather than the whole envelope,
    because this process is not going to reproduce the subprocess's
    ``dataset_id`` ordering of an unrelated store.
    """
    from agentprops.expansion.seeded import Seeded
    from agentprops.service.expansion import expansion_salt

    envelope: dict[str, Any] = json.loads(expand_in_a_subprocess(script, tmp_path, "random", 1))
    pool = envelope["data"]["dataset"]["pools"][POOL_NODE]
    seed = envelope["data"]["dataset"]["seed"]

    templates = pool[:2]
    here = Seeded(seed, expansion_salt(POOL_NODE, 2)).choice(templates)
    assert pool[2] == here, "the entry this process derives is not the one the subprocess wrote"
