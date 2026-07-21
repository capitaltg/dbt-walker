"""Column-level lineage over dbt's *compiled* SQL (phase 2).

We parse ``target/compiled/**/*.sql`` with sqlglot — never the raw jinja model
SQL — because refs/macros are already expanded there and every FROM clause names
a real relation, which maps back to an upstream node's unique_id.

For each model we resolve, per output column, the exact upstream (relation,
column) leaves it derives from and a transform kind (passthrough / rename / cast
/ expression / coalesce / case / aggregate). Anything we cannot resolve with
certainty — a ``SELECT *`` we can't expand, a parse failure, a Python model, a
missing compiled file — is reported as **unknown**, never guessed. Downstream,
unknown taints everything it can reach (fail closed, decision D4): the tool may
over-include a model in a blast radius, but never wrongly clear one.

If ``target/catalog.json`` exists (``dbt docs generate``), its per-relation
column inventories are fed to sqlglot so unqualified columns across joins and
``SELECT *`` over physical tables resolve instead of failing closed — but only
per relation, and genuine ambiguity still fails closed. See ``load_catalog``.

Requires the ``[col]`` extra (sqlglot). Model-level features never import this.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from dbt_walker.graph import Graph, GraphError

try:
    import sqlglot
    from sqlglot import exp
    from sqlglot.optimizer.qualify import qualify
    from sqlglot.optimizer.scope import build_scope
except ImportError:  # pragma: no cover - exercised via the CLI guard
    sqlglot = None


class ColumnLineageUnavailable(GraphError):
    """sqlglot isn't installed, or compiled SQL is missing."""


UNKNOWN = "unknown"

# dbt adapter_type -> sqlglot dialect (names mostly match; map the exceptions)
_ADAPTER_TO_DIALECT = {
    "duckdb": "duckdb",
    "postgres": "postgres",
    "redshift": "redshift",
    "snowflake": "snowflake",
    "bigquery": "bigquery",
    "databricks": "databricks",
    "spark": "spark",
    "trino": "trino",
    "athena": "trino",
    "clickhouse": "clickhouse",
}


def dialect_for(graph) -> str:
    """The sqlglot dialect matching the project's dbt adapter (from manifest
    metadata); falls back to postgres (the Redshift-family default)."""
    adapter = (graph.manifest.get("metadata") or {}).get("adapter_type")
    return _ADAPTER_TO_DIALECT.get(adapter, "postgres")


@dataclass
class Catalog:
    """Column inventories from ``target/catalog.json`` (``dbt docs generate``).

    A sqlglot ``schema`` dict (``{db: {schema: {table: {col: type}}}}``) built
    ONLY from relations the catalog actually lists, so resolution stays per
    relation: a relation in the catalog gets its real columns, one absent from
    it resolves exactly as before (fail closed). That is automatically correct
    for a partial catalog, a compile-only project (dbt writes a well-formed but
    EMPTY catalog there), and models added since the last docs generate.
    """
    schema: dict
    generated_at: str | None
    relation_count: int
    stale: bool = False        # older than the manifest it sits beside
    manifest_generated_at: str | None = None

    @property
    def present(self) -> bool:
        return self.relation_count > 0


def _catalog_path(root: Path) -> Path:
    return root / "target" / "catalog.json"


def load_catalog(graph: Graph, root: Path | None = None) -> Catalog | None:
    """Load ``catalog.json`` next to the manifest. None if the file is absent
    (the tool then behaves exactly as it always has). A present-but-empty
    catalog returns a Catalog with ``present is False``.
    """
    root = root or graph.project_root
    if root is None:
        return None
    path = _catalog_path(root)
    if not path.exists():
        return None
    try:
        cat = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None

    schema: dict = {}
    count = 0
    for section in ("nodes", "sources"):
        for entry in (cat.get(section) or {}).values():
            meta = entry.get("metadata") or {}
            db, sch, name = meta.get("database"), meta.get("schema"), meta.get("name")
            cols = entry.get("columns") or {}
            if not (name and cols):
                continue
            # order columns by their catalog index so an expanded `*` keeps
            # warehouse column order
            ordered = sorted(cols.values(), key=lambda c: c.get("index", 0))
            table = {c["name"]: (c.get("type") or "UNKNOWN") for c in ordered if c.get("name")}
            if not table:
                continue
            schema.setdefault(db, {}).setdefault(sch, {})[name] = table
            count += 1

    cat_gen = (cat.get("metadata") or {}).get("generated_at")
    man_gen = (graph.manifest.get("metadata") or {}).get("generated_at")
    stale = bool(cat_gen and man_gen and cat_gen < man_gen)
    return Catalog(schema=schema, generated_at=cat_gen, relation_count=count,
                   stale=stale, manifest_generated_at=man_gen)


