"""Python <-> JS parity for the lineage app's traversal logic.

The app re-implements `walk` / `taint_downstream` / model classification in JS so
the browser can answer questions without a server. That port can silently drift
from the Python it mirrors — and this logic is subtle (a per-column flag leaking
across columns once caused real over-reporting). So: Python computes expected
results across the synth fixture, the app's ACTUAL JS runs under node, and the
two are compared.

Skipped when node isn't installed; the JS is extracted from the real template,
never a copy.
"""
import json
import shutil
import subprocess

import pytest

from conftest import GEN_SMALL_CMD, SYNTH_SMALL, needs

pytest.importorskip("sqlglot", reason="app column data needs the [col] extra")

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed (JS parity unchecked)")

# markers bounding the traversal block inside the template's <script>
JS_START = "const N = DATA.nodes"
JS_END = "window.__api"


def _traversal_js() -> str:
    from dbt_walker import app_template

    src = app_template._TEMPLATE
    return src[src.index(JS_START):src.index(JS_END)]


def _payload():
    from pathlib import Path

    from dbt_walker import app
    from dbt_walker.graph import Graph

    graph = Graph.load(SYNTH_SMALL)
    return graph, app.collect(graph, Path(SYNTH_SMALL), include_sql=False)


def _cases(graph, payload, limit=12):
    """(model_uid, [columns]) probes: models with resolved column lineage."""
    cases = []
    for uid in sorted(payload["columns"]):
        info = payload["columns"][uid]
        if not info["resolved"] or not info["cols"]:
            continue
        cols = sorted(info["cols"])
        cases.append((uid, cols[:1]))
        if len(cols) > 2:
            cases.append((uid, cols[:2]))  # multi-column union
        if len(cases) >= limit:
            break
    return cases


def _python_expected(graph, cases, carried):
    """`carried` = node ids the app actually embeds. The app summarizes tests
    per-model rather than carrying them as graph nodes, so walk comparisons are
    restricted to the nodes both sides know about."""
    from dbt_walker import cli
    from dbt_walker.columns import ColumnGraph

    cg = ColumnGraph(graph, dialect="duckdb")
    out = []
    for uid, cols in cases:
        # union taint across the selected columns (what the app's chips do)
        merged_affected, merged_unknown = set(), set()
        for col in cols:
            t = cg.taint_downstream(uid, col)
            merged_affected |= {u for u in t.affected if graph.resource_type(u) == "model"}
            merged_unknown |= t.unknown_models
        # the changed model itself is part of the refresh plan
        plan_models = sorted(merged_affected | {uid})
        full, rebuild = cli._classify_models(graph, plan_models, additive=False)
        full_add, rebuild_add = cli._classify_models(graph, plan_models, additive=True)
        out.append({
            "model": uid, "cols": cols,
            "affected": sorted(merged_affected),
            "unknown": sorted(merged_unknown),
            "full": full, "rebuild": rebuild,
            "full_additive": full_add, "rebuild_additive": rebuild_add,
            "up": sorted(u for u in graph.walk(uid, "up") if u in carried),
            "down": sorted(u for u in graph.walk(uid, "down") if u in carried),
            "prereqs": cli._upstream_prereqs(graph, uid),
        })
    return out


_HARNESS = """
const DATA = require(process.argv[2]);
const CASES = require(process.argv[3]);
__TRAVERSAL__
const out = CASES.map(([model, cols]) => {
  // union taint across the selected columns, as the app's column chips do
  const affected = new Set(), unknown = new Set();
  for (const c of cols) {
    const t = taint(model, [c]);
    for (const u of t.affected) if (N[u] && N[u].type === "model") affected.add(u);
    for (const u of t.unknown) unknown.add(u);
  }
  const planSet = new Set(affected);
  if (N[model] && N[model].type === "model") planSet.add(model);
  const plan = Array.from(planSet);
  const a = classify(plan, false), b = classify(plan, true);
  return {
    model, cols,
    affected: Array.from(affected).sort(),
    unknown: Array.from(unknown).sort(),
    full: a.full, rebuild: a.rebuild,
    full_additive: b.full, rebuild_additive: b.rebuild,
    up: Object.keys(walk(model, "up")).sort(),
    down: Object.keys(walk(model, "down")).sort(),
    prereqs: upstreamPrereqs(model),
  };
});
process.stdout.write(JSON.stringify(out));
"""


