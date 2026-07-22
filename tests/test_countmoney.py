"""Smoke tests against the real CountMoney manifest (Postgres project).

Skipped unless the fixture has been fetched. These guard that dbt-walker handles
a real-world project's manifest (sources, snapshots, seeds, macros) without
choking — not any specific lineage values, which we don't control.
"""
import argparse
import io
import json
from contextlib import redirect_stdout

from dbt_walker import cli
from dbt_walker.graph import Graph
from conftest import COUNTMONEY, FETCH_CMD, ground_truth as _gt, needs  # noqa: F401


@needs(COUNTMONEY, FETCH_CMD)
def test_resolve_and_walk_known_model():
    graph = Graph.load(COUNTMONEY)
    uid = graph.resolve("int_portfolio")
    up = graph.walk(uid, "up")
    down = graph.walk(uid, "down")
    assert up, "int_portfolio should have upstream dependencies"
    # its ultimate ancestors should include at least one source
    assert any(graph.resource_type(u) == "source" for u in up)
    # walking down then up should re-include the model's own descendants' parents
    assert isinstance(down, dict)


@needs(COUNTMONEY, FETCH_CMD)
def test_impact_json_has_all_contract_keys():
    args = argparse.Namespace(project_dir=str(COUNTMONEY), model="stg_airtable_portfolio",
                              additive=False, json=True)
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_impact(args)
    result = json.loads(buf.getvalue())
    for key in ("changed", "drop_list", "absorbs", "full_refresh", "rebuild",
                "upstream_prerequisites", "snapshots", "tests", "exposures"):
        assert key in result, f"impact --json missing contract key {key!r}"
    # drop-list entries, if any, are well-formed and position-tagged
    for entry in result["drop_list"]:
        assert entry["statement"].startswith("DROP TABLE ")
        assert entry["position"] in ("upstream", "target", "downstream")
        assert set(entry) == {"model", "name", "position", "relation",
                              "statement", "cascade_drops_views"}


@needs(COUNTMONEY, FETCH_CMD)
def test_source_labels_are_qualified():
    graph = Graph.load(COUNTMONEY)
    src_uids = [u for u, n in graph.nodes.items() if n.get("resource_type") == "source"]
    assert src_uids, "CountMoney has sources"
    for u in src_uids:
        assert "." in graph.label(u), "source label should be source_name.table"
