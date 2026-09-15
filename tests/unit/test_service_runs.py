"""The runtime read path: pinning, drawing, warning, and never advancing twice.

M6's five acceptance clauses, and where each one is asserted:

1. *the same step key twice returns byte-identical fixtures and advances
   nothing* - :func:`test_a_repeated_fetch_returns_the_stored_document_not_a_fresh_draw`
   and :func:`test_a_repeated_fetch_appends_no_path_entry_and_allocates_no_seq`.
   The first is the losing side: it pre-seeds the step key with a marker
   document through ``upsert_step``, so a ``fetch_step`` that re-drew from the
   dataset would return the *authored* fixture and fail. A test that only
   compared two ``fetch_step`` responses to each other would pass against a
   re-drawing implementation, because the dataset is immutable and the two
   draws would be equal.
2. *a repeated tool name resolves by position, and errors with named
   candidates* - `test_service_resolution.py` owns the algorithm;
   :func:`test_the_walk_resolves_one_tool_name_to_two_nodes` walks it through
   the real store so the head comes from a real reconstructed path.
3. *a loop past pool length repeats the last entry and warns* -
   :func:`test_the_pool_draws_in_order_then_repeats_the_last_entry`, at both
   ends of ruling R-52's boundary: iteration 1 draws ``pools[1]`` with **no**
   warning and iteration 2 repeats it **with** one.
4. *a version mismatch warns and still serves* -
   :func:`test_a_declared_version_mismatch_warns_and_still_serves`, which
   fetches a step afterwards rather than only inspecting the warning.
5. *no write path from ``fetch_step`` to a dataset* -
   `test_runtime_is_read_only.py` owns it, mechanically.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from agentprops.models import (
    RT_E02,
    RT_E03,
    RT_E04,
    WARNING_BLUEPRINT_VERSION_MISMATCH,
    WARNING_DATASET_ARCHIVED,
    WARNING_POOL_EXHAUSTED,
    Dataset,
    RunQuery,
    StepRecord,
)
from agentprops.service import ServiceContext, blueprints, datasets, runs
from agentprops.service.envelope import AP_ARGUMENT, AP_DOCUMENT_SHAPE
from agentprops.service.limits import MAX_STORED_INT
from agentprops.service.runs import (
    DEFAULT_RUN_CLASS,
    RUN_ID_MAX_LENGTH,
    RUN_ID_MIN_LENGTH,
    WARNING_DATASET_SELECTION_AMBIGUOUS,
    WARNING_RUN_START_MISMATCH,
)
from agentprops.service.runs import paginate as paginate_runs
from conftest import FROZEN_NOW, load_document
from envelopes import codes, data, findings, rules, warnings_of

AGENT = "location-onboarding"
RUN_ID = "m6-priya-run"
PRIYA = "3f8c1a20-0000-4000-8000-000000000001"
ARUN = "3f8c1a20-0000-4000-8000-000000000002"

#: The tool name ``fetch_store_profile`` and ``recheck_store`` share.
REPEATED_TOOL = "acme.stores.get"

#: The golden loop node and the length of its pool. **Two** entries, so
#: exhaustion begins at iteration 2 (ruling R-52 corrects `worked-example.md`
#: section 7 step 7, which says 3). Read off the fixture rather than written as
#: a literal, so padding the pool could never make this file silently agree.
POOL_NODE = "request_docs"
POOL_LENGTH = len(load_document("datasets/priya-missing-docs.json")["pools"][POOL_NODE])


@pytest.fixture
def seeded(
    context: ServiceContext,
    blueprint_document: dict[str, Any],
    dataset_document: dict[str, Any],
    other_dataset_document: dict[str, Any],
) -> ServiceContext:
    """The published blueprint and both golden datasets. `priya` is the older."""
    blueprints.upsert(context, blueprint_document, publish=True)
    context.store.put_dataset(Dataset.model_validate(dataset_document))
    context.store.put_dataset(Dataset.model_validate(other_dataset_document))
    return context


@pytest.fixture
def started(seeded: ServiceContext) -> ServiceContext:
    """A run pinned to `priya` by explicit id, with nothing served yet."""
    reply = runs.start(seeded, RUN_ID, AGENT, {"dataset_id": PRIYA})
    assert reply.ok, reply
    return seeded


def fetch(context: ServiceContext, **arguments: Any) -> Any:
    """``fetch_step`` on :data:`RUN_ID`, for the many calls that walk a path."""
    return runs.fetch_step(context, RUN_ID, **arguments)


def step_of(reply: Any) -> dict[str, Any]:
    payload: dict[str, Any] = data(reply)["step"]
    return payload


def stored_run(context: ServiceContext) -> dict[str, Any]:
    document: dict[str, Any] = data(runs.get(context, RUN_ID))["run"]
    return document


# ------------------------------------------------------------------ run_start


def test_start_pins_the_dataset_version_and_the_blueprint_version(
    seeded: ServiceContext,
) -> None:
    """The three values PRD 5.6 says a run holds for its whole life."""
    payload = data(runs.start(seeded, RUN_ID, AGENT, {"dataset_id": PRIYA}))["start"]
    assert payload["pin"] == {
        "dataset_id": PRIYA,
        "dataset_version": 1,
        "blueprint_version": "1.0.0",
    }
    assert payload["run"]["pin"] == payload["pin"], "the pin is carried in both places"
    assert payload["run"]["status"] == "running"
    assert payload["run"]["run_class"] == DEFAULT_RUN_CLASS
    assert payload["run"]["path"] == [] and payload["run"]["steps"] == []


def test_start_selects_by_labels(seeded: ServiceContext) -> None:
    payload = data(
        runs.start(seeded, RUN_ID, AGENT, {"labels": {"scenario": "missing-documents"}})
    )["start"]
    assert payload["pin"]["dataset_id"] == PRIYA


def test_a_label_selector_matching_several_datasets_assigns_the_oldest_and_warns(
    seeded: ServiceContext,
) -> None:
    """Selection is assignment, not reservation (PRD 5.6 point 2).

    Both golden datasets carry ``edge_case: none``, so this selector matches two.
    The oldest wins - ``find_datasets`` orders by ``(created_at, id)``, which
    ruling R-35 makes total - and the caller is told, because a label query
    broader than the author intended is otherwise invisible.
    """
    reply = runs.start(seeded, RUN_ID, AGENT, {"labels": {"edge_case": "none"}})
    assert data(reply)["start"]["pin"]["dataset_id"] == PRIYA
    assert codes(reply) == [WARNING_DATASET_SELECTION_AMBIGUOUS]
    detail = warnings_of(reply)[0].detail
    assert detail["matched"] == 2
    assert detail["assigned"] == PRIYA


def test_a_single_match_carries_no_ambiguity_warning(seeded: ServiceContext) -> None:
    """The other end of the boundary above: one match is not ambiguous."""
    reply = runs.start(seeded, RUN_ID, AGENT, {"labels": {"scenario": "compliance-overdue"}})
    assert data(reply)["start"]["pin"]["dataset_id"] == ARUN
    assert codes(reply) == []


def test_a_label_selector_matching_nothing_is_rt_e04(seeded: ServiceContext) -> None:
    reply = runs.start(seeded, RUN_ID, AGENT, {"labels": {"scenario": "no-such-scenario"}})
    assert rules(reply) == [RT_E04]
    assert findings(reply)[0].pointer == "/selector/labels"


def test_an_unknown_dataset_id_is_rt_e04(seeded: ServiceContext) -> None:
    reply = runs.start(
        seeded, RUN_ID, AGENT, {"dataset_id": "3f8c1a20-0000-4000-8000-999999999999"}
    )
    assert rules(reply) == [RT_E04]
    assert findings(reply)[0].pointer == "/selector/dataset_id"


def test_a_dataset_for_another_agent_is_rt_e04(seeded: ServiceContext) -> None:
    """A dataset from another graph would report RT-E02 at every step.

    Refusing at ``run_start`` is resolution, not policy: there is no dataset
    with that id *for this agent*.
    """
    reply = runs.start(seeded, "m6-other-agent", "delivery-dispatch", {"dataset_id": PRIYA})
    assert rules(reply) == [RT_E04]
    assert findings(reply)[0].context["dataset_agent_id"] == AGENT


@pytest.mark.parametrize(
    ("selector", "pointer"),
    [
        ({}, "/selector"),
        ({"dataset_id": PRIYA, "labels": {"tier": "regional"}}, "/selector"),
        ({"label": {"tier": "regional"}}, "/selector/label"),
        ({"dataset_id": 7}, "/selector/dataset_id"),
        ({"labels": "regional"}, "/selector/labels"),
        ({"labels": {"tier": 7}}, "/selector/labels/tier"),
    ],
    ids=["neither", "both", "typo", "id-not-a-string", "labels-not-an-object", "value-not-string"],
)
def test_a_malformed_selector_is_ap_001_pointed_at_the_offending_key(
    seeded: ServiceContext, selector: dict[str, Any], pointer: str
) -> None:
    """One of two keys, and a typo is refused rather than read as an empty selector."""
    reply = runs.start(seeded, RUN_ID, AGENT, selector)
    assert AP_ARGUMENT in rules(reply)
    assert pointer in [finding.pointer for finding in findings(reply)]


@pytest.mark.parametrize(
    ("run_id", "ok"),
    [
        ("a" * (RUN_ID_MIN_LENGTH - 1), False),
        ("a" * RUN_ID_MIN_LENGTH, True),
        ("a" * RUN_ID_MAX_LENGTH, True),
        ("a" * (RUN_ID_MAX_LENGTH + 1), False),
        ("run/with/slashes", False),
        ("run id with spaces", False),
        ("Run_1.2:3-4", True),
    ],
    ids=["too-short", "shortest", "longest", "too-long", "slashes", "spaces", "every-legal-class"],
)
def test_the_run_id_shape_is_checked_at_both_ends(
    seeded: ServiceContext, run_id: str, ok: bool
) -> None:
    """contracts 2.3: 8 to 128 characters, ``^[A-Za-z0-9_.:-]+$``.

    Both ends of the length range, because an entry that reasons about a
    boundary has to test both - the lesson M4's fix round recorded.
    """
    reply = runs.start(seeded, run_id, AGENT, {"dataset_id": PRIYA})
    assert reply.ok is ok, reply
    if not ok:
        assert rules(reply) == [AP_ARGUMENT]
        assert findings(reply)[0].pointer == "/run_id"


def test_an_unknown_run_class_is_ap_001_and_a_known_one_is_kept(seeded: ServiceContext) -> None:
    refused = runs.start(seeded, RUN_ID, AGENT, {"dataset_id": PRIYA}, run_class="production")
    assert rules(refused) == [AP_ARGUMENT]
    assert findings(refused)[0].pointer == "/run_class"
    kept = runs.start(seeded, RUN_ID, AGENT, {"dataset_id": PRIYA}, run_class="load")
    assert data(kept)["start"]["run"]["run_class"] == "load"


def test_the_model_is_recorded_and_a_partial_one_is_ap_003(seeded: ServiceContext) -> None:
    """``model`` is a document, so its shape failure is the R-04/R-23 residue."""
    model = {"provider": "anthropic", "name": "claude-opus-5", "version": "20260401"}
    recorded = runs.start(seeded, RUN_ID, AGENT, {"dataset_id": PRIYA}, model=model)
    assert data(recorded)["start"]["run"]["model"] == model
    partial = runs.start(seeded, "m6-partial", AGENT, {"dataset_id": PRIYA}, model={"name": "x"})
    assert rules(partial) == [AP_DOCUMENT_SHAPE, AP_DOCUMENT_SHAPE]


def test_two_bad_arguments_are_both_reported(seeded: ServiceContext) -> None:
    """Collected rather than short-circuited, the way ``ArgReader`` collects."""
    reply = runs.start(seeded, "short", AGENT, {}, run_class="production")
    assert [finding.pointer for finding in findings(reply)] == [
        "/run_id",
        "/run_class",
        "/selector",
    ]


# --------------------------------------------------- run_start: version pinning


def test_a_declared_version_mismatch_warns_and_still_serves(seeded: ServiceContext) -> None:
    """Acceptance clause 4. The warning is on the response *and* on the run.

    And "still serves" is asserted by serving: the run fetches a step
    afterwards. A test that stopped at the warning would pass against a
    ``run_start`` that recorded the mismatch and then refused every step.
    """
    reply = runs.start(
        seeded, RUN_ID, AGENT, {"dataset_id": PRIYA}, declared_blueprint_version="1.1.0"
    )
    assert reply.ok is True
    assert codes(reply) == [WARNING_BLUEPRINT_VERSION_MISMATCH]
    detail = warnings_of(reply)[0].detail
    assert (detail["declared"], detail["pinned"]) == ("1.1.0", "1.0.0")

    stored = stored_run(seeded)
    assert [item["code"] for item in stored["warnings"]] == [WARNING_BLUEPRINT_VERSION_MISMATCH]
    assert stored["declared_blueprint_version"] == "1.1.0"

    served = step_of(fetch(seeded, node_id="receive_request"))
    assert served["resolved_node_id"] == "receive_request"


def test_declaring_the_pinned_version_warns_about_nothing(seeded: ServiceContext) -> None:
    """The other end: agreement is silent."""
    reply = runs.start(
        seeded, RUN_ID, AGENT, {"dataset_id": PRIYA}, declared_blueprint_version="1.0.0"
    )
    assert codes(reply) == []


def test_an_archived_dataset_is_servable_by_explicit_id_and_invisible_to_labels(
    seeded: ServiceContext,
) -> None:
    """PRD 5.6's archive asymmetry, on the selector.

    An explicit id serves it - "an archived dataset disappears from
    ``dataset_find`` but stays servable to any run holding a pin to it" - and a
    label query does not, because discovery is exactly what archiving removes a
    dataset from.
    """
    datasets.archive(seeded, PRIYA)
    named = runs.start(seeded, RUN_ID, AGENT, {"dataset_id": PRIYA})
    assert named.ok is True
    assert codes(named) == [WARNING_DATASET_ARCHIVED]
    discovered = runs.start(
        seeded, "m6-by-labels", AGENT, {"labels": {"scenario": "missing-documents"}}
    )
    assert rules(discovered) == [RT_E04]


def test_a_dataset_archived_after_the_run_started_still_serves_with_a_warning(
    started: ServiceContext,
) -> None:
    """contracts 3.4's ``dataset_archived``: "has since been archived. Served anyway"."""
    datasets.archive(started, PRIYA)
    reply = fetch(started, node_id="receive_request")
    assert reply.ok is True
    assert codes(reply) == [WARNING_DATASET_ARCHIVED]
    assert (
        step_of(reply)["fixture"]
        == load_document("datasets/priya-missing-docs.json")["nodes"]["receive_request"]
    )


