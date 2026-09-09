"""Step identity resolution: contracts section 5, both directions of every branch.

The tests are pure - a ``Blueprint`` model and a ``Run`` model, no store - because
the algorithm is pure. What it reads from the run is ``path``, and constructing
that directly is what lets one test place the run at an arbitrary position
without walking it there.

The property M8's script exists to prove is
:func:`test_one_tool_name_resolves_to_two_nodes_depending_on_position`, and it
asserts **both** directions in one test on purpose: ``delightree.stores.get``
must resolve to ``fetch_store_profile`` early and to ``recheck_store`` after the
loop. Either half passing alone would be satisfied by a resolver that always
answered the same node.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from agentprops.models import RT_E01, RT_E02, Blueprint, PathStep, Run, RunPin
from agentprops.service.resolution import candidates_for, head_of, resolve, successors_of

#: The tool name the golden blueprint declares **twice** - on
#: ``fetch_store_profile`` and on ``recheck_store``. Position is the only thing
#: that can tell them apart, which is why the run id matters even for tool-name
#: addressing (PRD 5.5's rule 3).
REPEATED_TOOL = "delightree.stores.get"

#: A tool name exactly one node declares, so it resolves at step 2 without any
#: position at all.
UNIQUE_TOOL = "delightree.docs.request"

FROZEN_AT = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def blueprint(blueprint_document: dict[str, Any]) -> Blueprint:
    return Blueprint.model_validate(blueprint_document)


def run_at(*nodes: str) -> Run:
    """A run whose reconstructed path ends at the last of ``nodes``.

    ``path`` is what section 5 reads as the head. Built here rather than walked
    through ``fetch_step`` so that a resolution test needs no store, and so the
    position under test is stated at the top of the test rather than implied by
    eight preceding calls.
    """
    return Run(
        id="resolution-run",
        agent_id="location-onboarding",
        pin=RunPin(
            dataset_id=UUID("3f8c1a20-0000-4000-8000-000000000001"),
            dataset_version=1,
            blueprint_version="1.0.0",
        ),
        run_class="dev",
        status="running",
        started_at=FROZEN_AT,
        path=[PathStep(node_id=node, iteration=0, at=FROZEN_AT) for node in nodes],
    )


# ------------------------------------------------------------------ step 1


def test_a_known_node_id_resolves_to_itself(blueprint: Blueprint) -> None:
    resolved, findings = resolve(blueprint, run_at(), node_id="check_docs")
    assert (resolved, findings) == ("check_docs", [])


def test_an_unknown_node_id_is_rt_e02_and_never_a_guess(blueprint: Blueprint) -> None:
    """ "``node_id`` given and unknown is RT-E02, not the nearest match."""
    resolved, findings = resolve(blueprint, run_at(), node_id="check_documents")
    assert resolved is None
    assert [finding.rule for finding in findings] == [RT_E02]
    assert findings[0].pointer == "/node_id"
    assert findings[0].context["node_id"] == "check_documents"


def test_a_node_id_wins_when_both_arguments_are_given(blueprint: Blueprint) -> None:
    """Section 5's step 1 is unconditional, so ``tool_name`` is not even read.

    The tie-break matters because the two arguments can disagree: here
    ``tool_name`` would resolve to ``recheck_store`` from this position and
    ``node_id`` names something else entirely.
    """
    resolved, findings = resolve(
        blueprint, run_at("request_docs"), node_id="assign_training", tool_name=REPEATED_TOOL
    )
    assert (resolved, findings) == ("assign_training", [])


# ------------------------------------------------------------------ step 2


def test_a_unique_tool_name_resolves_without_a_position(blueprint: Blueprint) -> None:
    """Step 2's single-candidate branch runs before the head is even computed."""
    resolved, findings = resolve(blueprint, run_at(), tool_name=UNIQUE_TOOL)
    assert (resolved, findings) == ("request_docs", [])


def test_an_unknown_tool_name_is_rt_e02(blueprint: Blueprint) -> None:
    resolved, findings = resolve(blueprint, run_at(), tool_name="delightree.stores.list")
    assert resolved is None
    assert [finding.rule for finding in findings] == [RT_E02]
    assert findings[0].pointer == "/tool_name"


def test_one_tool_name_resolves_to_two_nodes_depending_on_position(
    blueprint: Blueprint,
) -> None:
    """The whole point of section 5, and of the M8 script's step 7.

    One tool name, two positions, two answers. Asserted together because a
    resolver that always returned ``fetch_store_profile`` would pass the first
    assertion and a resolver that always returned ``recheck_store`` would pass
    the second.
    """
    early, no_findings = resolve(blueprint, run_at("receive_request"), tool_name=REPEATED_TOOL)
    late, also_none = resolve(blueprint, run_at("request_docs"), tool_name=REPEATED_TOOL)
    assert (early, no_findings) == ("fetch_store_profile", [])
    assert (late, also_none) == ("recheck_store", [])
    assert early != late, "position is doing no work if both calls answer the same node"