def _require_sqlglot() -> None:
    if sqlglot is None:
        raise ColumnLineageUnavailable(
            "column lineage needs sqlglot. Install it with:\n"
            "    pip install dbt-walker[col]"
        )


def _norm(relation: str) -> str:
    """Normalize a relation string for matching: drop quoting, lowercase."""
    return relation.replace('"', "").replace("`", "").replace("'", "").strip().lower()


def _relation_name(table) -> str:
    parts = [table.catalog, table.db, table.name]
    return _norm(".".join(p for p in parts if p))


def _is_select_star(expr) -> bool:
    """True for a bare `*` or a qualified `t.*` projection (not count(*))."""
    if isinstance(expr, exp.Star):
        return True
    return isinstance(expr, exp.Column) and isinstance(expr.this, exp.Star)


def _classify(expr, out_name: str) -> str:
    if isinstance(expr, exp.Column):
        return "passthrough" if expr.name == out_name else "rename"
    if isinstance(expr, (exp.Cast, exp.TryCast)):
        return "cast"
    if isinstance(expr, (exp.Case, exp.DecodeCase)):  # DecodeCase = Redshift DECODE
        return "case"
    if isinstance(expr, exp.Coalesce):  # also NVL
        return "coalesce"
    # AggFunc covers sum/count/min/max/avg; WithinGroup wraps LISTAGG etc.
    if isinstance(expr, (exp.AggFunc, exp.WithinGroup)):
        return "aggregate"
    return "expression"


def parse_columns(sql: str, dialect: str,
                  schema: dict | None = None) -> dict[str, list[tuple[str | None, str, str]]] | None:
    """Resolve each output column of one compiled query to its (relation, column,
    transform) leaves. Manifest-free — relations are raw strings, not unique_ids.

    ``schema`` is an optional sqlglot column inventory (from ``catalog.json``);
    when given, sqlglot can attribute unqualified columns across joins and expand
    ``select *`` over physical tables — both of which otherwise fail closed.

    Returns None if the whole query is unresolvable (parse error, unexpandable
    SELECT *, empty). A per-column value of ``[(None, "", "unknown")]`` marks a
    single column we couldn't trace. Empty list means no column dependency
    (count(*), literals).
    """
    _require_sqlglot()
    if not sql or not sql.strip():
        return None
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
        qualified = qualify(tree, dialect=dialect, schema=schema,
                            validate_qualify_columns=False)
        scope = build_scope(qualified)
    except Exception:
        return None
    if scope is None or not getattr(qualified, "selects", None):
        return None

    columns: dict[str, list[tuple[str | None, str, str]]] = {}
    for proj in qualified.selects:
        inner = proj.this if isinstance(proj, exp.Alias) else proj
        if _is_select_star(inner):
            # `select *` / `t.*` — expandable when it stars over CTEs/subqueries
            # or a single physical table (the common staging pattern). Only a
            # star over a physical table whose columns we can't enumerate, or an
            # ambiguous multi-table star, fails closed.
            expanded = _expand_star(scope, inner)
            if expanded is None:
                return None
            for out_name, leaves in expanded:
                columns[out_name] = [(rel, col, "passthrough") for rel, col in sorted(set(leaves))]
            continue
        name = proj.alias_or_name
        transform = _classify(inner, name)
        leaves = _resolve_leaves(scope, proj)
        if leaves is None:
            columns[name] = [(None, "", UNKNOWN)]
            continue
        columns[name] = [(rel, col, transform) for rel, col in sorted(set(leaves))]
    return columns