# -------------------------------------------------------- run_start: the replay


def test_starting_an_existing_run_id_returns_it_unchanged_and_never_re_pins(
    started: ServiceContext, dataset_document: dict[str, Any]
) -> None:
    """The pin is immutable for the run's whole life (PRD 5.6).

    A second ``run_start`` for a live run id is a **replay**: same run, same
    pin, same ``started_at``, no write. This is the losing side that matters -
    the dataset has a version 2 by the time the second call arrives, and a
    ``run_start`` that re-resolved the selector would hand a run already served
    from version 1 a pin to version 2, which is the one thing copy-on-write
    exists to prevent.
    """
    fetch(started, node_id="receive_request")
    edited = copy.deepcopy(dataset_document)
    edited["narrative"] = dataset_document["narrative"] + " Edited."
    assert started.store.put_dataset(Dataset.model_validate(edited)).version == 2

    first = stored_run(started)
    reply = runs.start(started, RUN_ID, AGENT, {"labels": {"scenario": "missing-documents"}})
    replayed = data(reply)["start"]
    assert replayed["pin"]["dataset_version"] == 1
    assert replayed["run"]["started_at"] == first["started_at"]
    assert len(replayed["run"]["steps"]) == 1, "the replay kept the served step"
    assert started.store.health().counts.runs == 1
    assert codes(reply) == [WARNING_RUN_START_MISMATCH], (
        "ruling R-53: the label query now resolves to version 2, which is a divergence from the "
        "pin and has to be said out loud rather than silently ignored"
    )


