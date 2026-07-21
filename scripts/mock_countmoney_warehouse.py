"""Give the CountMoney fixture a real (empty) DuckDB warehouse so that
``dbt docs generate`` can produce a genuine ``target/catalog.json``.

Why: CountMoney's sources are external Tushare tables that don't exist locally,
so the fixture is compile-only and 3 models (``select *`` chains like
``int_balance_sheet_latest``) fail column resolution. A catalog fixes them — but
only a REAL catalog.json validates the loader against dbt's actual format, not
our understanding of it.

CountMoney's SQL is Postgres-flavored (``to_date(x,'YYYYMMDD')`` etc.), which
DuckDB can't execute — so ``dbt build`` fails. Instead we transpile each model's
compiled SQL to DuckDB with sqlglot and materialize it as an empty view in
dependency order; DuckDB infers exact column types over the empty inputs. Then
the REAL ``dbt docs generate`` inventories those relations into catalog.json.

What it does, fully offline:
  1. reads the manifest and, for each source, collects the columns referenced by
     the staging models that read it (minus window-function aliases like
     ``rn_created_at`` — those are computed, and a source column of the same name
     would collide with the staging ``select *``);
  2. creates ``warehouse.duckdb`` with those empty source tables;
  3. transpiles + materializes every model as an empty DuckDB view, in topo
     order, so the relations exist with real columns;
  4. runs the real ``dbt docs generate`` to produce catalog.json;
  5. verifies ``int_balance_sheet_latest`` now resolves through it.

Artifacts (the .duckdb files and catalog.json) are gitignored, like every other
fixture. Re-run any time; it is idempotent.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

PROJECT = REPO / "tests" / "fixtures" / "real" / "CountMoney" / "CountMoney_model"
PROFILES = REPO / "tests" / "fixtures" / "real" / "CountMoney" / "_duckdb_profiles"


def _env() -> dict:
    # CountMoney has non-ASCII (Chinese) files; cp1252 crashes dbt on Windows
    return {**os.environ, "PYTHONUTF8": "1"}


def source_columns(graph) -> dict[str, tuple[str, str, str, list[str]]]:
    """relation-key -> (database, schema, name, [column names]) for each source,
    from the columns the reading models reference. Over-inclusive but safe: an
    extra source column just rides along a `select *` and is dropped by the
    final projection."""
    import sqlglot
    from sqlglot import exp

    def relkey(uid):
        n = graph.nodes[uid]
        return (n.get("database"), n.get("schema"), n.get("name"))

    sources = {}
    for uid in graph.nodes:
        if graph.resource_type(uid) == "source":
            db, sch, name = relkey(uid)
            sources[f"{db}.{sch}.{name}".lower()] = (db, sch, name, set())

    # names computed by a window function anywhere — never real source columns,
    # and colliding with the staging `select *` would break the build
    window_aliases: set[str] = set()
    trees: dict[str, object] = {}
    for uid in graph.nodes:
        if graph.resource_type(uid) != "model":
            continue
        sql = graph.compiled_sql(uid)
        if not sql:
            continue
        try:
            tree = sqlglot.parse_one(sql, dialect="duckdb")
        except Exception:
            continue
        trees[uid] = tree
        for alias in tree.find_all(exp.Alias):
            if alias.this and alias.this.find(exp.Window):
                window_aliases.add(alias.alias_or_name.lower())

    for tree in trees.values():
        reads_source = any(
            ".".join(p for p in [t.catalog, t.db, t.name] if p).lower() in sources
            for t in tree.find_all(exp.Table)
        )
        if not reads_source:
            continue
        for t in tree.find_all(exp.Table):
            rel = ".".join(p for p in [t.catalog, t.db, t.name] if p).lower()
            if rel in sources:
                for col in tree.find_all(exp.Column):
                    nm = col.name.lower()
                    if nm and nm not in window_aliases:
                        sources[rel][3].add(nm)
    return {k: (v[0], v[1], v[2], sorted(v[3])) for k, v in sources.items() if v[3]}


def build_warehouse(sources: dict, wh_path: Path) -> None:
    import duckdb

    wh_path.unlink(missing_ok=True)
    con = duckdb.connect(str(wh_path))
    schemas = {(db, sch) for db, sch, _, _ in sources.values()}
    for _db, sch in schemas:
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{sch}"')
    for db, sch, name, cols in sources.values():
        coldefs = ", ".join(f'"{c}" VARCHAR' for c in cols)
        con.execute(f'CREATE TABLE "{sch}"."{name}" ({coldefs})')
    con.close()


def materialize_models(graph, main_path: Path, wh_path: Path) -> tuple[int, int]:
    """Create every model as an empty DuckDB view, in dependency order, from its
    compiled SQL transpiled postgres->duckdb. Returns (built, failed).

    Views over empty inputs cost nothing and still expose exact columns, which is
    all `dbt docs generate` needs. A model that won't transpile/execute is skipped
    (reported) — the catalog is simply missing that one relation, which then
    resolves exactly as it did before (fail closed)."""
    import duckdb
    import sqlglot

    con = duckdb.connect(str(main_path))
    con.execute(f"ATTACH IF NOT EXISTS '{wh_path.as_posix()}' AS warehouse")
    con.execute("CREATE SCHEMA IF NOT EXISTS main")

    models = {u for u in graph.nodes if graph.resource_type(u) == "model"}
    built = failed = 0
    for uid in graph.topo_order(models):
        sql = graph.compiled_sql(uid)
        if not sql:
            failed += 1
            continue
        n = graph.nodes[uid]
        rel = f'"{n.get("database")}"."{n.get("schema")}"."{n.get("alias") or n.get("name")}"'
        try:
            duck_sql = sqlglot.transpile(sql, read="postgres", write="duckdb")[0]
            con.execute(f"CREATE OR REPLACE VIEW {rel} AS {duck_sql}")
            built += 1
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"  skip {n.get('name'):34} {type(exc).__name__}: {str(exc).splitlines()[0][:60]}")
            failed += 1
    con.close()
    return built, failed


def write_profile(wh_path: Path) -> None:
    PROFILES.mkdir(parents=True, exist_ok=True)
    # main db = countmoney.duckdb (models); attach warehouse.duckdb (sources)
    (PROFILES / "profiles.yml").write_text(
        "CountMoney_model:\n"
        "  target: dev\n"
        "  outputs:\n"
        "    dev:\n"
        "      type: duckdb\n"
        "      path: countmoney.duckdb\n"
        "      attach:\n"
        f"        - path: {wh_path.as_posix()}\n"
        "          alias: warehouse\n",
        encoding="utf-8",
    )


def dbt(*args: str) -> None:
    cmd = [str(REPO / ".venv" / "Scripts" / "dbt"), *args,
           "--profiles-dir", str(PROFILES)]
    print(f"  $ dbt {' '.join(args)}")
    r = subprocess.run(cmd, cwd=PROJECT, env=_env(), capture_output=True, text=True)
    tail = "\n".join((r.stdout or r.stderr).splitlines()[-4:])
    print("   " + tail.replace("\n", "\n   "))
    if r.returncode != 0:
        raise SystemExit(f"dbt {args[0]} failed:\n{r.stdout}\n{r.stderr}")


def verify() -> None:
    from dbt_walker.columns import ColumnGraph, load_catalog
    from dbt_walker.graph import Graph

    graph = Graph.load(PROJECT)
    cat = load_catalog(graph)
    print(f"\ncatalog.json: present={cat and cat.present} "
          f"relations={cat.relation_count if cat else 0} stale={cat and cat.stale}")
    cg = ColumnGraph(graph)
    uid = next(u for u in graph.parents if u.endswith(".int_balance_sheet_latest"))
    mc = cg.columns_of(uid)
    total = unresolved = 0
    for u in graph.parents:
        if graph.resource_type(u) != "model":
            continue
        total += 1
        if not cg.columns_of(u).resolved:
            unresolved += 1
    print(f"int_balance_sheet_latest resolved: {mc.resolved} ({len(mc.columns)} columns)")
    print(f"wholly-unresolved models: {unresolved}/{total} "
          f"(was 3/23 without a catalog)")
    if not mc.resolved:
        raise SystemExit("FAILED: catalog did not resolve int_balance_sheet_latest")
    print("\nOK: dbt docs generate produced a catalog that resolves the select* chains.")


def main() -> None:
    if not (PROJECT / "target" / "manifest.json").exists():
        raise SystemExit("Build the CountMoney fixture first: "
                         "python scripts/fetch_real_fixture.py")
    from dbt_walker.graph import Graph

    graph = Graph.load(PROJECT)
    sources = source_columns(graph)
    print(f"mocking {len(sources)} source tables:")
    for db, sch, name, cols in sources.values():
        print(f"  {sch}.{name:32} {len(cols)} columns")

    wh_path = PROJECT / "warehouse.duckdb"
    main_path = PROJECT / "countmoney.duckdb"
    main_path.unlink(missing_ok=True)
    build_warehouse(sources, wh_path)
    write_profile(wh_path)

    print("\nmaterializing models as empty DuckDB views (transpiled from postgres):")
    built, failed = materialize_models(graph, main_path, wh_path)
    print(f"  built {built} relations, skipped {failed}")

    print("\nrunning the real dbt docs generate:")
    dbt("docs", "generate")
    verify()


if __name__ == "__main__":
    main()
