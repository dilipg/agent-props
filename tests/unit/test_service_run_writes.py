"""``record_step`` and ``run_finish``: the two writes ruling R-15 lands at M8.

`test_service_runs.py` owns the read path. This file owns the two writes, and
the properties it exists to hold are the ones a write has and a read does not:

**The value may already be there**, and **ruling R-65 splits that case in two.**
So every write here is tested three times: the first call, the identical repeat,
and the divergent repeat - and the three have three different answers:

- the first call succeeds;
- the identical repeat is a **silent no-op success**. A caller repeating a
  write is a client retrying after a timeout - the case R-53 kept ``run_start``
  idempotent for - so success is the *expected* outcome and there is nothing to
  report. R-65's first draft asked for a warning here and M8's fix round
  implemented one; the ruling was amended, because its two cited precedents both
  point the other way - R-29's re-publish is silent, R-47's CAS loser errors -
  and a vocabulary that fires on expected outcomes trains callers to ignore it;
- the divergent repeat is **``ok: false`` with ``AP-007``**, and **nothing is
  written**. M8 first shipped it as a warning on a success envelope; R-65
  corrected that, because ``ok: true`` for a refused write is the same class of
  defect as M3's silent success carrying the winner's value - and R-33 already
  makes the store *raise*, so a tool reporting success would be claiming one the
  storage layer declined to give.

Both refusal tests therefore assert the stored value is **unchanged** as well as
the code, which is what makes them write-once tests rather than error-code
tests. And both assert the run's warning list is untouched: a refusal is not a
warning, so it must not be merged onto the run.

**Neither may grade** (ground rule 2).
:func:`test_record_step_stores_an_actual_the_schema_would_reject` and
:func:`test_run_finish_stores_an_outcome_the_schema_would_reject` are that rule
as tests rather than as a docstring: both send a document the blueprint's own
schema rejects and assert it is stored verbatim. A service that validated here
would be holding the comparison the client owns, and would turn a finding
*about the agent* into a refusal to record what the agent did.

**Neither may refuse to *serve*** (ground rule 3, ruling R-54(c)) - which is a
different claim from the refused *write* above, and R-65 draws the line: ground
rule 3 governs the read path, and R-56 already settled that a refused write is
``ok: false``. A finished run still serves, and
:func:`test_a_finished_run_still_serves_and_says_so` asserts both halves: the
fixture comes back byte-identical to the one served before the run was closed,
*and* the response carries ``run_already_finished``. A test that only checked
the warning would pass against a ``fetch_step`` that refused.

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
from agentprops.service.envelope import AP_WRITE_ONCE_CONFLICT
from agentprops.service.runs import (
    STATUS_ABANDONED,
    STATUS_FINISHED,
    WARNING_RUN_ALREADY_FINISHED,
)
from agentprops.storage import StoreError
from conftest import FROZEN_NOW, load_document
from envelopes import codes, data, findings, rules, warnings_of

AGENT = "location-onboarding"
RUN_ID = "m8-priya-run"
PRIYA = "3f8c1a20-0000-4000-8000-000000000001"

#: The tool name ``fetch_store_profile`` and ``recheck_store`` share.
REPEATED_TOOL = "acme.stores.get"

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
    ``acme.stores.get`` resolves to ``fetch_store_profile`` - and recorded
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
    """The retry case, and R-65 as amended: a success, and it says **nothing**.

    A caller re-recording an identical actual is a client retrying after a
    timeout, so success is the *expected* outcome and a warning on an expected
    outcome is noise. Both responses are asserted silent, and so is the run - a
    retry that flagged the run would leave a permanent marker on it, because
    R-54(b)'s merge key would record it once per run forever.

    ``recorded_at`` is compared as well as ``actual``: a second write that
    happened to store the same document would still move the timestamp, and the
    timestamp is the evidence that says when the agent answered. So the step
    coming back identical is what makes this a *no-op* rather than a benign
    rewrite.
    """
    fetch(started, node_id=POOL_NODE, iteration=0)
    actual = {"requested": ["fssai"], "received": []}

    first = record(started, actual, node_id=POOL_NODE, iteration=0)
    second = record(started, actual, node_id=POOL_NODE, iteration=0)

    assert first.ok and second.ok, "an identical re-record is a success (ruling R-65)"
    assert codes(first) == [] and codes(second) == [], "a retry is not an event"
    assert data(first)["record"]["step"] == data(second)["record"]["step"], (
        "the repeat moved recorded_at, so it was not a no-op"
    )
    assert stored_run(started)["warnings"] == [], "a retry flagged the run"


def test_a_differing_actual_is_refused_and_writes_nothing(started: ServiceContext) -> None:
    """Ruling R-65: a differing re-record is ``AP-007``, and the write did not happen.

    M8 first shipped this as a warning on a successful response, arguing ground
    rule 3. R-65 corrected it: ``ok: true`` for a refused write "misrepresents
    the outcome, and that is the same class of defect as M3's silent success
    carrying the winner's value". ``set_step_actual`` *raises* on a differing
    actual (R-33), so a tool answering ``ok: true`` would report a success the
    storage layer explicitly declined to give.

    Four claims, and the third is the one that makes this a *write-once* test
    rather than an error-code test: the stored actual is still the first one.
    """
    fetch(started, node_id=POOL_NODE, iteration=1)
    first = {"requested": ["fssai"], "received": ["fssai"]}
    second = {"requested": ["fssai"], "received": []}
    record(started, first, node_id=POOL_NODE, iteration=1)

    reply = record(started, second, node_id=POOL_NODE, iteration=1)
    assert reply.ok is False, "a refused write must not report ok: true (ruling R-65)"
    assert rules(reply) == [AP_WRITE_ONCE_CONFLICT]
    assert stored_step(started, POOL_NODE, 1)["actual"] == first, "the stored actual was rewritten"

    finding = findings(reply)[0]
    assert finding.pointer == "/actual", "the argument that could not be accepted"
    assert finding.context["resolved_node_id"] == POOL_NODE
    assert finding.context["iteration"] == 1
    assert finding.context["recorded_at"] is not None, "when the stored actual was recorded"
    assert stored_run(started)["warnings"] == [], (
        "a refused write is not a warning, so it must not be merged onto the run"
    )


def test_a_refused_record_leaves_every_other_step_alone(started: ServiceContext) -> None:
    """The blast radius of a refusal: one step key, and nothing else on the run.

    Replaces the per-step warning-key test the warning made necessary. With the
    conflict now an ``ok: false``, what is worth asserting is that a refusal on
    one step does not touch the actual recorded on another, does not accumulate
    anything on the run, and does not stop the *next* legitimate record from
    landing.
    """
    for iteration in (0, 1):
        fetch(started, node_id=POOL_NODE, iteration=iteration)
    record(started, {"received": []}, node_id=POOL_NODE, iteration=0)

    refused = record(started, {"received": ["fssai"]}, node_id=POOL_NODE, iteration=0)
    assert rules(refused) == [AP_WRITE_ONCE_CONFLICT]

    landed = record(started, {"received": ["fssai"]}, node_id=POOL_NODE, iteration=1)
    assert landed.ok, "a refusal on one step blocked a first record on another"

    assert stored_step(started, POOL_NODE, 0)["actual"] == {"received": []}
    assert stored_step(started, POOL_NODE, 1)["actual"] == {"received": ["fssai"]}
    assert stored_run(started)["warnings"] == []


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
    reply = record(started, {"compliant": True}, tool_name="acme.compliance.check")
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
    Reporting the second as ``AP-007`` would tell a caller its evidence lost to
    a value that does not exist - and would name a *write-once conflict* where
    there is nothing to conflict with - so :func:`runs._conflicted` re-reads and
    classifies. This forces the contention branch, which no sequential test can
    reach.

    Both codes mean "the store refused", and the difference between them is the
    whole reason the classification exists: ``AP-007`` says a value is there and
    it is not yours, ``AP-005`` says the store refused with nothing recorded.
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
    """The retry case again, and the response is identical to the first.

    A retry is meant to be indistinguishable from the call it retries, so the
    two payloads are compared **whole** rather than field by field - which is
    only possible because nothing is attached to the second.

    ``run_already_finished`` is deliberately absent: that code belongs to the
    **read** path, where it means "a finished run served you a fixture anyway"
    (R-54(c), ratified by R-67(a)). A write is not serving, so attaching it here
    would say something else under the same name.
    """
    first = runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_FINISHED)
    second = runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_FINISHED)

    assert first.ok and second.ok
    assert codes(first) == [] and codes(second) == [], "a retry is not an event"
    assert data(first)["run"] == data(second)["run"], "the repeat changed the run"
    assert stored_run(started)["warnings"] == []


def test_a_diverging_second_finish_is_refused_and_writes_nothing(
    started: ServiceContext,
) -> None:
    """Ruling R-65: a differing re-finish is ``AP-007``, and the write did not happen.

    A run's recorded outcome is evidence about what the agent produced, so a
    second caller may not overwrite it - and may not be told it succeeded
    either. M8 shipped this as a warning; R-65 corrected it for the reason the
    ``record_step`` case is corrected: the compare-and-set reported that this
    call did not close the run, so ``ok: true`` would misreport what the store
    now holds.

    The finding names *which* fields diverged and what is recorded, and the run
    is left exactly as the first close left it - including its warning list,
    which a refusal must not touch.
    """
    runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_FINISHED)
    other = {"onboarding_status": "escalated", "outstanding_tasks": 1}

    reply = runs.finish(started, RUN_ID, other, STATUS_ABANDONED)
    assert reply.ok is False, "a refused write must not report ok: true (ruling R-65)"
    assert rules(reply) == [AP_WRITE_ONCE_CONFLICT]

    finding = findings(reply)[0]
    assert sorted(finding.context["diverged"]) == ["outcome", "status"]
    assert finding.context["recorded"]["status"] == STATUS_FINISHED
    assert finding.context["recorded"]["finished_at"] == FROZEN_NOW.isoformat()
    assert finding.context["requested"]["status"] == STATUS_ABANDONED

    stored = stored_run(started)
    assert stored["status"] == STATUS_FINISHED, "the second finish overwrote the first"
    assert stored["outcome"] == EXPECTED_FINAL
    assert stored["warnings"] == [], "a refused write is not a warning and must not be merged"


def test_a_second_finish_that_diverges_in_one_field_names_and_points_at_it(
    started: ServiceContext,
) -> None:
    """Both halves of the comparison, independently, so neither is decoration.

    A same-status finish with a different outcome names ``outcome`` alone, and a
    same-outcome finish with a different status names ``status`` alone. A
    divergence check that compared the pair as a unit would report both every
    time and still pass a test that only looked for the refusal.

    The **pointer** follows the same rule, which is what makes the finding
    actionable: it addresses the first diverged field in a fixed order, so a
    caller is pointed at an argument it actually sent.
    """
    runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_FINISHED)

    outcome_only = runs.finish(started, RUN_ID, {"onboarding_status": "complete"}, STATUS_FINISHED)
    assert findings(outcome_only)[0].context["diverged"] == ["outcome"]
    assert findings(outcome_only)[0].pointer == "/outcome"

    status_only = runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_ABANDONED)
    assert findings(status_only)[0].context["diverged"] == ["status"]
    assert findings(status_only)[0].pointer == "/status"


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

    The second call proves it *closed*: a run still open would let a ``finished``
    finish through, and instead it is refused with ``AP-007`` naming ``status``
    as the divergence. So the refusal is the assertion here rather than an
    inconvenience - if ``abandoned`` did not close the run, this would succeed.
    """
    reply = runs.finish(started, RUN_ID, {}, STATUS_ABANDONED)
    run = data(reply)["run"]
    assert run["status"] == STATUS_ABANDONED
    assert run["finished_at"] is not None

    reopened = runs.finish(started, RUN_ID, {}, STATUS_FINISHED)
    assert rules(reopened) == [AP_WRITE_ONCE_CONFLICT], "abandoned did not close the run"
    assert findings(reopened)[0].context["diverged"] == ["status"], "the outcome matched"
    assert stored_run(started)["status"] == STATUS_ABANDONED


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


def test_a_closed_run_still_records_and_says_nothing(started: ServiceContext) -> None:
    """``record_step`` never refuses a closed run - and never warns about one either.

    The evidence is worth keeping whatever the run's lifecycle says, so the
    actual lands. But ``run_already_finished`` is **not** attached: that code is
    the read path's, where it means "a finished run served you a fixture anyway"
    (R-54(c), ratified by R-67(a)), and a write is not serving. Both halves are
    asserted - it records, and it is silent - because a test that only checked
    the record would pass with the warning back.
    """
    fetch(started, node_id="receive_request")
    runs.finish(started, RUN_ID, EXPECTED_FINAL, STATUS_FINISHED)

    actual = {"store_id": "store_bengaluru_04"}
    reply = record(started, actual, node_id="receive_request")
    assert reply.ok
    assert codes(reply) == [], "run_already_finished belongs to the read path"
    assert stored_step(started, "receive_request")["actual"] == actual
    assert stored_run(started)["warnings"] == [], "a write flagged the run lifecycle"


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