def test_a_replay_reports_the_warnings_recorded_at_the_first_start(
    seeded: ServiceContext,
) -> None:
    """A mismatch recorded once stays visible on every retry.

    The replay path passes no fresh warning list, so the stored run's own
    warnings are what come back - which is what a client that retried through a
    network timeout needs to see.
    """
    runs.start(seeded, RUN_ID, AGENT, {"dataset_id": PRIYA}, declared_blueprint_version="9.9.9")
    replay = runs.start(seeded, RUN_ID, AGENT, {"dataset_id": PRIYA})
    assert codes(replay) == [WARNING_BLUEPRINT_VERSION_MISMATCH]


def test_a_malformed_argument_is_still_refused_on_a_replay(started: ServiceContext) -> None:
    """Argument checks precede the existence check: a bad request is a bad request."""
    assert rules(runs.start(started, RUN_ID, AGENT, {})) == [AP_ARGUMENT]


def test_a_replay_with_a_diverging_selector_warns_and_serves_the_pinned_run(
    started: ServiceContext,
) -> None:
    """Ruling R-53. The pin does not move, and the divergence does not pass silently.

    Ground rule 3 decides it without a new principle: "mismatches produce
    warnings attached to the response and to the stored run". A diverging
    selector is a mismatch of exactly the shape ``blueprint_version_mismatch``
    already handles - the caller declared one thing, the run is pinned to
    another - so serve the pin and say so.
    """
    reply = runs.start(started, RUN_ID, AGENT, {"dataset_id": ARUN})
    assert reply.ok is True
    assert data(reply)["start"]["pin"]["dataset_id"] == PRIYA, "the pin never moves"
    assert codes(reply) == [WARNING_RUN_START_MISMATCH]
    detail = warnings_of(reply)[0].detail
    assert detail["diverged"] == ["selector"]
    assert detail["pinned"]["pin"]["dataset_id"] == PRIYA
    assert detail["requested"]["pin"]["dataset_id"] == ARUN

    recorded = stored_run(started)["warnings"]
    assert [item["code"] for item in recorded] == [WARNING_RUN_START_MISMATCH], (
        "the warning has to reach the stored run too, not only the response"
    )


