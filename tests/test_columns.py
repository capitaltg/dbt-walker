"""Column-level lineage tests (phase 2).

Fast tests exercise the manifest-free parser (``parse_columns``) on inline SQL.
Ground-truth tests need the small synthetic fixture built; robustness tests need
the CountMoney fixture. All column tests skip if sqlglot isn't installed.
"""
import json

import pytest

_SPAN_SQL = """select
    t0.id as id,
    case when t3.col_2 > 50 then t3.col_2 else 0 end as col_0,
    row_number() over (
        partition by stock_code, end_date
        order by f_ann_date desc
        ) as rn1,
    coalesce(t3.col_0, t3.col_3) as col_2
from a t0 join b t3 on t0.id = t3.id"""


def test_select_spans_covers_multiline_expressions():
    """The point of spans: a window function spanning four lines highlights all
    four, not just the closing `) as rn1`."""
    from dbt_walker.columns import select_spans

    spans = select_spans(_SPAN_SQL, "postgres")
    assert spans["id"] == [(2, 2)]
    assert spans["col_0"] == [(3, 3)]
    assert spans["rn1"] == [(4, 7)]      # the whole over(...) clause
    assert spans["col_2"] == [(8, 8)]


@pytest.mark.parametrize("sql, why", [
    ("select a as x from t union all select b as x from u", "set op has no single projection list"),
    ("select * from t", "star has no producing expression"),
    ("this is not sql at all", "parse failure"),
    ("", "empty"),
])
def test_select_spans_fails_closed(sql, why):
    """A wrong span points the user at the wrong SQL, which is worse than none."""
    from dbt_walker.columns import select_spans

    assert select_spans(sql, "postgres") == {}, why


def test_select_spans_ignores_subquery_internals():
    """A scalar subquery's own FROM must not be mistaken for the outer one."""
    from dbt_walker.columns import select_spans

    spans = select_spans("select (select max(x) from y) as c,\n z as d from t", "postgres")
    assert spans["c"] == [(1, 1)]
    assert spans["d"] == [(2, 2)]


_CTE_SQL = """with
import as (
    select * from "db"."main"."stg_x"
),
deduplicated as (
    select * from (
        select
            *,
            row_number() over (
                partition by stock_code, end_date
                order by f_ann_date desc
                ) as rn1
        from import) as t
    where t.rn1 = 1
)
select * from deduplicated"""


def test_select_spans_reaches_into_ctes():
    """Real dbt SQL keeps its logic in CTEs and ends `select * from <final_cte>`,
    so a final-projection-only scan finds nothing (this was CountMoney: 0/23)."""
    from dbt_walker.columns import select_spans

    spans = select_spans(_CTE_SQL, "postgres")
    assert spans["rn1"] == [(9, 12)], "the window function inside the CTE"


def test_select_spans_records_every_producer():
    """Parallel CTEs each producing the column yield one range each -- they are
    all genuine producers, unlike lines that merely read the column."""
    from dbt_walker.columns import select_spans

    sql = ("with a as (select sum(x) as amt from t),\n"
           "     b as (select sum(y) as amt from u)\n"
           "select * from a")
    assert select_spans(sql, "postgres")["amt"] == [(1, 1), (2, 2)]


def test_final_projection_wins_over_inner_scopes():
    """When the final SELECT names the column itself, that is the producing
    expression -- inner scopes don't add noise to it."""
    from dbt_walker.columns import select_spans

    sql = ("with a as (select sum(x) as amt from t)\n"
           "select amt from a")
    assert select_spans(sql, "postgres")["amt"] == [(2, 2)]


def test_select_spans_skips_unnamed_inner_expressions():
    """An inner expression with no alias has no output name to attribute lines
    to; skipping beats guessing."""
    from dbt_walker.columns import select_spans

    sql = "with a as (select max(x), y as kept from t)\nselect kept from a"
    spans = select_spans(sql, "postgres")
    assert "kept" in spans
    assert all(k for k in spans), "no empty-named entries"

pytest.importorskip("sqlglot", reason="column lineage needs the [col] extra (sqlglot)")

from dbt_walker import columns as colmod
from dbt_walker.columns import ColumnGraph, parse_columns
from dbt_walker.graph import Graph
from conftest import (COUNTMONEY, FETCH_CMD, GEN_SMALL_CMD, MOCK_WH_CMD, SYNTH_SMALL,
                      ground_truth, needs, needs_catalog)

DUCK = "duckdb"


# ------------------------------------------------------------ manifest-free unit

def _cols(sql, dialect=DUCK):
    return parse_columns(sql, dialect)


def test_passthrough_and_rename():
    r = _cols("select a as a, b as c from db.sch.t")
    assert r["a"] == [("db.sch.t", "a", "passthrough")]
    assert r["c"] == [("db.sch.t", "b", "rename")]


def test_cast_expression_coalesce_case():
    r = _cols(
        "select cast(a as double) as a, (b*2+c) as x, coalesce(b,c) as y, "
        "case when a>0 then a else 0 end as z from db.sch.t"
    )
    assert r["a"][0][2] == "cast"
    assert {i[1] for i in r["x"]} == {"b", "c"} and r["x"][0][2] == "expression"
    assert {i[1] for i in r["y"]} == {"b", "c"} and r["y"][0][2] == "coalesce"
    assert r["z"] == [("db.sch.t", "a", "case")]


