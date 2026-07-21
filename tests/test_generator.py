"""Tests for the synthetic fixture generator.

Fast tests generate into tmp dirs and need no dbt. The round-trip test needs the
small fixture built (`--dbt build`) so there's a real manifest to walk.
"""
import hashlib
import re

import pytest

import gen_fixture
from conftest import (
    GEN_SMALL_CMD,
    SYNTH_SMALL,
    ground_truth,
    needs,
)
from dbt_walker.graph import Graph

SEED_COLUMNS = [name for name, _ in gen_fixture.SOURCE_COLUMNS]
REF_RE = re.compile(r"ref\(\s*'([^']+)'\s*\)")
SOURCE_RE = re.compile(r"source\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)")


def _generate(tmp_path, seed=42, models=60):
    out = tmp_path / "synth"
    gen_fixture.main(["--out", str(out), "--models", str(models), "--seed", str(seed)])
    return out


def _tree_hash(root):
    h = hashlib.sha1()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            h.update(path.relative_to(root).as_posix().encode())
            h.update(path.read_bytes())
    return h.hexdigest()


def test_deterministic_same_seed(tmp_path):
    a = _generate(tmp_path / "a")
    b = _generate(tmp_path / "b")
    assert _tree_hash(a) == _tree_hash(b)


def test_different_seed_differs(tmp_path):
    a = _generate(tmp_path / "a", seed=1)
    b = _generate(tmp_path / "b", seed=2)
    assert _tree_hash(a) != _tree_hash(b)


def test_model_count_matches_request(tmp_path):
    out = _generate(tmp_path, models=60)
    gt = ground_truth(out)
    assert len(gt["models"]) == 60


def test_dag_is_acyclic_and_layered(tmp_path):
    out = _generate(tmp_path)
    gt = ground_truth(out)
    models = gt["models"]
    source_labels = set(gt["sources"])
    seed_names = set(gt["seeds"])

    # topological sort must succeed (no cycles), using only model->model edges
    indeg = {n: 0 for n in models}
    for n, m in models.items():
        for p in m["parents"]:
            if p in models:
                indeg[n] += 1
    queue = [n for n, d in indeg.items() if d == 0]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for other, m in models.items():
            if n in m["parents"]:
                indeg[other] -= 1
                if indeg[other] == 0:
                    queue.append(other)
    assert seen == len(models), "cycle detected among models"

    # layering: staging parents are sources/seeds; marts parents are all models
    for n, m in models.items():
        if m["kind"] in ("staging", "seed_staging"):
            assert all(p in source_labels or p in seed_names for p in m["parents"])
        if m["kind"] == "mart":
            assert all(p in models for p in m["parents"])
            assert len(m["parents"]) >= 2


def test_sql_refs_match_ground_truth(tmp_path):
    out = _generate(tmp_path)
    gt = ground_truth(out)
    for sql_path in (out / "models").rglob("*.sql"):
        name = sql_path.stem
        sql = sql_path.read_text(encoding="utf-8")
        refs = set(REF_RE.findall(sql))
        srcs = {f"{g}.{t}" for g, t in SOURCE_RE.findall(sql)}
        emitted = refs | srcs
        expected = set(gt["models"][name]["parents"])
        assert emitted == expected, f"{name}: SQL refs {emitted} != ground truth {expected}"


def test_materialization_mix_in_bounds(tmp_path):
    out = _generate(tmp_path, models=300)
    gt = ground_truth(out)
    inter = [m for m in gt["models"].values() if m["kind"] == "intermediate"]
    counts = {"view": 0, "table": 0, "incremental": 0}
    for m in inter:
        counts[m["materialized"]] += 1
    total = len(inter)
    # target mix is 40/35/25; allow generous tolerance for RNG on a finite sample
    assert 0.25 < counts["view"] / total < 0.55
    assert 0.20 < counts["table"] / total < 0.50
    assert 0.10 < counts["incremental"] / total < 0.40


def test_column_inputs_reference_real_parent_columns(tmp_path):
    out = _generate(tmp_path)
    gt = ground_truth(out)
    models, sources, seeds = gt["models"], gt["sources"], set(gt["seeds"])

    def parent_columns(label):
        if label in models:
            return set(models[label]["columns"])
        if label in sources:
            return set(sources[label]["columns"])
        if label in seeds:
            return set(SEED_COLUMNS)
        return None

    for name, m in models.items():
        for col, spec in m["columns"].items():
            for parent_label, parent_col in spec["inputs"]:
                cols = parent_columns(parent_label)
                assert cols is not None, f"{name}.{col}: unknown parent {parent_label}"
                assert parent_col in cols, (
                    f"{name}.{col}: input {parent_label}.{parent_col} not a real column"
                )


def test_redshift_only_set_emitted(tmp_path):
    out = _generate(tmp_path)
    rs = out / "redshift_only"
    assert (rs / "ground_truth.json").exists()
    sql_files = list(rs.glob("*.sql"))
    assert len(sql_files) >= 5
    # these live OUTSIDE the dbt model path, so dbt never sees them
    assert not (out / "models" / "redshift_only").exists()


# --------------------------------------------------------------------- round-trip

@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_round_trip_parents_match_manifest():
    """Model-level lineage from the real built manifest == generator ground truth."""
    gt = ground_truth(SYNTH_SMALL)
    graph = Graph.load(SYNTH_SMALL)
    for name, m in gt["models"].items():
        uid = graph.resolve(name)
        direct = graph.walk(uid, "up", depth=1)
        got = {graph.label(u) for u in direct}
        assert got == set(m["parents"]), f"{name}: manifest parents {got} != {m['parents']}"


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_round_trip_impact_incremental_classification():
    """append/sync incrementals -> rebuild under --additive; ignore/fail -> full_refresh."""
    import argparse
    import io
    from contextlib import redirect_stdout

    from dbt_walker import cli

    gt = ground_truth(SYNTH_SMALL)
    graph = Graph.load(SYNTH_SMALL)

    # pick an incremental model and one of its ancestors to change
    incrementals = {n: m for n, m in gt["models"].items() if m["materialized"] == "incremental"}
    assert incrementals, "fixture should contain incremental models"

    for additive in (False, True):
        args = argparse.Namespace(project_dir=str(SYNTH_SMALL), model="stg", additive=additive, json=True)
        # run impact from the project root (source-backed staging feeds everything)
        root_model = next(n for n in gt["models"] if n.startswith("stg_"))
        args.model = root_model
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_impact(args)
        import json as _json
        result = _json.loads(buf.getvalue())

        for uid in result["full_refresh"]:
            name = graph.label(uid)
            if name in incrementals:
                osc = incrementals[name]["on_schema_change"]
                if additive:
                    assert osc not in ("append_new_columns", "sync_all_columns"), (
                        f"{name} with osc={osc} should not need full refresh under --additive"
                    )
        # every full_refresh entry must have a matching DDL statement
        assert {e["model"] for e in result["ddl"]} == set(result["full_refresh"])
