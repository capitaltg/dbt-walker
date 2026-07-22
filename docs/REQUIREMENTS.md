# dbt-walker — Requirements, Assumptions & Expectations

Spec of record for dbt-walker. Reflects decisions made with the maintainer
(a data engineer running dbt on Redshift/Postgres), initial spec 2026-07-19,
**substantially revised 2026-07-21** for the drop-list redesign (0.4.0). This
document governs the whole roadmap; sections marked **(shipped)** are
implemented today.

> **0.4.0 redesign (2026-07-21).** `impact` was reframed around the maintainer's
> actual workflow — *"the work is done, which models do I drop now?"* — after a
> column is threaded through the staging/intermediate layers into a mart. It now
> emits ONE topologically-ordered **drop list** of every incremental on the
> change's lineage (upstream ∪ target ∪ downstream), column-pruned, with db-less
> `DROP TABLE` DDL. `--additive` moves absorb-capable incrementals into a
> separate bucket rather than silently reclassifying them. The visual explorer
> (`build-app`, §3.4), `catalog.json` support, structural passthrough, and
> external-terminal reporting (§3.5) are also shipped. The old per-model
> full_refresh/rebuild framing (below where noted) is superseded.

---

## 1. Purpose & scope

dbt-walker is a **local-developer CLI** that answers, before you change a dbt
model or column, what you need to know to refresh safely:

- what a model reads from (upstream lineage),
- what reads from it (downstream lineage),
- **which incrementals to drop** — upstream, target, and downstream — before the
  change resolves cleanly, versus which just rebuild for free, plus the tests,
  snapshots, and exposures in the blast radius (the drop list, §2.3),
- and all of the above **pruned to a single column's lineage** (§3.1–3.2).

Two front-ends: a **CLI** (text-first, `--json` scriptable) and a **one-file
offline visual explorer** (`build-app`, §3.4). It **plans** refreshes; dbt
**executes** them. It reads dbt's compiled artifacts (`target/manifest.json`,
`target/compiled/`, `target/catalog.json`) and **never connects to a warehouse**.

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

### 2.3 `impact` (drop-list model, 0.4.0)

```
dbt-walker impact <model> [--column C [--dialect D]] [--additive] [--json]
```

The refresh-planning command. It answers *"which models do I drop now?"* with
one **DROP THESE** list: every **incremental** on the change's lineage, in
topological order, each tagged by position:

- **upstream** — an incremental ancestor the change flows down from. (In the
  maintainer's workflow the developer has already threaded the new column
  through these, so they hold old-logic history and must be rebuilt.)
- **target** — the changed model itself, if incremental.
- **downstream** — an incremental descendant that reads the change.

Each row carries an explicit `DROP TABLE <schema.alias>;` — **db-less** (no
database qualifier: you're connected to the database, and Redshift/Postgres have
no cross-database DDL) and **without `CASCADE`** (removed 0.4.1: a plain drop is
the safer default, and the drop list already names every affected model
explicitly). A dropped incremental is rebuilt in full by the next scheduled
`dbt run`. Neutral framing: *drop the ones you changed, or whose stored history
you don't trust* — the tool does not try to infer which models were edited. In
the app the drop list is grouped under one heading per position
(upstream/target/downstream), each group's `DROP` statements listed together.

Alongside the drop list: models that **rebuild for free** (views/tables), the
downstream tests that re-run, affected exposures/snapshots, and the safe dbt
alternative (`dbt run --select <drop-list> --full-refresh` +
`dbt build --select <changed>+`) — both refresh paths always shown (decision D3,
revised 0.4.1: the DDL path is a plain `DROP TABLE`, no longer `... CASCADE`).

`--column C` prunes the WHOLE list — upstream and downstream — to the lineage of
column `C` (fails closed, §3.2). `--additive`: see §2.5.

### 2.4 `--additive` and the absorbs bucket (2.5)

`--additive` asserts the change only **adds** a column (no rename/drop/type
change). A *downstream* incremental whose `on_schema_change` is
`append_new_columns` or `sync_all_columns` can then add the column on its next
normal run, so it moves OUT of the drop list into a visible **ABSORBS SCHEMA
CHANGE** bucket — rather than silently reclassifying. The bucket carries the
honest caveat: existing rows get **NULL** for the new column, so if the value is
derivable and you need the history backfilled, drop it anyway. Additive affects
only downstream classification; upstream incrementals are always dropped (they
were edited). Renames/drops/type changes are never additive.

### 2.6 `--json` output contract (v1 — stable)

- `upstream` / `downstream`: a list of objects, each
  `{unique_id, name, distance, resource_type, materialization, relation}`,
  sorted by `(distance, unique_id)`.
- `impact`: an object with keys `changed` (unique_id), `drop_list`, `absorbs`,
  `rebuild`, `upstream_prerequisites`, `full_refresh`, `tests`, `exposures`,
  `snapshots` (and, with `--column`, `columns` and `unknown_models`).
  `drop_list` is a list of
  `{model, name, position, relation, statement}` in
  topological order, `position` ∈ {upstream, target, downstream}, `relation`
  db-less. `upstream_prerequisites` and `full_refresh` are back-compat views of
  the drop list (the `upstream` rows, and the `target`+`downstream` rows,
  respectively). The pre-0.4.0 top-level `ddl` key is removed (its content now
  lives inline on each `drop_list` row).

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

