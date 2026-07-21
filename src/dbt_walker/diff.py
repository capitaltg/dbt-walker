"""Diff a current dbt manifest against an older one (phase 3).

Answers "what changed since production?" so the changed set can be discovered
rather than named — then fed into `impact`. This is a lineage/refresh-planning
diff (materialization, on_schema_change, SQL checksum, parent edges), not a full
manifest diff. Stdlib-only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from dbt_walker.graph import Graph

# resource types whose changes matter for refresh planning
DIFFABLE = {"model", "snapshot", "seed"}


@dataclass
class ModelChange:
    unique_id: str
    sql_changed: bool = False
    materialization: tuple[str, str] | None = None  # (old, new)
    on_schema_change: tuple[str | None, str | None] | None = None
    parents_added: list[str] = field(default_factory=list)
    parents_removed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "model": self.unique_id,
            "sql_changed": self.sql_changed,
            "materialization": list(self.materialization) if self.materialization else None,
            "on_schema_change": list(self.on_schema_change) if self.on_schema_change else None,
            "parents_added": self.parents_added,
            "parents_removed": self.parents_removed,
        }

    def summary(self, graph: Graph) -> str:
        bits = []
        if self.sql_changed:
            bits.append("sql changed")
        if self.materialization:
            bits.append(f"materialization {self.materialization[0]} -> {self.materialization[1]}")
        if self.on_schema_change:
            bits.append(
                f"on_schema_change {self.on_schema_change[0]} -> {self.on_schema_change[1]}"
            )
        if self.parents_added:
            bits.append("added deps: " + ", ".join(graph.label(p) for p in self.parents_added))
        if self.parents_removed:
            bits.append("removed deps: " + ", ".join(graph.label(p) for p in self.parents_removed))
        return "; ".join(bits)


@dataclass
class Diff:
    added: list[str]
    removed: list[str]
    modified: list[ModelChange]


def _checksum(node: dict) -> str | None:
    return (node.get("checksum") or {}).get("checksum")


def _node_change(uid: str, old_node: dict, new_node: dict,
                 old: Graph, new: Graph) -> ModelChange | None:
    change = ModelChange(uid)
    if _checksum(old_node) != _checksum(new_node):
        change.sql_changed = True
    if old.materialization(uid) != new.materialization(uid):
        change.materialization = (old.materialization(uid), new.materialization(uid))
    if old.on_schema_change(uid) != new.on_schema_change(uid):
        change.on_schema_change = (old.on_schema_change(uid), new.on_schema_change(uid))
    old_parents = set(old.parents.get(uid, []))
    new_parents = set(new.parents.get(uid, []))
    change.parents_added = sorted(new_parents - old_parents)
    change.parents_removed = sorted(old_parents - new_parents)
    if (change.sql_changed or change.materialization or change.on_schema_change
            or change.parents_added or change.parents_removed):
        return change
    return None


def diff_graphs(old: Graph, new: Graph) -> Diff:
    old_ids = {u for u in old.nodes if old.resource_type(u) in DIFFABLE}
    new_ids = {u for u in new.nodes if new.resource_type(u) in DIFFABLE}
    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)
    modified = []
    for uid in sorted(old_ids & new_ids):
        change = _node_change(uid, old.nodes[uid], new.nodes[uid], old, new)
        if change:
            modified.append(change)
    return Diff(added=added, removed=removed, modified=modified)
