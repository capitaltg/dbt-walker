"""Tests for graph rendering (mermaid / dot)."""
from dbt_walker.graph import Graph
from dbt_walker import render


def _graph():
    # src -> stg (view) -> inc (incremental) ; stg -> tbl (table)
    manifest = {
        "nodes": {
            "model.p.stg": {"name": "stg", "resource_type": "model",
                            "config": {"materialized": "view"}},
            "model.p.inc": {"name": "inc", "resource_type": "model",
                            "config": {"materialized": "incremental"}},
            "model.p.tbl": {"name": "tbl", "resource_type": "model",
                            "config": {"materialized": "table"}},
        },
        "sources": {
            "source.p.raw.orders": {"name": "orders", "source_name": "raw",
                                    "resource_type": "source"},
        },
        "parent_map": {
            "model.p.stg": ["source.p.raw.orders"],
            "model.p.inc": ["model.p.stg"],
            "model.p.tbl": ["model.p.stg"],
        },
        "child_map": {
            "source.p.raw.orders": ["model.p.stg"],
            "model.p.stg": ["model.p.inc", "model.p.tbl"],
        },
    }
    return Graph(manifest)


ALL = {"model.p.stg", "model.p.inc", "model.p.tbl", "source.p.raw.orders"}


def test_subgraph_edges():
    g = _graph()
    edges = render.subgraph_edges(g, ALL)
    assert ("source.p.raw.orders", "model.p.stg") in edges
    assert ("model.p.stg", "model.p.inc") in edges
    assert ("model.p.stg", "model.p.tbl") in edges
    assert len(edges) == 3


def test_mermaid_structure_and_styles():
    g = _graph()
    out = render.to_mermaid(g, ALL)
    assert out.startswith("graph LR")
    # incremental node: bold name, labeled type line, incremental class
    assert '<b>inc</b><br/><small>type: incremental</small>"]:::incremental' in out
    assert "classDef incremental fill:#f9a03f" in out
    # an edge is present in mermaid arrow form
    assert "model_p_stg --> model_p_inc" in out


def test_dot_structure():
    g = _graph()
    out = render.to_dot(g, ALL)
    assert out.startswith("digraph lineage {")
    assert "rankdir=LR;" in out
    assert '"model.p.stg" -> "model.p.inc";' in out
    assert 'label="inc\\ntype: incremental"' in out


def test_render_dispatch_and_bad_format():
    g = _graph()
    assert render.render(g, ALL, "mermaid").startswith("graph LR")
    assert render.render(g, ALL, "dot").startswith("digraph")
    try:
        render.render(g, ALL, "svg")
    except ValueError as exc:
        assert "svg" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown format")


def test_root_highlight_and_column_labels():
    g = _graph()
    root = "model.p.inc"
    columns = {"model.p.inc": ["amount", "status"], "model.p.stg": ["amount"]}
    out = render.to_mermaid(g, ALL, root=root, columns=columns)
    # root gets the marker + a focus class with a thick border
    assert "CHANGED HERE" in out
    assert "classDef focus stroke:#d00,stroke-width:4px;" in out
    assert f"class {render._sanitize(root)} focus;" in out
    # affected columns appear as a bulleted list under a "columns:" line
    assert "columns:" in out
    assert "&#8226; amount" in out           # root's columns, bulleted
    assert "&#8226; status" in out
    # the marker/bullets are ASCII in the text stream (Windows-console safe)
    out.encode("ascii")


def test_column_label_truncation():
    g = _graph()
    many = [f"c{i}" for i in range(9)]
    out = render.to_mermaid(g, {"model.p.stg"}, columns={"model.p.stg": many})
    assert "+4 more" in out                  # 9 cols, 5 shown -> +4 more


def test_column_labels_show_transform_kind():
    g = _graph()
    # entries may be (name, transform) tuples -> rendered as "name [transform]"
    cols = {"model.p.stg": [("amount", "aggregate"), ("status", "passthrough"),
                            ("raw", None)]}
    out = render.to_mermaid(g, {"model.p.stg"}, columns=cols)
    assert "amount [aggregate]" in out
    assert "status [passthrough]" in out
    assert "raw" in out and "raw [" not in out   # None transform -> bare name


def test_html_embeds_mermaid_and_cdn():
    g = _graph()
    html = render.to_html(g, ALL, title="my graph", timestamp="2026-07-19 10:00:00")
    assert html.startswith("<!doctype html>")
    assert '<pre class="mermaid">' in html
    assert "graph LR" in html                       # the diagram is embedded
    assert "mermaid.esm.min.mjs" in html            # loads mermaid from a CDN
    assert "maxEdges" in html                        # big-graph cap raised
    assert "my graph" in html and "2026-07-19 10:00:00" in html


