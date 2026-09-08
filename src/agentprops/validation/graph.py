"""Graph derivations over a blueprint's nodes and edges.

Four rules need reachability or cycles and would each otherwise re-walk the
graph: BP-005 (every node reachable from ``entry_node``), BP-018 (every cycle
passes through a ``kind: loop`` node), DS-010 (a revision's ``after_node`` is an
ancestor of the referencing node) and DS-015 (``expected_path`` is a real
traversal).

Everything here is total: an edge naming a node that does not exist is BP-004's
finding to report, not this module's to crash on, so unknown ids simply
contribute no adjacency.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

__all__ = ["Graph"]


class Graph:
    """Adjacency over ``(node_ids, edges)``, built once per document.

    ``node_ids`` is the declared node set. Edges to or from an id outside it are
    kept out of the adjacency entirely: BP-004 reports them, and leaving them in
    would let a phantom node make a real one look reachable.
    """

    def __init__(self, node_ids: Iterable[str], edges: Sequence[Mapping[str, Any]]) -> None:
        self.node_ids: frozenset[str] = frozenset(node_ids)
        self._successors: dict[str, set[str]] = {node_id: set() for node_id in self.node_ids}
        self._predecessors: dict[str, set[str]] = {node_id: set() for node_id in self.node_ids}
        self.pairs: set[tuple[str, str]] = set()
        for edge in edges:
            source = edge.get("from")
            target = edge.get("to")
            if not isinstance(source, str) or not isinstance(target, str):
                continue
            if source not in self.node_ids or target not in self.node_ids:
                continue
            self._successors[source].add(target)
            self._predecessors[target].add(source)
            self.pairs.add((source, target))

    def successors(self, node_id: str) -> frozenset[str]:
        return frozenset(self._successors.get(node_id, ()))

    def predecessors(self, node_id: str) -> frozenset[str]:
        return frozenset(self._predecessors.get(node_id, ()))

    def has_edge(self, source: str, target: str) -> bool:
        return (source, target) in self.pairs

    def reachable_from(self, start: str, *, include_start: bool) -> frozenset[str]:
        """Every node reachable from ``start`` by following edges.

        ``include_start=False`` means "reachable in one or more steps", so a
        node in a cycle still reaches itself while a node in an acyclic region
        does not. DS-010 wants that reading: a revision's ``after_node`` is the
        node whose execution *produced* the state, so a fixture may only observe
        it strictly afterwards.
        """
        if start not in self.node_ids:
            return frozenset()
        seen: set[str] = {start} if include_start else set()
        frontier = [start]
        while frontier:
            current = frontier.pop()
            for successor in self._successors.get(current, ()):
                if successor not in seen:
                    seen.add(successor)
                    frontier.append(successor)
        return frozenset(seen)

    def cycle_ignoring(self, ignored: frozenset[str]) -> list[str] | None:
        """One cycle among the nodes *outside* ``ignored``, or ``None``.

        This is BP-018 turned into a decidable question. "Every cycle passes
        through at least one ``kind: loop`` node" cannot be checked by
        enumerating cycles - there can be exponentially many - but it is exactly
        equivalent to: with every loop node deleted, no cycle remains. The
        returned path is for the message only.
        """
        colour: dict[str, int] = {}  # 0 = on the stack, 1 = finished
        stack: list[str] = []

        def visit(node_id: str) -> list[str] | None:
            colour[node_id] = 0
            stack.append(node_id)
            for successor in sorted(self._successors.get(node_id, ())):
                if successor in ignored:
                    continue
                state = colour.get(successor)
                if state is None:
                    found = visit(successor)
                    if found is not None:
                        return found
                elif state == 0:
                    return [*stack[stack.index(successor) :], successor]
            stack.pop()
            colour[node_id] = 1
            return None

        for node_id in sorted(self.node_ids - ignored):
            if node_id not in colour:
                found = visit(node_id)
                if found is not None:
                    return found
        return None
