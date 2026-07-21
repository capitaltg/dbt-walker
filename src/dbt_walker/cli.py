"""dbt-walker command-line interface.

Commands (v0, model-level — column-level lineage lands in phase 2):

    dbt-walker upstream <model>      what this model reads from
    dbt-walker downstream <model>    what reads from this model
    dbt-walker impact <model>        full blast radius + refresh plan
"""
from __future__ import annotations

import argparse
import json
import sys

from dbt_walker.graph import Graph, GraphError

# on_schema_change values that let an incremental absorb *additive* column
# changes without a full refresh
SCHEMA_CHANGE_SAFE = {"append_new_columns", "sync_all_columns"}


def _load(args: argparse.Namespace) -> Graph:
    return Graph.load(args.project_dir)


def _fmt(graph: Graph, uid: str) -> str:
    mat = graph.materialization(uid)
    extra = ""
    if mat == "incremental":
        osc = graph.on_schema_change(uid) or "ignore"
        extra = f", on_schema_change={osc}"
    return f"{graph.label(uid)}  [{mat}{extra}]"


def _print_walk(graph: Graph, root: str, direction: str, args: argparse.Namespace) -> None:
    found = graph.walk(root, direction, args.depth)
    if args.mat:
        found = {u: d for u, d in found.items() if graph.materialization(u) in args.mat}
    if args.json:
        out = [
            {
                "unique_id": uid,
                "name": graph.label(uid),
                "distance": dist,
                "resource_type": graph.resource_type(uid),
                "materialization": graph.materialization(uid),
                "relation": graph.relation(uid),
            }
            for uid, dist in sorted(found.items(), key=lambda kv: (kv[1], kv[0]))
        ]
        print(json.dumps(out, indent=2))
        return
    arrow = "<-" if direction == "up" else "->"
    print(_fmt(graph, root))
    for uid, dist in sorted(found.items(), key=lambda kv: (kv[1], kv[0])):
        if graph.resource_type(uid) == "test" and not args.tests:
            continue
        print(f"{'  ' * dist}{arrow} {_fmt(graph, uid)}")
    if not found:
        print(f"  (no {'upstream' if direction == 'up' else 'downstream'} nodes)")


def cmd_upstream(args: argparse.Namespace) -> None:
    graph = _load(args)
    _print_walk(graph, graph.resolve(args.model), "up", args)


def cmd_downstream(args: argparse.Namespace) -> None:
    graph = _load(args)
    _print_walk(graph, graph.resolve(args.model), "down", args)


def _classify_models(graph: Graph, model_uids, additive: bool):
    """Split models into (full_refresh, rebuild) in topological order.

    additive column changes are absorbed by append/sync incrementals; renames,
    drops, and type changes are never safe without a full refresh.
    """
    full_refresh, rebuild = [], []
    for uid in graph.topo_order(set(model_uids)):
        if graph.materialization(uid) != "incremental":
            rebuild.append(uid)
        elif additive and (graph.on_schema_change(uid) or "ignore") in SCHEMA_CHANGE_SAFE:
            rebuild.append(uid)
        else:
            full_refresh.append(uid)
    return full_refresh, rebuild


def _upstream_prereqs(graph: Graph, root: str) -> list[str]:
    """Incremental ancestors of `root`, in the order you'd refresh them.

    Rebuilding an incremental model re-reads its parents from scratch. Any
    ancestor that is itself incremental only holds whatever ITS incremental runs
    accumulated — so the rebuild is only as complete as that stored history. A
    full refresh therefore has an upstream precondition, and when several need
    refreshing they must go ancestors-first (topological order).
    """
    ancestors = {u for u in graph.walk(root, "up")
                 if graph.materialization(u) == "incremental"}
    return graph.topo_order(ancestors)


def _ddl_entries(graph: Graph, full_refresh):
    """An explicit DROP per full-refresh table (frees disk before the rebuild,
    which matters for very large tables). A DROP ... CASCADE also removes
    downstream views, so each statement is annotated with the views it takes out.
    """
    ddl = []
    for uid in full_refresh:
        victims = [d for d in graph.walk(uid, "down") if graph.materialization(d) == "view"]
        ddl.append(
            {
                "statement": f"DROP TABLE {graph.relation(uid)} CASCADE;",
                "relation": graph.relation(uid),
                "model": uid,
                "cascade_drops_views": sorted(victims),
            }
        )
    return ddl