def test_html_sql_drawer_embeds_sql_and_click_directives():
    g = _graph()
    sql = {
        "model.p.stg": {"label": "stg", "raw": "select 1 as x", "compiled": "select 1 as x",
                        "cols": ["x"]},
        "model.p.inc": {"label": "inc", "raw": "select x", "compiled": "select x", "cols": ["x"]},
    }
    out = render.to_html(g, ALL, title="t", timestamp="now", sql=sql)
    assert 'id="drawer"' in out                       # the drawer exists
    assert "window.showSql" in out                     # click handler
    # only nodes with SQL get a click directive
    assert f'click {render._sanitize("model.p.stg")} call showSql(' in out
    assert f'click {render._sanitize("source.p.raw.orders")} call showSql(' not in out
    # the SQL payload is embedded, keyed by sanitized id
    assert render._sanitize("model.p.stg") in out
    assert "select 1 as x" in out


def test_html_has_panzoom_expand_and_selected_highlight():
    g = _graph()
    sql = {"model.p.stg": {"label": "stg", "raw": "x", "compiled": "x",
                           "cols": [["amount", "aggregate"]]}}
    out = render.to_html(g, ALL, title="t", timestamp="now", sql=sql)
    assert 'id="graph"' in out                        # pan/zoom viewport wrapper
    assert "addEventListener('wheel'" in out           # zoom
    assert "grabbing" in out                            # pan cursor
    assert 'id="drawer-expand"' in out                 # expand/collapse button
    assert "selectNode" in out and ".node.selected" in out   # selected-node highlight
    # drawer renders columns as a bulleted list with the transform kind
    assert "related columns:<ul>" in out
    assert "[' + escapeHtml(k) + ']" in out or "<em>[" in out


def test_html_sql_data_escapes_script_close():
    g = _graph()
    sql = {"model.p.stg": {"label": "s", "raw": "select '</script>' as x",
                           "compiled": "", "cols": []}}
    out = render.to_html(g, {"model.p.stg"}, title="t", timestamp="now", sql=sql)
    # a literal </script> in SQL must not close the data script tag
    assert "</script>" not in out.split('const NODES =')[1].split('mermaid.run')[0]


def test_html_command_writes_timestamped_file(tmp_path):
    # end-to-end through the CLI against the jaffle fixture, if it's built
    import json as _json

    from dbt_walker import cli

    manifest = (
        __import__("pathlib").Path(__file__).parent
        / "fixtures" / "jaffle_shop_duckdb" / "target" / "manifest.json"
    )
    if not manifest.exists():
        import pytest
        pytest.skip("jaffle fixture not built")

    out_dir = tmp_path / "out"
    cli.main([
        "--project-dir", str(manifest.parent.parent),
        "graph", "stg_orders", "--direction", "down", "--format", "html",
        "--out", str(out_dir) + "/",
    ])
    files = list(out_dir.glob("dbt-walker-stg_orders-down-*.html"))
    assert len(files) == 1, f"expected one timestamped html, got {files}"
    assert '<pre class="mermaid">' in files[0].read_text(encoding="utf-8")


def test_graph_writes_files_by_format_and_stdout_dash(tmp_path, capsys):
    from dbt_walker import cli

    manifest = (
        __import__("pathlib").Path(__file__).parent
        / "fixtures" / "jaffle_shop_duckdb" / "target" / "manifest.json"
    )
    if not manifest.exists():
        import pytest
        pytest.skip("jaffle fixture not built")
    proj = str(manifest.parent.parent)

    # each format writes a file with the right extension (no console spew)
    for fmt, ext in (("html", "html"), ("mermaid", "mmd"), ("dot", "dot")):
        out_dir = tmp_path / fmt
        cli.main(["--project-dir", proj, "graph", "stg_orders",
                  "--format", fmt, "--out", str(out_dir) + "/"])
        files = list(out_dir.glob(f"*.{ext}"))
        assert len(files) == 1, f"{fmt}: expected one .{ext}, got {files}"
        printed = capsys.readouterr().out
        assert "Wrote" in printed and "graph LR" not in printed  # path, not diagram

    # --out - writes the diagram to stdout (for piping)
    cli.main(["--project-dir", proj, "graph", "stg_orders", "--format", "dot", "--out", "-"])
    assert "digraph lineage" in capsys.readouterr().out


def test_sanitize_ids_are_mermaid_safe():
    g = _graph()
    out = render.to_mermaid(g, {"source.p.raw.orders"})
    # the node id line must not contain dots (mermaid can't parse them in ids)
    node_line = next(l for l in out.splitlines() if l.strip().startswith("source_p_raw_orders"))
    assert "source.p.raw.orders" not in node_line