def test_aggregate_and_count_star():
    r = _cols("select id, sum(amt) as total, count(*) as n from db.sch.t group by id")
    assert r["total"] == [("db.sch.t", "amt", "aggregate")]
    assert r["n"] == []  # count(*) has no column dependency (known, not unknown)


def test_join_qualifies_sources():
    r = _cols(
        "select t0.id as id, t1.v as w from db.sch.a t0 join db.sch.b t1 on t0.id=t1.id"
    )
    assert r["id"] == [("db.sch.a", "id", "passthrough")]
    assert r["w"] == [("db.sch.b", "v", "rename")]


def test_nested_subquery_resolves_through():
    r = _cols(
        "select id, amount from (select id, amount, "
        "row_number() over (partition by id order by u desc) rn from db.sch.e) where rn=1"
    )
    assert r["id"] == [("db.sch.e", "id", "passthrough")]
    assert r["amount"] == [("db.sch.e", "amount", "passthrough")]


def test_select_star_over_physical_table_is_unknown():
    # can't enumerate a physical table's columns without a catalog -> fail closed
    assert _cols("select * from db.sch.t") is None
    assert _cols("select t.* from db.sch.t t") is None


def test_select_star_over_cte_resolves():
    # the classic dbt staging pattern: `select * from renamed`
    sql = (
        "with renamed as (select id as order_id, status from db.sch.raw_orders) "
        "select * from renamed"
    )
    r = _cols(sql)
    assert r is not None
    assert r["order_id"] == [("db.sch.raw_orders", "id", "passthrough")]
    assert r["status"] == [("db.sch.raw_orders", "status", "passthrough")]


def test_unqualified_column_over_single_source_resolves():
    sql = "with p as (select * from db.sch.pay) select sum(amount) as total from p"
    r = _cols(sql)
    assert r["total"] == [("db.sch.pay", "amount", "aggregate")]


def test_unqualified_column_over_join_fails_closed():
    # `amount` is unqualified and could come from either side of the join;
    # without a catalog we can't prove which -> fail closed (unknown), not a guess
    sql = (
        "select sum(amount) as total from db.sch.pay p "
        "left join db.sch.ord o on p.oid = o.id"
    )
    r = _cols(sql)
    assert r["total"] == [(None, "", "unknown")]


def test_qualified_star_over_join_resolves_by_name():
    """The DMS/SCD dedup pattern: `select src.* ... left join ... qualify
    row_number()=1` provably projects ONLY src's columns, so a column named in
    the outer select traces through src to its source -- not a fail-closed
    unknown. A *bare* `*` over a join stays ambiguous (see the test above)."""
    sql = (
        "with initial as (select * from db.sch.raw), "
        "deduped as ("
        "  select src.* from initial as src "
        "  left join db.sch.tgt as tgt on src.id = tgt.id "
        "  qualify row_number() over (partition by src.id order by src.seq desc) = 1"
        ") "
        "select id, employment_eligibility_type from deduped"
    )
    r = _cols(sql)
    assert r is not None
    assert r["id"] == [("db.sch.raw", "id", "passthrough")]
    assert r["employment_eligibility_type"] == \
        [("db.sch.raw", "employment_eligibility_type", "passthrough")]


# ------------------------------------------------------- catalog.json (schema)

_SCHEMA = {"db": {"sch": {
    "pay": {"amount": "DOUBLE", "oid": "INT"},
    "ord": {"id": "INT", "region": "TEXT"},
    "raw": {"id": "INT", "status": "TEXT", "amt": "DOUBLE"},
}}}


def test_catalog_resolves_unqualified_column_over_join():
    """The stock_picks pattern: with column inventories, an unqualified column
    across a join attributes to the one side that actually has it."""
    sql = ("select sum(amount) as total, region from db.sch.pay p "
           "left join db.sch.ord o on p.oid = o.id group by region")
    r = parse_columns(sql, DUCK, _SCHEMA)
    assert r["total"] == [("db.sch.pay", "amount", "aggregate")]
    assert r["region"] == [("db.sch.ord", "region", "passthrough")]


def test_catalog_expands_select_star_over_physical_table():
    """Roadmap limitation lifted: `select *` over a real table, unresolvable
    without a catalog, expands to proven passthroughs with one."""
    assert parse_columns("select * from db.sch.raw", DUCK) is None
    r = parse_columns("select * from db.sch.raw", DUCK, _SCHEMA)
    assert r == {
        "id": [("db.sch.raw", "id", "passthrough")],
        "status": [("db.sch.raw", "status", "passthrough")],
        "amt": [("db.sch.raw", "amt", "passthrough")],
    }


def test_catalog_keeps_failing_closed_on_true_ambiguity():
    """A catalog sharpens resolution but must not abandon fail-closed: a column
    present on BOTH sides of a join is genuinely ambiguous -> still unknown."""
    schema = {"db": {"sch": {"pay": {"id": "INT", "amount": "D"}, "ord": {"id": "INT"}}}}
    sql = "select id from db.sch.pay p join db.sch.ord o on p.amount = o.id"
    r = parse_columns(sql, DUCK, schema)
    assert r["id"] == [(None, "", "unknown")]


def test_catalog_absent_leaves_behaviour_unchanged():
    """No schema arg == today's behaviour, exactly."""
    sql = "select sum(amount) as total from db.sch.pay p left join db.sch.ord o on p.oid=o.id"
    assert parse_columns(sql, DUCK) == parse_columns(sql, DUCK, None)