def test_a_replay_naming_a_dataset_that_resolves_to_nothing_still_warns(
    started: ServiceContext,
) -> None:
    """R-53's motivating case: "the one that surprises a caller who mistyped a
    ``dataset_id``".

    A mistyped id does not resolve to a *different* pin, it resolves to nothing.
    Treating an unresolvable selector as "cannot compare, say nothing" would
    miss the example the ruling was written about, so it counts as divergence
    and ``requested.pin`` is null.
    """
    reply = runs.start(
        started, RUN_ID, AGENT, {"dataset_id": "3f8c1a20-0000-4000-8000-00000000ffff"}
    )
    assert reply.ok is True, "a retry must not fail because the world changed"
    assert codes(reply) == [WARNING_RUN_START_MISMATCH]
    detail = warnings_of(reply)[0].detail
    assert detail["diverged"] == ["selector"]
    assert detail["requested"]["pin"] is None


def test_a_replay_under_another_agent_id_warns_too(started: ServiceContext) -> None:
    """The half of R-53 the review added: ``run_start(same_id, other_agent, ...)``.

    It silently returned another agent's run. The ruling was extended to cover
    it because it is the same shape of surprise as the selector case.

    ``agent_id`` cannot diverge *alone*: a dataset belongs to one agent, so the
    selector cannot resolve to the same pin under a different one, and both
    fields are reported. Asserted as membership rather than equality for that
    reason.
    """
    reply = runs.start(started, RUN_ID, "delivery-dispatch", {"dataset_id": PRIYA})
    assert reply.ok is True
    assert data(reply)["start"]["run"]["agent_id"] == AGENT
    detail = warnings_of(reply)[0].detail
    assert "agent_id" in detail["diverged"]
    assert detail["pinned"]["agent_id"] == AGENT
    assert detail["requested"]["agent_id"] == "delivery-dispatch"


def test_an_identical_replay_warns_about_nothing(started: ServiceContext) -> None:
    """The other end of the boundary: a retry that matches is silent.

    This is the case the run id exists for - a client repeating its request
    after a network blip - and a warning on it would be noise on every retry.
    """
    assert codes(runs.start(started, RUN_ID, AGENT, {"dataset_id": PRIYA})) == []
    assert stored_run(started)["warnings"] == []


def test_the_divergence_warning_is_recorded_once_however_many_retries(
    started: ServiceContext,
) -> None:
    """R-54(b)'s key, on a code with no ``node_id`` or ``iteration``.

    ``run_start_mismatch`` keys on ``(code, None, None)``, so a client retrying
    a diverging request in a loop cannot grow the run's warning list without
    bound - while every response still carries the warning, which is what
    ground rule 3 asks for.
    """
    for _ in range(3):
        reply = runs.start(started, RUN_ID, AGENT, {"dataset_id": ARUN})
        assert codes(reply) == [WARNING_RUN_START_MISMATCH]
    assert len(stored_run(started)["warnings"]) == 1


def test_an_unparseable_dataset_id_is_rt_e04_rather_than_an_exception(
    seeded: ServiceContext,
) -> None:
    """A malformed id is a miss, not a raise - the store's read path guarantees it.

    ``run_find``'s equivalent was tested and ``run_start``'s was not, which is
    the asymmetry this closes: any string can arrive as a ``dataset_id``, and
    the adapter returns ``None`` for one that is not a well-formed UUID rather
    than raising.
    """
    reply = runs.start(seeded, RUN_ID, AGENT, {"dataset_id": "not-a-uuid"})
    assert rules(reply) == [RT_E04]
    assert findings(reply)[0].pointer == "/selector/dataset_id"


