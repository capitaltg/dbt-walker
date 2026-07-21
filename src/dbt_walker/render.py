"""Render a lineage subgraph to mermaid or graphviz dot (phase 3).

Stdlib-only. Nodes are styled by materialization / resource type so incrementals
(the ones that hurt on a full refresh) stand out, and sources/seeds read as
inputs. Mermaid output renders directly in GitHub markdown.
"""
from __future__ import annotations

import json
import re

from dbt_walker.graph import Graph

# node style buckets -> (mermaid class fill, dot fillcolor)
_STYLES = {
    "incremental": ("#f9a03f", "#f9a03f"),
    "table": ("#7bb0ff", "#7bb0ff"),
    "view": ("#cfe3ff", "#cfe3ff"),
    "source": ("#b5e6b5", "#b5e6b5"),
    "seed": ("#d9d2e9", "#d9d2e9"),
    "snapshot": ("#ffd966", "#ffd966"),
    "other": ("#e6e6e6", "#e6e6e6"),
}


def _bucket(graph: Graph, uid: str) -> str:
    rtype = graph.resource_type(uid)
    if rtype in ("source", "seed", "snapshot"):
        return rtype
    mat = graph.materialization(uid)
    return mat if mat in ("incremental", "table", "view") else "other"


def _sanitize(uid: str) -> str:
    return re.sub(r"\W", "_", uid)


def subgraph_edges(graph: Graph, nodes: set[str]) -> list[tuple[str, str]]:
    """Parent->child edges among the given node set, sorted for determinism."""
    edges = []
    for uid in nodes:
        for parent in graph.parents.get(uid, []):
            if parent in nodes:
                edges.append((parent, uid))
    return sorted(set(edges))


_MAX_COLS_SHOWN = 5


def _fmt_col(entry) -> str:
    """A column entry is either a bare name or a (name, transform) tuple; render
    it as 'name [transform]' when a transform kind is present."""
    if isinstance(entry, tuple):
        name, kind = entry
        return f"{name} [{kind}]" if kind else name
    return entry


def _kind(graph: Graph, uid: str) -> str:
    """source/seed/snapshot, or the model's materialization."""
    rtype = graph.resource_type(uid)
    return rtype if rtype in ("source", "seed", "snapshot") else graph.materialization(uid)


def _cols_lines(uid: str, columns: dict | None, bullet: str, sep: str) -> str:
    """A bulleted 'related columns' block for a node label, or ''."""
    if not columns or uid not in columns or not columns[uid]:
        return ""
    cols = list(columns[uid])
    shown = cols[:_MAX_COLS_SHOWN]
    extra = len(cols) - len(shown)
    items = [f"{bullet}{_fmt_col(e)}" for e in shown]
    if extra:
        items.append(f"{bullet}+{extra} more")
    return sep + sep.join(items)


def to_mermaid(graph: Graph, nodes: set[str], root: str | None = None,
               columns: dict | None = None, clickable: set | None = None) -> str:
    lines = ["graph LR"]
    used_buckets = set()
    for uid in sorted(nodes):
        bucket = _bucket(graph, uid)
        used_buckets.add(bucket)
        # HTML entity for the diamond: ASCII in the text stream (Windows-console
        # safe) but renders as a diamond in the browser
        marker = "&#9670; CHANGED HERE<br/>" if uid == root else ""
        cols = _cols_lines(uid, columns, "&#8226; ", "<br/>")
        cols = f"<br/><small>columns:{cols}</small>" if cols else ""
        label = (f"{marker}<b>{graph.label(uid)}</b><br/>"
                 f"<small>type: {_kind(graph, uid)}</small>{cols}")
        lines.append(f'    {_sanitize(uid)}["{label}"]:::{bucket}')
    for parent, child in subgraph_edges(graph, nodes):
        lines.append(f"    {_sanitize(parent)} --> {_sanitize(child)}")
    for bucket in sorted(used_buckets):
        fill = _STYLES[bucket][0]
        lines.append(f"    classDef {bucket} fill:{fill},stroke:#333,color:#000;")
    if root is not None and root in nodes:
        # thick red border on top of the materialization fill, so the focus node
        # is unmistakable
        lines.append("    classDef focus stroke:#d00,stroke-width:4px;")
        lines.append(f"    class {_sanitize(root)} focus;")
    if clickable:
        for uid in sorted(clickable & nodes):
            sid = _sanitize(uid)
            lines.append(f'    click {sid} call showSql("{sid}")')
    return "\n".join(lines) + "\n"


def to_dot(graph: Graph, nodes: set[str], root: str | None = None,
           columns: dict | None = None) -> str:
    lines = ["digraph lineage {", "    rankdir=LR;",
             '    node [shape=box, style="rounded,filled", fontname="Helvetica"];']
    for uid in sorted(nodes):
        bucket = _bucket(graph, uid)
        fill = _STYLES[bucket][1]
        marker = "CHANGED HERE\\n" if uid == root else ""
        label = f"{marker}{graph.label(uid)}\\ntype: {_kind(graph, uid)}"
        label += _cols_lines(uid, columns, "- ", "\\n")
        extra = ', penwidth=4, color="#d00"' if uid == root else ""
        lines.append(f'    "{uid}" [label="{label}", fillcolor="{fill}"{extra}];')
    for parent, child in subgraph_edges(graph, nodes):
        lines.append(f'    "{parent}" -> "{child}";')
    lines.append("}")
    return "\n".join(lines) + "\n"