def test_load_catalog_absent_is_none(tmp_path):
    from dbt_walker.columns import load_catalog
    g = Graph({"metadata": {}, "nodes": {}, "parent_map": {}, "child_map": {}},
              project_root=tmp_path)
    (tmp_path / "target").mkdir()
    assert load_catalog(g) is None


def test_load_catalog_empty_is_present_false(tmp_path):
    """dbt writes a well-formed but empty catalog for a compile-only project;
    that must read as 'no inventory', not a usable catalog."""
    from dbt_walker.columns import load_catalog
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "catalog.json").write_text(
        json.dumps({"metadata": {"generated_at": "2026-01-01T00:00:00Z"},
                    "nodes": {}, "sources": {}}), encoding="utf-8")
    g = Graph({"metadata": {"generated_at": "2026-01-01T00:00:00Z"},
               "nodes": {}, "parent_map": {}, "child_map": {}}, project_root=tmp_path)
    cat = load_catalog(g)
    assert cat is not None and cat.present is False and cat.relation_count == 0


def test_load_catalog_builds_schema_and_detects_staleness(tmp_path):
    from dbt_walker.columns import load_catalog
    (tmp_path / "target").mkdir()
    catalog = {
        "metadata": {"generated_at": "2026-01-01T00:00:00Z"},  # older than manifest
        "nodes": {"model.p.stg": {"metadata": {"database": "db", "schema": "sch", "name": "stg"},
                                  "columns": {"a": {"name": "a", "type": "INT", "index": 1},
                                              "b": {"name": "b", "type": "TEXT", "index": 2}}}},
        "sources": {},
    }
    (tmp_path / "target" / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    g = Graph({"metadata": {"generated_at": "2026-06-01T00:00:00Z"},
               "nodes": {}, "parent_map": {}, "child_map": {}}, project_root=tmp_path)
    cat = load_catalog(g)
    assert cat.present and cat.relation_count == 1
    assert cat.schema == {"db": {"sch": {"stg": {"a": "INT", "b": "TEXT"}}}}
    assert cat.stale is True, "catalog older than manifest -> stale hint"


def test_columngraph_uses_catalog_end_to_end(tmp_path):
    """The whole wiring: ColumnGraph finds catalog.json, threads its schema into
    parsing, and an otherwise-unknown column comes back resolved to a node."""
    from dbt_walker.columns import ColumnGraph

    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "catalog.json").write_text(json.dumps({
        "metadata": {"generated_at": "2026-06-02T00:00:00Z"},
        "nodes": {"model.p.src": {
            "metadata": {"database": "db", "schema": "sch", "name": "src"},
            "columns": {"amount": {"name": "amount", "type": "DOUBLE", "index": 1},
                        "oid": {"name": "oid", "type": "INT", "index": 2}}}},
        "sources": {},
    }), encoding="utf-8")

    manifest = {
        "metadata": {"generated_at": "2026-06-01T00:00:00Z", "adapter_type": "duckdb"},
        "nodes": {
            "model.p.src": {"resource_type": "model", "name": "src", "database": "db",
                            "schema": "sch", "alias": "src", "compiled_code": "select 1"},
            "model.p.mart": {"resource_type": "model", "name": "mart", "database": "db",
                             "schema": "sch", "alias": "mart",
                             # unqualified `amount` over a lone source: needs the
                             # catalog only if joined, but proves schema plumbing
                             "compiled_code": "select sum(amount) as total from db.sch.src"},
        },
        "sources": {}, "parent_map": {"model.p.mart": ["model.p.src"], "model.p.src": []},
        "child_map": {"model.p.src": ["model.p.mart"], "model.p.mart": []},
    }
    g = Graph(manifest, project_root=tmp_path)
    cg = ColumnGraph(g)
    assert cg.catalog is not None and cg.catalog.present
    mc = cg.columns_of("model.p.mart")
    assert mc.resolved
    # resolved to the upstream NODE (unique_id), not a bare relation string
    edges = mc.columns["total"]
    assert [(e.parent, e.column, e.transform) for e in edges] == \
        [("model.p.src", "amount", "aggregate")]


@pytest.mark.parametrize("node, sql, expect", [
    ({"language": "python"}, "irrelevant", "python"),
    ({"language": "sql"}, "", "nosql"),
    ({"language": "sql"}, "select * from db.sch.t", "star"),
    ({"language": "sql"}, "select * from (select", "parse"),
    ({"language": "sql"}, "select a from db.sch.t", "unknown"),
])
def test_unresolved_reason_classifies_the_remedy(node, sql, expect):
    """The picker nudges toward a fix; only `star`/`nosql` have a clean one, so
    the reason must be honest per model."""
    from dbt_walker.columns import unresolved_reason

    node = {**node, "resource_type": "model", "compiled_code": sql}
    g = Graph({"metadata": {}, "nodes": {"model.p.m": node},
               "parent_map": {}, "child_map": {}})
    assert unresolved_reason(g, "model.p.m", DUCK) == expect


def test_parse_error_is_unknown():
    assert _cols("this is not sql !!!") is None


def test_empty_sql_is_unknown():
    assert _cols("") is None
    assert _cols("   ") is None


# --------------------------------------------------------- synth ground truth

