# dbt-walker

**"I changed a model (or one column) — which incrementals do I drop now?"**
dbt incremental models only append on a normal run, so change the logic and
every incremental on that path still holds rows built with the *old* logic
until someone full-refreshes it. dbt-walker reads dbt's own artifacts (never
your warehouse) and answers with **one ordered drop list**: every incremental
on the change's lineage — upstream, the target, and downstream — with the exact
`DROP TABLE` DDL and dbt commands to run. Column-level, so changing one
column usually touches far fewer models than the whole thing.

![The lineage explorer in Impact mode: changing one column in a model produces an ordered drop list of the incrementals on its lineage, grouped under one heading per position — upstream, target, and downstream — each with the DROP TABLE DDL and dbt commands to copy.](https://raw.githubusercontent.com/Hugs401/dbt-walker/main/docs/img/app-impact.png)

*The visual explorer in Impact mode — changing `col_0` in `int_38`. The **drop
list** names the incrementals on that column's lineage, grouped under one
heading per position (`int_11`, `int_24` upstream; `int_38` the target), each
group's `DROP TABLE` statements ready to copy and each model badged in the
graph. The graph traces `col_0` back through the chain; the SQL pane highlights
the line that produces it.*

## Highlights

- **Never touches your warehouse.** Everything works from `target/manifest.json`
  (and `target/compiled/` for column lineage) — artifacts `dbt compile` already
  produces.
- **Model-level commands are stdlib-only.** No dependencies at all; runs
  anywhere a manifest exists.
- **Column-level lineage** via [sqlglot](https://github.com/tobymao/sqlglot)
  over dbt's *compiled* SQL — change one column and see the (usually much
  smaller) set of models that actually read it.
- **Fails closed.** Lineage that can't be proven (`select *` over a join,
  dynamic macros, Python models) stays *in* the blast radius and is marked
  unproven — the tool never claims "safe" without proof.
- **A merged, ordered drop list.** `impact` gives you one topologically-ordered
  list of the incrementals to drop — upstream, target, and downstream — each
  carrying its `DROP TABLE schema.table;` (grouped under one heading per position
  in the app). Under `--additive`, incrementals that can just *add* the new
  column move to a separate bucket.
- **A one-file visual explorer** (`build-app`): a self-contained HTML page with
  the model tree, pan/zoom lineage graph, the drop-list plan, and SQL with the
  producing lines highlighted. Fully offline (mermaid is bundled, no CDN) —
  fine behind a corporate proxy, fine to email to a teammate.

## Install

Not on PyPI (yet). From a clone:

```bash
pip install .[col]        # [col] adds sqlglot for the column-level commands
# or, model-level only, zero dependencies:
pip install .
```

