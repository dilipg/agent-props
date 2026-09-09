"""``record_step`` and ``run_finish``: the two writes ruling R-15 lands at M8.

`test_service_runs.py` owns the read path. This file owns the two writes, and
the properties it exists to hold are the ones a write has and a read does not:

**The value may already be there.** Both tools can meet a recorded value, and
both answer the way ruling R-53 answers a diverging ``run_start`` - keep what is
stored, return it, warn. So every write here is tested three times: the first
call, the identical repeat (which must be a *silent* no-op, or a retry after a
network blip is not safe), and the divergent repeat (which must warn and must
**not** overwrite). The middle case is the one a naive implementation gets right
by accident and the third is the one it gets wrong.

**Neither may grade** (ground rule 2).
:func:`test_record_step_stores_an_actual_the_schema_would_reject` and
:func:`test_run_finish_stores_an_outcome_the_schema_would_reject` are that rule
as tests rather than as a docstring: both send a document the blueprint's own
schema rejects and assert it is stored verbatim. A service that validated here
would be holding the comparison the client owns, and would turn a finding
*about the agent* into a refusal to record what the agent did.

**Neither may gate** (ground rule 3, ruling R-54(c)). A finished run still
serves, and :func:`test_a_finished_run_still_serves_and_says_so` asserts both
halves: the fixture comes back byte-identical to the one served before the run
was closed, *and* the response carries ``run_already_finished``. A test that
only checked the warning would pass against a ``fetch_step`` that refused.

**The lifecycle write may not touch the warnings column.** That is the M6
finding in the opposite direction, and
:func:`test_a_stale_finish_cannot_revert_the_runs_warnings` forces it with the
same stale-read technique: ``run_finish`` handed a snapshot taken before a
``pool_exhausted`` fetch must still leave that warning on the run. It fails
against a ``run_finish`` built on ``put_run``.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from agentprops.models import WARNING_POOL_EXHAUSTED, Dataset, StepRecord
from agentprops.service import ServiceContext, blueprints, runs
from agentprops.service.runs import (
    STATUS_ABANDONED,
    STATUS_FINISHED,
    WARNING_RUN_ALREADY_FINISHED,
    WARNING_RUN_FINISH_MISMATCH,
    WARNING_STEP_ACTUAL_CONFLICT,
)
from agentprops.storage import StoreError
from conftest import FROZEN_NOW, load_document
from envelopes import codes, data, findings, rules, warnings_of

AGENT = "location-onboarding"
RUN_ID = "m8-priya-run"
PRIYA = "3f8c1a20-0000-4000-8000-000000000001"

#: The tool name ``fetch_store_profile`` and ``recheck_store`` share.
REPEATED_TOOL = "delightree.stores.get"

POOL_NODE = "request_docs"
POOL_LENGTH = len(load_document("datasets/priya-missing-docs.json")["pools"][POOL_NODE])

#: `priya-missing-docs`'s ``expected.final``, which is what the M8 script grades
#: the recorded outcome against. Read off the fixture rather than copied, so a
#: fixture edit cannot leave this file quietly asserting the old answer.
EXPECTED_FINAL: dict[str, Any] = load_document("datasets/priya-missing-docs.json")["expected"][
    "final"
]


@pytest.fixture
def started(
    context: ServiceContext, blueprint_document: dict[str, Any], dataset_document: dict[str, Any]
) -> ServiceContext:
    """The published blueprint, `priya`, and a run pinned to it by explicit id."""
    blueprints.upsert(context, blueprint_document, publish=True)
    context.store.put_dataset(Dataset.model_validate(dataset_document))
    reply = runs.start(context, RUN_ID, AGENT, {"dataset_id": PRIYA})
    assert reply.ok, reply
    return context


def fetch(context: ServiceContext, **arguments: Any) -> Any:
    return runs.fetch_step(context, RUN_ID, **arguments)


def record(context: ServiceContext, actual: dict[str, Any], **arguments: Any) -> Any:
    return runs.record_step(context, RUN_ID, actual, **arguments)


def stored_run(context: ServiceContext) -> dict[str, Any]:
    document: dict[str, Any] = data(runs.get(context, RUN_ID))["run"]
    return document


def stored_step(context: ServiceContext, node_id: str, iteration: int = 0) -> dict[str, Any]:
    steps: list[dict[str, Any]] = stored_run(context)["steps"]
    return next(
        step for step in steps if step["node_id"] == node_id and step["iteration"] == iteration
    )


# ----------------------------------------------------------------- record_step


def test_record_step_writes_the_actual_onto_the_served_step(started: ServiceContext) -> None:
    """The happy path, and the payload contracts section 4's ``{ok}`` sits in.

    ``served`` is untouched by the write, which is the division ruling R-33
    draws: ``upsert_step`` records what was served and its repeat is a literal
    no-op, so ``set_step_actual`` is the only thing that may add an actual.
    """
    served = data(fetch(started, node_id="receive_request"))["step"]["fixture"]
    actual = {"store_id": "store_bengaluru_04", "franchisee": "priya-raman"}

    reply = record(started, actual, node_id="receive_request")
    payload = data(reply)["record"]
    assert payload["resolved_node_id"] == "receive_request"
    assert payload["step"]["actual"] == actual
    assert payload["step"]["served"] == served, "recording an actual did not rewrite the fixture"
    assert codes(reply) == []

    assert stored_step(started, "receive_request")["actual"] == actual
    assert len(stored_run(started)["steps"]) == 1, "no second row for the same key"


def test_the_record_key_is_the_resolved_one(started: ServiceContext) -> None:
    """Ruling R-64's "the same key means the same step", on the write side.

    The step is fetched by **tool name** from the entry node - where
    ``delightree.stores.get`` resolves to ``fetch_store_profile`` - and recorded
    by **node id**. One step, one row, one actual. A ``record_step`` that keyed
    on the argument shape rather than on the resolved node would report
    ``AP-004`` here.
    """
    fetch(started, node_id="receive_request")
    fetched = data(fetch(started, tool_name=REPEATED_TOOL))["step"]
    assert fetched["resolved_node_id"] == "fetch_store_profile"

    reply = record(started, {"store": {"status": "pending_docs"}}, node_id="fetch_store_profile")
    assert data(reply)["record"]["resolved_node_id"] == "fetch_store_profile"
    assert len(stored_run(started)["steps"]) == 2, "the record allocated no third step"
    assert stored_step(started, "fetch_store_profile")["actual"] == {
        "store": {"status": "pending_docs"}
    }


def test_recording_the_same_actual_twice_is_a_silent_no_op(started: ServiceContext) -> None:
    """The retry case. Identical means identical - no warning, no second write.

    ``recorded_at`` is compared as well as ``actual``: a second write that
    happened to store the same document would still move the timestamp, and the
    timestamp is the evidence that says when the agent answered.
    """
    fetch(started, node_id=POOL_NODE, iteration=0)
    actual = {"requested": ["fssai"], "received": []}

    first = record(started, actual, node_id=POOL_NODE, iteration=0)
    second = record(started, actual, node_id=POOL_NODE, iteration=0)

    assert codes(first) == [] and codes(second) == []
    assert data(first)["record"]["step"] == data(second)["record"]["step"]
    assert stored_run(started)["warnings"] == [], "a retry flagged the run"


def test_a_differing_actual_keeps_the_first_and_warns(started: ServiceContext) -> None:
    """Write-once, and the refusal reaches the caller as a warning (ground rule 3).

    Three claims, and the second is the one that makes this a *write-once* test
    rather than a warning test: the response carries the **stored** actual, not
    the one just sent. So a caller that ignored the warning still cannot mistake
    its own document for the recorded one.
    """
    fetch(started, node_id=POOL_NODE, iteration=1)
    first = {"requested": ["fssai"], "received": ["fssai"]}
    second = {"requested": ["fssai"], "received": []}
    record(started, first, node_id=POOL_NODE, iteration=1)

    reply = record(started, second, node_id=POOL_NODE, iteration=1)
    assert reply.ok, "a conflicting record is not a refusal (ground rule 3)"
    assert codes(reply) == [WARNING_STEP_ACTUAL_CONFLICT]
    assert data(reply)["record"]["step"]["actual"] == first, "the response carries the stored value"
    assert stored_step(started, POOL_NODE, 1)["actual"] == first, "the stored actual was rewritten"

    detail = warnings_of(reply)[0].detail
    assert detail["node_id"] == POOL_NODE and detail["iteration"] == 1
    assert detail["differs"] is True
    assert [item["code"] for item in stored_run(started)["warnings"]] == [
        WARNING_STEP_ACTUAL_CONFLICT
    ], "contracts 3.4: a warning is attached to the response and to the stored run"


def test_the_conflict_warning_is_keyed_per_step(started: ServiceContext) -> None:
    """Ruling R-54(b)'s merge key, applied to M8's warning.

    Two conflicts on two different steps are two entries; a third conflict on a
    step already flagged adds nothing. The key carries ``node_id`` and
    ``iteration`` precisely so this is per-step rather than per-run.
    """
    for iteration in (0, 1):
        fetch(started, node_id=POOL_NODE, iteration=iteration)
        record(started, {"received": []}, node_id=POOL_NODE, iteration=iteration)
    record(started, {"received": ["fssai"]}, node_id=POOL_NODE, iteration=0)
    record(started, {"received": ["fssai"]}, node_id=POOL_NODE, iteration=1)
    record(started, {"received": ["pan"]}, node_id=POOL_NODE, iteration=0)

    flagged = stored_run(started)["warnings"]
    assert [item["code"] for item in flagged] == [WARNING_STEP_ACTUAL_CONFLICT] * 2
    assert [item["detail"]["iteration"] for item in flagged] == [0, 1]


def test_an_actual_for_an_unserved_step_is_ap_004_and_writes_nothing(
    started: ServiceContext,
) -> None:
    """Ruling R-33: an actual cannot be reported for a step that was never served.

    ``AP-004`` rather than an ``RT-*`` code, because resolution *succeeded* -
    ``verify_compliance`` is a real node in this blueprint - and what names
    nothing is the step key. The pointer addresses the argument the caller used.
    """
    reply = record(started, {"compliant": True}, node_id="verify_compliance")
    assert rules(reply) == ["AP-004"]
    finding = findings(reply)[0]
    assert finding.pointer == "/node_id"
    assert finding.context["resolved_node_id"] == "verify_compliance"
    assert finding.context["iteration"] == 0
    assert stored_run(started)["steps"] == [], "the refused record allocated a step"


def test_the_unserved_pointer_follows_the_argument_the_caller_used(
    started: ServiceContext,
) -> None:
    """A ``tool_name`` caller is not pointed at a ``node_id`` it never sent.

    The tool-name path is the one where the caller cannot know the node id, so
    pointing at ``/node_id`` would name an argument that is absent from the
    request - and ``resolved_node_id`` in the context is how the caller learns
    which node the key was built from.
    """
    reply = record(started, {"compliant": True}, tool_name="delightree.compliance.check")
    assert rules(reply) == ["AP-004"]
    finding = findings(reply)[0]
    assert finding.pointer == "/tool_name"
    assert finding.context["resolved_node_id"] == "verify_compliance"


def test_record_step_reports_the_resolution_findings_fetch_step_would(
    started: ServiceContext,
) -> None:
    """The same addressing, so the same failures: RT-E02, RT-E01 and the iteration.

    ``record_step`` resolves through `resolution.py` exactly as ``fetch_step``
    does, and asserting that here is what stops the two drifting into two
    addressing schemes. ``check_docs`` is where the repeated tool name is
    genuinely ambiguous: its successors are ``request_docs`` and
    ``assign_training``, neither of which declares it.
    """
    assert rules(record(started, {}, node_id="no_such_node")) == ["RT-E02"]
    assert rules(record(started, {}, node_id=POOL_NODE, iteration=-1)) == ["RT-E02"]
    assert rules(record(started, {})) == ["RT-E02"], "no address at all"

    fetch(started, node_id="check_docs")
    ambiguous = record(started, {}, tool_name=REPEATED_TOOL)
    assert rules(ambiguous) == ["RT-E01"]
    assert findings(ambiguous)[0].context["candidates"] == [
        "fetch_store_profile",
        "recheck_store",
    ]


def test_record_step_stores_an_actual_the_schema_would_reject(started: ServiceContext) -> None:
    """Ground rule 2, as a test: the service stores evidence and forms no opinion.

    ``receive_request``'s ``output_schema`` requires ``store_id`` and
    ``franchisee`` and forbids additional properties, so this actual violates it
    three ways. It is stored verbatim, because the finding belongs to whatever
    grades the run - and a service that refused it would make an agent's mistake
    unrecordable.
    """
    fetch(started, node_id="receive_request")
    wrong = {"nonsense": True}
    reply = record(started, wrong, node_id="receive_request")
    assert reply.ok
    assert codes(reply) == []
    assert stored_step(started, "receive_request")["actual"] == wrong


def test_a_store_refusal_with_nothing_recorded_is_ap_005(
    started: ServiceContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other ``StoreError``, and why the service classifies rather than assumes.

    ``set_step_actual`` raises for two conditions: a *different* actual is
    already recorded, and its retry budget ran out with nothing recorded at all.
    Reporting the second as ``step_actual_conflict`` would tell a caller its
    evidence lost to a value that does not exist, so :func:`runs._conflicted`
    re-reads and classifies. This forces the contention branch, which no
    sequential test can reach.
    """
    fetch(started, node_id="receive_request")

    def exhausted(*arguments: Any, **keywords: Any) -> Any:
        raise StoreError("could not record an actual")

    monkeypatch.setattr(started.store, "set_step_actual", exhausted)
    reply = record(started, {"store_id": "s1"}, node_id="receive_request")
    assert rules(reply) == ["AP-005"]
    assert findings(reply)[0].pointer == "/actual"
    assert stored_step(started, "receive_request")["actual"] is None