def test_an_empty_path_takes_the_entry_node_as_the_head(blueprint: Blueprint) -> None:
    """A run with nothing served yet still has a position: ``entry_node``.

    ``receive_request``'s only successor is ``fetch_store_profile``, so the
    repeated tool name resolves on the very first call of a run rather than
    reporting RT-E01 for want of a path.
    """
    assert head_of(blueprint, run_at()) == blueprint.entry_node
    resolved, findings = resolve(blueprint, run_at(), tool_name=REPEATED_TOOL)
    assert (resolved, findings) == ("fetch_store_profile", [])


def test_an_unnarrowable_repeated_tool_name_is_rt_e01_naming_every_candidate(
    blueprint: Blueprint,
) -> None:
    """ "Never guess", and the error has to be usable.

    From ``check_docs`` the one-hop successors are ``request_docs`` and
    ``assign_training`` - neither of which declares the repeated tool - so the
    narrowing eliminates both candidates and the answer is RT-E01. The finding
    lists **every** candidate, not the narrowed set, because the caller's next
    move is to retry with one of those node ids.
    """
    resolved, findings = resolve(blueprint, run_at("check_docs"), tool_name=REPEATED_TOOL)
    assert resolved is None
    assert [finding.rule for finding in findings] == [RT_E01]
    assert findings[0].context["candidates"] == ["fetch_store_profile", "recheck_store"]
    assert findings[0].context["head"] == "check_docs"
    assert findings[0].pointer == "/tool_name"


def test_the_named_candidates_are_a_working_retry(blueprint: Blueprint) -> None:
    """The losing side of the ambiguity, closed: each named candidate resolves.

    An RT-E01 whose candidate list did not resolve when passed back as
    ``node_id`` would be a dead end dressed as a recovery path.
    """
    _, findings = resolve(blueprint, run_at("check_docs"), tool_name=REPEATED_TOOL)
    for candidate in findings[0].context["candidates"]:
        assert resolve(blueprint, run_at("check_docs"), node_id=candidate) == (candidate, [])


# ------------------------------------------------------------------ step 3


def test_neither_argument_is_rt_e02(blueprint: Blueprint) -> None:
    """Section 5's step 3. An ``AP-001`` reading is defensible; the contract is not.

    The pseudocode says RT-E02 for "no ``node_id``, no ``tool_name``", and the
    algorithm is the contract. Recorded in ``DECISIONS.md`` rather than quietly
    upgraded to a boundary code.
    """
    resolved, findings = resolve(blueprint, run_at())
    assert resolved is None
    assert [finding.rule for finding in findings] == [RT_E02]


# ------------------------------------------------------- the helpers, directly


def test_candidates_are_listed_in_declaration_order(blueprint: Blueprint) -> None:
    assert candidates_for(blueprint, REPEATED_TOOL) == ["fetch_store_profile", "recheck_store"]
    assert candidates_for(blueprint, UNIQUE_TOOL) == ["request_docs"]
    assert candidates_for(blueprint, "nothing.declares.this") == []


def test_a_node_with_no_tool_name_is_never_a_candidate(blueprint: Blueprint) -> None:
    """Three golden nodes declare no ``tool_name``; ``None`` must not match.

    A comprehension comparing ``node.tool_name == tool_name`` is only correct
    because ``tool_name`` is a ``str`` here. This pins that a decision node
    cannot be reached by tool-name addressing at all.
    """
    undeclared = [node.id for node in blueprint.nodes if node.tool_name is None]
    assert set(undeclared) == {"check_docs", "complete", "escalate"}
    for node_id in undeclared:
        assert candidates_for(blueprint, node_id) == []


def test_the_head_is_the_last_step_served(blueprint: Blueprint) -> None:
    assert head_of(blueprint, run_at("receive_request", "fetch_store_profile")) == (
        "fetch_store_profile"
    )


def test_successors_are_one_hop_and_total_over_unknown_nodes(blueprint: Blueprint) -> None:
    """One hop, never transitive - and an unknown head contributes nothing.

    Transitive reachability would make ``recheck_store`` a successor of
    ``receive_request`` through the loop, and the repeated tool name would then
    be ambiguous from the first call of every run.
    """
    assert successors_of(blueprint, "receive_request") == frozenset({"fetch_store_profile"})
    assert successors_of(blueprint, "check_docs") == frozenset({"request_docs", "assign_training"})
    assert successors_of(blueprint, "complete") == frozenset()
    assert successors_of(blueprint, "no_such_node") == frozenset()


def test_bp_014_is_what_makes_the_narrowing_decisive(blueprint: Blueprint) -> None:
    """Why ``len(narrowed) > 1`` cannot happen for a *stored* blueprint.

    BP-014 rejects a blueprint where two nodes reachable in one step from the
    same node share a ``tool_name``. This asserts the golden blueprint has that
    property, which is what makes RT-E01's reachable case the *empty* narrowing
    rather than the crowded one - the distinction the module docstring records.
    """
    by_tool = {node.id: node.tool_name for node in blueprint.nodes}
    for node in blueprint.nodes:
        tools = [by_tool[successor] for successor in successors_of(blueprint, node.id)]
        declared = [tool for tool in tools if tool is not None]
        assert len(declared) == len(set(declared)), f"{node.id}'s successors share a tool name"