def _paren_depths(tokens, TT) -> list[int]:
    """Depth each token sits AT (a paren pair's own tokens share the outer depth)."""
    out, depth = [], 0
    for t in tokens:
        if t.token_type == TT.L_PAREN:
            out.append(depth)
            depth += 1
        elif t.token_type == TT.R_PAREN:
            depth -= 1
            out.append(depth)
        else:
            out.append(depth)
    return out


# tokens that end a projection list without a FROM (bare selects, set operations)
def _projection_regions(tokens, depths, TT) -> list[tuple[int, int, int]]:
    """(lo, hi, depth) token ranges holding each SELECT's projection list, at any
    nesting level — CTE bodies and inline subqueries included."""
    enders = {TT.FROM, TT.UNION, TT.INTERSECT, TT.EXCEPT}
    regions = []
    for i, tok in enumerate(tokens):
        if tok.token_type != TT.SELECT:
            continue
        d = depths[i]
        j = i + 1
        while j < len(tokens):
            if depths[j] < d:               # the enclosing paren closed
                break
            if depths[j] == d and tokens[j].token_type in enders:
                break
            j += 1
        regions.append((i + 1, j, d))
    return regions


def _split_projections(tokens, depths, lo, hi, d) -> list[list]:
    comma = sqlglot.tokens.TokenType.COMMA
    groups: list[list] = [[]]
    for k in range(lo, hi):
        if depths[k] == d and tokens[k].token_type == comma:
            groups.append([])
            continue
        groups[-1].append(tokens[k])
    return [g for g in groups if g]


def _projection_name(toks, TT) -> str | None:
    """Output name of one select item, from its tokens: the identifier after its
    last top-level AS, else a bare `t.col` / `col` reference. None for anything
    unnamed (a bare expression, a star) — better to skip than to guess."""
    depth, alias_at = 0, None
    for i, t in enumerate(toks):
        if t.token_type == TT.L_PAREN:
            depth += 1
        elif t.token_type == TT.R_PAREN:
            depth -= 1
        elif depth == 0 and t.token_type == TT.ALIAS:
            alias_at = i
    idents = (TT.VAR, TT.IDENTIFIER)
    if alias_at is not None and alias_at + 1 < len(toks):
        tok = toks[alias_at + 1]
        return tok.text if tok.token_type in idents else None
    # unaliased: only a plain column reference has an output name
    if toks and toks[-1].token_type in idents:
        ref = [t for t in toks if t.token_type not in (TT.DOT,)]
        if all(t.token_type in idents for t in ref):
            return toks[-1].text
    return None


def select_spans(sql: str, dialect: str) -> dict[str, list[tuple[int, int]]]:
    """Map each output column of a compiled query to the 1-based line range(s)
    that PRODUCE it, so a viewer can highlight the whole expression.

    sqlglot puts source positions on tokens, not on expression nodes, so this
    works at the token level: locate each SELECT's projection list and split it
    on commas at that SELECT's own paren depth. Item N of the split is the
    source text of projection N.

    Two tiers, because real dbt SQL keeps its logic in CTEs and ends with
    `select * from <final_cte>`:

    1. If the FINAL projection names the column, that expression is the answer.
       Names come from the parsed tree and the split is checked against it.
    2. Otherwise fall back to every named projection in the enclosed scopes
       (CTEs, subqueries). Names there are read from the tokens, so an unnamed
       expression is skipped rather than guessed at.

    Fails closed — {} on parse errors or a split that disagrees with the parse.
    A wrong span silently points at the wrong SQL, which is worse than none.
    """
    _require_sqlglot()
    if not sql or not sql.strip():
        return {}
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
        tokens = list(sqlglot.tokenize(sql, read=dialect))
    except Exception:
        return {}
    if not isinstance(tree, exp.Select) or not getattr(tree, "selects", None):
        return {}          # a set operation has no single projection list

    TT = sqlglot.tokens.TokenType
    depths = _paren_depths(tokens, TT)
    regions = _projection_regions(tokens, depths, TT)
    if not regions:
        return {}
    outermost = min(d for _, _, d in regions)
    top = [r for r in regions if r[2] == outermost]
    if len(top) != 1:
        return {}          # more than one outermost SELECT: don't guess

    def span(toks):
        lines = [t.line for t in toks]
        return (min(lines), max(lines))

    # tier 1 -- the final projection, names taken from the parsed tree
    final: dict[str, list[tuple[int, int]]] = {}
    lo, hi, d = top[0]
    groups = _split_projections(tokens, depths, lo, hi, d)
    if len(groups) != len(tree.selects):
        return {}          # split disagrees with the parse
    for proj, toks in zip(tree.selects, groups):
        name = proj.alias_or_name
        if not name or name == "*" or _is_select_star(proj):
            continue        # a star has no single producing expression
        final[name] = [span(toks)]

    # tier 2 -- named projections inside CTEs and subqueries
    inner: dict[str, list[tuple[int, int]]] = {}
    for lo, hi, d in regions:
        if (lo, hi, d) == top[0]:
            continue
        for toks in _split_projections(tokens, depths, lo, hi, d):
            name = _projection_name(toks, TT)
            if name:
                inner.setdefault(name, []).append(span(toks))

    return {name: final.get(name) or inner[name] for name in {**inner, **final}}