# ------------------------------------------------------------------ run_finish


def test_run_finish_closes_the_run(started: ServiceContext) -> None:
    """``status``, ``outcome`` and ``finished_at``, and the payload is the run.

    ``finished_at`` comes from the injected clock (ruling R-09), which is frozen
    in tests - so it is compared rather than merely asserted non-null.
    """
    reply = runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_FINISHED)
    run = data(reply)["run"]
    assert run["status"] == STATUS_FINISHED
    assert run["outcome"] == EXPECTED_FINAL
    assert run["finished_at"] == FROZEN_NOW.isoformat().replace("+00:00", "Z")
    assert codes(reply) == []

    stored = stored_run(started)
    assert stored["status"] == STATUS_FINISHED
    assert stored["outcome"] == EXPECTED_FINAL
    assert stored["finished_at"] == run["finished_at"]


def test_finishing_twice_with_the_same_values_is_a_silent_no_op(started: ServiceContext) -> None:
    """The retry case again. A repeated identical finish must not warn."""
    first = runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_FINISHED)
    second = runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_FINISHED)
    assert codes(first) == [] and codes(second) == []
    assert data(first)["run"] == data(second)["run"]
    assert stored_run(started)["warnings"] == []


def test_a_diverging_second_finish_keeps_the_first_and_warns(started: ServiceContext) -> None:
    """Ruling R-53's shape, one tool along: the first close wins and the second is told.

    The stored outcome is evidence about what the agent produced, so a second
    caller may not overwrite it - and may not be refused either, because the
    service never gates. The warning names which fields diverged, the response
    carries the **recorded** run, and the run itself carries the warning.
    """
    runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_FINISHED)
    other = {"onboarding_status": "escalated", "outstanding_tasks": 1}

    reply = runs.finish(started, RUN_ID, other, STATUS_ABANDONED)
    assert reply.ok, "a diverging finish is not a refusal"
    assert codes(reply) == [WARNING_RUN_FINISH_MISMATCH]
    detail = warnings_of(reply)[0].detail
    assert sorted(detail["diverged"]) == ["outcome", "status"]
    assert detail["recorded"]["status"] == STATUS_FINISHED
    assert detail["requested"]["status"] == STATUS_ABANDONED

    run = data(reply)["run"]
    assert run["status"] == STATUS_FINISHED, "the second finish overwrote the first"
    assert run["outcome"] == EXPECTED_FINAL
    assert [item["code"] for item in run["warnings"]] == [WARNING_RUN_FINISH_MISMATCH], (
        "the returned run carries the warning this call attached"
    )
    assert [item["code"] for item in stored_run(started)["warnings"]] == [
        WARNING_RUN_FINISH_MISMATCH
    ]


