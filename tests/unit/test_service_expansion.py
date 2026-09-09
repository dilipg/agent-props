"""``dataset_expand``: ruling R-56's three clauses, and what it seeds from.

R-56 says three things and each one is a test here.

**It validates before it stores.** An expansion that would push a loop node's
pool past ``max_iterations`` returns **DS-023** and stores nothing - and the
"stores nothing" half is asserted separately from the rule id, because a
version written and then reported as a failure is the shape of bug the ruling
was written against.

**It never silently clamps.** The rejected alternative was clamping to
``max_iterations``, and the reason it was rejected is that a caller asking for
``count=10`` and receiving 3 without being told cannot know its output is not
what it requested. So the tests here assert the *refusal* rather than a
truncated success, and the per-call maximum is refused the same way for the same
reason.

**The finding carries the numbers.** "Put ``max_iterations`` and the resulting
length in the error's ``context`` so the caller can retry with a workable
``count`` rather than guessing" - which DS-023 already does, and this file pins
so that a future change to the rule cannot quietly drop them.

The seeding decisions get their own tests, because they are the part no ruling
specified: an entry is addressed by its **position in the pool**, which is what
makes expanding version 2 leave version 1's entries alone. It does **not** make
expanding by 5 equal to expanding by 2 then 3 - this file's own
``test_a_split_expansion_keeps_the_prefix_and_may_diverge_after_it`` was written
to assert that and disproved it, and this paragraph said the opposite until the
same grep that found the claim on the tool surface found it here.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Final

import pytest
from hypothesis import given
from hypothesis import strategies as st

from agentprops.expansion.seeded import Seeded
from agentprops.models import Dataset, DatasetQuery
from agentprops.service import ServiceContext, blueprints
from agentprops.service.expansion import (
    MAX_EXPAND_COUNT,
    WARNING_EXPANSION_ADDED_NOTHING,
    expand,
    expansion_salt,
    jittered_range,
)
from agentprops.service.limits import MAX_STORED_INT, clamp, storable
from conftest import FROZEN_NOW, load_document

#: The golden blueprint declares this node ``pool: true`` with
#: ``max_iterations: 3``, and the golden dataset authors two entries for it - so
#: ``count=1`` is the largest expansion DS-023 permits and ``count=2`` is the
#: smallest it refuses. Every boundary in this file is that one.
POOL_NODE: Final = "request_docs"
AUTHORED_ENTRIES: Final = 2
MAX_ITERATIONS: Final = 3

#: A node that exists and is **not** a pool node, so DS-018 owns it.
PLAIN_NODE: Final = "check_docs"


@pytest.fixture
def seeded(context: ServiceContext) -> ServiceContext:
    """The published blueprint plus the golden dataset at version 1."""
    assert blueprints.upsert(
        context, load_document("blueprints/location-onboarding-1.0.0.json"), publish=True
    ).ok
    context.store.put_dataset(
        Dataset.model_validate(load_document("datasets/priya-missing-docs.json"))
    )
    return context


#: A blueprint version and a dataset built for the one property the golden
#: fixtures cannot express: a pool node with headroom. ``request_docs`` caps at
#: ``max_iterations: 3`` and the golden dataset already fills two of those, so
#: no split of a legal expansion is possible against it - and the split is the
#: property that justifies addressing an entry by its position rather than
#: drawing it from a stream.
#:
#: Derived from the golden pair rather than hand-written, so it stays valid when
#: the fixtures change: the blueprint is 1.0.0 with a wider cap under a new
#: version, and the dataset is the golden one pointed at it under a new id.
ROOMY_VERSION: Final = "1.1.0"
ROOMY_MAX_ITERATIONS: Final = 12
ROOMY_DATASET_ID: Final = "3f8c1a20-0000-4000-8000-00000000e001"


def _roomy_blueprint() -> dict[str, Any]:
    document = load_document("blueprints/location-onboarding-1.0.0.json")
    document["version"] = ROOMY_VERSION
    for node in document["nodes"]:
        if node["id"] == POOL_NODE:
            node["max_iterations"] = ROOMY_MAX_ITERATIONS
    return document


def _roomy_dataset() -> dict[str, Any]:
    document = load_document("datasets/priya-missing-docs.json")
    document["id"] = ROOMY_DATASET_ID
    document["blueprint"] = {**document["blueprint"], "version": ROOMY_VERSION}
    return document


def _roomy_context(context: ServiceContext) -> ServiceContext:
    """A *second* context over the same store, for the two-step comparison.

    Two-step and one-step expansions of the same lineage cannot share a store -
    the second call would expand what the first wrote - so the comparison needs
    two stores. This returns a context over a fresh one.
    """
    from agentprops.service import sqlite_context

    built = sqlite_context(_next_database(), clock=context.clock)
    assert blueprints.upsert(built, _roomy_blueprint(), publish=True).ok
    built.store.put_dataset(Dataset.model_validate(_roomy_dataset()))
    return built


_DATABASES: list[Path] = []


def _next_database() -> Path:
    """A fresh SQLite path, from the directory the ``roomy`` fixture reserved."""
    path = _DATABASES[-1].parent / f"roomy-{len(_DATABASES)}.db"
    _DATABASES.append(path)
    return path


@pytest.fixture
def roomy(context: ServiceContext, tmp_path: Path) -> ServiceContext:
    """A store holding a blueprint whose pool node has room, and a dataset for it."""
    _DATABASES.clear()
    _DATABASES.append(tmp_path / "roomy-0.db")
    assert blueprints.upsert(context, _roomy_blueprint(), publish=True).ok
    context.store.put_dataset(Dataset.model_validate(_roomy_dataset()))
    return context


def dataset_id() -> str:
    identifier: str = load_document("datasets/priya-missing-docs.json")["id"]
    return identifier


def payload(reply: Any) -> dict[str, Any]:
    dumped: dict[str, Any] = reply.model_dump(mode="json")
    return dumped


def test_expansion_adds_entries_as_a_new_version(seeded: ServiceContext) -> None:
    """Copy-on-write, like every dataset edit. Version 1 stays exactly as it was."""
    reply = expand(seeded, dataset_id(), POOL_NODE, 1)
    assert reply.ok, reply
    grown = payload(reply)["data"]["dataset"]
    assert grown["version"] == 2
    assert len(grown["pools"][POOL_NODE]) == AUTHORED_ENTRIES + 1

    first = seeded.store.get_dataset(dataset_id(), 1)
    assert first is not None
    assert len(first.pools[POOL_NODE]) == AUTHORED_ENTRIES, "version 1 was mutated"


def test_the_expanded_version_is_stamped_by_the_injected_clock(seeded: ServiceContext) -> None:
    """Ruling R-09: ``validated_at`` comes from the ``Clock`` port, frozen in tests.

    Which is also what makes the cross-process determinism test possible - an
    unfrozen timestamp is the one field that could never be byte-identical.
    """
    reply = expand(seeded, dataset_id(), POOL_NODE, 1)
    stamped = payload(reply)["data"]["dataset"]["validated_at"]
    assert datetime.fromisoformat(stamped) == FROZEN_NOW


def test_an_expansion_past_max_iterations_is_ds_023_and_stores_nothing(
    seeded: ServiceContext,
) -> None:
    """Ruling R-56, both halves, asserted separately.

    Two entries plus two more is four, against ``max_iterations: 3``. The rule
    id is one claim; "and stores nothing" is a different one, and it is the one
    that would be false if this module wrote first and validated after.
    """
    reply = expand(seeded, dataset_id(), POOL_NODE, 2)
    body = payload(reply)
    assert body["ok"] is False
    assert [finding["rule"] for finding in body["errors"]] == ["DS-023"]

    assert seeded.store.get_dataset(dataset_id(), 2) is None, "a refused expansion wrote a version"
    latest = seeded.store.get_dataset(dataset_id(), None)
    assert latest is not None and latest.version == 1


def test_the_ds_023_finding_names_both_numbers(seeded: ServiceContext) -> None:
    """R-56: "so the caller can retry with a workable ``count`` rather than guessing".

    The rule already puts ``pool_length`` and ``max_iterations`` in its context;
    this pins them, because a later edit to the rule that dropped either one
    would leave the caller with "too long" and nothing to compute from.
    """
    (finding,) = payload(expand(seeded, dataset_id(), POOL_NODE, 5))["errors"]
    assert finding["rule"] == "DS-023"
    assert finding["pointer"] == f"/pools/{POOL_NODE}"
    assert finding["context"]["max_iterations"] == MAX_ITERATIONS
    assert finding["context"]["pool_length"] == AUTHORED_ENTRIES + 5


def test_a_count_above_the_per_call_maximum_is_refused_by_name(seeded: ServiceContext) -> None:
    """A **stated** bound, not a clamp, which is the distinction R-56 draws.

    DS-023 cannot be the only bound: it caps a *loop* node, and ruling R-51
    allows a ``pool: true`` node that is not a loop node, so nothing in the
    catalogue stops ``count = 2**62`` from being attempted - and R-23 requires
    the document be built before it is validated. So the refusal names the
    maximum, and a caller is told rather than quietly served something smaller.
    """
    body = payload(expand(seeded, dataset_id(), POOL_NODE, MAX_EXPAND_COUNT + 1))
    assert body["ok"] is False
    (finding,) = body["errors"]
    assert finding["rule"] == "AP-001"
    assert finding["pointer"] == "/count"
    assert finding["context"]["maximum"] == MAX_EXPAND_COUNT
    assert finding["context"]["requested"] == MAX_EXPAND_COUNT + 1


def test_an_enormous_count_is_refused_rather_than_attempted(seeded: ServiceContext) -> None:
    """The reason the per-call maximum exists at all: this must not allocate.

    Without the bound, ``count = MAX_STORED_INT`` builds a list of that many
    fixtures before any rule gets an opinion - which is not a validation failure
    but an out-of-memory, and the one failure mode an envelope cannot describe.
    """
    body = payload(expand(seeded, dataset_id(), POOL_NODE, MAX_STORED_INT))
    assert body["ok"] is False
    assert [finding["rule"] for finding in body["errors"]] == ["AP-001"]


@pytest.mark.parametrize("count", [0, -1, -(10**9)], ids=["zero", "negative", "very-negative"])
def test_a_count_of_zero_adds_nothing_and_says_so(seeded: ServiceContext, count: int) -> None:
    """A well-formed request that adds nothing gets a warning, not a refusal.

    The service does not refuse well-formed arguments (ground rule 3), and a
    byte-identical new version would be worse than no version at all - it would
    put a meaningless entry in a lineage a reviewer reads. A negative count
    clamps to zero, where zero is what a negative quantity means.
    """
    reply = expand(seeded, dataset_id(), POOL_NODE, count)
    body = payload(reply)
    assert body["ok"] is True
    assert [item["code"] for item in body["warnings"]] == [WARNING_EXPANSION_ADDED_NOTHING]
    assert body["data"]["dataset"]["version"] == 1
    assert seeded.store.get_dataset(dataset_id(), 2) is None, "an empty expansion wrote a version"


def test_a_node_that_is_not_a_pool_node_is_answered_by_the_catalogue(
    seeded: ServiceContext,
) -> None:
    """DS-018 owns "``pools`` has no entry for any other node", so DS-018 answers.

    Rather than an invented boundary code. Expansion replicates authored
    fixtures, so a node with none cannot be expanded - and the reason is always
    a rule the catalogue already names, which is a better thing for an LLM
    caller to receive than a message this module wrote.
    """
    body = payload(expand(seeded, dataset_id(), PLAIN_NODE, 1))
    assert body["ok"] is False
    assert "DS-018" in {finding["rule"] for finding in body["errors"]}
    assert seeded.store.get_dataset(dataset_id(), 2) is None


def test_a_node_the_blueprint_does_not_declare_is_also_ds_018(seeded: ServiceContext) -> None:
    body = payload(expand(seeded, dataset_id(), "no-such-node", 1))
    assert body["ok"] is False
    assert "DS-018" in {finding["rule"] for finding in body["errors"]}


def test_an_unknown_dataset_is_ap_004(seeded: ServiceContext) -> None:
    for unknown in ("3f8c1a20-0000-4000-8000-0000000000ff", "not-a-uuid", ""):
        body = payload(expand(seeded, unknown, POOL_NODE, 1))
        assert body["ok"] is False
        assert [finding["rule"] for finding in body["errors"]] == ["AP-004"]
        assert body["errors"][0]["pointer"] == "/dataset_id"


def test_an_archived_dataset_expands_with_a_warning(seeded: ServiceContext) -> None:
    """The service never gates, and ``dataset_get`` set the precedent.

    Expanding a dataset that has been retired from discovery but is still pinned
    by old runs is a reasonable thing to do; refusing would be a policy
    judgement this layer does not get to make. The archive flag is
    lineage-level (ruling R-34), so the new version inherits it and the dataset
    stays out of ``dataset_find``.
    """
    seeded.store.set_archived(dataset_id(), True)
    reply = expand(seeded, dataset_id(), POOL_NODE, 1)
    body = payload(reply)
    assert body["ok"] is True
    assert "dataset_archived" in {item["code"] for item in body["warnings"]}
    assert body["data"]["dataset"]["archived"] is True
    assert seeded.store.find_datasets(DatasetQuery()) == []


# --------------------------------------------------------------------------
# what it seeds from: an entry is addressed by its position
# --------------------------------------------------------------------------


def test_an_entry_is_addressed_by_its_position_in_the_pool(seeded: ServiceContext) -> None:
    """The derivation, checked from outside: position N, and nothing else.

    Re-derived here with the public :func:`expansion_salt` and ``Seeded``, so
    the assertion is that the *contract* holds rather than that the code agrees
    with itself. If ``_grown`` ever shares one stream across the batch, the
    entry at position 2 stops being this value.
    """
    grown = payload(expand(seeded, dataset_id(), POOL_NODE, 1))["data"]["dataset"]
    templates = grown["pools"][POOL_NODE][:AUTHORED_ENTRIES]
    expected = Seeded(grown["seed"], expansion_salt(POOL_NODE, AUTHORED_ENTRIES)).choice(templates)
    assert grown["pools"][POOL_NODE][AUTHORED_ENTRIES] == expected


def test_a_split_expansion_keeps_the_prefix_and_may_diverge_after_it(
    roomy: ServiceContext,
) -> None:
    """The guarantee, and the residue - and the residue is here because a test found it.

    This test was written to assert that expanding by 5 equals expanding by 2
    then 3, because the first version of this module claimed that in its
    docstring. **It does not**, and the reason is not a bug in the seeding: a
    new entry is chosen from the pool it is being added to, and after the first
    call that pool is longer - so the second call is expanding a *different
    dataset*, and the draw over a five-entry list is not the draw over a
    two-entry one. Measured: the two agree up to position 4 and differ at 5.

    So the claim was wrong and the guarantee is narrower, which is what this
    now asserts:

    - **The prefix is preserved.** Positions the earlier call wrote are still
      those entries, byte for byte, in every later version. That is what
      addressing an entry by its position buys, and it is the property a
      reviewer reading a lineage depends on.
    - **The continuation may differ** between one call of five and two calls of
      two and three, because those are two different sequences of requests
      against two different starting datasets.

    Design principle 3 is untouched: "same seed plus same blueprint version
    yields the same dataset" is a statement about repeating a request, and
    repeating either of these sequences reproduces its own result exactly -
    which is what `test_expansion_determinism.py` proves across processes.
    Recorded in `DECISIONS.md` rather than left as a surprise, because the
    alternative - choosing only ever from the *authored* entries - would need
    the store to remember which entries were authored, and nothing does.
    """
    one_call = payload(expand(roomy, ROOMY_DATASET_ID, POOL_NODE, 5))["data"]["dataset"]

    other = _roomy_context(roomy)
    first = payload(expand(other, ROOMY_DATASET_ID, POOL_NODE, 2))["data"]["dataset"]
    second = payload(expand(other, ROOMY_DATASET_ID, POOL_NODE, 3))["data"]["dataset"]
    assert first["version"] == 2 and second["version"] == 3

    shared = AUTHORED_ENTRIES + 2
    assert second["pools"][POOL_NODE][:shared] == one_call["pools"][POOL_NODE][:shared], (
        "the entries the first call wrote were rewritten by the second"
    )
    assert second["pools"][POOL_NODE] != one_call["pools"][POOL_NODE], (
        "a split expansion now equals a single one; the DECISIONS.md entry that "
        "records the residue is stale, and the trade should be revisited rather "
        "than this assertion relaxed"
    )


def test_a_second_expansion_leaves_the_first_entries_alone(roomy: ServiceContext) -> None:
    """Stability across versions, which is the same property read the other way.

    The entries version 2 wrote are still there in version 3, byte for byte,
    because their positions did not move. A stream would have re-derived them
    from a different offset.
    """
    first = payload(expand(roomy, ROOMY_DATASET_ID, POOL_NODE, 2))["data"]["dataset"]
    second = payload(expand(roomy, ROOMY_DATASET_ID, POOL_NODE, 2))["data"]["dataset"]
    assert second["pools"][POOL_NODE][: AUTHORED_ENTRIES + 2] == first["pools"][POOL_NODE]


#: A dataset whose pool entries carry ``latency_hint_ms``, which the golden
#: fixtures do not - so the one field expansion actually *varies* had no test
#: and the clamp arithmetic around it had no case. The value is a plain
#: ``NodeFixture`` field with no schema behind it (DS-019 validates ``output``
#: against ``output_schema`` and ``input`` against ``input_schema``), which is
#: exactly why it is the field chosen.
LATENCY_DATASET_ID: Final = "3f8c1a20-0000-4000-8000-00000000e002"


def _with_latency(hint: int, dataset_id: str = LATENCY_DATASET_ID) -> dict[str, Any]:
    """The golden dataset under a new id, with ``hint`` on every pool entry."""
    document = load_document("datasets/priya-missing-docs.json")
    document["id"] = dataset_id
    for entry in document["pools"][POOL_NODE]:
        entry["latency_hint_ms"] = hint
    return document


@pytest.fixture
def latency(seeded: ServiceContext) -> ServiceContext:
    """The published blueprint plus a dataset whose pool carries a latency hint."""
    seeded.store.put_dataset(Dataset.model_validate(_with_latency(400)))
    return seeded


def test_the_latency_hint_is_varied_within_half_to_double_the_template(
    latency: ServiceContext,
) -> None:
    """The one field expansion changes, and the range the module docstring claims.

    Every other field is copied verbatim, because inventing schema-conforming
    content needs a model and ground rule 4 forbids one. ``latency_hint_ms`` is
    the exception: no schema constrains it and the entity timeline does not
    depend on it, so varying it cannot make a conforming fixture
    non-conforming - which is the whole basis for varying it at all.

    Half to double, so a template of 400 yields something in ``[200, 800]``.
    The generated entry is otherwise identical to the template it was copied
    from, which is asserted here rather than left implied.
    """
    reply = expand(latency, LATENCY_DATASET_ID, POOL_NODE, 1)
    assert reply.ok, reply
    pool = payload(reply)["data"]["dataset"]["pools"][POOL_NODE]
    added_entry = pool[AUTHORED_ENTRIES]

    assert 200 <= added_entry["latency_hint_ms"] <= 800
    without_hint = {key: value for key, value in added_entry.items() if key != "latency_hint_ms"}
    templates = [
        {key: value for key, value in entry.items() if key != "latency_hint_ms"}
        for entry in pool[:AUTHORED_ENTRIES]
    ]
    assert without_hint in templates, "the entry is not a copy of an authored fixture"


def test_the_jitter_is_deterministic_like_everything_else(latency: ServiceContext) -> None:
    """The hint is drawn from the same seeded stream as the template choice.

    Which means it is re-derivable: one ``Seeded`` per position, one ``choice``
    then one ``int``, in that order. Re-deriving it here from the public
    :func:`expansion_salt` is what makes this a test of the *contract* rather
    than of the code agreeing with itself.
    """
    grown = payload(expand(latency, LATENCY_DATASET_ID, POOL_NODE, 1))["data"]["dataset"]
    templates = grown["pools"][POOL_NODE][:AUTHORED_ENTRIES]

    source = Seeded(grown["seed"], expansion_salt(POOL_NODE, AUTHORED_ENTRIES))
    chosen = source.choice(templates)
    hint = int(chosen["latency_hint_ms"])
    assert grown["pools"][POOL_NODE][AUTHORED_ENTRIES]["latency_hint_ms"] == source.int(
        clamp(hint // 2), clamp(hint * 2)
    )


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        (0, (0, 0)),
        (1, (0, 2)),
        (400, (200, 800)),
        (MAX_STORED_INT, (MAX_STORED_INT // 2, MAX_STORED_INT)),
        (MAX_STORED_INT // 2 + 1, (MAX_STORED_INT // 4 + 1, MAX_STORED_INT)),
        (-5, (0, 0)),
        (-(10**30), (0, 0)),
    ],
    ids=["zero", "one", "typical", "at-the-width", "doubles-past-the-width", "negative", "absurd"],
)
def test_the_jitter_range_clamps_at_both_ends(hint: int, expected: tuple[int, int]) -> None:
    """The clamp arithmetic, asserted **exactly** rather than through a draw.

    This is the second attempt and the reason for it is worth recording. The
    first version asserted the *stored* hint fell inside the expected band, and
    at ``MAX_STORED_INT`` it **passed without the clamp** - the draw happened to
    land below the column width, so the test proved nothing about the case it
    was written for. Verified by removing the clamp: the negative case failed,
    the at-the-width case did not.

    So the range is the unit. ``doubles-past-the-width`` is the case that
    matters: the unclamped upper bound is above ``MAX_STORED_INT`` while the
    lower bound is not, so the clamp is doing work no draw can hide.
    """
    assert jittered_range(hint) == expected


@given(hint=st.integers())
def test_the_jitter_range_is_never_inverted_and_never_unstorable(hint: int) -> None:
    """The two properties, over every ``int`` rather than over seven of them.

    ``lo <= hi``, because an inverted range raises ``ValueError`` out of
    ``Seeded.int`` - and a ``latency_hint_ms`` is authored content that ruling
    R-04 puts no constraint on, so any integer can arrive. And both ends
    storable, because the value drawn from this range is written to a column.
    """
    low, high = jittered_range(hint)
    assert low <= high
    assert storable(low) and storable(high)


def test_the_stored_hint_lands_in_the_range_for_a_hint_at_the_column_width(
    seeded: ServiceContext,
) -> None:
    """And the behavioural half, at the boundary, so the two are connected.

    The range is exact above; this is that range reaching a column. A hint at
    ``MAX_STORED_INT`` is the case where an unclamped range could write an
    unstorable value, so the assertion is ``storable`` rather than a band.
    """
    identifier = "3f8c1a20-0000-4000-8000-00000000e0ff"
    seeded.store.put_dataset(Dataset.model_validate(_with_latency(MAX_STORED_INT, identifier)))

    reply = expand(seeded, identifier, POOL_NODE, 1)
    assert reply.ok, reply
    added_entry = payload(reply)["data"]["dataset"]["pools"][POOL_NODE][AUTHORED_ENTRIES]
    low, high = jittered_range(MAX_STORED_INT)
    assert low <= added_entry["latency_hint_ms"] <= high
    assert storable(added_entry["latency_hint_ms"])


def test_the_expansion_salt_is_the_documented_shape() -> None:
    """The determinism contract in one line, pinned like a derivation.

    Every previously expanded entry stops being re-derivable from its dataset's
    seed if this changes, which is the same class of consequence
    ``Seeded.uuid``'s pinned derivation carries.
    """
    assert expansion_salt("request_docs", 2) == "expand:request_docs:2"
    assert expansion_salt("a.b", 0) == "expand:a.b:0"


def test_a_replicated_entry_still_satisfies_the_output_schema(seeded: ServiceContext) -> None:
    """Why expansion copies rather than invents, stated as the property it buys.

    DS-019 validates every pool fixture against the node's ``output_schema``,
    the schema is user-supplied JSON Schema, and generating a conforming
    instance of an arbitrary schema needs a model - which ground rule 4 forbids
    anywhere in this service. Copying an authored entry satisfies the schema by
    construction, and the successful expansion above is the proof: DS-019 ran
    against the built document before it was stored.
    """
    reply = expand(seeded, dataset_id(), POOL_NODE, 1)
    assert reply.ok
    grown = payload(reply)["data"]["dataset"]["pools"][POOL_NODE]
    assert grown[AUTHORED_ENTRIES] in grown[:AUTHORED_ENTRIES], (
        "an expanded entry is not a copy of an authored one"
    )