def _print_refresh_plan(graph: Graph, full_refresh, ddl, changed_label) -> None:
    print("\nSuggested commands (safe: atomic swap, needs ~2x storage during rebuild):")
    if full_refresh:
        names = " ".join(graph.label(u) for u in full_refresh)
        print(f"  dbt run --select {names} --full-refresh")
    print(f"  dbt build --select {changed_label}+")
    if ddl:
        print("\nDDL alternative (drop now, rebuild later - frees disk immediately,")
        print("but the table is gone until rebuilt and a failed rebuild leaves nothing):")
        for entry in ddl:
            print(f"  {entry['statement']}")
            if entry["cascade_drops_views"]:
                views = ", ".join(graph.label(v) for v in entry["cascade_drops_views"])
                print(f"    !! CASCADE also drops downstream views: {views}")
                print("       (rebuild them with the dbt command above)")


def cmd_impact(args: argparse.Namespace) -> None:
    """Blast radius of changing a model: descendants grouped by how they must
    be handled, plus the dbt commands to rebuild them."""
    graph = _load(args)
    root = graph.resolve(args.model)
    if getattr(args, "column", None):
        _impact_column(graph, root, args)
        return
    down = graph.walk(root, "down")

    # the model you're changing needs rebuilding too — and if it's incremental,
    # a plain run only appends, so its existing rows keep the OLD logic
    models, tests, exposures, snapshots = [], [], [], []
    if graph.resource_type(root) == "model":
        models.append(root)
    for uid in down:
        rtype = graph.resource_type(uid)
        if rtype == "model":
            models.append(uid)
        elif rtype == "test":
            tests.append(uid)
        elif rtype == "exposure":
            exposures.append(uid)
        elif rtype == "snapshot":
            snapshots.append(uid)

    full_refresh, rebuild_only = _classify_models(graph, models, args.additive)
    ddl = _ddl_entries(graph, full_refresh)
    prereqs = _upstream_prereqs(graph, root)

    if args.json:
        print(
            json.dumps(
                {
                    "changed": root,
                    "upstream_prerequisites": prereqs,
                    "full_refresh": full_refresh,
                    "rebuild": rebuild_only,
                    "snapshots": snapshots,
                    "tests": tests,
                    "exposures": exposures,
                    "ddl": ddl,
                },
                indent=2,
            )
        )
        return

    print(f"Changing: {_fmt(graph, root)}\n")
    if prereqs:
        print("BEFORE rebuilding, upstream incrementals that gate its history")
        print("(a rebuild is only as complete as what these hold) - refresh order:")
        for i, uid in enumerate(prereqs, 1):
            print(f"  {i}. {_fmt(graph, uid)}")
        print()
    if full_refresh:
        print("Incremental models needing FULL REFRESH (topological order):")
        for uid in full_refresh:
            print(f"  ! {_fmt(graph, uid)}   ({graph.relation(uid)})")
    if rebuild_only:
        label = "Models to rebuild (normal run is enough):"
        print(("\n" if full_refresh else "") + label)
        for uid in rebuild_only:
            print(f"  - {_fmt(graph, uid)}")
    if snapshots:
        print("\nSnapshots downstream (check-cols/timestamp logic may capture bogus diffs):")
        for uid in snapshots:
            print(f"  * {_fmt(graph, uid)}")
    if tests:
        print(f"\nDownstream tests that will re-run: {len(tests)}")
    if exposures:
        print("\nExposures (dashboards/apps) affected:")
        for uid in exposures:
            print(f"  @ {graph.label(uid)}")
    if not down:
        print("Nothing downstream - leaf model.")
        return

    _print_refresh_plan(graph, full_refresh, ddl, graph.label(root))


# --------------------------------------------------------------- column lineage

def _column_graph(graph: Graph, args: argparse.Namespace):
    from dbt_walker.columns import ColumnGraph  # lazy: keeps model-level path stdlib-only

    cg = ColumnGraph(graph, dialect=args.dialect)
    _catalog_note(cg)
    return cg