def test_a_second_finish_that_diverges_in_one_field_names_that_field(
    started: ServiceContext,
) -> None:
    """Both halves of the comparison, independently, so neither is decoration.

    A same-status finish with a different outcome names ``outcome`` alone, and a
    same-outcome finish with a different status names ``status`` alone. A
    divergence check that compared the pair as a unit would report both every
    time and still pass a test that only looked for a warning.
    """
    runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_FINISHED)

    outcome_only = runs.finish(started, RUN_ID, {"onboarding_status": "complete"}, STATUS_FINISHED)
    assert warnings_of(outcome_only)[0].detail["diverged"] == ["outcome"]

    status_only = runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_ABANDONED)
    assert warnings_of(status_only)[0].detail["diverged"] == ["status"]


def test_an_unknown_finish_status_writes_nothing(started: ServiceContext) -> None:
    """``AP-001`` at the argument, checked before the store is touched."""
    reply = runs.finish(started, RUN_ID, EXPECTED_FINAL, "running")
    assert rules(reply) == ["AP-001"]
    assert findings(reply)[0].pointer == "/status"
    assert findings(reply)[0].context["allowed"] == [STATUS_ABANDONED, STATUS_FINISHED]

    stored = stored_run(started)
    assert stored["status"] == "running" and stored["finished_at"] is None