def unresolved_reason(graph, uid: str, dialect: str) -> str:
    """Why a model's column lineage didn't resolve — so the UI can nudge toward
    the right remedy instead of a bare 'lineage unresolved'. One of:

    - ``"python"``  : a Python model; there is no SQL to trace (no catalog fix).
    - ``"nosql"``   : no compiled SQL found (run dbt compile).
    - ``"parse"``   : sqlglot couldn't parse it (dialect gap / exotic syntax).
    - ``"star"``    : a ``select *`` we couldn't expand — ``dbt docs generate``
                      (catalog.json) resolves this one.
    - ``"unknown"`` : resolved-but-unclassifiable; generic fallback.

    Only ``"star"`` and ``"nosql"`` have a clean remedy; the message differs.
    """
    node = graph.nodes.get(uid) or {}
    if (node.get("language") or "sql").lower() == "python":
        return "python"
    sql = graph.compiled_sql(uid)
    if not sql or not sql.strip():
        return "nosql"
    if sqlglot is None:
        return "unknown"
    try:
        sqlglot.parse_one(sql, dialect=dialect)
    except Exception:
        return "parse"
    import re as _re
    if "*" in _re.sub(r"--.*", "", sql):
        return "star"
    return "unknown"


def _synth_column(alias: str, name: str):
    return exp.column(name, table=alias) if alias else exp.column(name)


def _from_sources(scope) -> dict:
    """Sources actually in this select's FROM/JOIN (scope.sources also lists
    every visible CTE, which would wrongly widen star expansion and the
    single-source inference)."""
    expr = scope.expression
    refs = []
    # the FROM arg key varies across sqlglot versions (from / from_); find by type
    frm = next((v for v in expr.args.values() if isinstance(v, exp.From)), None)
    if frm is not None:
        refs.append(frm.this)
    for join in expr.args.get("joins") or []:
        refs.append(join.this)
    out = {}
    for ref in refs:
        if ref is None:
            continue
        alias = ref.alias_or_name
        if alias in scope.sources:
            out[alias] = scope.sources[alias]
    return out


def _output_names(source) -> list[str] | None:
    """Output column names of a subquery/CTE scope, or None if not enumerable
    (a physical table, or a nested unexpandable star)."""
    if isinstance(source, exp.Table):
        return None  # physical table: needs catalog.json to enumerate
    names = []
    for proj in source.expression.selects:
        inner = proj.this if isinstance(proj, exp.Alias) else proj
        if _is_select_star(inner):
            return None  # nested star we don't expand here -> fail closed
        names.append(proj.alias_or_name)
    return names


