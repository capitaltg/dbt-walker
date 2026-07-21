"""Load a dbt manifest.json and expose the node DAG.

The manifest already contains the fully-resolved dependency graph in
``parent_map`` / ``child_map`` (keyed by unique_id, covering models, seeds,
snapshots, sources, tests, and exposures), so no SQL parsing or graph library
is needed at the model level.
"""
from __future__ import annotations

import json
from pathlib import Path

# resource_types a user would name on the command line
ADDRESSABLE = {"model", "seed", "snapshot", "source"}


class GraphError(Exception):
    pass


class Graph:
    def __init__(self, manifest: dict, project_root: Path | None = None):
        self.manifest = manifest
        # project root (the dir containing target/); needed to locate compiled SQL.
        # None when the graph is built from a bare manifest dict (e.g. unit tests).
        self.project_root = project_root
        self.nodes: dict[str, dict] = {}
        for section in ("nodes", "sources", "exposures"):
            self.nodes.update(manifest.get(section) or {})
        self.parents: dict[str, list[str]] = manifest.get("parent_map") or {}
        self.children: dict[str, list[str]] = manifest.get("child_map") or {}

    @classmethod
    def load(cls, project_dir: str | Path) -> "Graph":
        project_dir = Path(project_dir)
        path = project_dir / "target" / "manifest.json"
        if project_dir.name == "target" or project_dir.suffix == ".json":
            path = project_dir if project_dir.suffix == ".json" else project_dir / "manifest.json"
        if not path.exists():
            raise GraphError(
                f"No manifest at {path} — run `dbt compile` (or any dbt build command) first."
            )
        # path is <root>/target/manifest.json -> root is two levels up
        root = path.parent.parent
        return cls(json.loads(path.read_text(encoding="utf-8")), project_root=root)

    # ------------------------------------------------------------------ lookup

    def resolve(self, name: str) -> str:
        """Resolve a bare model/seed/snapshot/source name to a unique_id."""
        if name in self.nodes:
            return name
        matches = [
            uid
            for uid, node in self.nodes.items()
            if node.get("name") == name and node.get("resource_type") in ADDRESSABLE
        ]
        if not matches:
            raise GraphError(f"No model/seed/snapshot/source named {name!r} in manifest.")
        if len(matches) > 1:
            raise GraphError(f"Ambiguous name {name!r}; use a unique_id: {', '.join(matches)}")
        return matches[0]

    def resource_type(self, uid: str) -> str:
        return (self.nodes.get(uid) or {}).get("resource_type", "?")

    def materialization(self, uid: str) -> str:
        node = self.nodes.get(uid) or {}
        rtype = node.get("resource_type")
        if rtype in ("model", "snapshot", "seed"):
            return (node.get("config") or {}).get("materialized") or rtype
        return rtype or "?"

    def on_schema_change(self, uid: str) -> str | None:
        return ((self.nodes.get(uid) or {}).get("config") or {}).get("on_schema_change")

    def label(self, uid: str) -> str:
        """Human name: model name, or source_name.table for sources."""
        node = self.nodes.get(uid) or {}
        if node.get("resource_type") == "source":
            return f"{node.get('source_name')}.{node.get('name')}"
        return node.get("name") or uid

    def relation(self, uid: str) -> str:
        """database.schema.identifier the node materializes as."""
        node = self.nodes.get(uid) or {}
        parts = [node.get("database"), node.get("schema"), node.get("alias") or node.get("name")]
        return ".".join(p for p in parts if p)

    def raw_sql(self, uid: str) -> str | None:
        """The model's SQL as written (jinja/refs/macros), from the manifest."""
        return (self.nodes.get(uid) or {}).get("raw_code") or None

    def compiled_sql(self, uid: str) -> str | None:
        """The compiled SQL (jinja/macros resolved): manifest ``compiled_code`` if
        present, else the file at ``compiled_path`` under the project root."""
        node = self.nodes.get(uid) or {}
        code = node.get("compiled_code")
        if code:
            return code
        cpath = node.get("compiled_path")
        if not cpath or self.project_root is None:
            return None
        path = self.project_root / Path(str(cpath).replace("\\", "/"))
        return path.read_text(encoding="utf-8") if path.exists() else None

    # ---------------------------------------------------------------- walking

    def walk(self, uid: str, direction: str, depth: int | None = None) -> dict[str, int]:
        """BFS up ('up') or down ('down'); returns {unique_id: distance}."""
        edge_map = self.parents if direction == "up" else self.children
        seen: dict[str, int] = {}
        frontier = [uid]
        dist = 0
        while frontier and (depth is None or dist < depth):
            dist += 1
            nxt = []
            for node in frontier:
                for neighbor in edge_map.get(node, []):
                    if neighbor not in seen and neighbor != uid:
                        seen[neighbor] = dist
                        nxt.append(neighbor)
            frontier = nxt
        return seen

    def topo_order(self, uids: set[str]) -> list[str]:
        """Order a subset of nodes so every node comes after its ancestors."""
        result: list[str] = []
        visiting: set[str] = set()
        done: set[str] = set()

        def visit(uid: str) -> None:
            if uid in done or uid in visiting:
                return
            visiting.add(uid)
            for parent in self.parents.get(uid, []):
                if parent in uids:
                    visit(parent)
            visiting.discard(uid)
            done.add(uid)
            result.append(uid)

        for uid in sorted(uids):
            visit(uid)
        return result
