# dbt-walker — Requirements, Assumptions & Expectations

Spec of record for dbt-walker. Reflects decisions made with the maintainer
(a data engineer running dbt on Redshift/Postgres) on 2026-07-19. This document
governs the whole roadmap; sections marked **(shipped)** are implemented today,
**(planned)** are specified but not yet built.

---

## 1. Purpose & scope

dbt-walker is a **local-developer CLI** that answers, before you change a dbt
model or column, what you need to know to refresh safely:

- what a model reads from (upstream lineage),
- what reads from it (downstream lineage),
- which downstream incrementals must be dropped / `--full-refresh` versus which
  just rebuild, plus the tests, snapshots, and exposures in the blast radius.

It **plans** refreshes; dbt **executes** them. It reads dbt's compiled artifacts
(`target/manifest.json`, later `target/compiled/` and `target/catalog.json`) and
**never connects to a warehouse**.

Output priorities: **human-readable text first**; `--json` is a stable,
scriptable contract (see §2.4).

---

## 2. Functional requirements — shipped commands

All commands take `--project-dir` (a dbt project directory, or a path directly to
a `manifest.json`; default: cwd) and resolve the manifest at
`<project-dir>/target/manifest.json`.

### 2.1 Name resolution (all commands)

A positional `model` argument may be a bare name or a full `unique_id`. A bare
name must resolve to exactly one node whose `resource_type` is in
{model, seed, snapshot, source}. Zero matches → error naming the missing node;
more than one → error listing the candidate unique_ids so the user can
disambiguate.

### 2.2 `upstream` / `downstream`

```
dbt-walker upstream   <model> [--depth N] [--mat M ...] [--tests] [--json]
dbt-walker downstream <model> [--depth N] [--mat M ...] [--tests] [--json]
```

- Transitive ancestors (`upstream`) or descendants (`downstream`) via the
  manifest's `parent_map` / `child_map`, breadth-first.
- `--depth N` caps hops; `--mat M` filters to one or more materializations
  (repeatable); `--tests` includes test nodes (hidden by default).
- Text output: an indented tree annotated with each node's materialization
  (and `on_schema_change` for incrementals).

### 2.3 `impact`

```
dbt-walker impact <model> [--additive] [--json]
```

The refresh-planning command. It classifies every downstream **model** into:

- **full_refresh** — incremental models that must be dropped and rebuilt.
- **rebuild** — everything else (a normal `dbt run`/`build` suffices).

Classification rule (frozen): an incremental model needs a full refresh unless
`--additive` is set **and** its `on_schema_change` is `append_new_columns` or
`sync_all_columns` (those absorb *additive* column changes in place). Renames,
drops, and type changes are never additive and always force a full refresh.
Non-incremental models are always just `rebuild`.

It also reports downstream snapshots (whose check/timestamp logic can capture
bogus diffs after a schema change), counts downstream tests, and lists affected
exposures.

**Refresh output — both paths, always (maintainer decision D3):**

1. **dbt commands** (the safe default): `dbt run --select <models> --full-refresh`
   for the full-refresh set, plus `dbt build --select <changed>+`. dbt's
   full-refresh rebuilds into a new relation and swaps atomically — no downtime,
   but transiently needs ~2× the table's storage.
2. **DDL alternative** (explicit `DROP TABLE <db.schema.alias> CASCADE;` per
   full-refresh model): frees disk immediately (drop first, rebuild after), at
   the cost of the table being absent until rebuilt and nothing surviving a
   failed rebuild. Each statement is annotated with the downstream **view**
   models that its `CASCADE` would also drop, since those must be rebuilt too.
   This mirrors the maintainer's current manual practice for very large tables
   (hundreds of millions of rows) where 2× storage is not available.

Both paths are always shown; the user picks per situation.

### 2.4 `--json` output contract (v1 — stable)

- `upstream` / `downstream`: a list of objects, each
  `{unique_id, name, distance, resource_type, materialization, relation}`,
  sorted by `(distance, unique_id)`.
- `impact`: an object with keys
  `changed` (unique_id), `full_refresh`, `rebuild`, `snapshots`, `tests`,
  `exposures` (each a list of unique_ids in topological order where meaningful),
  and `ddl` — a list of
  `{statement, relation, model, cascade_drops_views}` where
  `cascade_drops_views` is a list of downstream view unique_ids the DROP CASCADE
  would remove.

Additive keys may be introduced in later versions; existing keys will not change
meaning within v1.

---

## 3. Functional requirements — column-level (phase 2, shipped) & phase 3

