"""Build the static lineage app: a single self-contained HTML explorer.

Design: `plans/2026-07-19-lineage-app-design.md`.

The expensive analysis (SQL parsing for column lineage) runs here, once, in
Python. What gets embedded is the *edge graph* — the model DAG plus each
model-column's direct parent columns — so the browser can answer any
lineage/impact question on demand by walking those edges in JS. No server, and
mermaid is inlined so the page renders with zero network.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from dbt_walker.graph import Graph, GraphError

VENDOR = Path(__file__).parent / "_vendor"
# node types worth listing in the tree / walking as lineage
TREE_TYPES = {"model", "seed", "snapshot", "source"}


def _git(project_root: Path, *args: str) -> str | None:
    """Best-effort git metadata for provenance; None outside a repo."""
    try:
        out = subprocess.run(["git", "-C", str(project_root), *args],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    value = out.stdout.strip()
    return value if out.returncode == 0 and value else None


def staleness(graph: Graph, project_root: Path) -> dict:
    """Is the manifest older than the model files it was built from?

    build-app never runs dbt (decision Q4) — it reports, and the app shows a
    banner, so nobody trusts a stale picture.
    """
    manifest = project_root / "target" / "manifest.json"
    if not manifest.exists():
        return {"stale": False, "checked": False, "newer": []}
    built = manifest.stat().st_mtime
    newer = []
    for uid, node in graph.nodes.items():
        rel = node.get("original_file_path")
        if not rel or node.get("resource_type") not in ("model", "snapshot"):
            continue
        path = project_root / Path(str(rel).replace("\\", "/"))
        try:
            if path.exists() and path.stat().st_mtime > built:
                newer.append(graph.label(uid))
        except OSError:
            continue
    return {"stale": bool(newer), "checked": True, "newer": sorted(newer)[:20],
            "newer_count": len(newer)}


def _folder(node: dict) -> str:
    """Folder shown in the tree: the model's directory under models/."""
    if node.get("resource_type") == "source":
        return f"sources/{node.get('source_name', '')}"
    rel = str(node.get("original_file_path") or "").replace("\\", "/")
    parts = rel.split("/")
    # models/staging/stg_x.sql -> staging ; models/x.sql -> models
    if len(parts) >= 3:
        return "/".join(parts[1:-1])
    return parts[0] if parts else ""