# pinned mermaid major version, loaded from a CDN at view time (the HTML is not
# self-contained by design — see the graph command's docs)
_MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs"

# raw string so the JS backslashes (\b, \n) stay literal; tokens filled by replace()
_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; margin: 1.5rem; }
  header { color: #666; font-size: 0.9rem; margin-bottom: 0.6rem; }
  .hint { color: #888; font-size: 0.8rem; margin: 0.4rem 0 0.8rem; }
  #graph { height: 78vh; overflow: hidden; border: 1px solid #8883; border-radius: 6px;
           cursor: grab; }
  #graph.grabbing { cursor: grabbing; }
  .mermaid { margin: 0; }
  .mermaid svg { max-width: none !important; transform-origin: 0 0; }
  .mermaid .node { cursor: pointer; }
  .mermaid .node.selected > rect, .mermaid .node.selected > polygon,
  .mermaid .node.selected > path { stroke: #06c !important; stroke-width: 3px !important;
           filter: drop-shadow(0 0 6px #06c); }
  #drawer { position: fixed; top: 0; right: 0; height: 100%; width: min(46rem, 92vw);
            background: Canvas; color: CanvasText; border-left: 1px solid #8888;
            box-shadow: -4px 0 16px #0004; transform: translateX(100%);
            transition: transform 0.18s ease, width 0.18s ease; overflow: auto;
            padding: 1rem 1.2rem; box-sizing: border-box; }
  #drawer.open { transform: translateX(0); }
  #drawer.expanded { width: 96vw; }
  #drawer h2 { margin: 0.2rem 0; font-size: 1.1rem; }
  #drawer .cols { color: #888; font-size: 0.85rem; margin-bottom: 0.6rem; }
  #drawer .cols ul { margin: 0.3rem 0; padding-left: 1.2rem; }
  #drawer .cols em { font-style: normal; color: #06c; }
  .drawer-btns { float: right; }
  .drawer-btns button { cursor: pointer; border: none; background: none; color: inherit;
                        font-size: 1.2rem; margin-left: 0.3rem; }
  .toolbar { margin-bottom: 0.5rem; }
  .toolbar button { margin-right: 0.4rem; cursor: pointer; }
  .toolbar button.active { font-weight: bold; text-decoration: underline; }
  #sqlbox { background: #8881; padding: 0.6rem; border-radius: 6px; overflow: auto;
            font: 0.82rem/1.4 ui-monospace, Consolas, monospace; }
  #sqlbox span { display: block; padding: 0 0.3rem; white-space: pre; }
  #sqlbox span.hl { background: #ffd54f66; }
</style>
</head>
<body>
<header>
  <strong>__TITLE__</strong><br>
  generated __TIMESTAMP____SUBTITLE__
</header>
<p class="hint">Scroll to zoom &middot; drag to pan &middot; click a node for its SQL.</p>
<div id="graph"><pre class="mermaid">
__DIAGRAM__
</pre></div>

<aside id="drawer" aria-hidden="true">
  <span class="drawer-btns">
    <button id="drawer-expand" title="Expand / collapse">&#8596;</button>
    <button id="drawer-close" title="Close (Esc)">&times;</button>
  </span>
  <h2 id="drawer-title"></h2>
  <div class="cols" id="drawer-cols"></div>
  <div class="toolbar">
    <button data-mode="compiled" class="active">compiled</button>
    <button data-mode="raw">raw (jinja)</button>
  </div>
  <div id="sqlbox"></div>
</aside>

<script type="module">
  import mermaid from "__CDN__";
  const NODES = __SQLDATA__;   // { sanitizedId: {label, raw, compiled, cols:[[name,kind],...]} }
  const drawer = document.getElementById('drawer');
  const box = document.getElementById('sqlbox');
  const dtitle = document.getElementById('drawer-title');
  const dcols = document.getElementById('drawer-cols');
  let current = null, mode = 'compiled', nodeEls = {};

  const colName = c => Array.isArray(c) ? c[0] : c;
  const colKind = c => Array.isArray(c) ? c[1] : null;
  function escapeHtml(s){ return String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

  function renderCols(d){
    if(!d.cols || !d.cols.length){ dcols.innerHTML = ''; return; }
    const items = d.cols.map(c => {
      const k = colKind(c);
      return '<li>' + escapeHtml(colName(c)) + (k ? ' <em>[' + escapeHtml(k) + ']</em>' : '') + '</li>';
    }).join('');
    dcols.innerHTML = 'related columns:<ul>' + items + '</ul>';
  }
  function renderSql(){
    const d = NODES[current]; if(!d) return;
    const sql = d[mode] || '-- no ' + mode + ' SQL for this node --';
    const words = (d.cols||[]).map(c => String(colName(c)).replace(/[^0-9A-Za-z_]/g,'')).filter(Boolean);
    const re = words.length ? new RegExp('\\b(' + words.join('|') + ')\\b') : null;
    box.innerHTML = sql.split('\n').map(line => {
      const hl = re && re.test(line) ? ' class="hl"' : '';
      return '<span' + hl + '>' + (escapeHtml(line) || ' ') + '</span>';
    }).join('');
  }
  function selectNode(sid){
    for(const el of Object.values(nodeEls)) el.classList.remove('selected');
    if(nodeEls[sid]) nodeEls[sid].classList.add('selected');
  }
  window.showSql = function(sid){
    const d = NODES[sid]; if(!d) return;
    current = sid;
    dtitle.textContent = d.label;
    renderCols(d);
    renderSql();
    selectNode(sid);
    drawer.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
  };
  function closeDrawer(){ drawer.classList.remove('open'); drawer.setAttribute('aria-hidden','true');
                          for(const el of Object.values(nodeEls)) el.classList.remove('selected'); }
  document.getElementById('drawer-close').onclick = closeDrawer;
  document.getElementById('drawer-expand').onclick = () => drawer.classList.toggle('expanded');
  document.addEventListener('keydown', e => { if(e.key === 'Escape') closeDrawer(); });
  for(const btn of document.querySelectorAll('[data-mode]')){
    btn.onclick = () => { mode = btn.dataset.mode;
      for(const b of document.querySelectorAll('[data-mode]')) b.classList.toggle('active', b === btn);
      renderSql(); };
  }

  mermaid.initialize({ startOnLoad: false, securityLevel: 'loose', maxEdges: 5000,
                       theme: matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'default' });
  await mermaid.run();

  // map sanitized ids -> rendered node elements (for the selected-node highlight)
  const graphEl = document.getElementById('graph');
  const svg = graphEl.querySelector('svg');
  for(const sid of Object.keys(NODES)){
    const el = graphEl.querySelector('.node[id^="flowchart-' + sid + '-"]');
    if(el) nodeEls[sid] = el;
  }

  // pan (drag) + zoom (wheel) on the SVG
  let scale = 1, tx = 0, ty = 0, dragging = false, moved = false, sx = 0, sy = 0;
  const applyView = () => { if(svg) svg.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')'; };
  if(svg){
    graphEl.addEventListener('wheel', e => { e.preventDefault();
      const r = graphEl.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
      const f = e.deltaY < 0 ? 1.1 : 1/1.1;
      tx = mx - (mx - tx)*f; ty = my - (my - ty)*f; scale *= f; applyView();
    }, {passive:false});
    graphEl.addEventListener('mousedown', e => { dragging = true; moved = false;
      sx = e.clientX - tx; sy = e.clientY - ty; graphEl.classList.add('grabbing'); });
    window.addEventListener('mousemove', e => { if(!dragging) return;
      tx = e.clientX - sx; ty = e.clientY - sy; moved = true; applyView(); });
    window.addEventListener('mouseup', () => { dragging = false; graphEl.classList.remove('grabbing'); });
    // a drag that ends on a node shouldn't also open that node's SQL
    graphEl.addEventListener('click', e => { if(moved){ e.stopPropagation(); e.preventDefault(); moved = false; } }, true);
    applyView();
  }
</script>
</body>
</html>
"""


def to_html(graph: Graph, nodes: set[str], title: str, timestamp: str,
            subtitle: str = "", root: str | None = None, columns: dict | None = None,
            sql: dict | None = None) -> str:
    """An HTML page with the mermaid diagram; mermaid.js loads from a CDN at view
    time. When ``sql`` (``{uid: {label, raw, compiled, cols}}``) is given, nodes
    become clickable and open a drawer with that model's SQL."""
    sql = sql or {}
    clickable = set(sql)
    diagram = to_mermaid(graph, nodes, root=root, columns=columns, clickable=clickable)
    data = {_sanitize(uid): entry for uid, entry in sql.items()}
    data_json = json.dumps(data).replace("</", "<\\/")  # keep </script> from closing the tag
    sub = f"<br>{subtitle}" if subtitle else ""
    return (
        _HTML_TEMPLATE.replace("__TITLE__", _escape_html(title))
        .replace("__TIMESTAMP__", _escape_html(timestamp))
        .replace("__SUBTITLE__", sub)
        .replace("__DIAGRAM__", diagram)
        .replace("__CDN__", _MERMAID_CDN)
        .replace("__SQLDATA__", data_json)
    )


def _escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(graph: Graph, nodes: set[str], fmt: str, root: str | None = None,
           columns: dict | None = None) -> str:
    if fmt == "mermaid":
        return to_mermaid(graph, nodes, root=root, columns=columns)
    if fmt == "dot":
        return to_dot(graph, nodes, root=root, columns=columns)
    raise ValueError(f"unknown format {fmt!r} (expected mermaid or dot)")