Prunes the drop list (§2.3) — both the upstream incrementals and the downstream
taint — to the lineage of column `c`. Upstream: incremental ancestors on `c`'s
upstream closure (`col-upstream`), fail-closed — where the trace dead-ends at an
opaque model, ALL incrementals above it are kept. Downstream: incremental
descendants that read `c` (taint). **Fails closed (decision D4):** any model
whose column lineage cannot be fully resolved — `SELECT *` over a join,
unqualified columns across a join without a catalog, dynamically generated
columns, a sqlglot parse failure, or a Python model — stays in the blast radius,
marked `unknown`, and taints everything it can reach. A false positive costs one
unnecessary rebuild; a false negative costs a silent stale-data incident, so the
asymmetry is deliberate. `--json` adds `columns` and `unknown_models` keys.

### 3.4 `build-app` — the offline visual explorer (**shipped**)

```
dbt-walker build-app [<project-root>] [--out PATH] [--dialect D]
```

Emits ONE self-contained HTML file (mermaid vendored inline — zero network, safe
behind a corporate proxy). The expensive analysis (sqlglot parsing, the column
edge-graph, SQL spans, catalog status) runs once in Python at build time and is
embedded; **all traversal — lineage walks, the drop list, column taint,
highlighting — runs on demand in JavaScript in the browser**, mirroring the
Python engine (kept honest by `tests/test_app_parity.py`, which runs the app's
actual JS under node and asserts it agrees with Python). Three modes: **Lineage**
(the bare map), **Impact** (the drop list, direction toggle filtering
upstream/target/downstream, a DROP badge on each listed node), **Columns** (one
collapsible group per selected column tracing COMES FROM / FEEDS DOWNSTREAM). The
Detail pane splits into a fixed **TARGET DETAIL** section (the plan) and an
**INSPECTING** section (the clicked node's SQL, producing lines highlighted;
proven solid, unproven hatched). `build-app` never runs dbt; missing `target/`
prints how to compile, and a manifest older than the model files stamps a
staleness banner.

### 3.5 Column resolution — catalog, passthrough, external (**shipped**)

Beyond §3.1's compiled-SQL parsing, three mechanisms widen what resolves without
ever failing OPEN:

- **`catalog.json` (per-relation, best-effort).** If `target/catalog.json` exists
  (`dbt docs generate`), its per-relation column inventories feed sqlglot to
  resolve unqualified columns across joins and expand `SELECT *` over physical
  tables. Per-relation: a relation absent from the catalog resolves exactly as
  before; genuine ambiguity (a column on both join sides) still fails closed;
  staleness is a warning, never a gate. Reading it never touches the warehouse.
- **Structural passthrough (no inventory needed).** A `select *` chain — the
  staging/dedup pattern (`select *, row_number() ... where rn = 1`) that through
  single-source CTEs/subqueries bottoms out at ONE physical relation — is traced
  **by name**: a real column passes through to that terminal, computed additions
  (the `row_number`) keep their own lineage. This cannot fail open (it never
  invents a column set) and needs no catalog, so a column threaded through
  several `select *` layers resolves offline. A `select *` over a JOIN stays
  fail-closed without a catalog. **NB:** this replaced an earlier plan to feed
  manifest-declared (schema.yml) columns into sqlglot — those declarations are
  frequently partial (measured: real sources declaring 1 of N columns), which
  would drop the undeclared ones from the blast radius, i.e. fail OPEN.
- **External terminals.** A column resolving to a real relation that isn't a dbt
  node (a cross-db table, an unmanaged source) is reported as `[external]` with
  the relation and column — a resolved answer, distinct from `unknown`. External
  terminals do NOT taint downstream (an off-project source cannot carry an
  in-project change). Reserved for sqlglot-confident resolutions; a relation that
  maps ambiguously to two dbt nodes fails closed as `unknown`.

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
- **0.4.0 drop-list redesign (met):** `impact` (and `impact --column`) emit a
  topologically-ordered drop list (upstream/target/downstream, db-less DDL),
  column-pruned and fail-closed, matched against the generator's ground truth;
  `--additive` yields the absorbs bucket. `build-app` produces a self-contained
  offline page whose JS drop-list / taint / passthrough / external logic agrees
  with Python (`test_app_parity`), driven end-to-end in headless Chromium
  (`test_app_browser`, any console error fails). Structural passthrough takes
  the real CountMoney project from 3 to 1 unresolved models with **no catalog**
  (the 1 being a genuine `select *`-over-join), and traces
  `int_balance_sheet_latest.lt_borr` to its source with zero unknowns.
  Total suite: 152 tests.

---

## 8. Risks

- **Manifest schema drift** across dbt versions (mitigated: pinned to v12, which
  dbt Labs has evolved only additively across 1.8–1.12).
- **sqlglot dialect gaps** — Redshift `QUALIFY`-less window dedup,
  `DISTKEY`/`SORTKEY` DDL, late-binding (`bind: false`) views, `DECODE`. The
  `redshift_only/` fixture set exists specifically to surface these.
- **`SELECT *` propagation.** A single-source `select *` chain is traced by name
  (structural passthrough, §3.5) with no inventory; a `select *` over a join
  needs `catalog.json` and otherwise falls back to `unknown` (fail closed).
  Manifest-declared columns are deliberately NOT used as an inventory — real
  declarations are too often partial, which would fail open (§3.5).

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