def collect(graph: Graph, project_root: Path, dialect: str | None = None,
            include_sql: bool = True) -> dict:
    """Everything the app needs, as a JSON-serializable payload."""
    from dbt_walker.columns import (ColumnGraph, dialect_for, select_spans,
                                     unresolved_reason)

    nodes: dict[str, dict] = {}
    for uid, node in graph.nodes.items():
        rtype = node.get("resource_type")
        if rtype not in TREE_TYPES and rtype != "exposure":
            continue  # tests are summarized per-model instead of listed
        nodes[uid] = {
            "name": graph.label(uid),
            "type": rtype,
            "mat": graph.materialization(uid),
            "osc": graph.on_schema_change(uid),
            "relation": graph.relation(uid),
            "rel_st": graph.relation_schema_table(uid),  # db-less, for DROP DDL
            "folder": _folder(node),
            "path": str(node.get("original_file_path") or ""),
        }

    # tests attached to each model, so impact can report "N tests will re-run"
    tests: dict[str, list[str]] = {}
    for uid, node in graph.nodes.items():
        if node.get("resource_type") != "test":
            continue
        for parent in graph.parents.get(uid, []):
            tests.setdefault(parent, []).append(graph.label(uid))

    payload: dict = {
        "nodes": nodes,
        "parents": {u: [p for p in ps if p in nodes]
                    for u, ps in graph.parents.items() if u in nodes},
        "children": {u: [c for c in cs if c in nodes]
                     for u, cs in graph.children.items() if u in nodes},
        "tests": tests,
        "columns": {},
        "sql": {},
    }

    # column edge graph (the expensive part) + SQL for the viewer
    try:
        cg = ColumnGraph(graph, dialect=dialect)
        payload["dialect"] = cg.dialect
        cat = cg.catalog
        payload["catalog"] = {
            "present": bool(cat and cat.present),
            "relations": cat.relation_count if cat else 0,
            "stale": bool(cat and cat.stale),
            "generated_at": cat.generated_at if cat else None,
        }
        for uid in nodes:
            if graph.resource_type(uid) != "model":
                continue
            mc = cg.columns_of(uid)
            # line ranges of the expression(s) producing each column, so the SQL
            # viewer can highlight multi-line expressions exactly. A column can
            # have several ranges when parallel CTEs each produce it. Empty on
            # unparseable SQL — the viewer falls back to alias matching.
            spans = select_spans(graph.compiled_sql(uid) or "", cg.dialect)
            entry = {
                "resolved": mc.resolved,
                "cols": {
                    # [parent_uid, parent_column, transform, external_relation]
                    col: [[e.parent, e.column, e.transform, e.parent_rel] for e in edges]
                    for col, edges in mc.columns.items()
                },
                "spans": {c: [[a, b] for a, b in ranges]
                          for c, ranges in spans.items() if c in mc.columns},
            }
            if mc.passthrough is not None:
                # a `select *` chain: any column not in `cols` passes through by
                # name to this terminal (a dbt node, or an external relation)
                entry["passthrough"] = {"parent": cg.relation_uid(mc.passthrough),
                                        "rel": mc.passthrough}
            if not mc.resolved:
                # why it didn't resolve, so the picker can nudge toward a remedy
                entry["why"] = unresolved_reason(graph, uid, cg.dialect)
            payload["columns"][uid] = entry
    except GraphError as exc:
        # sqlglot missing or no compiled SQL: model-level features still work
        payload["dialect"] = dialect_for(graph) if dialect is None else dialect
        payload["columns_error"] = str(exc)

    if include_sql:
        for uid in nodes:
            raw, compiled = graph.raw_sql(uid), graph.compiled_sql(uid)
            if raw or compiled:
                payload["sql"][uid] = {"raw": raw or "", "compiled": compiled or ""}

    counts: dict[str, int] = {}
    for meta in nodes.values():
        key = meta["mat"] if meta["type"] == "model" else meta["type"]
        counts[key] = counts.get(key, 0) + 1

    project_name = (graph.manifest.get("metadata") or {}).get("project_name") or "dbt project"
    branch = _git(project_root, "rev-parse", "--abbrev-ref", "HEAD")
    payload["project"] = {
        "name": project_name,
        "branch": branch,
        "sha": _git(project_root, "rev-parse", "--short", "HEAD"),
        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "adapter": (graph.manifest.get("metadata") or {}).get("adapter_type"),
        "counts": counts,
        "staleness": staleness(graph, project_root),
    }
    return payload


def mermaid_js() -> str:
    path = VENDOR / "mermaid.min.js"
    if not path.exists():  # pragma: no cover - packaging guard
        raise GraphError(
            f"vendored mermaid is missing at {path}. Reinstall dbt-walker, or see "
            "src/dbt_walker/_vendor/README.md to restore it."
        )
    return path.read_text(encoding="utf-8")


def default_filename(payload: dict) -> str:
    """<project>-lineage[-<branch>]-<YYYYMMDD-HHMM>.html (decision Q6)."""
    import re

    project = payload["project"]
    safe = lambda s: re.sub(r"[^0-9A-Za-z._-]+", "_", str(s)).strip("_")  # noqa: E731
    parts = [safe(project["name"]), "lineage"]
    branch = project.get("branch")
    if branch and branch not in ("HEAD",):
        parts.append(safe(branch))
    parts.append(datetime.now().strftime("%Y%m%d-%H%M"))
    return "-".join(parts) + ".html"


def build(graph: Graph, project_root: Path, dialect: str | None = None) -> tuple[str, dict]:
    """Render the whole app. Returns (html, payload)."""
    from dbt_walker import app_template

    payload = collect(graph, project_root, dialect=dialect)
    html = app_template.render(payload, mermaid_js())
    return html, payload