Or as a standalone tool via [pipx](https://pipx.pypa.io/):

```bash
python -m build --wheel                       # writes dist/dbt_walker-*.whl
pipx install "dist/dbt_walker-0.4.1-py3-none-any.whl[col]"
```

Requires Python 3.10+.

## CLI

Run from inside a dbt project (anywhere with `target/manifest.json` — run
`dbt compile` first), or point `--project-dir` at one:

```bash
dbt-walker upstream customers                    # what it reads from
dbt-walker downstream stg_orders --mat incremental   # what reads it, filtered
dbt-walker impact stg_orders                     # the drop list (see below)
dbt-walker impact stg_orders --column status     # pruned to what reads `status`
dbt-walker impact stg_orders --additive          # adding a column: append/sync
                                                 #   incrementals move to "absorbs"
```

`impact` prints one topologically-ordered **DROP THESE** list — every
incremental on the change's lineage, tagged `upstream` / `target` /
`downstream`, each with its `DROP TABLE schema.table;`. Below it: what rebuilds for free,
the tests that re-run, and the safe dbt alternative
(`dbt run --select <models> --full-refresh` + `dbt build --select <model>+`).
A dropped incremental is rebuilt in full by the next scheduled run. Add
`--column <c>` to prune the whole thing — upstream and downstream — to just the
lineage of that column (fails closed on lineage it can't resolve).

### Column-level

Changing one column usually affects far fewer models than changing the whole
model. These need the `[col]` extra; the SQL dialect is auto-detected from your
adapter (override with `--dialect`):

```bash
dbt-walker col-upstream   orders     --column amount     # where a column comes from
dbt-walker col-downstream stg_orders --column order_id   # what derives from it
dbt-walker impact stg_orders --column status             # impact, pruned to readers of `status`
```

A column that resolves to a real relation outside your dbt project (a cross-db
table, an unmanaged source) is reported as an `[external]` terminal — a real
answer, not a failure. And a `select *` staging/dedup chain
(`select *, row_number() ... where rn = 1`) that bottoms out at one relation is
traced **by name**, no inventory needed — so a column threaded through several
`select *` layers still resolves offline. Genuinely ambiguous cases (a `select *`
over a *join*) still fail closed.

If `target/catalog.json` exists (from `dbt docs generate`), the column
commands additionally use its per-relation column inventories to resolve the
harder cases — unqualified columns across joins. It's per-relation and
best-effort: a partial or missing catalog just means those relations resolve as
before, and a stale catalog is a warning, never a hard stop. Reading it never
touches the warehouse.

### Graphs and diffs

```bash
dbt-walker graph stg_orders                       # browser-viewable HTML (default)
dbt-walker graph stg_orders --column status       # only what a `status` change touches
dbt-walker graph stg_orders --format mermaid      # .mmd for GitHub / mermaid.live
dbt-walker graph --format dot --out - | dot -Tpng -o dag.png
dbt-walker diff --state prod/target/manifest.json # what changed vs prod, and where impact helps
```

Add `--json` to any command for machine-readable output.

## The visual explorer

```bash
cd your-dbt-project
dbt compile                 # the app is built from dbt's artifacts
dbt-walker build-app .      # -> ./<project>-lineage-<branch>-<timestamp>.html
```

One self-contained HTML file, open it in any browser. The expensive analysis
(sqlglot parsing, column edge extraction) runs once at build time in Python;
all traversal — lineage walks, impact classification, column taint,
SQL highlighting — runs on demand in the browser. No server, no network.

Inside:

- A searchable **model tree** and a pan/zoom **lineage graph**. Click a node to
  inspect it (SQL + details); Ctrl/Cmd-click to make it the **target**.
- **Three modes:** Lineage, Impact (the drop list from the screenshot, with a
  DROP badge on each listed node and a direction toggle that filters it to
  upstream / downstream / both), and Columns (pick columns, each a collapsible
  group tracing where it comes from and what derives from it). Selecting several
  columns unions everything affected by *any* of them.
- **A two-part Detail pane:** a red **TARGET DETAIL** half that stays fixed on
  the model you picked (the plan / the column trace), over a blue **INSPECTING**
  half that follows whatever node you click — its SQL, with the producing lines
  highlighted.
- **SQL highlighting** distinguishes *proven* derivations (solid) from
  *unproven* fail-closed ones (hatched), with exact line spans where sqlglot
  can prove them — including inside CTEs, and for upstream nodes the lines that
  *feed* your selection.
- If a model's columns can't be resolved, the app says *why* (and when
  `dbt docs generate` would fix it).

`build-app` never runs dbt for you — if `target/` is missing it tells you to
compile, and if model files are newer than the manifest it stamps a staleness
banner into the page.

## Documentation

- [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) — requirements, assumptions,
  non-goals, and per-phase acceptance criteria.

## Development

```bash
python -m venv .venv && .venv/Scripts/pip install -e .[dev]   # (bin/ on mac/linux)
cd tests/fixtures/jaffle_shop_duckdb && ../../../.venv/Scripts/dbt build --profiles-dir . && cd ../../..
.venv/Scripts/python -m pytest tests/ -q
```

Tests come in four layers: unit tests on inline manifests; column lineage
asserted against a deterministic synthetic-project generator's
`ground_truth.json`; a **parity** suite that runs the app's actual JavaScript
under node and asserts it agrees with the Python traversal; and a **browser**
suite where Playwright drives the real generated app in headless Chromium and
fails on any console error (`python -m playwright install chromium` to enable).
Larger fixtures — the synthetic projects and the real-world
[CountMoney](https://github.com/flyanakin/CountMoney) project — are gitignored
and built on demand by the scripts in `scripts/`; fixture-dependent tests skip
when they're absent.