def test_run_finish_stores_an_outcome_the_schema_would_reject(started: ServiceContext) -> None:
    """Ground rule 2 on the other write. ``outcome_schema`` is not consulted here.

    The blueprint's ``outcome_schema`` requires ``onboarding_status`` and
    ``outstanding_tasks``, enumerates the first and forbids additional
    properties. This outcome breaks all three and is stored anyway: whether an
    agent produced the right answer is the client's comparison to make, and
    ``run_evidence`` at M10 is what hands both documents to it.
    """
    broken = {"onboarding_status": "vibes", "unexpected": [1, 2, 3]}
    reply = runs.finish(started, RUN_ID, broken, STATUS_FINISHED)
    assert reply.ok
    assert codes(reply) == []
    assert stored_run(started)["outcome"] == broken


def test_an_abandoned_run_is_closed_too(started: ServiceContext) -> None:
    """``abandoned`` sets ``finished_at`` like ``finished`` does.

    Which is why ``mark_run_finished``'s compare-and-set keys on
    ``finished_at IS NULL`` rather than on ``status``: one predicate covers both
    terminal statuses, and the Protocol holds no opinion about the vocabulary.
    """
    reply = runs.finish(started, RUN_ID, {}, STATUS_ABANDONED)
    run = data(reply)["run"]
    assert run["status"] == STATUS_ABANDONED
    assert run["finished_at"] is not None
    assert runs.finish(started, RUN_ID, {}, STATUS_FINISHED).ok
    assert stored_run(started)["status"] == STATUS_ABANDONED, "abandoned did not close the run"