### 3.1 Column-level lineage (phase 2, **shipped**)

```
dbt-walker col-upstream   <model> --column <c> [--dialect D] [--json]
dbt-walker col-downstream <model> --column <c> [--dialect D] [--json]
```

Transitive **column** provenance, parsed with `sqlglot` over
`target/compiled/**/*.sql` (compiled SQL only — never raw jinja; see §5).
Requires the `[col]` extra; without it the command exits with an install hint.
`--dialect` defaults to the project's own adapter (read from the manifest's
`adapter_type`; `postgres` if unknown), and can be overridden. Each lineage edge is
tagged with a transform kind:
`passthrough | rename | cast | expression | coalesce | case | aggregate | unknown`.
Nested subqueries and CTEs are traced through to their physical relations;
relations map back to unique_ids via dbt's own `relation_name` (so real tables
and dbt-duckdb external sources both resolve).

- `col-upstream --json`: a list of edges,
  `{model, column, parent, parent_column, transform, distance}` (parent is a
  unique_id, or null at an unknown/leaf boundary).
- `col-downstream --json`: `{column: {model, name}, derived: [{model, column}],
  unknown_models: [unique_id]}`.

### 3.2 Column-aware `impact` (phase 2, **shipped**)

```
dbt-walker impact <model> --column <c> [--additive] [--dialect D] [--json]
```

Prunes the model-level blast radius to descendants that actually read column
`c`, then classifies just those into full_refresh / rebuild (same rule as §2.3)
and emits the same dual refresh output (dbt commands + DDL). **Fails closed
(decision D4):** any model whose column lineage cannot be fully resolved —
`SELECT *`, dynamically generated columns, a sqlglot parse failure, or a Python
model — stays in the blast radius, marked `unknown`, and taints everything it
can reach. The tool never reports a model as safe to skip without proof: a false
positive costs one unnecessary rebuild; a false negative costs a silent
stale-data incident, so the asymmetry is deliberate. `--json` adds `column` and
`unknown_models` keys to the §2.4 impact object.

### 3.3 `graph` and `diff` (phase 3, **shipped**)

```
dbt-walker graph [<model>] [--format mermaid|dot|html] [--out PATH]
                 [--direction up|down|both] [--column C [--dialect D]]
                 [--depth N] [--mat M ...] [--tests]
dbt-walker diff --state <old_manifest.json> [--json]
```

- `--column C` scopes the graph to a single column: it draws **only** the models
  a change to `<model>.C` touches — `down`/`both` = the affected (taint) set,
  `up`/`both` = the column's upstream provenance. Needs compiled SQL and a model
  root; fails closed like `impact --column`.

- `graph` — draw the DAG (or a subgraph rooted at a model, walked up/down/both).
  Every format **writes a timestamped file** to `./graphs/`
  (`dbt-walker-<model>[-<column>]-<direction>-<timestamp>.<ext>`) and prints its
  path; `--out` overrides to a file/dir, and `--out -` writes to stdout (for
  piping dot to graphviz). `--format html` (default) is a browser-viewable page
  embedding the mermaid diagram, loading mermaid.js from a CDN at view time
  (small file, needs internet to render, not auto-opened); `mermaid` writes a
  `.mmd` to paste into GitHub/mermaid.live; `dot` writes graphviz. The focus
  node is marked "CHANGED HERE" with a thick border; in `--column` mode each node
  is labeled with the columns that carry the change. Nodes are styled by
  materialization / resource type; node ids are sanitized so mermaid parses them.
