"""Phase 19E — NON-RECURSIVE TOPOLOGICAL ORDER for world plans (pure).

The world composition graph: every placement plan is a node; an edge
``parent -> child`` means the child's placement/definition references the
parent's identity first (generated part ``parentId`` chains, shared procedural
definitions, ``near_victim``-style reference ordering). This module provides a
deterministic, NON-RECURSIVE topological order (Kahn's algorithm with a stable
min-heap tie-break), so arbitrarily deep plan graphs can never push the Python
call stack into ``RecursionError`` (DEF-067 hardening principle: bounded,
iterative, never recursive).

Contract:

- ``topological_plan_order(plans, edge_fn)`` returns a NEW tuple of the SAME
  items in an order where every parent precedes its children. Items compare on
  their stable sort key (use ``item_id``), so equal inputs ALWAYS produce equal
  outputs.
- A CYCLE never crashes and never loops forever: the cycle's members are
  emitted in deterministic id order AFTER every acyclic member (the caller's
  validators still fail closed on a real cycle; the sorter itself is total on
  any input).
- Pure: no I/O, no mutation, ``O(|V| log |V| + |E|)``.

``topological_plan_order`` replaces any recursive depth-first ordering of the
plan graph — the only documented route for plan ordering; callers MUST NOT add
a recursive alternative.
"""

from __future__ import annotations

import heapq
from typing import Any, Callable, Mapping, Sequence

# Exit code convention for the O(n log n) bounded-iterative sort: the caller
# treats a NOT_FULLY_ORDERED cycle the way it treats any validation issue
# (never a crash, never an infinite loop).
EXIT_OK = 0
EXIT_CYCLE = 1


def _identity(value: Any) -> Any:
    return value


def topological_plan_order(
    plans: Sequence[Any],
    edge_fn: Callable[[Any], Sequence[Any]] | None = None,
    *,
    key: Callable[[Any], Any] = _identity,
) -> tuple[tuple[Any, ...], int]:
    """Deterministic NON-RECURSIVE topological order of one plan list.

    ``plans`` — the sequence of plan objects (any hashable-by-key items).
    ``edge_fn(item)`` — returns the parents of ``item`` (children reference
    parents first); None uses ``item.parents`` when present else no edges.
    ``key(item)`` — the stable sort/equality key (default identity).

    Returns ``(ordered, exit_code)``:
    - ``ordered`` — every acyclic item in a valid order (parents first,
      tie-break by the stable key); cyclic members (only when a cycle exists)
      are appended last in key order;
    - ``exit_code`` — ``EXIT_OK`` when the whole list is acyclic, otherwise
      ``EXIT_CYCLE`` (the non-empty cycled members are still deterministically
      ordered; validation decides whether a cycle is fatal).
    """
    plans = tuple(plans)
    if not plans:
        return (), EXIT_OK
    keyed = [(key(item), item) for item in plans]
    index_of: dict[Any, int] = {}
    for index, (stable, item) in enumerate(keyed):
        if stable in index_of:
            raise ValueError(
                f"plan key {stable!r} is not unique: duplicate plans are not "
                "topologically sortable"
            )
        index_of[stable] = index

    # household -> parent keys (stable only; a missing parent key is a broken
    # reference the caller's validators report — here we just treat it as a
    # soft edge so the graph stays total).
    parent_keys: dict[Any, list[Any]] = {}
    for stable, item in keyed:
        try:
            parents = edge_fn(item) if edge_fn is not None else getattr(item, "parents", ())
        except Exception:  # noqa: BLE001 - a bad edge lookup never crashes sort
            parents = ()
        for parent in parents or ():
            if parent in index_of:
                parent_keys.setdefault(stable, []).append(parent)

    indegree: dict[Any, int] = {stable: 0 for stable, _ in keyed}
    children: dict[Any, list[Any]] = {stable: [] for stable, _ in keyed}
    for stable, parents in parent_keys.items():
        indegree[stable] = len(set(parents))
        for parent in set(parents):
            children.setdefault(parent, []).append(stable)

    # Kahn with a stable min-heap tie-break: NEVER recursive.
    heap: list[Any] = sorted(
        [stable for stable, degree in indegree.items() if degree == 0]
    )
    heapq.heapify(heap)
    ordered: list[Any] = []
    remaining = set(index_of)
    while heap:
        stable = heapq.heappop(heap)
        if stable not in remaining:
            continue
        ordered.append(stable)
        remaining.discard(stable)
        for child in sorted(children.get(stable, ())):
            if child in remaining:
                indegree[child] -= 1
                if indegree[child] == 0:
                    heapq.heappush(heap, child)

    by_stable = {stable: item for stable, item in keyed}
    if remaining:
        # cycle: remaining members emitted deterministically AFTER the acyclic
        # prefix (never a loop, never a crash).
        ordered.extend(sorted(remaining))
        result = tuple(by_stable[stable] for stable in ordered)
        return result, EXIT_CYCLE
    return tuple(by_stable[stable] for stable in ordered), EXIT_OK


def order_plans_deterministic(
    plans: Sequence[Any],
    *,
    key: Callable[[Any], Any] = _identity,
    parent_attr: str = "parents",
) -> tuple[Any, ...]:
    """Convenience: stable topological order ignoring the exit code.

    Use for callers where the world graph validation is a SEPARATE concern
    (the composer validates placement integrity after ordering).
    """
    return topological_plan_order(
        plans,
        edge_fn=(lambda item: getattr(item, parent_attr, ())),
        key=key,
    )[0]


__all__ = [
    "EXIT_CYCLE",
    "EXIT_OK",
    "order_plans_deterministic",
    "topological_plan_order",
]