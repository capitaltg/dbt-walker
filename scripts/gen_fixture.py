"""Generate a synthetic dbt project of configurable size for testing dbt-walker.

Deterministic: all randomness flows through one ``random.Random(seed)``, so the
same ``--seed`` yields a byte-identical output tree (regardless of ``--out``
location — every emitted path is relative). Alongside the project it writes
``ground_truth.json``: the exact model- and column-level lineage the project
encodes, so lineage output can be asserted against a known answer.

Layered, acyclic-by-construction DAG:

    sources (CSV-backed via dbt-duckdb external_location)
      -> staging     (views, one per source table; a couple back onto seeds)
      -> intermediate (2-3 sub-layers; view/table/incremental mix)
      -> marts        (join + group-by aggregate, tables)

SQL is ANSI (valid on DuckDB *and* Postgres/Redshift). A separate, static
``redshift_only/`` set of dialect-specific SQL is emitted outside the dbt
model path for future sqlglot parser tests (never executed by dbt).

Usage:
    python scripts/gen_fixture.py --out tests/fixtures/generated/synth \\
        --models 2000 --seed 42 [--max-fanin 4] [--dbt none|parse|compile|build]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

GROUND_TRUTH_VERSION = 1
SOURCE_GROUPS = ["raw_app", "raw_billing", "raw_web"]
# fixed source-table schema (name, type); id is the join key everywhere
SOURCE_COLUMNS = [("id", "num"), ("n1", "num"), ("n2", "num"), ("n3", "num"), ("s1", "txt")]
WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]
INCREMENTAL_OSC = ["ignore", "append_new_columns", "sync_all_columns", "fail"]


@dataclass
class Column:
    name: str
    ctype: str  # 'num' | 'txt'
    transform: str  # passthrough|rename|cast|expression|coalesce|case|aggregate
    inputs: list[tuple[str, str]]  # [(parent_label, parent_column), ...]


@dataclass
class Model:
    name: str
    kind: str  # staging | intermediate | mart | seed_staging
    materialized: str
    parents: list[str]  # parent *labels* (model name, "source.table", or seed name)
    columns: list[Column]
    on_schema_change: str | None = None
    # (parent_label, ref_expr) pairs in join order; ref_expr is the jinja ref/source
    parent_refs: list[tuple[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Building the graph
# --------------------------------------------------------------------------- #

def _sizes(models: int) -> tuple[int, int, int]:
    """Return (n_source_tables == n_staging, n_intermediate, n_marts)."""
    n_src = min(max(models // 30, 4), 50)
    n_marts = max(models // 20, 2)
    n_inter = max(models - n_src - n_marts, 1)
    return n_src, n_inter, n_marts


def _num(cols: list[Column]) -> list[Column]:
    return [c for c in cols if c.ctype == "num"]


def _txt(cols: list[Column]) -> list[Column]:
    return [c for c in cols if c.ctype == "txt"]


class Builder:
    def __init__(self, rng: random.Random, max_fanin: int):
        self.rng = rng
        self.max_fanin = max_fanin
        self.models: list[Model] = []
        self.by_name: dict[str, Model] = {}
        # source label -> (source_name, table, ref_expr, columns)
        self.sources: dict[str, tuple[str, str, str, list[Column]]] = {}
        self.seeds: list[str] = []
        self._inc_counter = 0

    # -- sources & seeds ---------------------------------------------------- #

    def build_sources(self, n: int) -> None:
        for i in range(n):
            group = SOURCE_GROUPS[i % len(SOURCE_GROUPS)]
            table = f"t{i}"
            label = f"{group}.{table}"
            cols = [Column(name, t, "passthrough", []) for name, t in SOURCE_COLUMNS]
            ref = f"{{{{ source('{group}', '{table}') }}}}"
            self.sources[label] = (group, table, ref, cols)

    def build_seeds(self, n: int) -> None:
        self.seeds = [f"seed_{i}" for i in range(n)]

    # -- staging ------------------------------------------------------------ #

    def build_staging(self, source_labels: list[str], seed_names: list[str]) -> list[Model]:
        staging: list[Model] = []
        for label in source_labels:
            group, table, ref, _ = self.sources[label]
            cols = [
                Column("id", "num", "passthrough", [(label, "id")]),
                Column("val1", "num", "rename", [(label, "n1")]),
                Column("val2", "num", "cast", [(label, "n2")]),
                Column("n3", "num", "passthrough", [(label, "n3")]),
                Column("s1", "txt", "passthrough", [(label, "s1")]),
            ]
            m = Model(
                name=f"stg_{group}_{table}",
                kind="staging",
                materialized="view",
                parents=[label],
                columns=cols,
                parent_refs=[(label, ref)],
            )
            self._add(m)
            staging.append(m)
        # a couple of staging models back onto seeds instead of sources
        for seed in seed_names:
            cols = [
                Column("id", "num", "passthrough", [(seed, "id")]),
                Column("val1", "num", "rename", [(seed, "n1")]),  # n1 -> val1
                Column("s1", "txt", "passthrough", [(seed, "s1")]),
            ]
            m = Model(
                name=f"stg_{seed}",
                kind="seed_staging",
                materialized="view",
                parents=[seed],
                columns=cols,
                parent_refs=[(seed, f"{{{{ ref('{seed}') }}}}")],
            )
            self._add(m)
            staging.append(m)
        return staging

    # -- intermediate ------------------------------------------------------- #

    def build_intermediate(self, staging: list[Model], n: int) -> list[list[Model]]:
        n_sub = 3 if n >= 6 else 1
        per = n // n_sub
        counts = [per] * n_sub
        for i in range(n - per * n_sub):  # spread remainder onto earliest sub-layers
            counts[i] += 1

        sublayers: list[list[Model]] = []
        pool: list[Model] = list(staging)
        idx = 0
        for s, count in enumerate(counts):
            layer: list[Model] = []
            for _ in range(count):
                m = self._make_intermediate(idx, pool)
                self._add(m)
                layer.append(m)
                idx += 1
            sublayers.append(layer)
            pool = pool + layer  # later sub-layers may draw on earlier ones
        return sublayers

    def _make_intermediate(self, idx: int, pool: list[Model]) -> Model:
        k = self.rng.randint(1, min(self.max_fanin, len(pool)))
        parents = self.rng.sample(pool, k)
        # Bias toward incremental when a parent already is, so incremental
        # CHAINS form. Those are the interesting case for refresh planning: a
        # rebuild's completeness depends on the history its incremental
        # ancestors hold, so the fixture must actually contain them.
        parent_incremental = any(p.materialized == "incremental" for p in parents)
        weights = [25, 25, 50] if parent_incremental else [40, 35, 25]
        materialized = self.rng.choices(["view", "table", "incremental"], weights=weights)[0]
        osc = None
        if materialized == "incremental":
            osc = INCREMENTAL_OSC[self._inc_counter % len(INCREMENTAL_OSC)]
            self._inc_counter += 1
        cols = self._make_columns(parents, prefix="col", n=self.rng.randint(3, 6))
        return Model(
            name=f"int_{idx}",
            kind="intermediate",
            materialized=materialized,
            parents=[p.name for p in parents],
            columns=cols,
            on_schema_change=osc,
            parent_refs=[(p.name, f"{{{{ ref('{p.name}') }}}}") for p in parents],
        )

    # -- marts -------------------------------------------------------------- #

    def build_marts(self, intermediate_pool: list[Model], n: int) -> list[Model]:
        marts: list[Model] = []
        for i in range(n):
            k = self.rng.randint(2, min(self.max_fanin, max(2, len(intermediate_pool))))
            k = min(k, len(intermediate_pool))
            parents = self.rng.sample(intermediate_pool, k)
            cols = self._make_aggregates(parents)
            m = Model(
                name=f"mart_{i}",
                kind="mart",
                materialized="table",
                parents=[p.name for p in parents],
                columns=cols,
                parent_refs=[(p.name, f"{{{{ ref('{p.name}') }}}}") for p in parents],
            )
            self._add(m)
            marts.append(m)
        return marts

    # -- column construction ------------------------------------------------ #

    def _make_columns(self, parents: list[Model], prefix: str, n: int) -> list[Column]:
        # id is always a passthrough of the first parent's id (the join key)
        p0 = parents[0]
        cols = [Column("id", "num", "passthrough", [(p0.name, "id")])]
        used = {"id"}
        all_num = [(p.name, c.name) for p in parents for c in _num(p.columns)]
        all_txt = [(p.name, c.name) for p in parents for c in _txt(p.columns)]

        def claim(base: str) -> str:
            name, suffix = base, 0
            while name in used:
                suffix += 1
                name = f"{base}_{suffix}"
            used.add(name)
            return name

        for i in range(n):
            kind = self.rng.choice(["passthrough", "rename", "cast", "expression",
                                    "coalesce", "case"])
            default = f"{prefix}_{i}"
            if kind == "expression":
                ins = [self.rng.choice(all_num)]
                if len(all_num) > 1 and self.rng.random() < 0.6:
                    ins.append(self.rng.choice(all_num))
                cols.append(Column(claim(default), "num", "expression", ins))
            elif kind == "coalesce":
                ins = [self.rng.choice(all_num)]
                if len(all_num) > 1:
                    ins.append(self.rng.choice(all_num))
                cols.append(Column(claim(default), "num", "coalesce", ins))
            elif kind == "case":
                cols.append(Column(claim(default), "num", "case", [self.rng.choice(all_num)]))
            elif kind == "cast":
                cols.append(Column(claim(default), "num", "cast", [self.rng.choice(all_num)]))
            elif kind == "rename":
                src = self.rng.choice(all_num)
                name = claim(default)
                # a "rename" whose name coincidentally equals the source column
                # name is really a passthrough — label by what the SQL says
                transform = "rename" if name != src[1] else "passthrough"
                cols.append(Column(name, "num", transform, [src]))
            else:  # passthrough: keep the source column name so it's a *true*
                   # passthrough (name unchanged); fall back to rename on a clash
                pick_txt = all_txt and self.rng.random() < 0.3
                src = self.rng.choice(all_txt if pick_txt else all_num)
                ctype = "txt" if pick_txt else "num"
                name = claim(src[1])
                transform = "passthrough" if name == src[1] else "rename"
                cols.append(Column(name, ctype, transform, [src]))
        return cols

    def _make_aggregates(self, parents: list[Model]) -> list[Column]:
        p0 = parents[0]
        cols = [Column("id", "num", "passthrough", [(p0.name, "id")])]  # group key
        all_num = [(p.name, c.name) for p in parents for c in _num(p.columns)]
        n = self.rng.randint(2, 4)
        for i in range(n):
            if i == 0:
                cols.append(Column("row_count", "num", "aggregate", []))  # count(*)
            else:
                cols.append(Column(f"agg_{i}", "num", "aggregate", [self.rng.choice(all_num)]))
        return cols

    def _add(self, m: Model) -> None:
        self.models.append(m)
        self.by_name[m.name] = m


# --------------------------------------------------------------------------- #
# Rendering SQL
# --------------------------------------------------------------------------- #

def _alias_map(m: Model) -> dict[str, str]:
    return {label: f"t{i}" for i, (label, _ref) in enumerate(m.parent_refs)}


def _col_expr(col: Column, alias: dict[str, str]) -> str:
    def ref(inp: tuple[str, str]) -> str:
        return f"{alias[inp[0]]}.{inp[1]}"

    t = col.transform
    if t == "passthrough":
        return f"{ref(col.inputs[0])} as {col.name}"
    if t == "rename":
        return f"{ref(col.inputs[0])} as {col.name}"
    if t == "cast":
        return f"cast({ref(col.inputs[0])} as double) as {col.name}"
    if t == "expression":
        if len(col.inputs) == 2:
            return f"({ref(col.inputs[0])} * 2 + {ref(col.inputs[1])}) as {col.name}"
        return f"({ref(col.inputs[0])} * 2 + 1) as {col.name}"
    if t == "coalesce":
        if len(col.inputs) == 2:
            return f"coalesce({ref(col.inputs[0])}, {ref(col.inputs[1])}) as {col.name}"
        return f"coalesce({ref(col.inputs[0])}, 0) as {col.name}"
    if t == "case":
        r = ref(col.inputs[0])
        return f"case when {r} > 50 then {r} else 0 end as {col.name}"
    if t == "aggregate":
        if not col.inputs:  # row_count
            return f"count(*) as {col.name}"
        return f"sum({ref(col.inputs[0])}) as {col.name}"
    raise ValueError(f"unknown transform {t!r}")


def _from_join(m: Model, alias: dict[str, str]) -> str:
    (first_label, first_ref) = m.parent_refs[0]
    lines = [f"from {first_ref} {alias[first_label]}"]
    for label, ref in m.parent_refs[1:]:
        a = alias[label]
        lines.append(f"join {ref} {a} on {alias[first_label]}.id = {a}.id")
    return "\n".join(lines)


def render_sql(m: Model) -> str:
    alias = _alias_map(m)
    cfg = {"materialized": m.materialized}
    if m.materialized == "incremental":
        cfg["unique_key"] = "id"
        cfg["on_schema_change"] = m.on_schema_change or "ignore"
    cfg_str = ", ".join(
        f"{k}='{v}'" if isinstance(v, str) else f"{k}={v}" for k, v in cfg.items()
    )
    select_lines = ",\n    ".join(_col_expr(c, alias) for c in m.columns)
    parts = [f"{{{{ config({cfg_str}) }}}}", "", "select", f"    {select_lines}",
             _from_join(m, alias)]
    if m.kind == "mart":
        parts.append(f"group by {alias[m.parent_refs[0][0]]}.id")
    if m.materialized == "incremental":
        first_alias = alias[m.parent_refs[0][0]]
        parts += [
            "{% if is_incremental() %}",
            f"where {first_alias}.id > (select coalesce(max(id), 0) from {{{{ this }}}})",
            "{% endif %}",
        ]
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------- #
# Writing the project
# --------------------------------------------------------------------------- #

def write_project(out: Path, builder: Builder, seed: int, n_models: int) -> None:
    if out.exists():
        shutil.rmtree(out)
    (out / "models" / "staging").mkdir(parents=True)
    (out / "models" / "intermediate").mkdir(parents=True)
    (out / "models" / "marts").mkdir(parents=True)
    (out / "data").mkdir()
    (out / "seeds").mkdir()
    (out / "snapshots").mkdir()

    _write(out / "dbt_project.yml", _dbt_project_yml())
    _write(out / "profiles.yml", _profiles_yml())

    # source CSVs + declaration
    for label, (group, table, _ref, _cols) in sorted(builder.sources.items()):
        _write(out / "data" / f"{group}_{table}.csv", _csv(builder.rng))
    _write(out / "models" / "sources.yml", _sources_yml(builder))

    # seeds
    for seed_name in builder.seeds:
        _write(out / "seeds" / f"{seed_name}.csv", _csv(builder.rng))

    # model SQL
    for m in builder.models:
        sub = {"staging": "staging", "seed_staging": "staging",
               "intermediate": "intermediate", "mart": "marts"}[m.kind]
        _write(out / "models" / sub / f"{m.name}.sql", render_sql(m))

    _write(out / "models" / "_schema.yml", _schema_yml(builder))
    _write(out / "snapshots" / "snapshots.sql", _snapshots_sql(builder))

    _write_redshift_only(out / "redshift_only")

    _write(out / "ground_truth.json", _ground_truth(builder, seed, n_models))


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")


def _csv(rng: random.Random) -> str:
    rows = ["id,n1,n2,n3,s1"]
    for i in range(1, rng.randint(5, 10) + 1):
        rows.append(f"{i},{rng.randint(1, 100)},{rng.randint(1, 100)},"
                    f"{rng.randint(1, 100)},{rng.choice(WORDS)}")
    return "\n".join(rows) + "\n"


def _dbt_project_yml() -> str:
    return (
        "name: synth\n"
        "profile: synth\n"
        "config-version: 2\n"
        "version: '1.0'\n"
        'model-paths: ["models"]\n'
        'seed-paths: ["seeds"]\n'
        'snapshot-paths: ["snapshots"]\n'
        "flags:\n"
        "  send_anonymous_usage_stats: false\n"
    )


def _profiles_yml() -> str:
    return (
        "synth:\n"
        "  target: dev\n"
        "  outputs:\n"
        "    dev:\n"
        "      type: duckdb\n"
        "      path: synth.duckdb\n"
        "      threads: 4\n"
    )


def _sources_yml(builder: Builder) -> str:
    lines = ["version: 2", "sources:"]
    for group in SOURCE_GROUPS:
        tables = sorted(
            (t for (g, t, _r, _c) in builder.sources.values() if g == group)
        )
        if not tables:
            continue
        lines += [
            f"  - name: {group}",
            "    schema: main",
            "    meta:",
            f'      external_location: "data/{group}_{{name}}.csv"',
            "    tables:",
        ]
        lines += [f"      - name: {t}" for t in tables]
    return "\n".join(lines) + "\n"


def _schema_yml(builder: Builder) -> str:
    """not_null/unique tests on id for ~1 in 4 models, plus exposures on marts."""
    lines = ["version: 2", "models:"]
    tested = [m for i, m in enumerate(builder.models) if i % 4 == 0]
    for m in tested:
        lines += [
            f"  - name: {m.name}",
            "    columns:",
            "      - name: id",
            "        data_tests: [not_null, unique]",
        ]
    marts = [m for m in builder.models if m.kind == "mart"]
    if marts:
        lines.append("exposures:")
        for i, m in enumerate(marts[:5]):
            lines += [
                f"  - name: dashboard_{i}",
                "    type: dashboard",
                "    owner:",
                "      name: analytics",
                "    depends_on:",
                f"      - ref('{m.name}')",
            ]
    return "\n".join(lines) + "\n"


def _snapshots_sql(builder: Builder) -> str:
    """Two check-strategy snapshots over staging models (node-type coverage)."""
    staging = [m for m in builder.models if m.kind in ("staging", "seed_staging")]
    blocks = []
    for i, m in enumerate(staging[:2]):
        blocks.append(
            f"{{% snapshot snap_{i} %}}\n"
            "{{ config(target_schema='snapshots', unique_key='id', "
            "strategy='check', check_cols=['val1']) }}\n"
            f"select id, val1 from {{{{ ref('{m.name}') }}}}\n"
            "{% endsnapshot %}\n"
        )
    return "\n".join(blocks)


def _ground_truth(builder: Builder, seed: int, n_models: int) -> str:
    models = {}
    for m in builder.models:
        models[m.name] = {
            "parents": m.parents,
            "kind": m.kind,
            "materialized": m.materialized,
            "on_schema_change": m.on_schema_change,
            "columns": {
                # inputs are a set of (parent, column) leaves — dedupe (e.g. x*2+x)
                c.name: {"transform": c.transform,
                         "inputs": [list(i) for i in dict.fromkeys(c.inputs)]}
                for c in m.columns
            },
        }
    sources = {
        label: {"columns": [c.name for c in cols]}
        for label, (_g, _t, _r, cols) in sorted(builder.sources.items())
    }
    payload = {
        "generator": {"seed": seed, "models": n_models, "version": GROUND_TRUTH_VERSION},
        "sources": sources,
        "seeds": sorted(builder.seeds),
        "models": models,
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


# Static Redshift-dialect SQL for sqlglot parser tests. These represent COMPILED
# SQL (no jinja/config — dbt strips that at compile time), exercising dialect
# features that affect column lineage. Never run by dbt.
_REDSHIFT_ONLY: dict[str, str] = {
    "getdate_dateadd.sql": (
        "-- Redshift date functions\n"
        "select id, getdate() as loaded_at, dateadd(day, 7, order_date) as due_date\n"
        "from ext_orders\n"
    ),
    "decode.sql": (
        "-- Redshift DECODE (Oracle-style)\n"
        "select id, decode(status, 1, 'new', 2, 'paid', 'other') as status_label\n"
        "from ext_payments\n"
    ),
    "window_dedup.sql": (
        "-- dedup via row_number (no QUALIFY in Redshift)\n"
        "select id, amount from (\n"
        "  select id, amount,\n"
        "         row_number() over (partition by id order by updated_at desc) as rn\n"
        "  from ext_events\n"
        ") where rn = 1\n"
    ),
    "nvl_cast.sql": (
        "-- Redshift NVL + :: cast\n"
        "select id, nvl(amount, 0) as amount, id::varchar as id_str\n"
        "from ext_revenue\n"
    ),
    "listagg.sql": (
        "-- Redshift LISTAGG aggregate\n"
        "select customer_id,\n"
        "       listagg(product, ',') within group (order by product) as products\n"
        "from ext_orders\n"
        "group by customer_id\n"
    ),
}

_REDSHIFT_ONLY_GROUND_TRUTH = {
    "getdate_dateadd.sql": {
        "id": {"transform": "passthrough", "inputs": [["ext_orders", "id"]]},
        "loaded_at": {"transform": "expression", "inputs": []},
        "due_date": {"transform": "expression", "inputs": [["ext_orders", "order_date"]]},
    },
    "decode.sql": {
        "id": {"transform": "passthrough", "inputs": [["ext_payments", "id"]]},
        "status_label": {"transform": "case", "inputs": [["ext_payments", "status"]]},
    },
    "window_dedup.sql": {
        "id": {"transform": "passthrough", "inputs": [["ext_events", "id"]]},
        "amount": {"transform": "passthrough", "inputs": [["ext_events", "amount"]]},
    },
    "nvl_cast.sql": {
        "id": {"transform": "passthrough", "inputs": [["ext_revenue", "id"]]},
        "amount": {"transform": "coalesce", "inputs": [["ext_revenue", "amount"]]},
        "id_str": {"transform": "cast", "inputs": [["ext_revenue", "id"]]},
    },
    "listagg.sql": {
        "customer_id": {"transform": "passthrough", "inputs": [["ext_orders", "customer_id"]]},
        "products": {"transform": "aggregate", "inputs": [["ext_orders", "product"]]},
    },
}


def _write_redshift_only(dir_: Path) -> None:
    dir_.mkdir(parents=True)
    _write(dir_ / "README.md",
           "# redshift_only\n\nRedshift-dialect SQL for future sqlglot parser "
           "tests. NOT part of the dbt model path — dbt never compiles these. "
           "Expected column lineage is in `ground_truth.json`.\n")
    for name, sql in sorted(_REDSHIFT_ONLY.items()):
        _write(dir_ / name, sql)
    _write(dir_ / "ground_truth.json",
           json.dumps(_REDSHIFT_ONLY_GROUND_TRUTH, indent=2, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- #
# dbt shell-out
# --------------------------------------------------------------------------- #

def _dbt_exe() -> str:
    for cand in (Path(sys.prefix) / "Scripts" / "dbt.exe",
                 Path(sys.prefix) / "bin" / "dbt"):
        if cand.exists():
            return str(cand)
    found = shutil.which("dbt")
    if not found:
        sys.exit("error: could not find the dbt executable (looked in the venv and PATH).")
    return found


def run_dbt(out: Path, command: str) -> None:
    exe = _dbt_exe()
    steps = ["deps"] if (out / "packages.yml").exists() else []
    if command == "parse":
        steps.append("parse")
    elif command == "compile":
        steps += ["seed", "parse", "compile"]
    elif command == "build":
        steps.append("build")
    for step in steps:
        print(f"\n=== dbt {step} ===", flush=True)
        proc = subprocess.run([exe, step, "--profiles-dir", "."], cwd=out)
        if proc.returncode != 0:
            sys.exit(f"error: `dbt {step}` failed (exit {proc.returncode}).")


# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Generate a synthetic dbt project for dbt-walker tests.")
    p.add_argument("--out", required=True, type=Path, help="output project directory")
    p.add_argument("--models", type=int, default=2000, help="approx total model count")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--max-fanin", type=int, default=4)
    p.add_argument("--dbt", choices=["none", "parse", "compile", "build"], default="none",
                   help="run dbt after generating (default: none)")
    args = p.parse_args(argv)

    rng = random.Random(args.seed)
    n_seeds = 2
    n_src, n_inter, n_marts = _sizes(args.models)
    # seed-backed staging models also count toward the total; take them out of
    # the intermediate budget so the grand total matches --models exactly
    n_inter = max(n_inter - n_seeds, 1)

    builder = Builder(rng, args.max_fanin)
    builder.build_sources(n_src)
    builder.build_seeds(n_seeds)
    staging = builder.build_staging(sorted(builder.sources), builder.seeds)
    sublayers = builder.build_intermediate(staging, n_inter)
    intermediate_pool = [m for layer in sublayers for m in layer]
    mart_pool = sublayers[-1] if sublayers[-1] else intermediate_pool
    builder.build_marts(mart_pool or intermediate_pool, n_marts)

    out = args.out.resolve()
    write_project(out, builder, args.seed, args.models)

    total = len(builder.models)
    print(f"Generated {total} models "
          f"({n_src} staging, {len(intermediate_pool)} intermediate, {n_marts} marts), "
          f"{len(builder.sources)} sources, {n_seeds} seeds -> {out}")
    print(f"Ground truth: {out / 'ground_truth.json'}")

    if args.dbt != "none":
        run_dbt(out, args.dbt)


if __name__ == "__main__":
    main()