# ------------------------------------------------------------------ fetch_step


def test_the_served_fixture_is_the_authored_document_byte_for_byte(
    started: ServiceContext,
) -> None:
    """PRD design principle 2: the environment is held byte-identical.

    ``exclude_unset`` is what makes this true - the golden fixture omits
    ``fault`` and ``input`` in places, and a full dump would hand the agent
    those keys as ``null``.
    """
    golden = load_document("datasets/priya-missing-docs.json")
    served = step_of(fetch(started, node_id="fetch_store_profile"))
    assert served["fixture"] == golden["nodes"]["fetch_store_profile"]
    assert served["resolved_node_id"] == "fetch_store_profile"


def test_a_served_step_is_recorded_on_the_run_and_reconstructs_the_path(
    started: ServiceContext,
) -> None:
    fetch(started, node_id="receive_request")
    fetch(started, node_id="fetch_store_profile")
    stored = stored_run(started)
    assert [(entry["node_id"], entry["iteration"]) for entry in stored["path"]] == [
        ("receive_request", 0),
        ("fetch_store_profile", 0),
    ]
    assert [step["seq"] for step in stored["steps"]] == [1, 2]
    assert stored["steps"][0]["actual"] is None, "fetch_step records what was served, only that"


def test_an_unknown_run_is_rt_e03(seeded: ServiceContext) -> None:
    reply = runs.fetch_step(seeded, "no-such-run", node_id="receive_request")
    assert rules(reply) == [RT_E03]
    assert findings(reply)[0].pointer == "/run_id"


def test_fetch_step_surfaces_the_resolution_findings(started: ServiceContext) -> None:
    """The service does not repackage what `resolution.py` reported."""
    assert rules(fetch(started, node_id="no_such_node")) == [RT_E02]
    assert rules(fetch(started)) == [RT_E02]


def test_the_walk_resolves_one_tool_name_to_two_nodes(started: ServiceContext) -> None:
    """Acceptance clause 2, through the store rather than against a hand-built path.

    This is the M8 script's step 7: the head comes from a path reconstructed
    from ``run_steps``, so it exercises ruling R-37's ordering as well as the
    algorithm.
    """
    fetch(started, node_id="receive_request")
    early = step_of(fetch(started, tool_name=REPEATED_TOOL))
    assert early["resolved_node_id"] == "fetch_store_profile"

    fetch(started, node_id="check_docs")
    ambiguous = fetch(started, tool_name=REPEATED_TOOL)
    assert rules(ambiguous) == ["RT-E01"]
    assert findings(ambiguous)[0].context["candidates"] == [
        "fetch_store_profile",
        "recheck_store",
    ]

    fetch(started, node_id=POOL_NODE, iteration=0)
    late = step_of(fetch(started, tool_name=REPEATED_TOOL))
    assert late["resolved_node_id"] == "recheck_store"


# -------------------------------------------------------- fetch_step: the pool


def test_the_pool_draws_in_order_then_repeats_the_last_entry(started: ServiceContext) -> None:
    """Acceptance clause 3, and ruling R-52's boundary at **both** ends.

    The golden pool has two entries. Iterations 0 and 1 draw their own entry and
    warn about nothing; iteration 2 is already a repeat of ``pools[1]`` and
    carries ``pool_exhausted``. `worked-example.md` section 7 step 7 puts that
    boundary at 3; R-52 corrects it, and the pool length is read off the fixture
    here so the assertion follows the fixture rather than a literal.
    """
    pool = load_document("datasets/priya-missing-docs.json")["pools"][POOL_NODE]
    assert POOL_LENGTH == 2, "R-52's boundary is stated for a two-entry pool"

    for iteration in range(POOL_LENGTH):
        reply = fetch(started, node_id=POOL_NODE, iteration=iteration)
        assert step_of(reply)["fixture"] == pool[iteration]
        assert codes(reply) == [], f"iteration {iteration} is inside the pool"

    for iteration in (POOL_LENGTH, POOL_LENGTH + 1):
        reply = fetch(started, node_id=POOL_NODE, iteration=iteration)
        assert step_of(reply)["fixture"] == pool[-1], "the last entry repeats"
        assert codes(reply) == [WARNING_POOL_EXHAUSTED]
        detail = warnings_of(reply)[0].detail
        assert detail == {
            "node_id": POOL_NODE,
            "iteration": iteration,
            "pool_length": POOL_LENGTH,
            "served_index": POOL_LENGTH - 1,
        }


def test_an_exhausted_pool_flags_the_run_once_per_iteration(started: ServiceContext) -> None:
    """PRD 5.2: "the run is flagged". Contracts 3.4: on the response *and* the run.

    De-duplication is on the whole ``(code, detail)`` pair, so a replay of one
    exhausted iteration adds nothing while a *different* exhausted iteration
    records its own entry.
    """
    fetch(started, node_id=POOL_NODE, iteration=POOL_LENGTH)
    fetch(started, node_id=POOL_NODE, iteration=POOL_LENGTH)
    fetch(started, node_id=POOL_NODE, iteration=POOL_LENGTH + 1)
    recorded = stored_run(started)["warnings"]
    assert [item["code"] for item in recorded] == [WARNING_POOL_EXHAUSTED] * 2
    assert [item["detail"]["iteration"] for item in recorded] == [POOL_LENGTH, POOL_LENGTH + 1]