def _catalog_note(cg) -> None:
    """One-line note (stderr, so --json stays clean) on catalog use. A catalog
    sharpens column resolution; a stale one is a hint, never a hard stop."""
    cat = cg.catalog
    if cat is None or not cat.present:
        return
    msg = f"note: using catalog.json ({cat.relation_count} relations) to resolve columns"
    if cat.stale:
        msg += ("\nwarning: catalog.json is OLDER than the manifest; its column lists may be\n"
                "         out of date. Re-run `dbt docs generate` if lineage looks wrong.")
    print(msg, file=sys.stderr)


def _check_column(cg, graph: Graph, uid: str, column: str) -> None:
    mc = cg.columns_of(uid)
    if mc.resolved and column not in mc.columns:
        have = ", ".join(sorted(mc.columns)) or "(none)"
        raise GraphError(f"{graph.label(uid)} has no column {column!r}. Columns: {have}")


def cmd_col_upstream(args: argparse.Namespace) -> None:
    graph = _load(args)
    cg = _column_graph(graph, args)
    uid = graph.resolve(args.model)
    _check_column(cg, graph, uid, args.column)
    trace = cg.upstream(uid, args.column)
    if args.json:
        print(json.dumps(trace.edges, indent=2))
        return
    print(f"{graph.label(uid)}.{args.column}")
    for e in trace.edges:
        indent = "  " * e["distance"]
        if e["parent"] is None or e["transform"] == "unknown":
            print(f"{indent}<- (lineage unknown - cannot trace further)")
        else:
            print(f"{indent}<- {graph.label(e['parent'])}.{e['parent_column']}  [{e['transform']}]")
    if not trace.edges:
        print("  (no upstream columns - literal or count(*))")


def cmd_col_downstream(args: argparse.Namespace) -> None:
    graph = _load(args)
    cg = _column_graph(graph, args)
    uid = graph.resolve(args.model)
    _check_column(cg, graph, uid, args.column)
    taint = cg.taint_downstream(uid, args.column)
    derived = sorted(
        (u, c) for (u, c) in taint.tainted
        if not (u == uid and c == args.column) and c != "*"
    )
    if args.json:
        print(json.dumps(
            {
                "column": {"model": uid, "name": args.column},
                "derived": [{"model": u, "column": c} for u, c in derived],
                "unknown_models": sorted(taint.unknown_models),
            },
            indent=2,
        ))
        return
    print(f"Columns derived from {graph.label(uid)}.{args.column}:")
    by_model: dict[str, list[str]] = {}
    for u, c in derived:
        by_model.setdefault(u, []).append(c)
    for u in graph.topo_order(set(by_model)):
        print(f"  {graph.label(u)}: {', '.join(sorted(by_model[u]))}")
    if not derived:
        print("  (nothing downstream reads this column)")
    if taint.unknown_models:
        print("\nIncluded conservatively - lineage unresolved (fail closed):")
        for u in sorted(taint.unknown_models):
            print(f"  ? {graph.label(u)}")