@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_synth_column_lineage_matches_ground_truth():
    gt = ground_truth(SYNTH_SMALL)
    graph = Graph.load(SYNTH_SMALL)
    cg = ColumnGraph(graph, dialect=DUCK)
    name_to_uid = {graph.nodes[u]["name"]: u for u in graph.nodes
                   if graph.resource_type(u) == "model"}

    checked = 0
    for name, model in gt["models"].items():
        mc = cg.columns_of(name_to_uid[name])
        assert mc.resolved, f"{name} should resolve (no SELECT * in the generator)"
        for col, spec in model["columns"].items():
            checked += 1
            want = {tuple(i) for i in spec["inputs"]}
            got = {(graph.label(e.parent), e.column) for e in mc.columns[col] if e.parent}
            assert got == want, f"{name}.{col}: inputs {got} != ground truth {want}"
            # no unmapped/unknown leaves should have slipped through
            assert all(e.parent is not None or e.column is None for e in mc.columns[col])
            # transforms match too (generator kinds map 1:1 to the parser's)
            transforms = {e.transform for e in mc.columns[col]}
            if mc.columns[col]:
                assert transforms == {spec["transform"]}, (
                    f"{name}.{col}: transform {transforms} != {spec['transform']}"
                )
    assert checked > 100  # sanity: we actually covered the fixture


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_redshift_only_set_matches_ground_truth():
    rs_dir = SYNTH_SMALL / "redshift_only"
    rs_gt = json.loads((rs_dir / "ground_truth.json").read_text(encoding="utf-8"))
    for sql_file in sorted(rs_dir.glob("*.sql")):
        parsed = parse_columns(sql_file.read_text(encoding="utf-8"), "redshift")
        assert parsed is not None, f"{sql_file.name} should parse (compiled-style SQL)"
        for col, spec in rs_gt[sql_file.name].items():
            want = {tuple(i) for i in spec["inputs"]}
            got = {(r, c) for r, c, _t in parsed.get(col, []) if r}
            assert got == want, f"{sql_file.name}.{col}: {got} != {want}"


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_transitive_upstream_reaches_leaves():
    graph = Graph.load(SYNTH_SMALL)
    cg = ColumnGraph(graph, dialect=DUCK)
    mart = next(u for u in graph.nodes if graph.nodes[u].get("name", "").startswith("mart_"))
    col = next(iter(cg.columns_of(mart).columns))
    trace = cg.upstream(mart, col)
    assert not trace.has_unknown
    leaf_types = {graph.resource_type(e["parent"]) for e in trace.edges if e["parent"]}
    assert {"source", "seed"} & leaf_types, "upstream should bottom out at a source or seed"


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_column_impact_is_subset_of_model_impact():
    """Changing one column must never affect more models than changing the whole
    model, and every column-affected model must actually read the column."""
    graph = Graph.load(SYNTH_SMALL)
    cg = ColumnGraph(graph, dialect=DUCK)
    # a source-backed staging model with a rich downstream
    root = graph.resolve("stg_raw_web_t2")
    model_down = {u for u in graph.walk(root, "down") if graph.resource_type(u) == "model"}

    col = "val1"
    taint = cg.taint_downstream(root, col)
    affected = {u for u in taint.affected if graph.resource_type(u) == "model"}
    assert affected <= model_down
    assert len(affected) < len(model_down), "a single column should prune the radius"
    # every affected known model transitively reads (root, col)
    for uid in affected - taint.unknown_models:
        assert any(c for (u, c) in taint.tainted if u == uid), uid


# --------------------------------------------------------- real-world robustness

@needs(SYNTH_SMALL, GEN_SMALL_CMD)
def test_graph_column_scope_is_the_affected_set():
    import argparse

    from dbt_walker import cli

    graph = Graph.load(SYNTH_SMALL)
    cg = ColumnGraph(graph, dialect=DUCK)
    root = graph.resolve("stg_raw_app_t0")
    affected = {u for u in cg.taint_downstream(root, "val1").affected
                if graph.resource_type(u) == "model"}

    args = argparse.Namespace(project_dir=str(SYNTH_SMALL), model="stg_raw_app_t0",
                              column="val1", dialect=DUCK, direction="down",
                              depth=None, mat=None, tests=False)
    nodes, sel_root, columns = cli._graph_selection(graph, args)
    # a downstream column graph draws exactly the affected models plus the root
    assert nodes == affected | {root}
    assert sel_root == root
    # each drawn node carries (column, transform) entries; root has the changed col
    root_cols = dict(columns[root])
    assert args.column in root_cols
    assert all(uid in columns for uid in nodes)
    # transforms are real kinds (or None/unknown), never fabricated
    valid = {"passthrough", "rename", "cast", "expression", "coalesce", "case",
             "aggregate", "unknown", None}
    for entries in columns.values():
        for col, kind in entries:
            assert kind in valid, f"unexpected transform {kind!r} for {col}"
    # and it's a strict subset of the full downstream model graph (it pruned)
    full_down = {u for u in graph.walk(root, "down")
                 if graph.resource_type(u) == "model"} | {root}
    assert nodes < full_down