def _js_actual(payload, cases, tmp_path):
    data_file = tmp_path / "data.json"
    data_file.write_text(json.dumps(payload), encoding="utf-8")
    cases_file = tmp_path / "cases.json"
    cases_file.write_text(json.dumps([[u, c] for u, c in cases]), encoding="utf-8")
    script = tmp_path / "harness.js"
    script.write_text(_HARNESS.replace("__TRAVERSAL__", _traversal_js()), encoding="utf-8")

    proc = subprocess.run([NODE, str(script), str(data_file), str(cases_file)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
@needs_node
def test_js_traversal_matches_python(tmp_path):
    graph, payload = _payload()
    cases = _cases(graph, payload)
    assert cases, "fixture should yield probe cases"

    expected = _python_expected(graph, cases, set(payload["nodes"]))
    actual = _js_actual(payload, cases, tmp_path)

    assert len(actual) == len(expected)
    for exp, act in zip(expected, actual):
        label = f"{exp['model']} cols={exp['cols']}"
        for field in ("affected", "unknown", "full", "rebuild",
                      "full_additive", "rebuild_additive", "up", "down", "prereqs"):
            assert act[field] == exp[field], (
                f"{label}: JS {field} != Python\n  JS:     {act[field]}\n  Python: {exp[field]}"
            )


_HL_HARNESS = """
const DATA = {nodes:{}, parents:{}, children:{}, columns:{}, sql:{}};  // hlRegex is pure
__TRAVERSAL__
const re = hlRegex(["col_2"]);
process.stdout.write(JSON.stringify(
  JSON.parse(require("fs").readFileSync(process.argv[2], "utf8")).map(l => re.test(l))));
"""

# (sql line, should it highlight when col_2 is the affected column?)
_HL_LINES = [
    ("    t0.id as id,", False),
    # reads col_2, produces col_0 -- the shipped bug highlighted these three
    ("    case when t3.col_2 > 50 then t3.col_2 else 0 end as col_0,", False),
    ("    coalesce(t3.col_2, t0.col_1) as col_4,", False),
    ("    (t0.col_2 * 2 + t1.col_3) as col_5", False),
    ("    t1.col_4 as col_1,", False),
    ('join "synth"."main"."int_793" t3 on t0.id = t3.id', False),
    # actually produces col_2
    ("    coalesce(t3.col_0, t3.col_3) as col_2,", True),
    ('    t0.col_9 as "col_2",', True),
    ("    t3.col_2,", True),          # bare passthrough select item
]


@needs_node
def test_sql_highlight_matches_producers_only(tmp_path):
    """Highlight the line that PRODUCES an affected column, not every line whose
    text mentions it. Reading t3.col_2 to build col_0 is not blast radius, and
    marking it claims impact the taint engine never claimed."""
    lines_file = tmp_path / "lines.json"
    lines_file.write_text(json.dumps([l for l, _ in _HL_LINES]), encoding="utf-8")
    script = tmp_path / "hl.js"
    script.write_text(_HL_HARNESS.replace("__TRAVERSAL__", _traversal_js()), encoding="utf-8")

    proc = subprocess.run([NODE, str(script), str(lines_file)], capture_output=True, text=True)
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"

    for (line, want), got in zip(_HL_LINES, json.loads(proc.stdout)):
        assert got == want, (
            f"highlight={got}, expected {want} for:\n  {line}")


_SPAN_HARNESS = """
const DATA = {nodes:{}, parents:{}, children:{}, sql:{}, columns:{
  m1: {resolved:true, cols:{rn1:[], id:[], amt:[]},
       spans:{rn1:[[4,7]], id:[[2,2]], amt:[[1,1],[9,9]]}},   // amt: parallel CTEs
  m2: {resolved:true, cols:{a:[]}}            // unparseable model: no spans
}};
__TRAVERSAL__
const hits = s => s ? Array.from(s).sort((a,b)=>a-b) : s;
process.stdout.write(JSON.stringify({
  multiline: hits(hlLines("m1", ["rn1"], "compiled")),
  union:     hits(hlLines("m1", ["rn1","id"], "compiled")),
  ranges:    hits(hlLines("m1", ["amt"], "compiled")),
  raw:       hlLines("m1", ["rn1"], "raw"),        // jinja lines != compiled lines
  nospans:   hlLines("m2", ["a"], "compiled"),     // -> falls back
  unknowncol:hlLines("m1", ["nope"], "compiled")
}));
"""


@needs_node
def test_js_span_highlighting(tmp_path):
    """Spans highlight a whole multi-line expression, and fall back (null) where
    they can't be trusted: raw jinja, and models with no span data."""
    script = tmp_path / "spans.js"
    script.write_text(_SPAN_HARNESS.replace("__TRAVERSAL__", _traversal_js()), encoding="utf-8")
    proc = subprocess.run([NODE, str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    got = json.loads(proc.stdout)

    assert got["multiline"] == [4, 5, 6, 7], "the whole over(...) clause, not just its last line"
    assert got["union"] == [2, 4, 5, 6, 7], "multi-column selection unions the spans"
    assert got["ranges"] == [1, 9], "a column produced in two CTEs highlights both"
    assert got["raw"] is None, "compiled line numbers must not be applied to raw jinja"
    assert got["nospans"] is None, "no span data -> caller falls back to alias matching"
    assert got["unknowncol"] is None, "unknown column -> fall back rather than highlight nothing"


@needs(SYNTH_SMALL, GEN_SMALL_CMD)
@needs_node
def test_js_taint_is_per_column(tmp_path):
    """The bug that shipped once: a per-column flag leaking across columns, so
    everything after the first match got marked. Guard the JS port against it."""
    graph, payload = _payload()
    # a model with several columns where only some derive from the probe column
    target = None
    for uid, info in sorted(payload["columns"].items()):
        if info["resolved"] and len(info["cols"]) >= 3:
            target = uid
            break
    assert target, "need a multi-column model"
    col = sorted(payload["columns"][target]["cols"])[0]

    expected = _python_expected(graph, [(target, [col])], set(payload["nodes"]))[0]
    actual = _js_actual(payload, [(target, [col])], tmp_path)[0]
    assert actual["affected"] == expected["affected"]
    # and the affected set must be a strict subset of everything downstream
    down_models = [u for u in expected["down"] if graph.resource_type(u) == "model"]
    if down_models:
        assert set(actual["affected"]) - {target} <= set(down_models)


_UNPROVEN_HARNESS = """
const DATA = {nodes:{}, parents:{}, children:{}, sql:{}, columns:{
  mixed:  {resolved:true, cols:{
             good: [["m.up","a","passthrough"]],
             bad:  [[null,"","unknown"]],
             both: [["m.up","b","case"],[null,"","unknown"]]}},
  broken: {resolved:false, cols:{x:[], y:[]}},
  clean:  {resolved:true, cols:{a:[["m.up","a","passthrough"]]}}
}};
__TRAVERSAL__
const s = u => Array.from(unprovenCols(u)).sort();
process.stdout.write(JSON.stringify({
  mixed: s("mixed"), broken: s("broken"), clean: s("clean"), missing: s("nope")
}));
"""


@needs_node
def test_js_marks_untraceable_columns(tmp_path):
    """Fail-closed inclusions must be distinguishable from proven derivations --
    otherwise "we couldn't tell" is presented as a finding."""
    script = tmp_path / "unproven.js"
    script.write_text(_UNPROVEN_HARNESS.replace("__TRAVERSAL__", _traversal_js()), encoding="utf-8")
    proc = subprocess.run([NODE, str(script)], capture_output=True, text=True)
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    got = json.loads(proc.stdout)

    assert got["mixed"] == ["bad", "both"], "a null parent anywhere makes the column unproven"
    assert got["broken"] == ["x", "y"], "an unparseable model taints all of its columns"
    assert got["clean"] == [], "fully traced model has nothing unproven"
    assert got["missing"] == [], "unknown node id must not throw"