def _impact_column(graph: Graph, root: str, args: argparse.Namespace) -> None:
    cg = _column_graph(graph, args)
    _check_column(cg, graph, root, args.column)
    taint = cg.taint_downstream(root, args.column)
    affected_models = [u for u in taint.affected if graph.resource_type(u) == "model"]
    if graph.resource_type(root) == "model" and root not in affected_models:
        affected_models.append(root)  # the changed model itself must be rebuilt

    full_refresh, rebuild_only = _classify_models(graph, affected_models, args.additive)
    ddl = _ddl_entries(graph, full_refresh)
    affected = set(affected_models)
    # tests / exposures that hang off an affected model
    tests, exposures = [], []
    for uid in graph.walk(root, "down"):
        rtype = graph.resource_type(uid)
        if rtype in ("test", "exposure") and affected.intersection(graph.parents.get(uid, [])):
            (tests if rtype == "test" else exposures).append(uid)

    total_models = sum(
        1 for u in graph.walk(root, "down") if graph.resource_type(u) == "model"
    )
    if args.json:
        print(json.dumps(
            {
                "changed": root,
                "column": args.column,
                "full_refresh": full_refresh,
                "rebuild": rebuild_only,
                "tests": tests,
                "exposures": exposures,
                "ddl": ddl,
                "unknown_models": sorted(taint.unknown_models),
            },
            indent=2,
        ))
        return

    print(f"Changing column: {graph.label(root)}.{args.column}\n")
    print(f"{len(affected_models)} of {total_models} downstream models read this column.")
    if taint.unknown_models:
        print(f"({len(taint.unknown_models)} included conservatively - lineage unresolved.)")
    print()
    if full_refresh:
        print("Incremental models needing FULL REFRESH (topological order):")
        for uid in full_refresh:
            mark = " [lineage unknown]" if uid in taint.unknown_models else ""
            print(f"  ! {_fmt(graph, uid)}   ({graph.relation(uid)}){mark}")
    if rebuild_only:
        print(("\n" if full_refresh else "") + "Models to rebuild (normal run is enough):")
        for uid in rebuild_only:
            mark = " [lineage unknown]" if uid in taint.unknown_models else ""
            print(f"  - {_fmt(graph, uid)}{mark}")
    if tests:
        print(f"\nDownstream tests that will re-run: {len(tests)}")
    if exposures:
        print("\nExposures (dashboards/apps) affected:")
        for uid in exposures:
            print(f"  @ {graph.label(uid)}")
    if not affected_models:
        print("No downstream model reads this column - nothing to refresh.")
        return
    _print_refresh_plan(graph, full_refresh, ddl, graph.label(root))


# ------------------------------------------------------------------ graph/diff

def _column_selection(graph: Graph, args: argparse.Namespace):
    """(nodes, root, columns) for a column-scoped graph: only the models a change
    to <model>.<column> touches, plus a per-node map of which columns carry the
    change. down/both = affected (taint) set; up/both = the column's provenance."""
    if not args.model:
        raise GraphError("graph --column requires a model to root the column on.")
    cg = _column_graph(graph, args)
    root = graph.resolve(args.model)
    _check_column(cg, graph, root, args.column)

    def transform_of(uid: str, col: str) -> str | None:
        # how `col` is computed in `uid` (passthrough/rename/cast/expression/
        # aggregate/...); None for sources/seeds (raw), "unknown" if unresolved
        if graph.resource_type(uid) != "model":
            return None
        mc = cg.columns_of(uid)
        if not mc.resolved:
            return "unknown"
        edges = mc.columns.get(col)
        return edges[0].transform if edges else None

    nodes = {root}
    columns: dict[str, set] = {root: {args.column}}
    if args.direction in ("down", "both"):
        taint = cg.taint_downstream(root, args.column)
        for uid in taint.affected:
            if graph.resource_type(uid) == "model":
                nodes.add(uid)
        for uid, col in taint.tainted:
            if col != "*":
                columns.setdefault(uid, set()).add(col)
    if args.direction in ("up", "both"):
        for edge in cg.upstream(root, args.column).edges:
            if edge["parent"]:
                nodes.add(edge["parent"])
                if edge["parent_column"]:
                    columns.setdefault(edge["parent"], set()).add(edge["parent_column"])

    # attach the transform kind to each column: {uid: [(col, transform), ...]}
    annotated = {
        uid: [(col, transform_of(uid, col)) for col in sorted(cols)]
        for uid, cols in columns.items()
    }
    return nodes, root, annotated


def _apply_graph_filters(graph: Graph, nodes: set[str], args: argparse.Namespace) -> set[str]:
    keep = set()
    for uid in nodes:
        rtype = graph.resource_type(uid)
        if rtype == "test" and not args.tests:
            continue
        if rtype == "exposure":
            continue  # exposures aren't lineage nodes worth drawing by default
        if args.mat and graph.materialization(uid) not in args.mat:
            continue
        keep.add(uid)
    return keep


