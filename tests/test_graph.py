import json
import subprocess
import sys
from pathlib import Path

import pytest

from dbt_walker.graph import Graph, GraphError

FIXTURE = Path(__file__).parent / "fixtures" / "jaffle_shop_duckdb"

# a -> b -> c, where b is incremental; t1 tests b; e1 exposure on c
SYNTH = {
    "nodes": {
        "model.p.a": {"name": "a", "resource_type": "model", "config": {"materialized": "table"}},
        "model.p.b": {
            "name": "b",
            "resource_type": "model",
            "config": {"materialized": "incremental", "on_schema_change": "append_new_columns"},
        },
        "model.p.c": {"name": "c", "resource_type": "model", "config": {"materialized": "view"}},
        "test.p.t1": {"name": "t1", "resource_type": "test", "config": {}},
    },
    "sources": {
        "source.p.raw.orders": {"name": "orders", "source_name": "raw", "resource_type": "source"}
    },
    "exposures": {"exposure.p.e1": {"name": "e1", "resource_type": "exposure"}},
    "parent_map": {
        "model.p.a": ["source.p.raw.orders"],
        "model.p.b": ["model.p.a"],
        "model.p.c": ["model.p.b"],
        "test.p.t1": ["model.p.b"],
        "exposure.p.e1": ["model.p.c"],
    },
    "child_map": {
        "source.p.raw.orders": ["model.p.a"],
        "model.p.a": ["model.p.b"],
        "model.p.b": ["model.p.c", "test.p.t1"],
        "model.p.c": ["exposure.p.e1"],
    },
}


@pytest.fixture
def graph():
    return Graph(SYNTH)


def test_resolve_bare_name(graph):
    assert graph.resolve("b") == "model.p.b"


def test_resolve_unknown_raises(graph):
    with pytest.raises(GraphError):
        graph.resolve("nope")


def test_walk_up_transitive(graph):
    up = graph.walk("model.p.c", "up")
    assert up == {"model.p.b": 1, "model.p.a": 2, "source.p.raw.orders": 3}


def test_walk_down_includes_tests_and_exposures(graph):
    down = graph.walk("model.p.a", "down")
    assert set(down) == {"model.p.b", "model.p.c", "test.p.t1", "exposure.p.e1"}


def test_walk_depth_limit(graph):
    assert set(graph.walk("model.p.c", "up", depth=1)) == {"model.p.b"}


def test_materialization_and_schema_change(graph):
    assert graph.materialization("model.p.b") == "incremental"
    assert graph.on_schema_change("model.p.b") == "append_new_columns"
    assert graph.materialization("source.p.raw.orders") == "source"


def test_topo_order(graph):
    order = graph.topo_order({"model.p.c", "model.p.a", "model.p.b"})
    assert order == ["model.p.a", "model.p.b", "model.p.c"]


def test_relation_schema_table_strips_database():
    g = Graph({"nodes": {"model.p.m": {"resource_type": "model", "database": "warehouse",
                                       "schema": "analytics", "alias": "orders"}},
               "parent_map": {}, "child_map": {}})
    assert g.relation("model.p.m") == "warehouse.analytics.orders"
    # DROP DDL uses schema.table only (Redshift/Postgres: no cross-db DDL)
    assert g.relation_schema_table("model.p.m") == "analytics.orders"
    # falls back to bare identifier when there is no schema
    g2 = Graph({"nodes": {"model.p.m": {"resource_type": "model", "alias": "orders"}},
                "parent_map": {}, "child_map": {}})
    assert g2.relation_schema_table("model.p.m") == "orders"


@pytest.mark.skipif(
    not (FIXTURE / "target" / "manifest.json").exists(),
    reason="fixture manifest not built — see CLAUDE.md",
)
def test_cli_against_jaffle_shop():
    out = subprocess.run(
        [sys.executable, "-m", "dbt_walker", "--project-dir", str(FIXTURE), "impact", "stg_orders", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(out.stdout)
    assert "model.jaffle_shop.orders" in result["full_refresh"]
    assert "model.jaffle_shop.customers" in result["rebuild"]
    # the merged drop list carries a plain DROP per incremental (no CASCADE),
    # tagged by position, with the database qualifier stripped
    drop = {e["model"]: e for e in result["drop_list"]}
    assert set(drop) == set(result["full_refresh"])
    orders = drop["model.jaffle_shop.orders"]
    assert orders["position"] == "downstream"
    assert orders["statement"].startswith("DROP TABLE ") and orders["statement"].endswith(";")
    assert "CASCADE" not in orders["statement"]
    assert orders["relation"] == "main.orders"  # db-less schema.table
