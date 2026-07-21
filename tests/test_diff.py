"""Tests for manifest diffing."""
import copy

from dbt_walker.diff import diff_graphs
from dbt_walker.graph import Graph


def _node(name, materialized="view", checksum="c0", osc=None):
    config = {"materialized": materialized}
    if osc is not None:
        config["on_schema_change"] = osc
    return {
        "name": name,
        "resource_type": "model",
        "config": config,
        "checksum": {"name": "sha256", "checksum": checksum},
    }


def _graph(nodes, parents):
    return Graph({"nodes": nodes, "parent_map": parents, "child_map": {}})


BASE_NODES = {
    "model.p.a": _node("a"),
    "model.p.b": _node("b", "incremental", "b0", osc="ignore"),
    "model.p.c": _node("c", "table"),
}
BASE_PARENTS = {"model.p.a": [], "model.p.b": ["model.p.a"], "model.p.c": ["model.p.b"]}


def test_self_diff_is_empty():
    g = _graph(copy.deepcopy(BASE_NODES), copy.deepcopy(BASE_PARENTS))
    old = _graph(copy.deepcopy(BASE_NODES), copy.deepcopy(BASE_PARENTS))
    d = diff_graphs(old, g)
    assert d.added == [] and d.removed == [] and d.modified == []


def test_added_and_removed():
    old = _graph(copy.deepcopy(BASE_NODES), copy.deepcopy(BASE_PARENTS))
    new_nodes = copy.deepcopy(BASE_NODES)
    del new_nodes["model.p.c"]                      # removed
    new_nodes["model.p.d"] = _node("d")             # added
    new = _graph(new_nodes, {"model.p.a": [], "model.p.b": ["model.p.a"], "model.p.d": []})
    d = diff_graphs(old, new)
    assert d.added == ["model.p.d"]
    assert d.removed == ["model.p.c"]


def test_sql_and_materialization_and_osc_changes():
    old = _graph(copy.deepcopy(BASE_NODES), copy.deepcopy(BASE_PARENTS))
    new_nodes = copy.deepcopy(BASE_NODES)
    new_nodes["model.p.a"]["checksum"]["checksum"] = "a1"          # sql changed
    new_nodes["model.p.c"]["config"]["materialized"] = "incremental"  # mat change
    new_nodes["model.p.b"]["config"]["on_schema_change"] = "append_new_columns"  # osc
    new = _graph(new_nodes, copy.deepcopy(BASE_PARENTS))
    d = diff_graphs(old, new)
    by_id = {c.unique_id: c for c in d.modified}
    assert by_id["model.p.a"].sql_changed is True
    assert by_id["model.p.c"].materialization == ("table", "incremental")
    assert by_id["model.p.b"].on_schema_change == ("ignore", "append_new_columns")


def test_parent_edge_changes():
    old = _graph(copy.deepcopy(BASE_NODES), copy.deepcopy(BASE_PARENTS))
    new_parents = copy.deepcopy(BASE_PARENTS)
    new_parents["model.p.c"] = ["model.p.a"]  # was [b] -> now [a]
    new = _graph(copy.deepcopy(BASE_NODES), new_parents)
    d = diff_graphs(old, new)
    change = next(c for c in d.modified if c.unique_id == "model.p.c")
    assert change.parents_added == ["model.p.a"]
    assert change.parents_removed == ["model.p.b"]


def test_summary_is_human_readable():
    old = _graph(copy.deepcopy(BASE_NODES), copy.deepcopy(BASE_PARENTS))
    new_nodes = copy.deepcopy(BASE_NODES)
    new_nodes["model.p.c"]["config"]["materialized"] = "incremental"
    new = _graph(new_nodes, copy.deepcopy(BASE_PARENTS))
    d = diff_graphs(old, new)
    change = next(c for c in d.modified if c.unique_id == "model.p.c")
    assert "materialization table -> incremental" in change.summary(new)