def test_iterating_past_the_pool_is_never_an_error_however_far(started: ServiceContext) -> None:
    """Ruling R-03: RT-E05 is deleted, and there is no cap to trip.

    ``max_iterations`` on the golden loop node is 3. This asks for iteration
    ``MAX_STORED_INT``, which is both far past the pool and far past
    ``max_iterations``, and it must **serve** - the service never gates on
    iteration count, and the largest storable value is still storable.
    """
    reply = fetch(started, node_id=POOL_NODE, iteration=MAX_STORED_INT)
    assert reply.ok is True
    assert codes(reply) == [WARNING_POOL_EXHAUSTED]
    assert stored_run(started)["steps"][0]["iteration"] == MAX_STORED_INT


@pytest.mark.parametrize(
    "iteration", [-1, MAX_STORED_INT + 1, -(2**63) - 1], ids=["negative", "above", "below"]
)
def test_an_unaddressable_iteration_is_rt_e02(started: ServiceContext, iteration: int) -> None:
    """R-03 assigns a negative iteration to RT-E02; an unstorable one is the same answer.

    ``iteration`` is an *identifier* of a step, not a quantity, so it is refused
    rather than clamped - and without the range check the value reaches
    ``upsert_step`` and pysqlite raises, which is ruling R-50's whole subject.
    Both ends of the storable range, plus the negative case R-03 names.
    """
    reply = fetch(started, node_id=POOL_NODE, iteration=iteration)
    assert rules(reply) == [RT_E02]
    assert findings(reply)[0].pointer == "/iteration"
    assert stored_run(started)["steps"] == [], "a refused iteration records nothing"


def test_a_non_zero_iteration_on_a_node_without_a_pool_is_rt_e02(
    started: ServiceContext,
) -> None:
    """R-03's complement, and not a revived RT-E05: the node has one fixture."""
    reply = fetch(started, node_id="receive_request", iteration=1)
    assert rules(reply) == [RT_E02]
    assert findings(reply)[0].context["node_id"] == "receive_request"
    assert fetch(started, node_id="receive_request", iteration=0).ok is True


def test_an_omitted_iteration_means_zero(started: ServiceContext) -> None:
    pool = load_document("datasets/priya-missing-docs.json")["pools"][POOL_NODE]
    assert step_of(fetch(started, node_id=POOL_NODE))["fixture"] == pool[0]


def test_a_pool_on_a_non_loop_node_draws_exactly_like_a_loop_node(
    seeded: ServiceContext, blueprint_document: dict[str, Any], dataset_document: dict[str, Any]
) -> None:
    """Ruling R-51: the pool mechanism is orthogonal to ``kind``.

    BP-017 makes ``kind: loop`` imply ``pool: true`` and deliberately not the
    converse, so this publishes a 1.1.0 where the **entry** node - a
    ``tool_call`` with no ``max_iterations`` - declares ``pool: true``, and a
    dataset that moves its fixture into ``pools`` accordingly (ruling R-01). The
    draw is asserted to be identical, including the exhaustion boundary: in
    order, then the last entry repeating with a warning.
    """
    graph = copy.deepcopy(blueprint_document)
    graph["version"] = "1.1.0"
    for node in graph["nodes"]:
        if node["id"] == "receive_request":
            node["pool"] = True
    assert blueprints.upsert(seeded, graph, publish=True).ok

    document = copy.deepcopy(dataset_document)
    document["id"] = "3f8c1a20-0000-4000-8000-0000000000aa"
    document["blueprint"]["version"] = "1.1.0"
    first = document["nodes"].pop("receive_request")
    second = copy.deepcopy(first)
    second["output"]["store_id"] = "ST-4472"
    document["pools"]["receive_request"] = [first, second]
    assert datasets.validate(seeded, document).ok, datasets.validate(seeded, document)
    seeded.store.put_dataset(Dataset.model_validate(document))

    assert runs.start(seeded, "m6-r51-run", AGENT, {"dataset_id": document["id"]}).ok

    def draw(iteration: int) -> Any:
        return runs.fetch_step(seeded, "m6-r51-run", node_id="receive_request", iteration=iteration)

    assert step_of(draw(0))["fixture"] == first
    assert codes(draw(0)) == []
    assert step_of(draw(1))["fixture"] == second
    assert codes(draw(1)) == []
    assert step_of(draw(2))["fixture"] == second
    assert codes(draw(2)) == [WARNING_POOL_EXHAUSTED]


# ------------------------------------------------------- fetch_step: idempotency


def test_a_repeated_fetch_returns_byte_identical_output(started: ServiceContext) -> None:
    """Acceptance clause 1, the visible half."""
    first = fetch(started, node_id="fetch_store_profile")
    second = fetch(started, node_id="fetch_store_profile")
    assert json.dumps(first.model_dump(mode="json"), sort_keys=True) == json.dumps(
        second.model_dump(mode="json"), sort_keys=True
    )


def test_a_repeated_fetch_appends_no_path_entry_and_allocates_no_seq(
    started: ServiceContext,
) -> None:
    """ "...and advances nothing." The half a byte-comparison cannot see."""
    fetch(started, node_id="fetch_store_profile")
    before = stored_run(started)
    for _ in range(3):
        fetch(started, node_id="fetch_store_profile")
    after = stored_run(started)
    assert after["path"] == before["path"]
    assert after["steps"] == before["steps"]
    assert [step["seq"] for step in after["steps"]] == [1]


