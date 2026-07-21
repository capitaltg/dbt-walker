"""Performance smoke tests against the big synthetic manifest.

Skipped unless the 2000-model fixture is built. Thresholds are deliberately
generous (see docs/REQUIREMENTS.md "Measured performance"): they exist to catch
accidental O(n^2) regressions, not to benchmark. Times are in-process (no
subprocess/CLI-startup noise).
"""
import time

import pytest

from dbt_walker.graph import Graph
from conftest import GEN_BIG_CMD, SYNTH, needs

# generous ceiling per operation on a 2000-model manifest; the real numbers are
# ~an order of magnitude under this (recorded in REQUIREMENTS.md).
MAX_SECONDS = 2.0


def _first(graph, prefix):
    for uid, node in graph.nodes.items():
        if node.get("resource_type") == "model" and node.get("name", "").startswith(prefix):
            return uid
    raise AssertionError(f"no model starting with {prefix!r}")


@needs(SYNTH, GEN_BIG_CMD)
def test_load_and_walks_under_threshold():
    t0 = time.perf_counter()
    graph = Graph.load(SYNTH)
    load = time.perf_counter() - t0
    assert load < MAX_SECONDS, f"Graph.load took {load:.3f}s"

    n_models = sum(1 for n in graph.nodes.values() if n.get("resource_type") == "model")
    assert n_models >= 1500, f"expected the big fixture, saw {n_models} models"

    staging = _first(graph, "stg_")
    mart = _first(graph, "mart_")

    t0 = time.perf_counter()
    down = graph.walk(staging, "down")
    assert time.perf_counter() - t0 < MAX_SECONDS
    assert down, "staging model should have descendants"

    t0 = time.perf_counter()
    up = graph.walk(mart, "up")
    assert time.perf_counter() - t0 < MAX_SECONDS
    assert up, "mart should have ancestors"


@needs(SYNTH, GEN_BIG_CMD)
def test_impact_under_threshold():
    import argparse
    import io
    from contextlib import redirect_stdout

    from dbt_walker import cli

    graph = Graph.load(SYNTH)
    staging_name = graph.nodes[_first(graph, "stg_")]["name"]

    args = argparse.Namespace(project_dir=str(SYNTH), model=staging_name, additive=False,
                              column=None, json=True)
    t0 = time.perf_counter()
    with redirect_stdout(io.StringIO()):
        cli.cmd_impact(args)
    elapsed = time.perf_counter() - t0
    assert elapsed < MAX_SECONDS, f"impact took {elapsed:.3f}s"


# column commands parse compiled SQL, so they get a looser ceiling (REQUIREMENTS
# targets < 5s); the worst case parses every downstream model's SQL.
MAX_COL_SECONDS = 5.0


@needs(SYNTH, GEN_BIG_CMD)
def test_column_impact_under_threshold():
    pytest.importorskip("sqlglot")
    import argparse
    import io
    from contextlib import redirect_stdout

    from dbt_walker import cli

    graph = Graph.load(SYNTH)
    staging_name = graph.nodes[_first(graph, "stg_")]["name"]
    args = argparse.Namespace(project_dir=str(SYNTH), model=staging_name, additive=False,
                              column="val1", dialect="duckdb", json=True)
    t0 = time.perf_counter()
    with redirect_stdout(io.StringIO()):
        cli.cmd_impact(args)
    elapsed = time.perf_counter() - t0
    assert elapsed < MAX_COL_SECONDS, f"column impact took {elapsed:.3f}s"