- `diff` — added / removed / modified nodes between a prior manifest (e.g.
  production's) and the current one, so the changed set is discovered rather
  than named. A node is *modified* if its SQL checksum, materialization,
  `on_schema_change`, or parent edges changed. `--json` returns
  `{added, removed, modified: [{model, sql_changed, materialization,
  on_schema_change, parents_added, parents_removed}]}`. The text output ends
  with a pointer to run `impact` on the changed models.

---

## 4. Non-functional requirements

- **Performance** (measured on the 2000-model synthetic fixture; see the
  "Measured performance" note at the end, filled from real numbers per D8):
  every model-level command completes well under **2 s** cold, including
  manifest load. Column commands (phase 2) target **< 5 s** per invocation on the
  same project once compiled SQL is parsed.
- **Memory:** < ~500 MB on a 2000-model manifest.
- **Dependencies:** the core (`dbt_walker` package) is **stdlib-only**. `sqlglot`
  is an optional extra (`pip install dbt-walker[col]`); column commands print a
  clear installation hint if it is absent rather than crashing.
- **Portability:** Windows, macOS, Linux; Python ≥ 3.10.
- **No warehouse connection, ever** — see §6.

---

## 5. Assumptions

- Manifest is dbt schema **v12** (dbt ≥ 1.8; verified across 1.8–1.12). The tool
  reads `parent_map` / `child_map`, which are authoritative for model-level
  lineage — no SQL parsing is needed at that level.
- Column-level lineage parses **compiled** SQL only (`target/compiled/`), where
  jinja, macros, and `ref()`/`source()` are already expanded. Raw model SQL is
  never parsed.
- Primary SQL dialect is **Redshift/Postgres** (sqlglot `redshift` / `postgres`).
  DuckDB is also supported because the test fixtures compile on it.
- `on_schema_change` semantics: `append_new_columns` / `sync_all_columns` absorb
  **additive** column changes in place; every other change type requires a full
  refresh.
- The user runs `dbt compile` (or any build command) before invoking column
  commands, so compiled SQL and (ideally) `catalog.json` exist.

---

## 6. Non-goals

- **Never connects to a warehouse.** All inputs are dbt's local artifacts.
- **Not a replacement for dbt node selection.** dbt's `state:modified`, `+`
  selectors, and tag/path selection remain the execution interface; dbt-walker
  explains *what a change touches and why*, then hands off to dbt.
- **No lineage guesses for opaque SQL.** Python models, `run_query`-driven
  dynamic SQL, and unparseable constructs produce an explicit `unknown` verdict
  (§3.2), never a silently fabricated edge.
- **No dbt Cloud / Discovery (metadata) API integration.**

---

## 7. Acceptance criteria per phase

- **Phase 1 (shipped):** `upstream` / `downstream` / `impact` produce correct
  model-level results on the jaffle_shop and synthetic fixtures; `impact` emits
  both the dbt-command and DDL refresh paths; the round-trip test confirms
  model-level lineage matches the generator's ground truth for every model.
- **Phase 2 (met):** column lineage matches the synthetic generator's
  `ground_truth.json` **100 %** for supported transform kinds (verified over
  every column of the 60-model fixture), and returns `unknown` (never a wrong
  answer) for unsupported ones — verified on both DuckDB-compiled ANSI models
  and the Redshift-dialect `redshift_only/` set, and exercised for robustness
  against the real CountMoney compiled SQL (resolves what it can, fails closed
  on the rest).
- **Phase 3 (met):** mermaid output is well-formed (sanitized ids, one classDef
  per used style) and renders on GitHub; `dot` output is valid graphviz; `diff`
  of a manifest against itself is empty and detects sql/materialization/
  on_schema_change/edge changes.

---

## 8. Risks

- **Manifest schema drift** across dbt versions (mitigated: pinned to v12, which
  dbt Labs has evolved only additively across 1.8–1.12).
- **sqlglot dialect gaps** — Redshift `QUALIFY`-less window dedup,
  `DISTKEY`/`SORTKEY` DDL, late-binding (`bind: false`) views, `DECODE`. The
  `redshift_only/` fixture set exists specifically to surface these.
- **`SELECT *` propagation** requires knowing upstream column inventories
  (from `catalog.json` or inference); treated as best-effort, and falls back to
  `unknown` (fail closed) when inventories are unavailable.

---

## Measured performance

Measured on the 2000-model synthetic fixture (`scripts/gen_fixture.py
--models 2000 --seed 42`), dbt-core 1.12 / Python 3.11, Windows, best of 5:

| Operation | Time |
|---|---|
| `Graph.load` (parse 2000-model manifest.json) | ~85 ms |
| `walk` down from a staging model (full descendants) | < 1 ms |
| `walk` up from a mart (full ancestors) | < 1 ms |
| full `impact` (load + classify + DDL), end to end | ~120 ms |
| `col-upstream` (parses only the ancestor chain) | ~0.5 s (CLI, incl. startup) |
| `impact --column` worst case (parses all ~300 downstream models' SQL) | ~1.4 s (CLI) |

Model-level load dominates and is essentially the cost of `json.loads` on the
manifest; graph traversal is negligible — all model-level operations are
~15-20x under the 2 s requirement. Column commands add sqlglot parsing of the
relevant compiled SQL subset (lazy: only what the query needs) and stay well
under the 5 s target. `tests/test_perf.py` asserts generous ceilings (2.0 s
model-level, 5.0 s column-level) as regression guards against accidental O(n²)
behavior, not as benchmarks.