def _expand_star(scope, star_expr) -> list[tuple[str, list[tuple[str, str]]]] | None:
    """Expand a `*` / `t.*` projection to (output_name, leaves) pairs, resolving
    each expanded column to its physical leaves. None => not expandable."""
    target = star_expr.table if isinstance(star_expr, exp.Column) else None
    items = ([(target, scope.sources.get(target))] if target
             else list(_from_sources(scope).items()))
    result: list[tuple[str, list[tuple[str, str]]]] = []
    for alias, source in items:
        if source is None:
            return None
        names = _output_names(source)
        if names is None:
            return None
        for name in names:
            leaves = _resolve_column(scope, _synth_column(alias, name))
            if leaves is None:
                return None
            result.append((name, leaves))
    return result


def _resolve_leaves(scope, projection) -> list[tuple[str, str]] | None:
    """Trace a projection's column refs to physical (relation, column) leaves,
    descending through subquery/CTE scopes. None => unresolvable."""
    leaves: list[tuple[str, str]] = []
    for column in projection.find_all(exp.Column):
        resolved = _resolve_column(scope, column)
        if resolved is None:
            return None
        leaves.extend(resolved)
    return leaves


def _resolve_column(scope, column) -> list[tuple[str, str]] | None:
    table = column.table
    if not table:
        # unqualified column (qualify couldn't attribute it without a catalog):
        # in a single-FROM-source scope it must come from that source; otherwise
        # it's ambiguous and we fail closed.
        from_sources = _from_sources(scope)
        if len(from_sources) == 1:
            table = next(iter(from_sources))
        else:
            return None
    source = scope.sources.get(table)
    if source is None:
        return None
    if isinstance(source, exp.Table):
        return [(_relation_name(source), column.name)]
    # nested Scope (subquery / CTE): find the matching output column, recurse
    matched = None
    for proj in source.expression.selects:
        if proj.alias_or_name == column.name:
            matched = proj
            break
    if matched is None or _is_select_star(matched.this if isinstance(matched, exp.Alias) else matched):
        # the inner scope selects `*` (or doesn't name this column explicitly):
        # a `select *` over a single source passes the column through by name
        return _resolve_through_star(source, column.name)
    out: list[tuple[str, str]] = []
    for inner_col in matched.find_all(exp.Column):
        resolved = _resolve_column(source, inner_col)
        if resolved is None:
            return None
        out.extend(resolved)
    return out


def _resolve_through_star(scope, colname: str) -> list[tuple[str, str]] | None:
    """Resolve a column reaching a `select *` scope: a star over one source
    passes columns through by name; a star over a join is ambiguous (None)."""
    from_sources = _from_sources(scope)
    if len(from_sources) != 1:
        return None
    alias, source = next(iter(from_sources.items()))
    if isinstance(source, exp.Table):
        return [(_relation_name(source), colname)]
    return _resolve_column(scope, _synth_column(alias, colname))


@dataclass
class ColumnEdge:
    parent: str | None  # parent unique_id, or None when the leaf is unknown
    column: str | None  # parent column name, or None when unknown
    transform: str


@dataclass
class ModelColumns:
    resolved: bool  # False => whole model is unknown (fail closed)
    columns: dict[str, list[ColumnEdge]] = field(default_factory=dict)