def _graph_selection(graph: Graph, args: argparse.Namespace):
    """Return (nodes, root_uid_or_None, columns_map_or_None): column-scoped,
    rooted (up/down/both), or the whole project, after resource/mat/tests filters."""
    columns = None
    if getattr(args, "column", None):
        nodes, root, columns = _column_selection(graph, args)
    elif getattr(args, "model", None):
        root = graph.resolve(args.model)
        nodes = {root}
        if args.direction in ("up", "both"):
            nodes |= set(graph.walk(root, "up", args.depth))
        if args.direction in ("down", "both"):
            nodes |= set(graph.walk(root, "down", args.depth))
    else:
        root = None
        nodes = set(graph.nodes)

    nodes = _apply_graph_filters(graph, nodes, args)
    if root is not None:
        nodes.add(root)  # never let filters drop the focus node
    return nodes, root, columns


_GRAPH_EXT = {"html": "html", "mermaid": "mmd", "dot": "dot"}


def _graph_out_path(args: argparse.Namespace):
    """Output path honoring --out (file or dir), else
    ./graphs/dbt-walker-<model>[-<column>]-<direction>-<timestamp>.<ext>."""
    import re
    from datetime import datetime
    from pathlib import Path

    root = re.sub(r"[^0-9A-Za-z]+", "_", args.model) if args.model else "project"
    col = f"-{re.sub(r'[^0-9A-Za-z]+', '_', args.column)}" if getattr(args, "column", None) else ""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    default_name = f"dbt-walker-{root}{col}-{args.direction}-{stamp}.{_GRAPH_EXT[args.format]}"
    if args.out:
        out = Path(args.out)
        return out / default_name if out.is_dir() or args.out.endswith(("/", "\\")) else out
    return Path("graphs") / default_name


def _graph_sql(graph: Graph, nodes, columns) -> dict:
    """{uid: {label, raw, compiled, cols}} for nodes that have SQL — powers the
    click-to-view drawer. cols are the node's affected column names (column mode)."""
    sql = {}
    for uid in nodes:
        raw = graph.raw_sql(uid)
        compiled = graph.compiled_sql(uid)
        if not raw and not compiled:
            continue  # sources/seeds have no SQL to show
        cols = columns[uid] if columns and uid in columns else []  # [(col, transform), ...]
        sql[uid] = {"label": graph.label(uid), "raw": raw or "",
                    "compiled": compiled or "", "cols": cols}
    return sql


def _graph_content(render, graph: Graph, nodes, root, columns, args) -> str:
    if args.format != "html":
        return render.render(graph, nodes, args.format, root=root, columns=columns)
    from datetime import datetime

    title = f"dbt-walker graph: {graph.label(root) if root else 'whole project'}"
    if args.column:
        title += f".{args.column}"
    filters = [f"direction={args.direction}"]
    if args.column:
        filters.append("column-scoped (affected models only)")
    if args.depth is not None:
        filters.append(f"depth={args.depth}")
    if args.mat:
        filters.append(f"mat={','.join(args.mat)}")
    return render.to_html(
        graph, nodes, title=title,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        subtitle=f"{len(nodes)} nodes &middot; {' &middot; '.join(filters)}",
        root=root, columns=columns, sql=_graph_sql(graph, nodes, columns),
    )


_GRAPH_HINT = {
    "html": "Open it in a browser to view (loads mermaid.js from a CDN, so needs internet to render).",
    "mermaid": "Paste into a GitHub ```mermaid block or https://mermaid.live to view.",
    "dot": "Render with Graphviz, e.g.  dot -Tpng {path} -o graph.png",
}


def cmd_graph(args: argparse.Namespace) -> None:
    from dbt_walker import render  # stdlib-only, but keep the CLI import surface small

    graph = _load(args)
    nodes, root, columns = _graph_selection(graph, args)
    if not nodes:
        sys.exit("error: no nodes to render (check filters / model name).")

    content = _graph_content(render, graph, nodes, root, columns, args)
    if args.out == "-":  # explicit stdout, e.g. for piping dot to Graphviz
        print(content, end="")
        return

    path = _graph_out_path(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"Wrote {path}")
    print(_GRAPH_HINT[args.format].format(path=path))