@needs(COUNTMONEY, FETCH_CMD)
def test_countmoney_lineage_never_crashes_and_fails_closed():
    graph = Graph.load(COUNTMONEY)
    cg = ColumnGraph(graph, dialect="postgres")
    models = [u for u in graph.nodes if graph.resource_type(u) == "model"]
    resolved = sum(1 for u in models if cg.columns_of(u).resolved)
    assert resolved > 0, "at least some CountMoney models should resolve"
    # unknown models don't crash; they just fail closed downstream
    root = next(u for u in models if graph.nodes[u]["name"].startswith("stg_"))
    mc = cg.columns_of(root)
    if mc.resolved and mc.columns:
        taint = cg.taint_downstream(root, next(iter(mc.columns)))
        # any unknown model in the downstream set is included (fail closed)
        for u in taint.unknown_models:
            assert u in taint.affected


@needs(COUNTMONEY, FETCH_CMD)
@needs_catalog(COUNTMONEY)
def test_countmoney_catalog_resolves_the_select_star_chains():
    """End-to-end on the real project: a genuine catalog.json (from dbt docs
    generate over a mocked warehouse) resolves the `select *` chains that fail
    closed without it. Guards the whole loader-against-real-dbt-format pipeline."""
    graph = Graph.load(COUNTMONEY)
    cg = ColumnGraph(graph)  # picks up catalog.json automatically
    assert cg.catalog and cg.catalog.present

    models = [u for u in graph.nodes if graph.resource_type(u) == "model"]
    unresolved = [u for u in models if not cg.columns_of(u).resolved]
    assert unresolved == [], f"catalog should resolve every model; still unresolved: {unresolved}"

    # the column that started it all: stock_picks.insolvent_index traces to the
    # four balance-sheet inputs of its CASE expression
    sp = next(u for u in models if u.endswith(".stock_picks"))
    leaves = {(graph.label(e.parent), e.column)
              for e in cg.columns_of(sp).columns["insolvent_index"] if e.parent}
    assert leaves == {("int_balance_sheet_latest", c) for c in
                      ("lt_borr", "bond_payable", "total_cur_assets", "total_cur_liab")}


# --------------------------------------------------------- fail-closed unit test

def test_taint_propagates_through_unknown_models(monkeypatch):
    """A synthetic 3-model chain a -> b -> c where b is unknown: changing a.x must
    taint b AND c (can't prove c is safe through an opaque b)."""
    manifest = {
        "nodes": {
            "model.p.a": {"name": "a", "resource_type": "model", "config": {}},
            "model.p.b": {"name": "b", "resource_type": "model", "config": {}},
            "model.p.c": {"name": "c", "resource_type": "model", "config": {}},
        },
        "parent_map": {"model.p.a": [], "model.p.b": ["model.p.a"], "model.p.c": ["model.p.b"]},
        "child_map": {"model.p.a": ["model.p.b"], "model.p.b": ["model.p.c"], "model.p.c": []},
    }
    from pathlib import Path
    graph = Graph(manifest, project_root=Path("."))
    cg = ColumnGraph(graph, dialect=DUCK)
    # b is opaque; a and c resolve with simple passthroughs of the chain
    edge = colmod.ColumnEdge
    cg._cache["model.p.a"] = colmod.ModelColumns(True, {"x": []})
    cg._cache["model.p.b"] = colmod.ModelColumns(False)  # unknown
    cg._cache["model.p.c"] = colmod.ModelColumns(True, {"y": [edge("model.p.b", "z", "passthrough")]})

    taint = cg.taint_downstream("model.p.a", "x")
    assert "model.p.b" in taint.affected and "model.p.b" in taint.unknown_models
    assert "model.p.c" in taint.affected, "c must be tainted through the opaque b"


def test_dialect_auto_detected_from_adapter():
    from pathlib import Path

    from dbt_walker.columns import dialect_for

    def g(adapter):
        return Graph({"metadata": {"adapter_type": adapter}, "nodes": {},
                      "parent_map": {}, "child_map": {}}, project_root=Path("."))

    assert dialect_for(g("duckdb")) == "duckdb"
    assert dialect_for(g("redshift")) == "redshift"
    assert dialect_for(g("databricks")) == "databricks"
    assert dialect_for(g(None)) == "postgres"      # fallback
    assert dialect_for(g("weird-adapter")) == "postgres"
    # explicit dialect still overrides the auto-detected one
    cg = ColumnGraph(g("duckdb"), dialect="snowflake")
    assert cg.dialect == "snowflake"
    assert ColumnGraph(g("redshift")).dialect == "redshift"


def test_taint_does_not_over_mark_independent_columns():
    """Regression: a model with one column that reads the change and one that
    doesn't must taint ONLY the dependent column (the per-column hit flag was
    leaking across columns, tainting everything after the first match)."""
    from pathlib import Path

    manifest = {
        "nodes": {
            "model.p.a": {"name": "a", "resource_type": "model", "config": {}},
            "model.p.b": {"name": "b", "resource_type": "model", "config": {}},
        },
        "parent_map": {"model.p.a": [], "model.p.b": ["model.p.a"]},
        "child_map": {"model.p.a": ["model.p.b"], "model.p.b": []},
    }
    graph = Graph(manifest, project_root=Path("."))
    cg = ColumnGraph(graph, dialect=DUCK)
    edge = colmod.ColumnEdge
    cg._cache["model.p.a"] = colmod.ModelColumns(True, {"x": [], "y": []})
    cg._cache["model.p.b"] = colmod.ModelColumns(True, {
        "dep": [edge("model.p.a", "x", "passthrough")],     # reads a.x
        "indep": [edge("model.p.a", "y", "passthrough")],   # reads a.y, NOT a.x
    })
    taint = cg.taint_downstream("model.p.a", "x")
    b_tainted = {c for (u, c) in taint.tainted if u == "model.p.b"}
    assert b_tainted == {"dep"}, f"only 'dep' should be tainted, got {b_tainted}"
    assert "model.p.b" in taint.affected