def test_a_stale_finish_cannot_revert_the_runs_warnings(
    started: ServiceContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The M6 finding in the other direction, and the losing side of it.

    M6 narrowed the *warning* write because ``put_run`` with an edited snapshot
    would revert ``status``, ``outcome`` and ``finished_at``. ``run_finish`` is
    the counterpart and has the same exposure with the columns swapped: built on
    ``put_run``, it would write the ``warnings`` list of whatever run model it
    was holding, reverting a ``pool_exhausted`` warning a concurrent
    ``fetch_step`` had just merged in.

    Forced with the M3 stale-read technique - ``get_run`` answers with a
    snapshot taken *before* the exhausted fetch, which is exactly what a
    concurrent caller holds. A sequential version of this test cannot fail,
    because a fresh snapshot already carries the warning. The stale read is the
    test.
    """
    real_get_run = started.store.get_run
    stale = real_get_run(RUN_ID)
    assert stale is not None and stale.warnings == []

    fetch(started, node_id=POOL_NODE, iteration=POOL_LENGTH)
    assert [item["code"] for item in stored_run(started)["warnings"]] == [WARNING_POOL_EXHAUSTED]

    monkeypatch.setattr(started.store, "get_run", lambda run_id: stale)
    assert runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_FINISHED).ok

    monkeypatch.undo()
    after = real_get_run(RUN_ID)
    assert after is not None
    assert [item.code for item in after.warnings] == [WARNING_POOL_EXHAUSTED], (
        "the lifecycle write reverted the run's warnings"
    )
    assert after.status == STATUS_FINISHED and after.outcome == EXPECTED_FINAL


# ---------------------------------------------------- a closed run still serves


def test_a_finished_run_still_serves_and_says_so(started: ServiceContext) -> None:
    """Ruling R-54(c): "M8 may add a warning if it proves useful; it must not add a refusal."

    Both halves, and in this order. The fixture served after the run is closed
    is compared **byte for byte** with the one served before, because that is
    the claim R-54(c) actually makes - the read path is pin-scoped and immutable,
    so a run's lifecycle is bookkeeping about the caller. Only then is the
    warning asserted. A test that checked the warning alone would pass against a
    ``fetch_step`` that had started refusing.
    """
    before = copy.deepcopy(data(fetch(started, node_id="receive_request"))["step"])
    runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_FINISHED)

    reply = fetch(started, node_id="receive_request")
    assert reply.ok, "a finished run refused to serve (ruling R-54(c))"
    assert data(reply)["step"] == before, "the served fixture changed after the run closed"
    assert codes(reply) == [WARNING_RUN_ALREADY_FINISHED]

    detail = warnings_of(reply)[0].detail
    assert detail["run_id"] == RUN_ID and detail["status"] == STATUS_FINISHED
    assert detail["finished_at"] == FROZEN_NOW.isoformat()


def test_a_fresh_step_is_still_servable_after_the_run_is_closed(started: ServiceContext) -> None:
    """Not only a replay: a step never served before is served after the close.

    The stronger half of R-54(c). A ``fetch_step`` that short-circuited to the
    stored step on a finished run would pass the byte-identity test above and
    fail this one.
    """
    runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_FINISHED)
    reply = fetch(started, node_id="verify_compliance")
    assert reply.ok
    assert data(reply)["step"]["fixture"]["output"]["compliant"] is True
    assert codes(reply) == [WARNING_RUN_ALREADY_FINISHED]
    assert stored_step(started, "verify_compliance")["served"] == data(reply)["step"]["fixture"]


def test_a_closed_run_still_records_and_says_so(started: ServiceContext) -> None:
    """``record_step`` takes the same "warn, never refuse" answer.

    An agent still reporting actuals after its harness closed the run is the
    same defect as one still fetching, and the evidence is worth keeping either
    way.
    """
    fetch(started, node_id="receive_request")
    runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_FINISHED)

    reply = record(started, {"store_id": "store_bengaluru_04"}, node_id="receive_request")
    assert reply.ok
    assert codes(reply) == [WARNING_RUN_ALREADY_FINISHED]
    assert stored_step(started, "receive_request")["actual"] == {"store_id": "store_bengaluru_04"}


def test_the_closed_run_warning_is_recorded_once_however_many_calls(
    started: ServiceContext,
) -> None:
    """Ruling R-54(b)'s key with neither ``node_id`` nor ``iteration``: once per run.

    An agent looping against a closed run must not grow the stored warning list
    without bound. Every *response* still carries it, which is the property that
    makes recording it once safe.
    """
    runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_FINISHED)
    for node in ("receive_request", "check_docs", "complete"):
        assert codes(fetch(started, node_id=node)) == [WARNING_RUN_ALREADY_FINISHED]

    flagged = [item["code"] for item in stored_run(started)["warnings"]]
    assert flagged == [WARNING_RUN_ALREADY_FINISHED]


def test_a_running_run_carries_no_lifecycle_warning(started: ServiceContext) -> None:
    """The negative control: the warning is not attached to every fetch.

    Without this, a ``_lifecycle`` that returned the warning unconditionally
    would pass every assertion above.
    """
    assert codes(fetch(started, node_id="receive_request")) == []
    fetch(started, node_id=POOL_NODE, iteration=0)
    assert stored_run(started)["warnings"] == []


def test_a_step_recorded_before_the_close_is_still_readable(started: ServiceContext) -> None:
    """The evidence survives the close, which is the point of recording it.

    ``run_get`` after ``run_finish`` returns the served fixture, the recorded
    actual and the outcome together - which is the bundle ``run_evidence`` will
    assemble at M10, and the reason ``record_step`` lands here rather than there.
    """
    fetch(started, node_id="receive_request")
    record(started, {"store_id": "store_bengaluru_04"}, node_id="receive_request")
    runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_FINISHED)

    run = stored_run(started)
    assert run["steps"][0]["served"]["output"]["store_id"] == "ST-4471", "the authored fixture"
    assert run["steps"][0]["actual"] == {"store_id": "store_bengaluru_04"}, "what the agent said"
    assert run["outcome"] == EXPECTED_FINAL
    assert run["path"] == [{"node_id": "receive_request", "iteration": 0, "at": run["started_at"]}]


def test_upsert_step_is_still_a_no_op_after_an_actual_is_recorded(
    started: ServiceContext,
) -> None:
    """Ruling R-33's division, from the losing side.

    ``upsert_step``'s repeat must stay a literal no-op - that is what makes
    ``fetch_step`` idempotent - so a ``fetch_step`` *after* a ``record_step``
    must not wipe the recorded actual by re-writing the row. The store is the
    thing that guarantees it, and this is the behavioural check that it does.
    """
    fetch(started, node_id="receive_request")
    record(started, {"store_id": "store_bengaluru_04"}, node_id="receive_request")

    replayed = fetch(started, node_id="receive_request")
    assert replayed.ok
    assert stored_step(started, "receive_request")["actual"] == {"store_id": "store_bengaluru_04"}

    direct = started.store.upsert_step(
        RUN_ID, StepRecord(node_id="receive_request", iteration=0, served={"marker": True})
    )
    assert direct.actual == {"store_id": "store_bengaluru_04"}
    assert direct.served != {"marker": True}, "upsert_step overwrote a served fixture"