class ColumnGraph:
    """Lazily parses compiled SQL to answer column-level questions."""

    def __init__(self, graph: Graph, dialect: str | None = None):
        _require_sqlglot()
        if graph.project_root is None:
            raise ColumnLineageUnavailable(
                "column lineage requires a graph loaded from a project directory "
                "(so compiled SQL can be found)."
            )
        self.graph = graph
        # default to the project's own adapter dialect; --dialect overrides
        self.dialect = dialect or dialect_for(graph)
        self.root = graph.project_root
        # catalog.json column inventories, if the user ran `dbt docs generate`.
        # Sharpens resolution (unqualified joins, `select *`); absent -> None,
        # and everything works exactly as before.
        self.catalog = load_catalog(graph)
        self._schema = self.catalog.schema if self.catalog and self.catalog.present else None
        self._cache: dict[str, ModelColumns] = {}
        # relation string -> unique_id, for mapping SQL FROM leaves back to nodes.
        # dbt's own `relation_name` is exactly what appears in compiled SQL (real
        # tables and duckdb external sources alike); fall back to the composed
        # database.schema.alias when a node lacks it.
        self._rel_to_uid: dict[str, str] = {}
        for uid, node in graph.nodes.items():
            rel = node.get("relation_name") or graph.relation(uid)
            if rel:
                self._rel_to_uid[_norm(rel)] = uid

    # -- per-model parsing -------------------------------------------------- #

    def _compiled_sql(self, uid: str) -> str | None:
        return self.graph.compiled_sql(uid)

    def columns_of(self, uid: str) -> ModelColumns:
        if uid in self._cache:
            return self._cache[uid]
        result = self._parse(uid)
        self._cache[uid] = result
        return result

    def _parse(self, uid: str) -> ModelColumns:
        if self.graph.resource_type(uid) != "model":
            # sources/seeds/snapshots are leaves: no upstream column lineage,
            # but they aren't "unknown" either — treat as resolved with no edges.
            return ModelColumns(resolved=True)
        parsed = parse_columns(self._compiled_sql(uid) or "", self.dialect, self._schema)
        if parsed is None:
            return ModelColumns(resolved=False)  # python model / SELECT * / parse error
        columns = {
            name: [ColumnEdge(self._rel_to_uid.get(rel) if rel else None,
                              col or None, transform)
                   for rel, col, transform in leaves]
            for name, leaves in parsed.items()
        }
        return ModelColumns(resolved=True, columns=columns)

    # -- traversal ---------------------------------------------------------- #

    def upstream(self, uid: str, column: str) -> "ColumnTrace":
        """Transitive column provenance (ancestor columns feeding uid.column)."""
        trace = ColumnTrace()
        self._walk_up(uid, column, 1, trace, set())
        return trace

    def _walk_up(self, uid, column, dist, trace, seen) -> None:
        mc = self.columns_of(uid)
        if not mc.resolved:
            trace.add(uid, column, None, None, UNKNOWN, dist)
            return
        for edge in mc.columns.get(column, []):
            key = (uid, column, edge.parent, edge.column)
            if key in seen:
                continue
            seen.add(key)
            trace.add(uid, column, edge.parent, edge.column, edge.transform, dist)
            if edge.parent is not None and edge.column is not None:
                self._walk_up(edge.parent, edge.column, dist + 1, trace, seen)

    def taint_downstream(self, root: str, column: str) -> "TaintResult":
        """Propagate a change to root.column through the downstream model DAG,
        failing closed on unknown lineage. Returns tainted columns and the set
        of affected models."""
        downstream = set(self.graph.walk(root, "down"))
        model_scope = [u for u in downstream if self.graph.resource_type(u) == "model"]
        order = self.graph.topo_order(set(model_scope))

        tainted: set[tuple[str, str]] = {(root, column)}
        affected: set[str] = set()
        unknown_models: set[str] = set()

        for uid in order:
            mc = self.columns_of(uid)
            if not mc.resolved:
                # can't prove it doesn't read the column -> taint the whole model
                unknown_models.add(uid)
                affected.add(uid)
                # taint every column name we know it produces (none, if opaque);
                # descendants that read from it become tainted via the uid marker
                tainted.add((uid, "*"))
                continue
            model_hit = False
            for col, edges in mc.columns.items():
                hit = False  # per-column: does THIS column read the change?
                for edge in edges:
                    if edge.parent is None:  # unknown leaf -> fail closed
                        hit = True
                    elif (edge.parent, edge.column) in tainted:
                        hit = True
                    elif (edge.parent, "*") in tainted:  # parent was opaque
                        hit = True
                if hit:
                    tainted.add((uid, col))
                    model_hit = True
            if model_hit:
                affected.add(uid)

        return TaintResult(tainted=tainted, affected=affected, unknown_models=unknown_models)


@dataclass
class ColumnTrace:
    edges: list[dict] = field(default_factory=list)

    def add(self, uid, column, parent, parent_col, transform, distance) -> None:
        self.edges.append(
            {
                "model": uid,
                "column": column,
                "parent": parent,
                "parent_column": parent_col,
                "transform": transform,
                "distance": distance,
            }
        )

    @property
    def has_unknown(self) -> bool:
        return any(e["transform"] == UNKNOWN for e in self.edges)


@dataclass
class TaintResult:
    tainted: set
    affected: set
    unknown_models: set