# ------------------------------------------- external terminals & relation mapping

def _cg_from(nodes, adapter="duckdb", parent_map=None, child_map=None, tmp=None):
    from pathlib import Path
    manifest = {"metadata": {"adapter_type": adapter}, "nodes": nodes, "sources": {},
                "parent_map": parent_map or {u: [] for u in nodes},
                "child_map": child_map or {}}
    g = Graph(manifest, project_root=tmp or Path("."))
    return g, ColumnGraph(g, dialect=adapter)


def _model(name, sql, db="db", sch="s"):
    return {"resource_type": "model", "name": name, "database": db, "schema": sch,
            "alias": name, "compiled_code": sql}


def test_external_relation_resolves_not_unknown():
    """A column qualified to a relation that isn't a dbt node is EXTERNAL, not
    unknown: we know where it comes from, it's just off-project."""
    _, cg = _cg_from({"model.p.m": _model("m", "select e.amount as amount from otherdb.ext.raw e")})
    e = cg.columns_of("model.p.m").columns["amount"][0]
    assert e.parent is None and e.is_external and not e.is_unknown
    assert e.parent_rel == "otherdb.ext.raw" and e.column == "amount"
    assert e.transform == "passthrough"  # a real transform, never "unknown"


def test_external_terminal_does_not_taint_but_unknown_does():
    """The fail-closed line: a genuinely unknown leaf taints downstream; a KNOWN
    external leaf does not (an off-project source can't carry an in-project change)."""
    from pathlib import Path
    nodes = {"model.p.a": {"name": "a", "resource_type": "model", "config": {}},
             "model.p.b": {"name": "b", "resource_type": "model", "config": {}}}
    g = Graph({"nodes": nodes, "parent_map": {"model.p.a": [], "model.p.b": ["model.p.a"]},
               "child_map": {"model.p.a": ["model.p.b"], "model.p.b": []}}, project_root=Path("."))
    cg = ColumnGraph(g, dialect=DUCK)
    edge = colmod.ColumnEdge
    cg._cache["model.p.a"] = colmod.ModelColumns(True, {"x": []})

    cg._cache["model.p.b"] = colmod.ModelColumns(True, {
        "ext": [edge(None, "amount", "passthrough", parent_rel="otherdb.ext.raw")]})
    assert "model.p.b" not in cg.taint_downstream("model.p.a", "x").affected

    cg._cache["model.p.b"] = colmod.ModelColumns(True, {"u": [edge(None, None, "unknown")]})
    assert "model.p.b" in cg.taint_downstream("model.p.a", "x").affected


def test_relation_mapping_normalizes_quotes_and_case():
    """A node aliased SRC in DB.S maps a `"db"."s"."src"` FROM back to it —
    matching is quote- and case-insensitive on both sides."""
    nodes = {
        "model.p.src": {"resource_type": "model", "name": "src", "database": "DB",
                        "schema": "S", "alias": "SRC", "compiled_code": "select 1"},
        "model.p.m": _model("m", 'select s.v as v from "db"."s"."src" s', db="DB", sch="S"),
    }
    _, cg = _cg_from(nodes, adapter="postgres",
                     parent_map={"model.p.src": [], "model.p.m": ["model.p.src"]})
    e = cg.columns_of("model.p.m").columns["v"][0]
    assert e.parent == "model.p.src" and e.column == "v"


def test_relation_collision_fails_closed_as_unknown():
    """Two models clobbering one relation is ambiguous -> the reference is UNKNOWN
    (fail closed), never external and never a guessed node."""
    nodes = {
        "model.p.a": {"resource_type": "model", "name": "a", "database": "db",
                      "schema": "s", "alias": "dup", "compiled_code": "select 1"},
        "model.p.b": {"resource_type": "model", "name": "b", "database": "db",
                      "schema": "s", "alias": "dup", "compiled_code": "select 1"},
        "model.p.m": _model("m", "select d.v as v from db.s.dup d"),
    }
    _, cg = _cg_from(nodes)
    assert "db.s.dup" in cg._collision_rels
    e = cg.columns_of("model.p.m").columns["v"][0]
    assert e.is_unknown and e.transform == "unknown"


def test_upstream_trace_carries_external_relation():
    """The col-upstream trace records the external relation so the CLI/app can
    show `otherdb.ext.raw.amount [external]` instead of 'lineage unknown'."""
    _, cg = _cg_from({"model.p.m": _model("m", "select e.amount as amount from otherdb.ext.raw e")})
    e = cg.upstream("model.p.m", "amount").edges[0]
    assert e["parent"] is None and e["parent_relation"] == "otherdb.ext.raw"
    assert e["parent_column"] == "amount" and e["transform"] != "unknown"


# ------------------------------------------- structural passthrough (select *)