def test_a_repeated_fetch_returns_the_stored_document_not_a_fresh_draw(
    started: ServiceContext,
) -> None:
    """The losing side, and the reason this test exists rather than a comparison.

    The dataset is immutable, so two fresh draws for one key are *equal* and a
    test comparing two ``fetch_step`` responses would pass against an
    implementation that re-drew every time - and would keep passing until
    something made the two differ. So the step key is claimed first, through
    the store's own idempotent ``upsert_step``, with a document the dataset does
    not contain. A ``fetch_step`` that answers from the dataset returns the
    authored fixture and fails here; one that answers from the run returns the
    marker.
    """
    marker = {"served": "by an earlier call", "entity_refs": []}
    started.store.upsert_step(
        RUN_ID, StepRecord(node_id="fetch_store_profile", iteration=0, served=marker)
    )
    replayed = step_of(fetch(started, node_id="fetch_store_profile"))
    assert replayed["fixture"] == marker
    assert (
        replayed["fixture"]
        != load_document("datasets/priya-missing-docs.json")["nodes"]["fetch_store_profile"]
    )


def test_the_loser_of_a_concurrent_first_fetch_answers_with_the_stored_document(
    started: ServiceContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The *other* return in ``_serve``, and it is a race, so it is forced.

    The replay branch reads the step off the run. This is the branch that runs
    when the run read came back **stale** - which is exactly what the loser of
    two concurrent first fetches sees - and it must still answer with the
    document that was actually recorded, because ``upsert_step`` is idempotent
    and the winner's row is the one that exists.

    Forced the way `DECISIONS.md` records for M3's race tests: monkeypatch
    **one** internal read to return a stale answer. Everything after it - the
    draw, the ``upsert_step``, the no-op it becomes - runs against the real
    store. Without this the branch would be reachable only by two threads, and
    a threaded test of a window this narrow is either flaky or forced by the
    same kind of hook.
    """
    marker = {"served": "by the winner", "entity_refs": []}
    started.store.upsert_step(
        RUN_ID, StepRecord(node_id="fetch_store_profile", iteration=0, served=marker)
    )
    real_get_run = started.store.get_run

    def stale(run_id: str) -> Any:
        run = real_get_run(run_id)
        return None if run is None else run.model_copy(update={"steps": [], "path": []})

    monkeypatch.setattr(started.store, "get_run", stale)
    served = step_of(fetch(started, node_id="fetch_store_profile"))
    assert served["fixture"] == marker, "the loser re-drew instead of reading the stored row"


def test_a_replayed_warning_writes_nothing_to_the_run(
    started: ServiceContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Advances nothing" includes the warning merge, not only the step row.

    The warning list is the one thing on a replay that could still provoke a
    write, because it is recomputed rather than read back. So the second fetch
    of an exhausted iteration runs with **both** run writes monkeypatched to
    raise: if either is called, this test fails, and the failure is loud rather
    than a duplicated warning nobody notices.

    Both, and not only the one ``_flag`` uses today, because that is how this
    test lost its teeth once already: it patched ``put_run``, and fix round 1
    narrowed the merge onto ``set_run_warnings`` - after which the assertion was
    green and covering nothing. A test named for a write should name every write
    that could satisfy it.
    """
    fetch(started, node_id=POOL_NODE, iteration=POOL_LENGTH)

    def refuse(*arguments: Any, **keywords: Any) -> Any:
        raise AssertionError("a replayed fetch wrote the run")

    monkeypatch.setattr(started.store, "put_run", refuse)
    monkeypatch.setattr(started.store, "set_run_warnings", refuse)
    reply = fetch(started, node_id=POOL_NODE, iteration=POOL_LENGTH)
    assert codes(reply) == [WARNING_POOL_EXHAUSTED], "the response still carries the warning"


def test_a_stale_fetch_that_flags_a_warning_leaves_the_run_lifecycle_alone(
    started: ServiceContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The losing side of the write ``_flag`` used to make. **M8's ``run_finish``.**

    The finished state used to be written here through ``put_run`` directly,
    because ``run_finish`` did not exist. It exists now, so this drives the real
    tool - which makes the test a claim about two production writes racing rather
    than about one write racing a simulation.

    What is being guarded: ``_flag`` once handed ``put_run`` an edited copy of
    the run snapshot ``fetch_step`` read at the top, and ``put_run`` writes
    **every** column from the model it is given. So a ``fetch_step`` adding a
    ``pool_exhausted`` warning from a snapshot taken before a concurrent
    ``run_finish`` committed would revert ``status`` to ``running`` and null
    ``outcome`` and ``finished_at`` - a run that un-finishes itself under load.
    The fix narrows the write to ``set_run_warnings``, one column.

    `test_service_run_writes.py::test_a_stale_finish_cannot_revert_the_runs_warnings`
    is the same race in the other direction, and between them the two narrow
    writes are shown not to be able to revert each other.

    Forced with the M3 race technique: one internal read returns the pre-finish
    snapshot, which is exactly what the concurrent caller holds. Note that a
    *sequential* version of this test cannot fail - a fresh snapshot carries the
    finished state, so writing every column writes it back unchanged - which is
    why the stale read is the test rather than a convenience.
    """
    real_get_run = started.store.get_run
    stale = real_get_run(RUN_ID)
    assert stale is not None
    assert runs.finish(started, RUN_ID, {"training": "reduced"}, "finished").ok

    monkeypatch.setattr(started.store, "get_run", lambda run_id: stale)
    reply = fetch(started, node_id=POOL_NODE, iteration=POOL_LENGTH)
    assert codes(reply) == [WARNING_POOL_EXHAUSTED], "the warning was still attached"

    after = real_get_run(RUN_ID)
    assert after is not None
    assert after.status == "finished", "a warning write reverted the run's lifecycle"
    assert after.finished_at == FROZEN_NOW
    assert after.outcome == {"training": "reduced"}
    assert [item.code for item in after.warnings] == [WARNING_POOL_EXHAUSTED]


def test_idempotency_is_keyed_on_the_resolved_node_and_the_iteration(
    started: ServiceContext,
) -> None:
    """The key is ``(run_id, resolved_node_id, iteration)`` - resolution first.

    Addressing one step by tool name and then by node id is the *same* key, so
    the second call is a replay rather than a second row. The tool-name call
    comes first because it is the one whose answer depends on the position: from
    ``receive_request`` the repeated tool resolves to ``fetch_store_profile``,
    and asking for that node id afterwards must land on the row already written
    rather than allocate a second one.
    """
    fetch(started, node_id="receive_request")
    by_tool = step_of(fetch(started, tool_name=REPEATED_TOOL))
    by_node = step_of(fetch(started, node_id="fetch_store_profile"))
    assert by_tool == by_node
    assert len(stored_run(started)["steps"]) == 2

    fetch(started, node_id=POOL_NODE, iteration=0)
    fetch(started, node_id=POOL_NODE, iteration=1)
    assert len(stored_run(started)["steps"]) == 4, "a different iteration is a different step"


# ------------------------------------------------------------ run_get, run_find


def test_run_get_returns_the_run_in_the_stores_order(started: ServiceContext) -> None:
    """Ruling R-37's ``(seq, node_id, iteration)``, not re-sorted here."""
    fetch(started, node_id=POOL_NODE, iteration=1)
    fetch(started, node_id="receive_request")
    fetch(started, node_id=POOL_NODE, iteration=0)
    from_store = started.store.get_run(RUN_ID)
    assert from_store is not None
    assert stored_run(started)["steps"] == [
        step.model_dump(mode="json") for step in from_store.steps
    ]
    assert [step["seq"] for step in stored_run(started)["steps"]] == [1, 2, 3]


def test_run_get_on_an_unknown_run_is_rt_e03(seeded: ServiceContext) -> None:
    assert rules(runs.get(seeded, "no-such-run")) == [RT_E03]


def test_run_find_returns_summaries_newest_first(seeded: ServiceContext) -> None:
    """``(started_at DESC, id)`` from the store, and no ``outcome`` on a summary.

    Both runs share the frozen clock's ``started_at``, so the id tie-break is
    what orders them - which is the half of ruling R-35 that a single-row test
    would not exercise.
    """
    runs.start(seeded, "m6-run-aaa", AGENT, {"dataset_id": PRIYA})
    runs.start(seeded, "m6-run-bbb", AGENT, {"dataset_id": ARUN})
    rows = data(runs.find(seeded, RunQuery()))["runs"]
    assert [row["id"] for row in rows] == ["m6-run-aaa", "m6-run-bbb"]
    assert all("outcome" not in row for row in rows)


def test_run_find_filters_on_every_documented_parameter(seeded: ServiceContext) -> None:
    model = {"provider": "anthropic", "name": "claude-opus-5", "version": "20260401"}
    runs.start(seeded, "m6-run-dev", AGENT, {"dataset_id": PRIYA}, model=model)
    runs.start(seeded, "m6-run-eval", AGENT, {"dataset_id": ARUN}, run_class="eval")

    def ids(**filters: Any) -> list[str]:
        return [row["id"] for row in data(runs.find(seeded, RunQuery(**filters)))["runs"]]

    assert ids(run_class="eval") == ["m6-run-eval"]
    assert ids(dataset_id=PRIYA) == ["m6-run-dev"]
    assert ids(model="claude-opus-5") == ["m6-run-dev"]
    assert ids(agent_id="nobody") == []
    assert ids(dataset_id="not-a-uuid") == [], "a malformed filter matches nothing, never raises"


def test_run_find_paginates_and_clamps_both_ends() -> None:
    """Ruling R-50 on ``run_find``'s two integers, through the shared helper."""
    assert paginate_runs(RunQuery()).limit == 50
    assert paginate_runs(RunQuery()).offset == 0
    assert paginate_runs(RunQuery(limit=5000)).limit == 5000
    assert paginate_runs(RunQuery(limit=-3, offset=-9)).limit == 0
    assert paginate_runs(RunQuery(limit=-3, offset=-9)).offset == 0
    exact = paginate_runs(RunQuery(limit=MAX_STORED_INT, offset=MAX_STORED_INT))
    assert (exact.limit, exact.offset) == (MAX_STORED_INT, MAX_STORED_INT)
    over = paginate_runs(RunQuery(limit=MAX_STORED_INT + 1, offset=10**40))
    assert (over.limit, over.offset) == (MAX_STORED_INT, MAX_STORED_INT)


def test_run_find_leaves_every_other_filter_alone() -> None:
    query = RunQuery(agent_id=AGENT, dataset_id=PRIYA, run_class="eval", model="claude-opus-5")
    assert paginate_runs(query).model_dump(exclude={"limit", "offset"}) == query.model_dump(
        exclude={"limit", "offset"}
    )


def test_a_run_with_an_out_of_range_limit_still_returns_its_rows(
    started: ServiceContext,
) -> None:
    """The clamp is representability, not a policy cap: nothing is refused."""
    rows = data(runs.find(started, RunQuery(limit=MAX_STORED_INT + 1)))["runs"]
    assert [row["id"] for row in rows] == [RUN_ID]