def cmd_build_app(args: argparse.Namespace) -> None:
    """Generate the standalone HTML lineage explorer for a dbt project."""
    from pathlib import Path

    from dbt_walker import app as app_mod

    # accept it positionally (`build-app path/to/project`) or via --project-dir
    project_root = Path(args.project_root or args.project_dir).resolve()
    manifest = project_root / "target" / "manifest.json"
    if not manifest.exists():
        sys.exit(
            f"error: no manifest at {manifest}\n"
            "The app is built from dbt's artifacts, so compile the project first:\n"
            f"    cd {project_root}\n"
            "    dbt compile"
        )

    graph = Graph.load(project_root)
    html, payload = app_mod.build(graph, project_root, dialect=args.dialect)

    out = Path(args.out) if args.out else Path(app_mod.default_filename(payload))
    if out.is_dir() or str(args.out or "").endswith(("/", "\\")):
        out = out / app_mod.default_filename(payload)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    counts = payload["project"]["counts"]
    size_mb = out.stat().st_size / 1_000_000
    print(f"Wrote {out}  ({size_mb:.1f} MB)")
    print("  " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    if payload.get("columns_error"):
        print(f"  NOTE: column-level lineage unavailable — {payload['columns_error']}")
    stale = payload["project"]["staleness"]
    if stale.get("stale"):
        print(f"  WARNING: {stale['newer_count']} model file(s) are newer than the manifest "
              "(e.g. " + ", ".join(stale["newer"][:3]) + ").")
        print("           Re-run `dbt compile` and rebuild for accurate results.")
    print("Open it in any browser - it works offline, no server needed.")


def cmd_diff(args: argparse.Namespace) -> None:
    from dbt_walker.diff import diff_graphs

    new = _load(args)
    old = Graph.load(args.state)
    result = diff_graphs(old, new)

    if args.json:
        print(json.dumps(
            {
                "added": result.added,
                "removed": result.removed,
                "modified": [c.as_dict() for c in result.modified],
            },
            indent=2,
        ))
        return

    if result.added:
        print(f"Added ({len(result.added)}):")
        for uid in result.added:
            print(f"  + {_fmt(new, uid)}")
    if result.removed:
        print(("\n" if result.added else "") + f"Removed ({len(result.removed)}):")
        for uid in result.removed:
            print(f"  - {old.label(uid)}")
    if result.modified:
        print(("\n" if (result.added or result.removed) else "")
              + f"Modified ({len(result.modified)}):")
        for change in result.modified:
            print(f"  ~ {new.label(change.unique_id)}: {change.summary(new)}")
    if not (result.added or result.removed or result.modified):
        print("No changes.")
        return
    changed_models = [c.unique_id for c in result.modified
                      if new.resource_type(c.unique_id) == "model"]
    if changed_models:
        names = " ".join(new.label(u) for u in changed_models)
        print(f"\nSee the blast radius with:  dbt-walker impact <model>  (changed: {names})")


_EXAMPLES = """\
examples (run from inside your dbt project, or pass --project-dir):

  what does this model read from, and what reads it?
    dbt-walker upstream customers
    dbt-walker downstream stg_orders

  if I change this model, what must I drop / full-refresh?
    dbt-walker impact stg_orders

  I'm only changing ONE column - what actually breaks?
    dbt-walker impact stg_orders --column status

  where does a column come from / what derives from it?
    dbt-walker col-upstream   orders --column amount
    dbt-walker col-downstream stg_orders --column order_id

  draw the lineage as a browser-viewable page (written to ./graphs/)
    dbt-walker graph stg_orders
    dbt-walker graph stg_orders --column status      # only what that column touches

  build the visual explorer (one offline HTML file, no server)
    dbt-walker build-app .
    dbt-walker build-app ~/repos/analytics

  what changed since production's manifest?
    dbt-walker diff --state prod/target/manifest.json

add --json to any command for machine-readable output.
"""


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="dbt-walker", description="Traverse dbt lineage to plan refreshes.",
        epilog=_EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project-dir",
        default=".",
        help="dbt project dir (or path to a manifest.json). Default: cwd",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_walk_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("model", help="model/seed/snapshot/source name or unique_id")
        p.add_argument("--depth", type=int, default=None, help="max hops (default: all)")
        p.add_argument("--mat", action="append", help="filter by materialization (repeatable)")
        p.add_argument("--tests", action="store_true", help="include test nodes")
        p.add_argument("--json", action="store_true", help="machine-readable output")

    p_up = sub.add_parser("upstream", help="what a model depends on")
    add_walk_args(p_up)
    p_up.set_defaults(func=cmd_upstream)

    p_down = sub.add_parser("downstream", help="what depends on a model")
    add_walk_args(p_down)
    p_down.set_defaults(func=cmd_downstream)

    p_imp = sub.add_parser("impact", help="blast radius + refresh plan for a change")
    p_imp.add_argument("model", help="model being changed")
    p_imp.add_argument(
        "--additive",
        action="store_true",
        help="change only ADDS columns (append/sync incrementals then skip full refresh)",
    )
    p_imp.add_argument(
        "--column",
        help="restrict the blast radius to descendants that read this column "
        "(needs compiled SQL; fails closed on unresolvable lineage)",
    )
    p_imp.add_argument("--dialect", default=None,
                       help="SQL dialect for --column (default: auto-detect from the project's adapter)")
    p_imp.add_argument("--json", action="store_true", help="machine-readable output")
    p_imp.set_defaults(func=cmd_impact)

    def add_col_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("model", help="model name or unique_id")
        p.add_argument("--column", required=True, help="column to trace")
        p.add_argument("--dialect", default=None,
                       help="SQL dialect (default: auto-detect from the project's adapter)")
        p.add_argument("--json", action="store_true", help="machine-readable output")

    p_cup = sub.add_parser("col-upstream", help="column-level: what feeds a column")
    add_col_args(p_cup)
    p_cup.set_defaults(func=cmd_col_upstream)

    p_cdown = sub.add_parser("col-downstream", help="column-level: what a column feeds")
    add_col_args(p_cdown)
    p_cdown.set_defaults(func=cmd_col_downstream)

    p_graph = sub.add_parser("graph", help="draw the DAG as an HTML page, mermaid, or dot")
    p_graph.add_argument("model", nargs="?", help="root the graph at this node (default: whole project)")
    p_graph.add_argument("--format", choices=["html", "mermaid", "dot"], default="html",
                         help="html (default, browser-viewable), mermaid, or dot")
    p_graph.add_argument("--out", default=None,
                         help="output file or directory (default: ./graphs/<timestamped name>); "
                         "use '-' to write to stdout (e.g. --format dot --out - | dot -Tpng)")
    p_graph.add_argument("--direction", choices=["up", "down", "both"], default="both",
                         help="which way to walk from the root model (default: both)")
    p_graph.add_argument("--column",
                         help="scope to a column: draw only the models a change to "
                         "<model>.<column> touches (needs compiled SQL; requires a model)")
    p_graph.add_argument("--dialect", default=None,
                         help="SQL dialect for --column (default: auto-detect from the adapter)")
    p_graph.add_argument("--depth", type=int, default=None, help="max hops from the root")
    p_graph.add_argument("--mat", action="append", help="filter by materialization (repeatable)")
    p_graph.add_argument("--tests", action="store_true", help="include test nodes")
    p_graph.set_defaults(func=cmd_graph)

    p_app = sub.add_parser("build-app",
                           help="generate a standalone HTML lineage explorer for the project")
    p_app.add_argument("project_root", nargs="?", default=None,
                       help="dbt project root (default: --project-dir, else the current directory)")
    p_app.add_argument("--out", default=None,
                       help="output file or directory (default: "
                       "./<project>-lineage-<branch>-<timestamp>.html)")
    p_app.add_argument("--dialect", default=None,
                       help="SQL dialect for column lineage (default: auto-detect from the adapter)")
    p_app.set_defaults(func=cmd_build_app)

    p_diff = sub.add_parser("diff", help="what changed vs an older manifest")
    p_diff.add_argument("--state", required=True,
                        help="path to the old manifest.json (or its project/target dir)")
    p_diff.add_argument("--json", action="store_true", help="machine-readable output")
    p_diff.set_defaults(func=cmd_diff)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except GraphError as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