def test_passthrough_analyze_finds_terminal_and_computed():
    from dbt_walker.columns import passthrough_analyze
    # a plain `select *` over one table -> terminal, no computed additions
    assert passthrough_analyze("select * from db.sch.src", DUCK) == ("db.sch.src", {})
    assert passthrough_analyze("select * from db.sch.src where x=1", DUCK) == ("db.sch.src", {})
    # the dbt dedup/latest pattern: a `select *, row_number()...` chain resolves
    # to the bottom relation, and the row_number resolves to its partition inputs
    dedup = ("with imp as (select * from db.sch.src), "
             "dd as (select * from (select *, row_number() over "
             "(partition by k order by d desc) as rn from imp) t where t.rn=1) "
             "select * from dd")
    terminal, computed = passthrough_analyze(dedup, DUCK)
    assert terminal == "db.sch.src"
    assert set(computed) == {"rn"}
    assert {(r, c) for r, c, _ in computed["rn"]} == {("db.sch.src", "k"), ("db.sch.src", "d")}
    # a star over a join is ambiguous; a bare-column rename alongside a star isn't
    # a clean passthrough -> both fail closed
    assert passthrough_analyze("select * from db.sch.a join db.sch.b on a.id=b.id", DUCK) is None
    assert passthrough_analyze("select *, id as also_id from db.sch.src", DUCK) is None


def test_passthrough_model_resolves_column_by_name():
    """`select * from source` isn't unknown: a named column traces to source.col
    without needing the source's full inventory (Huey's real-world dead-end)."""
    nodes = {
        "model.p.src": {"resource_type": "model", "name": "src", "database": "db",
                        "schema": "s", "alias": "src", "compiled_code": "select 1"},
        "model.p.stg": _model("stg", "select * from db.s.src"),
    }
    _, cg = _cg_from(nodes, parent_map={"model.p.src": [], "model.p.stg": ["model.p.src"]})
    mc = cg.columns_of("model.p.stg")
    assert mc.resolved and mc.passthrough == "db.s.src" and mc.columns == {}
    # any column resolves through the star, by name, to the source node
    e = cg.upstream("model.p.stg", "anything").edges[0]
    assert e["parent"] == "model.p.src" and e["parent_column"] == "anything"
    assert e["transform"] == "passthrough"


def test_passthrough_dedup_chain_resolves_real_and_computed_columns():
    """The canonical dbt latest-record pattern: a real column passes through to
    the source; the row_number artifact keeps its own (partition) lineage."""
    nodes = {
        "model.p.src": {"resource_type": "model", "name": "src", "database": "db",
                        "schema": "s", "alias": "src", "compiled_code": "select 1"},
        "model.p.latest": _model("latest",
            "with imp as (select * from db.s.src), "
            "dd as (select * from (select *, row_number() over "
            "(partition by k order by d desc) as rn from imp) t where t.rn=1) "
            "select * from dd"),
    }
    _, cg = _cg_from(nodes, parent_map={"model.p.src": [], "model.p.latest": ["model.p.src"]})
    mc = cg.columns_of("model.p.latest")
    assert mc.resolved and mc.passthrough == "db.s.src"
    # a real business column passes through by name
    assert cg.upstream("model.p.latest", "lt_borr").edges[0]["parent"] == "model.p.src"
    # the row_number keeps its real inputs (never fails open to src.rn)
    rn_parents = {(e["parent"], e["parent_column"]) for e in cg.upstream("model.p.latest", "rn").edges}
    assert ("model.p.src", "k") in rn_parents and ("model.p.src", "d") in rn_parents


def test_passthrough_taint_inherits_source_columns_by_name():
    """Changing source.col taints the passthrough staging model's col (and only
    that col), then flows on downstream."""
    from pathlib import Path
    nodes = {
        "model.p.src": _model("src", "select a.x as x, a.y as y from db.s.raw a"),
        "model.p.stg": _model("stg", "select * from db.s.src"),
    }
    g = Graph({"metadata": {"adapter_type": DUCK}, "nodes": nodes, "sources": {},
               "parent_map": {"model.p.src": [], "model.p.stg": ["model.p.src"]},
               "child_map": {"model.p.src": ["model.p.stg"], "model.p.stg": []}},
              project_root=Path("."))
    cg = ColumnGraph(g, dialect=DUCK)
    taint = cg.taint_downstream("model.p.src", "x")
    assert ("model.p.stg", "x") in taint.tainted
    assert ("model.p.stg", "y") not in taint.tainted  # only the changed column
    assert "model.p.stg" in taint.affected


def test_passthrough_over_external_source_is_terminal_not_tainting():
    """A passthrough over an off-project relation has no in-project source to
    inherit taint from — it stays a resolved external terminal upstream."""
    _, cg = _cg_from({"model.p.stg": _model("stg", "select * from otherdb.ext.raw")})
    mc = cg.columns_of("model.p.stg")
    assert mc.resolved and mc.passthrough == "otherdb.ext.raw"
    e = cg.upstream("model.p.stg", "col").edges[0]
    assert e["parent"] is None and e["parent_relation"] == "otherdb.ext.raw"


# --------------------------------------------------- impact drop-list engine (Stage 2)

def _inc(name, sql, osc="ignore"):
    return {"resource_type": "model", "name": name, "database": "db", "schema": "s",
            "alias": name, "config": {"materialized": "incremental", "on_schema_change": osc},
            "compiled_code": sql}


