"""Step identity resolution. `docs/contracts.md` section 5, implemented literally.

The algorithm is stated as pseudocode in the contract and this module is that
pseudocode, in the same order, with the same four outcomes. It is deliberately
not "improved": the one thing PRD 5.5 and CLAUDE.md both say about it is
**never guess**, and every shortcut available here is a guess.

    resolve(run, node_id?, tool_name?) -> node_id | RT-E01 | RT-E02

    1. if node_id is given:
         if node_id in blueprint.nodes: return node_id
         else: return RT-E02
    2. if tool_name is given:
         candidates = [n for n in blueprint.nodes if n.tool_name == tool_name]
         if len(candidates) == 0: return RT-E02
         if len(candidates) == 1: return candidates[0]
         head = run.path[-1].node_id if run.path else blueprint.entry_node
         reachable = successors(head)          # one hop, following edges
         narrowed = [c for c in candidates if c in reachable]
         if len(narrowed) == 1: return narrowed[0]
         return RT-E01 with candidates=[c.id for c in candidates]
    3. return RT-E02

Why position is the only disambiguator
--------------------------------------

PRD 5.5's rule 3 - "among nodes reachable from the current path head, pick the
one declaring that tool" - is what makes the run id load-bearing for tool-name
addressing: *without a known position, a repeated tool is unresolvable*. The
golden blueprint is the case in point. ``delightree.stores.get`` is declared by
both ``fetch_store_profile`` and ``recheck_store``, and the M8 script calls it
twice by tool name expecting two different answers:

- after ``receive_request``, the one-hop successors are ``{fetch_store_profile}``
  and the call resolves there;
- after ``request_docs``, they are ``{recheck_store}`` and the same tool name
  resolves to the other node.

The head is read from ``run.path``, which is *reconstructed* from the steps this
run has actually been served (ruling R-37 makes that order total). So the
position cannot be declared by the caller and cannot be lied about.

Three tie-breaks worth stating, because the pseudocode fixes them silently
--------------------------------------------------------------------------

**``node_id`` wins when both arguments are given.** Step 1 is unconditional, so
``tool_name`` is not even read. The alternative - refusing the pair as mutually
exclusive, the way ``dataset_validate`` refuses ``dataset`` plus
``dataset_json`` - would be a redesign of a stated algorithm, and a caller that
sends both has already said which node it means.

**Neither argument given is RT-E02**, per step 3, and not ``AP-001``. It reads
like a missing-argument boundary code, and the algorithm assigns it a runtime
code instead; the algorithm is the contract.

**A narrowing that eliminates *every* candidate is RT-E01, not RT-E02.** Step 2
tests ``len(narrowed) == 1``, so both zero and two or more fall through to
RT-E01 - which is right, because the tool name *is* known to this blueprint and
what failed is the position. This is also the reachable RT-E01 case: BP-014
rejects a blueprint where two nodes reachable in one step from the same node
share a ``tool_name``, so ``len(narrowed) > 1`` cannot occur for a stored
blueprint, while ``len(narrowed) == 0`` happens the moment an agent asks for a
repeated tool from a position where neither candidate is next.
"""

from __future__ import annotations

from agentprops.models import RT_E01, RT_E02, Blueprint, RuleError, Run
from agentprops.service.envelope import field_pointer, runtime
from agentprops.validation.graph import Graph

__all__ = ["candidates_for", "head_of", "resolve", "successors_of"]


def resolve(
    blueprint: Blueprint,
    run: Run,
    *,
    node_id: str | None = None,
    tool_name: str | None = None,
) -> tuple[str | None, list[RuleError]]:
    """The resolved node id, or the findings that say why there is not one.

    ``(node_id, [])`` or ``(None, findings)`` - the shape
    :func:`agentprops.service.documents.parse` uses, and for the same reason:
    there is no node id to use until the findings are known to be empty, so a
    caller cannot forget to check.
    """
    if node_id is not None:
        if any(node.id == node_id for node in blueprint.nodes):
            return node_id, []
        return None, [
            runtime(
                RT_E02,
                field_pointer("node_id"),
                f"blueprint {blueprint.agent_id} {blueprint.version} declares no node {node_id!r}.",
                node_id=node_id,
                agent_id=blueprint.agent_id,
                blueprint_version=blueprint.version,
            )
        ]

    if tool_name is not None:
        candidates = candidates_for(blueprint, tool_name)
        if not candidates:
            return None, [
                runtime(
                    RT_E02,
                    field_pointer("tool_name"),
                    f"blueprint {blueprint.agent_id} {blueprint.version} declares no node with "
                    f"tool_name {tool_name!r}.",
                    tool_name=tool_name,
                    agent_id=blueprint.agent_id,
                    blueprint_version=blueprint.version,
                )
            ]
        if len(candidates) == 1:
            return candidates[0], []

        head = head_of(blueprint, run)
        reachable = successors_of(blueprint, head)
        narrowed = [candidate for candidate in candidates if candidate in reachable]
        if len(narrowed) == 1:
            return narrowed[0], []
        return None, [
            runtime(
                RT_E01,
                field_pointer("tool_name"),
                f"tool_name {tool_name!r} is declared by {len(candidates)} nodes and the run's "
                f"position after {head!r} does not single one out; retry with an explicit "
                f"node_id.",
                tool_name=tool_name,
                candidates=candidates,
                head=head,
                narrowed=narrowed,
            )
        ]

    return None, [
        runtime(
            RT_E02,
            field_pointer("node_id"),
            "give either node_id or tool_name; neither was supplied.",
            agent_id=blueprint.agent_id,
            blueprint_version=blueprint.version,
        )
    ]


def candidates_for(blueprint: Blueprint, tool_name: str) -> list[str]:
    """Every node declaring ``tool_name``, in the blueprint's declaration order.

    Declaration order rather than sorted order, so an ``RT-E01`` names the
    candidates in the order the author wrote them - deterministic either way,
    and this one is the order a human reading the blueprint expects.
    """
    return [node.id for node in blueprint.nodes if node.tool_name == tool_name]


def head_of(blueprint: Blueprint, run: Run) -> str:
    """Where the run currently is: the last step served, else ``entry_node``.

    ``run.path`` is reconstructed from ``run_steps`` in ``(seq, node_id,
    iteration)`` order - total, per ruling R-37 - so ``path[-1]`` is a fact
    about what this run was served rather than anything the caller declared.
    """
    return run.path[-1].node_id if run.path else blueprint.entry_node


def successors_of(blueprint: Blueprint, head: str) -> frozenset[str]:
    """The one-hop successors of ``head``, following edges.

    Built through `validation/graph.py` rather than reimplemented here: that
    module is already total over a graph whose edges may name a node that does
    not exist (BP-004's finding to report, not this module's to crash on), and a
    second implementation of one-hop reachability would have to agree forever
    with the one four rules already use.
    """
    graph = Graph(
        [node.id for node in blueprint.nodes],
        [{"from": edge.from_node, "to": edge.to} for edge in blueprint.edges],
    )
    return graph.successors(head)