def test_upstream_drop_list_is_column_pruned():
    """The core of the redesign: only incremental ancestors on the CHANGED
    column's lineage go in the drop list; an ancestor feeding a different column
    is pruned out. With no column, every incremental ancestor is listed."""
    from pathlib import Path
    from dbt_walker import cli
    nodes = {
        "model.p.stg_a": _inc("stg_a", "select r.x as x from db.s.raw r"),
        "model.p.stg_b": _inc("stg_b", "select r.z as z from db.s.raw r"),
        "model.p.mart": {"resource_type": "model", "name": "mart", "database": "db",
                         "schema": "s", "alias": "mart", "config": {"materialized": "table"},
                         "compiled_code": "select a.x as c, b.z as d from db.s.stg_a a "
                                          "join db.s.stg_b b on a.x = b.z"},
    }
    g = Graph({"metadata": {"adapter_type": DUCK}, "nodes": nodes, "sources": {},
               "parent_map": {"model.p.stg_a": [], "model.p.stg_b": [],
                              "model.p.mart": ["model.p.stg_a", "model.p.stg_b"]},
               "child_map": {"model.p.stg_a": ["model.p.mart"],
                             "model.p.stg_b": ["model.p.mart"], "model.p.mart": []}},
              project_root=Path("."))
    cg = ColumnGraph(g, dialect=DUCK)
    # model-level: both incremental ancestors
    assert set(cli._upstream_incrementals(g, None, "model.p.mart", None)) == \
        {"model.p.stg_a", "model.p.stg_b"}
    # column c flows only from stg_a -> stg_b is pruned out
    assert cli._upstream_incrementals(g, cg, "model.p.mart", ["c"]) == ["model.p.stg_a"]


def test_upstream_fails_closed_on_opaque_ancestor():
    """If a column's lineage dead-ends at an opaque incremental, we can't prune
    above it -> every incremental ancestor is kept (fail closed)."""
    from pathlib import Path
    from dbt_walker import cli
    nodes = {
        "model.p.a": {"name": "a", "resource_type": "model", "config": {"materialized": "incremental"}},
        "model.p.b": {"name": "b", "resource_type": "model", "config": {"materialized": "incremental"}},
        "model.p.mart": {"name": "mart", "resource_type": "model", "config": {"materialized": "table"}},
    }
    g = Graph({"nodes": nodes,
               "parent_map": {"model.p.a": [], "model.p.b": ["model.p.a"],
                              "model.p.mart": ["model.p.b"]},
               "child_map": {"model.p.a": ["model.p.b"], "model.p.b": ["model.p.mart"],
                             "model.p.mart": []}}, project_root=Path("."))
    cg = ColumnGraph(g, dialect=DUCK)
    cg._cache["model.p.mart"] = colmod.ModelColumns(True, {"c": [colmod.ColumnEdge("model.p.b", "c", "passthrough")]})
    cg._cache["model.p.b"] = colmod.ModelColumns(False)  # opaque
    # can't trace past b -> both b and its ancestor a are kept
    assert set(cli._upstream_incrementals(g, cg, "model.p.mart", ["c"])) == {"model.p.a", "model.p.b"}


def test_classify_downstream_absorbs_bucket():
    from pathlib import Path
    from dbt_walker import cli
    nodes = {
        "model.p.app": {"name": "app", "resource_type": "model",
                        "config": {"materialized": "incremental", "on_schema_change": "append_new_columns"}},
        "model.p.ign": {"name": "ign", "resource_type": "model",
                        "config": {"materialized": "incremental", "on_schema_change": "ignore"}},
        "model.p.tbl": {"name": "tbl", "resource_type": "model", "config": {"materialized": "table"}},
    }
    g = Graph({"nodes": nodes, "parent_map": {u: [] for u in nodes}, "child_map": {}},
              project_root=Path("."))
    models = list(nodes)
    fr, ab, rb = cli._classify_downstream(g, models, additive=False)
    assert set(fr) == {"model.p.app", "model.p.ign"} and ab == [] and rb == ["model.p.tbl"]
    # under --additive, the append_new_columns incremental absorbs -> its own bucket
    fr, ab, rb = cli._classify_downstream(g, models, additive=True)
    assert set(fr) == {"model.p.ign"} and ab == ["model.p.app"] and rb == ["model.p.tbl"]


def test_drop_list_positions_and_db_less_ddl():
    from pathlib import Path
    from dbt_walker import cli
    nodes = {u: {"name": u, "resource_type": "model", "database": "warehouse",
                 "schema": "analytics", "alias": u, "config": {"materialized": "incremental"}}
             for u in ("up", "tgt", "down")}
    g = Graph({"nodes": nodes,
               "parent_map": {"up": [], "tgt": ["up"], "down": ["tgt"]},
               "child_map": {"up": ["tgt"], "tgt": ["down"], "down": []}}, project_root=Path("."))
    drop = cli._drop_list(g, ["up"], ["tgt", "down"], root="tgt")
    assert [(e["model"], e["position"]) for e in drop] == \
        [("up", "upstream"), ("tgt", "target"), ("down", "downstream")]  # topo order
    for e in drop:
        assert e["relation"].startswith("analytics.") and "warehouse" not in e["relation"]
        assert e["statement"] == f"DROP TABLE analytics.{e['model']};"


def test_missing_sqlglot_gives_install_hint(monkeypatch):
    from pathlib import Path

    from dbt_walker.columns import ColumnLineageUnavailable

    monkeypatch.setattr(colmod, "sqlglot", None)
    graph = Graph({"nodes": {}, "parent_map": {}, "child_map": {}}, project_root=Path("."))
    with pytest.raises(ColumnLineageUnavailable, match=r"pip install dbt-walker\[col\]"):
        ColumnGraph(graph)
