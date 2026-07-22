"""HTML/CSS/JS for the static lineage app.

Kept as a raw-string template with token replacement (the JS is brace-heavy, so
str.format would be a minefield). The JS mirrors the Python traversal in
``graph.py`` / ``columns.py`` &mdash; see ``tests/test_app_parity.py``, which runs
this very JS under node and asserts it agrees with Python.
"""
from __future__ import annotations

import json


def _embed(obj) -> str:
    """JSON for a <script> block: `</script>` inside SQL must not close it."""
    return json.dumps(obj, separators=(",", ":")).replace("</", "<\\/")


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{
  --ground:#f6f7f9; --panel:#fff; --ink:#1b1f27; --muted:#66707f; --line:#d6dbe3;
  --accent:#2f6f83; --changed:#ef2d56; --inspect:#22d3ee; --warn:#b5741f; --ok:#3f7d4e;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root{
  --ground:#14161b; --panel:#1c1f26; --ink:#e7eaf0; --muted:#8b95a6; --line:#2c313b;
  --accent:#5bb3c9; --changed:#ef2d56; --inspect:#22d3ee; --warn:#d8a24a; --ok:#6bbf7f;}}
:root[data-theme=dark]{--ground:#14161b;--panel:#1c1f26;--ink:#e7eaf0;--muted:#8b95a6;
  --line:#2c313b;--accent:#5bb3c9;--changed:#ef2d56; --inspect:#22d3ee;--warn:#d8a24a;--ok:#6bbf7f;}
:root[data-theme=light]{--ground:#f6f7f9;--panel:#fff;--ink:#1b1f27;--muted:#66707f;
  --line:#d6dbe3;--accent:#2f6f83;--changed:#ef2d56; --inspect:#22d3ee;--warn:#b5741f;--ok:#3f7d4e;}

*{box-sizing:border-box}
html,body{height:100%}
html{font-size:17.5px}   /* base for all rem sizes -- nudged up for legibility */
body{margin:0;background:var(--ground);color:var(--ink);font-family:var(--sans);
  font-size:1rem;line-height:1.5;display:flex;flex-direction:column;overflow:hidden}

/* ---------- header + controls ---------- */
header.top{padding:.45rem .9rem;border-bottom:1px solid var(--line);background:var(--panel);
  display:flex;align-items:center;gap:.7rem}
header.top .top-title{display:flex;align-items:baseline;gap:.7rem;flex-wrap:wrap;min-width:0}
header.top h1{font-size:.95rem;margin:0;letter-spacing:-.01em}
header.top .meta{font-family:var(--mono);font-size:.75rem;color:var(--muted)}

/* left control rail: the "what am I asking" controls, stacked, above the model list */
.rail{display:flex;flex-direction:column;gap:.6rem;padding:.6rem .6rem .7rem;
  border-bottom:1px solid var(--line)}
.rail .ctl{flex-direction:column;align-items:stretch;gap:.25rem}
.rail .ctl select{width:100%}
.rail .switch{margin-top:.1rem}
.seg.vert{flex-direction:column;overflow:visible}   /* don't clip the button tooltips */
.seg.vert button{text-align:left;padding:.3rem .55rem;border-bottom:1px solid var(--line)}
.seg.vert button:first-child{border-radius:6px 6px 0 0}
.seg.vert button:last-child{border-bottom:0;border-radius:0 0 6px 6px}
/* rail tooltips open to the RIGHT (into the graph) so they aren't clipped by the
   narrow sidebar and don't cover the control stacked below */
.rail [data-tip]:hover::after,.rail [data-tip]:focus-visible::after{
  left:calc(100% + 10px);top:0;max-width:17rem}
.tree-section{display:flex;flex-direction:column;min-height:0;flex:1}
.tree-filter{padding:.4rem .6rem 0}

/* thin control strip above the graph: the growing column chips + the type filter */
.graph-toolbar{display:flex;align-items:center;gap:.9rem;padding:.4rem .7rem;
  border-bottom:1px solid var(--line);background:var(--panel);flex-wrap:wrap}
.graph-toolbar .gb-cols{flex:1;min-width:0}
.graph-toolbar .gb-show{flex:none}
/* graph area below the toolbar: the positioning context for the legend/note overlays */
.graph-area{position:relative;flex:1;min-height:0;display:flex;flex-direction:column}
.banner{background:color-mix(in srgb,var(--warn) 18%,transparent);color:var(--ink);
  border-bottom:1px solid var(--line);padding:.4rem .9rem;font-size:.8rem}
.banner b{color:var(--warn)}
.controls{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;padding:.5rem .9rem;
  border-bottom:1px solid var(--line);background:var(--panel)}
.ctl{display:flex;align-items:center;gap:.35rem}
.ctl > label{font-family:var(--mono);font-size:.7rem;letter-spacing:.07em;
  text-transform:uppercase;color:var(--muted)}
select,input[type=search]{font:inherit;font-size:.85rem;background:var(--ground);color:var(--ink);
  border:1px solid var(--line);border-radius:6px;padding:.25rem .4rem}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:6px;overflow:hidden}
.seg button{font:inherit;font-size:.82rem;padding:.25rem .6rem;border:0;background:transparent;
  color:var(--muted);cursor:pointer}
.seg button[aria-pressed=true]{background:var(--accent);color:#fff;font-weight:600}
.chips{display:flex;gap:.3rem;flex-wrap:wrap;align-items:center}
.chip{display:inline-flex;align-items:center;gap:.3rem;font-family:var(--mono);font-size:.72rem;
  background:color-mix(in srgb,var(--accent) 14%,transparent);color:var(--ink);
  border:1px solid color-mix(in srgb,var(--accent) 40%,transparent);
  border-radius:99px;padding:.1rem .2rem .1rem .5rem}
.chip button{border:0;background:none;color:var(--muted);cursor:pointer;font-size:.9rem;
  line-height:1;padding:0 .25rem}
/* column picker trigger + two-pane popover */
.colbtn{font:inherit;font-size:.82rem;padding:.25rem .6rem;border:1px solid var(--line);
  background:var(--ground);color:var(--ink);border-radius:6px;cursor:pointer}
.colbtn:hover:not(:disabled){border-color:var(--accent)}
.colbtn:disabled{color:var(--muted);cursor:default}
.colbtn[aria-expanded=true]{border-color:var(--accent);background:color-mix(in srgb,var(--accent) 12%,transparent)}
.colpop[hidden]{display:none}
/* width fits the widest column name (max-content), capped to the graph pane by
   JS; drag the bottom-right grip to resize both ways */
.colpop{position:fixed;z-index:120;min-width:20rem;width:max-content;max-width:90vw;
  height:26rem;max-height:80vh;
  background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:9px;
  box-shadow:0 12px 34px rgba(0,0,0,.3);padding:.6rem .7rem;
  display:flex;flex-direction:column;resize:both;overflow:hidden}
.colpop-head{display:flex;align-items:center;gap:.5rem;font-size:.9rem;margin-bottom:.4rem}
.colpop-head #colPopModel{font-family:var(--mono);font-size:.78rem;color:var(--muted)}
.colpop-head #colPopClose{margin-left:auto;border:0;background:none;color:var(--muted);
  font-size:1.2rem;line-height:1;cursor:pointer}
#colPopFilter{width:100%;margin-bottom:.5rem}
/* auto tracks so each pane sizes to its widest column name; the popover's
   max-width (JS-clamped to the graph pane) makes overlong names scroll instead */
.colpop-panes{display:grid;grid-template-columns:auto auto;gap:.6rem;flex:1 1 auto;min-height:0}
.colpop-pane{min-width:0;display:flex;flex-direction:column;min-height:0}
.colpop-label{font-family:var(--mono);font-size:.62rem;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted);margin-bottom:.25rem}
.colpop-list{min-height:9rem;flex:1 1 auto;overflow:auto;border:1px solid var(--line);
  border-radius:6px;padding:.2rem}
.colpop-item{font-family:var(--mono);font-size:.85rem;padding:.2rem .4rem;border-radius:4px;cursor:pointer;
  display:flex;align-items:center;gap:.4rem}
.colpop-item:hover{background:color-mix(in srgb,var(--accent) 12%,transparent)}
.colpop-item .nm{flex:1;white-space:nowrap}   /* no truncation: the widest name sets the width */
.colpop-item .mk{flex:none;color:var(--muted);font-size:.9rem}
.colpop-item.unproven{color:var(--muted)}
.colpop-empty{color:var(--muted);font-size:.78rem;padding:.5rem .4rem}
.switch{display:inline-flex;align-items:center;gap:.3rem;font-size:.78rem;color:var(--ink)}
[data-tip]{position:relative}
[data-tip]:hover::after,[data-tip]:focus-visible::after{content:attr(data-tip);position:absolute;
  left:0;top:calc(100% + 6px);z-index:50;width:max-content;max-width:19rem;white-space:normal;
  pointer-events:none;   /* a tooltip must never intercept a click on the control beneath it */
  background:var(--ink);color:var(--ground);font-family:var(--sans);font-size:.72rem;
  line-height:1.35;padding:.4rem .55rem;border-radius:6px;box-shadow:0 4px 14px rgba(0,0,0,.25)}
.info{display:inline-flex;align-items:center;justify-content:center;width:1rem;height:1rem;
  border-radius:50%;border:1px solid var(--line);color:var(--muted);font-size:.62rem;cursor:help}
.info[hidden]{display:none}   /* explicit display would otherwise beat the [hidden] attribute */
#helpBtn{margin-left:auto;font:inherit;font-size:.8rem;font-weight:700;width:1.6rem;height:1.6rem;
  border-radius:50%;border:1px solid var(--line);background:var(--ground);color:var(--accent);
  cursor:pointer}
#helpBtn:hover{background:var(--accent);color:#fff}
#help[hidden]{display:none}
#help{position:fixed;inset:0;z-index:100;background:rgba(0,0,0,.45);display:flex;
  align-items:center;justify-content:center;padding:2rem}
#help .card{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:10px;
  max-width:46rem;width:100%;max-height:86vh;overflow:auto;padding:1.2rem 1.4rem;
  box-shadow:0 12px 40px rgba(0,0,0,.35)}
#help h2{margin:.2rem 0 .1rem;font-size:1.05rem}
#help h3{margin:1.1rem 0 .2rem;font-size:.86rem;color:var(--accent);font-family:var(--mono);
  letter-spacing:.04em;text-transform:uppercase}
#help p{margin:.25rem 0;font-size:.85rem}
#help dl{margin:.2rem 0}
#help dt{font-family:var(--mono);font-size:.8rem;font-weight:700;margin-top:.5rem}
#help dd{margin:.1rem 0 0 0;font-size:.84rem;color:var(--ink)}
#help .q{color:var(--muted);font-style:italic}
#help code{font-family:var(--mono);font-size:.78rem;background:var(--ground);padding:.05rem .25rem;
  border-radius:3px}
#help .close{float:right;border:0;background:none;color:var(--muted);font-size:1.3rem;cursor:pointer}

/* ---------- 3-pane body ---------- */
.body{flex:1;display:grid;min-height:0;
  grid-template-columns:var(--tree-w,210px) 1fr var(--detail-w,440px)}
/* the left pane holds the always-visible control rail, so collapsing the model
   list keeps the column (just folds the list); only detail can zero its column */
.body.no-detail{grid-template-columns:var(--tree-w,210px) 1fr 0}
.pane{min-width:0;min-height:0;display:flex;flex-direction:column;background:var(--panel);
  overflow:hidden}
/* pin each pane to its grid column: hiding one with display:none must NOT let the
   others shuffle left into the wrong (0-width) column */
/* overflow:visible so rail tooltips can spill into the graph; the model list
   still scrolls inside its own overflow:auto body, so nothing else escapes */
.pane.rail-pane{border-right:1px solid var(--line);grid-column:1;position:relative;overflow:visible;z-index:2}
.pane.graph{grid-column:2}
.pane.detail{border-left:1px solid var(--line);position:relative;grid-column:3}
/* drag the pane edges to resize */
.splitter{position:absolute;top:0;width:7px;height:100%;cursor:col-resize;z-index:6}
.splitter:hover,.splitter.active{background:color-mix(in srgb,var(--accent) 45%,transparent)}
.pane.detail .splitter{left:-4px}
.pane.rail-pane .splitter{right:-4px}
.body.no-detail .pane.detail{display:none}
/* fold just the model list (search + tree), keeping the rail + Models header */
.body.no-tree #treeSearch,.body.no-tree #treeBody{display:none}
.body.no-tree #treeHead .collapse{transform:rotate(180deg)}
.pane-head{display:flex;align-items:center;justify-content:space-between;gap:.4rem;
  padding:.45rem .6rem;border-bottom:1px solid var(--line)}
.pane-head h2{font-family:var(--mono);font-size:.65rem;letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted);margin:0;font-weight:600}
/* whole header is the collapse target */
.pane-head.clickable{cursor:pointer;user-select:none}
.pane-head.clickable:hover{background:color-mix(in srgb,var(--accent) 9%,transparent)}
/* the Detail header reads large so the pane is easy to find and collapse */
#detailHead{padding:.6rem .7rem}
#detailHead h2{font-size:1.15rem;letter-spacing:.04em;color:var(--ink)}
.collapse{border:0;background:none;color:var(--muted);cursor:pointer;font-size:.85rem;padding:0 .2rem}
#detailHead .collapse{font-size:1.15rem}
.pane-body{flex:1;overflow:auto;padding:.5rem .6rem}
.empty{color:var(--muted);font-size:.82rem;padding:.9rem .3rem;line-height:1.5}
.empty b{color:var(--ink);font-weight:600}

/* tree */
.folder{font-family:var(--mono);font-size:.72rem;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted);cursor:pointer;padding:.25rem .1rem;user-select:none;display:flex;gap:.3rem}
.folder .caret{transition:transform .12s ease;display:inline-block}
.folder.closed .caret{transform:rotate(-90deg)}
.tree-item{font-family:var(--mono);font-size:.84rem;padding:.14rem .35rem;border-radius:4px;
  cursor:pointer;display:flex;align-items:center;gap:.35rem;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.tree-item:hover{background:color-mix(in srgb,var(--accent) 10%,transparent)}
.tree-item[aria-current=true]{background:color-mix(in srgb,var(--accent) 20%,transparent);
  font-weight:600}
.dot{width:.5rem;height:.5rem;border-radius:50%;flex:none}
.dot.view{background:#8fb8d6}.dot.table{background:#5b8fc7}.dot.incremental{background:var(--warn)}
.dot.source{background:#7fb08a}.dot.seed{background:#a99cc4}.dot.snapshot{background:#d3b04a}
/* subtle type-colour stripe on tree items (inset shadow -> no layout shift) */
.tree-item.t-view{box-shadow:inset 3px 0 #8fb8d6}
.tree-item.t-table{box-shadow:inset 3px 0 #5b8fc7}
.tree-item.t-incremental{box-shadow:inset 3px 0 var(--warn)}
.tree-item.t-source{box-shadow:inset 3px 0 #7fb08a}
.tree-item.t-seed{box-shadow:inset 3px 0 #a99cc4}
.tree-item.t-snapshot{box-shadow:inset 3px 0 #d3b04a}

/* graph */
.pane.graph{background:var(--ground)}
#graph{flex:1;overflow:hidden;position:relative;cursor:grab}
#graph.grabbing{cursor:grabbing}
#graph svg{max-width:none!important;transform-origin:0 0}
#graph .node{cursor:pointer}
#graph .node rect{rx:6px;ry:6px}   /* slightly rounded node corners */
/* INSPECTING (clicked) node: light-blue ring + glow, distinct from the crimson
   TARGET outline. The stroke colour itself is set in markSelected -- mermaid
   inlines each node's stroke with !important, which external CSS can't beat. */
#graph .node.selected>rect,#graph .node.selected>polygon,#graph .node.selected>path{
  stroke-width:4px!important;
  filter:drop-shadow(0 0 6px rgba(34,211,238,.55))}
/* TARGET node has a slight crimson glow -- but NOT while it's also being
   inspected, so the crimson border + glow don't read as a doubled red border;
   there the light-blue selection ring is the only glow. */
#graph .node.focus:not(.selected)>rect{filter:drop-shadow(0 0 6px rgba(239,45,86,.5))}
#graph .node.focus.selected>rect{filter:none}
/* svg-injected in-node "make target" hint */
#graph .selhint{font:italic 9.5px var(--sans);fill:var(--muted)}
/* Left-justify node labels (mermaid centres them). Layout that changes WIDTH is
   set inline in the label instead, so mermaid measures it and nothing clips. */
#graph .node foreignObject div,#graph .node .nodeLabel,#graph .node .label{
  text-align:left!important;line-height:1.4}
.graph-note{padding:.4rem .7rem;font-size:.8rem;color:var(--muted);border-top:1px solid var(--line);
  background:var(--panel);display:flex;gap:.8rem;flex-wrap:wrap;align-items:center}
/* collapsible legend, parked top-right of the graph pane */
.pane.graph{position:relative}
/* click-through so nodes behind the legend stay clickable; only its controls grab clicks */
#legend{position:absolute;top:12px;right:14px;z-index:9;background:var(--panel);color:var(--ink);
  border:1px solid var(--line);border-radius:9px;box-shadow:0 5px 18px rgba(0,0,0,.35);
  font-size:.92rem;pointer-events:none}
#legend .lg-toggle,#legend .pill{pointer-events:auto}
#legend .card{padding:.6rem .9rem;min-width:15.5rem}
#legend .lg-head{display:flex;align-items:center;margin-bottom:.3rem}
#legend .lg-title{font:600 .72rem var(--mono);letter-spacing:.08em;color:var(--muted)}
#legend .lg-toggle{margin-left:auto;border:0;background:none;color:var(--muted);cursor:pointer;
  font-size:.85rem;padding:0 .1rem}
#legend .lg-row{display:flex;align-items:center;gap:.5rem;padding:.12rem 0}
#legend .lg-row i{width:1rem;height:1rem;border-radius:3px;flex:none}
#legend .lg-sep{border-top:1px solid var(--line);margin:.4rem 0 .35rem}
#legend .lg-hint{color:var(--muted);margin-top:.35rem;font-size:.8rem}
#legend .pill{display:flex;gap:.45rem;align-items:center;padding:.25rem .75rem;cursor:pointer;
  border-radius:99px;font-size:.8rem}
#legend .pill i{width:.7rem;height:.7rem;border-radius:2px}
/* tall reveal tabs for collapsed side panes */
#showTree,#showDetail{position:fixed;top:50%;transform:translateY(-50%);z-index:30;width:22px;
  height:120px;display:none;align-items:center;justify-content:center;background:var(--panel);
  border:1px solid var(--line);color:var(--muted);font-size:1rem;cursor:pointer;padding:0}
#showTree{left:0;border-left:0;border-radius:0 10px 10px 0}
#showDetail{right:0;border-right:0;border-radius:10px 0 0 10px}
#showTree:hover,#showDetail:hover{color:var(--accent);
  background:color-mix(in srgb,var(--accent) 10%,transparent)}
/* Detail header lights up light-blue while a node is being inspected */
#detailHead.inspecting{border-bottom:2px solid var(--inspect);
  box-shadow:0 5px 14px -8px var(--inspect)}
#detailHead .insp-badge{background:var(--inspect);color:#14161b;font-size:.62em;font-weight:700;
  padding:.08rem .5rem;border-radius:3px;letter-spacing:.04em}
#detailHead .insp-name{font-family:var(--mono);font-size:.75em;font-weight:400}

/* two themed sections: TARGET DETAIL (the plan, red) over INSPECTING (the SQL, blue) */
.det-sec{border-left:3px solid transparent;padding:.1rem .1rem .1rem .55rem;margin-bottom:1rem}
.det-shead{display:flex;align-items:center;gap:.45rem;font-family:var(--mono);font-size:.72rem;
  letter-spacing:.06em;margin:.1rem 0 .5rem;color:var(--muted);cursor:pointer;user-select:none}
.det-shead:hover{color:var(--ink)}
.det-caret{color:var(--muted);font-size:.7em;flex:none;transition:transform .12s ease}
.det-sec.collapsed .det-caret{transform:rotate(-90deg)}
.det-sec.collapsed .det-shead{margin-bottom:.1rem}
.det-sec.collapsed .det-body{display:none}
.det-badge{font-size:.62rem;font-weight:700;padding:.1rem .5rem;border-radius:3px;letter-spacing:.04em}
.det-shead #targetName,.det-shead #inspectName{font-family:var(--mono);font-size:.9em}
.det-target{border-left-color:var(--changed)}
.det-target .det-badge{background:color-mix(in srgb,var(--changed) 20%,transparent);color:var(--changed)}
.det-inspect{border-left-color:color-mix(in srgb,var(--inspect) 45%,transparent)}
.det-inspect .det-badge{background:color-mix(in srgb,var(--inspect) 22%,transparent);
  color:color-mix(in srgb,var(--inspect) 88%,var(--ink))}
/* glow the inspect section while a node is actually being inspected */
.det-inspect.on{border-left-color:var(--inspect)}
.det-inspect.on .det-badge{background:var(--inspect);color:#14161b}

/* collapsible per-column groups (Columns mode) */
.colgroup{border:1px solid var(--line);border-radius:7px;margin:.4rem 0;overflow:hidden}
.colgroup-head{display:flex;align-items:center;gap:.4rem;padding:.4rem .55rem;cursor:pointer;
  font-family:var(--mono);font-size:.92rem;background:color-mix(in srgb,var(--ink) 4%,transparent)}
.colgroup-head:hover{background:color-mix(in srgb,var(--ink) 8%,transparent)}
.colgroup-head .chev{color:var(--muted);width:.9em;flex:none}
.colgroup-sub{margin-left:auto;font-size:.75rem;color:var(--muted)}
.colgroup-body{padding:.15rem .55rem .45rem}

/* results + sql */
.section-label{font-family:var(--mono);font-size:.8rem;letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted);margin:.8rem 0 .35rem}
.section-label:first-child{margin-top:0}
.summary{font-size:1rem;margin:.15rem 0 .45rem}
.row{display:flex;align-items:center;gap:.4rem;font-family:var(--mono);font-size:.92rem;
  padding:.14rem 0;cursor:pointer;border-radius:4px}
.row:hover{background:color-mix(in srgb,var(--accent) 9%,transparent)}
.pill{font-family:var(--mono);font-size:.6rem;padding:.05rem .35rem;border-radius:99px;flex:none}
.pill.warn{background:color-mix(in srgb,var(--warn) 22%,transparent);color:var(--warn)}
.pill.ok{background:color-mix(in srgb,var(--ok) 20%,transparent);color:var(--ok)}
.pill.unknown{background:color-mix(in srgb,var(--muted) 22%,transparent);color:var(--muted)}
/* drop-list position tags: upstream feeds it, target is the change, downstream reads it */
.pill.pos-upstream{background:color-mix(in srgb,#a78bfa 24%,transparent);color:#a78bfa}
.pill.pos-target{background:color-mix(in srgb,var(--changed) 20%,transparent);color:var(--changed)}
.pill.pos-downstream{background:color-mix(in srgb,var(--warn) 22%,transparent);color:var(--warn)}
/* drop list grouped under ONE position heading, its DROP statements underneath */
.drop-group{margin:.35rem 0 .7rem}
.drop-ghead{display:flex;align-items:center;gap:.5rem;margin:.35rem 0 .15rem}
.drop-ghead .pill{font-size:.68rem;padding:.14rem .55rem;text-transform:uppercase;letter-spacing:.05em}
.drop-count{font-family:var(--mono);font-size:.72rem;color:var(--muted)}
pre.cmd{font-family:var(--mono);font-size:.78rem;background:var(--ground);border:1px solid var(--line);
  border-radius:6px;padding:.4rem .5rem;overflow-x:auto;margin:.25rem 0;white-space:pre-wrap;
  overflow-wrap:break-word;word-break:normal}
/* drag the bottom-right corner to resize the SQL view */
/* position:relative so a highlighted line's offsetTop is measured against the box */
.sqlbox{background:var(--ground);border:1px solid var(--line);border-radius:6px;overflow:auto;
  font:.8rem/1.5 var(--mono);height:38vh;resize:vertical;min-height:6rem;position:relative}
.sqlbox span{display:block;padding:0 .4rem;white-space:pre}
.sqlbox span.hl{background:color-mix(in srgb,var(--warn) 26%,transparent)}
/* fail-closed inclusion, NOT a proven derivation -- hatched so it never reads
   like the solid "we traced this" highlight */
.sqlbox span.hl.unproven{background:repeating-linear-gradient(45deg,
  color-mix(in srgb,var(--muted) 26%,transparent) 0 5px,
  transparent 5px 11px)}
/* line-number gutter: user-select:none so copying the SQL doesn't drag the numbers along */
.sqlbox i.ln{display:inline-block;width:var(--gutter,3ch);margin-right:.75rem;text-align:right;
  color:var(--muted);opacity:.5;font-style:normal;user-select:none;-webkit-user-select:none}
.sqlbox span.hl i.ln{opacity:.9}
.tabs{display:inline-flex;gap:.25rem;margin-bottom:.35rem}
.tabs button{font:inherit;font-size:.72rem;padding:.15rem .45rem;border:1px solid var(--line);
  background:var(--ground);color:var(--muted);border-radius:5px;cursor:pointer}
.tabs button[aria-pressed=true]{background:var(--panel);color:var(--ink);font-weight:600;
  border-color:var(--accent)}
/* promote the inspected node to the focus model */
.focusbtn{font:inherit;font-size:.68rem;text-transform:none;letter-spacing:0;font-weight:600;
  padding:.1rem .5rem;border:1px solid color-mix(in srgb,var(--accent) 45%,transparent);
  background:color-mix(in srgb,var(--accent) 14%,transparent);color:var(--accent);
  border-radius:99px;cursor:pointer;white-space:nowrap}
.focusbtn:hover{background:var(--accent);color:#fff}
ul.cols{margin:.2rem 0;padding-left:1.1rem;font-family:var(--mono);font-size:.92rem}
ul.cols em{font-style:normal;color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
</head>
<body>
<header class="top">
  <div class="top-title">
    <h1>__PROJECT__ <span style="color:var(--muted);font-weight:400">· dbt lineage explorer</span></h1>
    <span class="meta" id="provenance"></span>
  </div>
  <button id="helpBtn" title="What do these controls do?" aria-label="Help">?</button>
</header>
<div id="staleBanner" class="banner" hidden></div>

<div id="colPop" class="colpop" hidden>
  <div class="colpop-head"><b>Columns</b> <span id="colPopModel"></span>
    <button id="colPopClose" title="Close">&times;</button></div>
  <input id="colPopFilter" type="search" placeholder="filter columns&hellip;">
  <div class="colpop-panes">
    <div class="colpop-pane"><div class="colpop-label">Available &mdash; click to add</div>
      <div id="colAvail" class="colpop-list"></div></div>
    <div class="colpop-pane"><div class="colpop-label">Selected &mdash; click to remove</div>
      <div id="colSel" class="colpop-list"></div></div>
  </div>
</div>

<div id="help" hidden role="dialog" aria-modal="true" aria-label="Help">
  <div class="card">
    <button class="close" id="helpClose" title="Close (Esc)">&times;</button>
    <h2>What am I looking at?</h2>
    <p>Pick the model you're about to change. Everything else answers a question about it.</p>

    <h3>The three modes</h3>
    <dl>
      <dt>Lineage</dt>
      <dd><span class="q">"What's connected to this model?"</span> Just the map: what feeds it,
          what reads it. No advice, no judgement &mdash; use it to get your bearings.</dd>
      <dt>Impact</dt>
      <dd><span class="q">"I'm changing this &mdash; which models do I drop now?"</span> Gives you
          one ordered <b>drop list</b> of every incremental on the change's lineage (see below),
          plus the dbt commands and what just rebuilds for free.</dd>
      <dt>Columns</dt>
      <dd><span class="q">"Where does this column come from, and what's built on it?"</span> Traces
          one column back to its source and forward to everything derived from it.</dd>
    </dl>
    <p><b>Impact vs Columns in one line:</b> Impact tells you <i>what to rebuild</i>;
       Columns tells you <i>where data comes from</i>. Adding a column in Impact narrows the
       blast radius; adding one in Columns traces its lineage.</p>

    <h3>Materialization &mdash; why it decides everything</h3>
    <dl>
      <dt>view</dt><dd>No data stored; recomputed whenever queried. Rebuilding is free.</dd>
      <dt>table</dt><dd>Dropped and recreated on every run. Rebuilding just costs compute.</dd>
      <dt>incremental</dt>
      <dd>After the first build, only <i>new</i> rows get added. So rows already in the table were
          built with the <b>old</b> logic, and a normal run won't fix them &mdash; these are the ones
          that need a full refresh. This is the whole reason the tool exists.</dd>
    </dl>

    <h3>Additive change</h3>
    <p>Tick this only when you're <b>adding new columns</b> &mdash; not renaming, dropping, or
       changing types. A downstream incremental configured with <code>append_new_columns</code> or
       <code>sync_all_columns</code> can add a brand-new column on its own next run, so ticking it
       moves that model out of the drop list into an <b>absorbs schema change</b> bucket.</p>
    <p>But those existing rows get <b>NULL</b> for the new column &mdash; so if the value is
       derivable and you need the history filled in (a backfill), drop it anyway. Leave additive off
       for anything else: a rename or type change <b>cannot</b> be absorbed, because the existing rows
       were computed the old way.</p>

    <h3>"unproven" columns</h3>
    <p>Some SQL can't be traced column-by-column: an unqualified column read across a join,
       <code>select *</code> over a join, dynamic macros, a Python model. Rather than assume those
       columns are safe, they're kept in the blast radius and labelled <b>unproven</b> &mdash; shown
       with a <b>?</b> after the name and a hatched (not solid) highlight in the SQL.</p>
    <p>Read it as <i>"we couldn't tell, so it's included"</i>, not <i>"this is affected"</i>. Your
       refresh plan is still safe; it may just be wider than it strictly needs to be.</p>

    <h3>The drop list</h3>
    <p>Impact answers one question: <i>"the work is done &mdash; which models do I drop now?"</i> It lists
       every <b>incremental</b> on the change's lineage &mdash; <b>upstream</b> (feeds it), the
       <b>target</b>, and <b>downstream</b> (reads it) &mdash; in the order to drop them. A dropped
       incremental is rebuilt in full by the next scheduled run; views and tables rebuild for free and
       aren't listed. Drop the ones you changed, or whose stored history you don't trust.</p>
    <p>With <b>additive</b> on, an append/sync incremental that only needs the new column <i>added</i>
       moves to its own bucket (its next normal run adds it) &mdash; but existing rows get NULL, so
       full-refresh it anyway if you need the history backfilled.</p>

    <h3>The other controls</h3>
    <dl>
      <dt>Columns</dt>
      <dd>Opens a picker: click a column on the left to add it, click one on the right to remove it.
          Leave empty to mean "I'm changing the whole model". Pick several to ask "what breaks if I
          change <i>any</i> of these".</dd>
      <dt>Direction</dt>
      <dd>Which way to walk from the model. In Impact it filters the drop list to upstream, downstream,
          or both.</dd>
      <dt>Show</dt>
      <dd>Filter the graph to one materialization, e.g. incrementals only to see just the
          expensive part.</dd>
    </dl>

    <h3>Reading the graph</h3>
    <p>The crimson <b>TARGET</b> box is the model you're changing; the light-blue
       <b>INSPECTING</b> ring marks the node whose SQL is open in the Detail pane. In Impact,
       an amber <b>DROP</b> tag marks every node on the drop list. Click any node to inspect it
       &mdash; its <b>direct arrows light up</b> (what feeds it, what it feeds) so the local wiring
       reads at a glance; <b>Ctrl+click</b> (or double-click) makes it the target. Node colours mean
       materialization &mdash; see the legend in the graph's top-right corner. Scroll to zoom, drag
       to pan, and drag the pane edges to resize.</p>

    <h3>The Detail pane, two halves</h3>
    <p>The red <b>TARGET DETAIL</b> section always answers about the model you picked &mdash; the
       drop list, the plan, a column's trace. The blue <b>INSPECTING</b> section below it shows the
       SQL of whatever node you last clicked, with the lines that <i>produce</i> the relevant columns
       highlighted (solid = proven, hatched = unproven). Click around the graph and only the
       INSPECTING half changes; the plan stays put.</p>
    <p class="q">A model whose SQL couldn't be fully analyzed is included conservatively and marked
       &mdash; it's never assumed safe.</p>
  </div>
</div>

<div class="body" id="body">
  <aside class="pane rail-pane">
    <div class="splitter" id="splitTree" title="Drag to resize"></div>
    <div class="rail">
      <div class="ctl">
        <label for="modelPick">Model</label>
        <select id="modelPick" data-tip="The model you're changing. Everything else follows from this."></select>
      </div>
      <div class="ctl">
        <label>Mode</label>
        <div class="seg vert" id="modeSeg">
          <button data-mode="lineage" aria-pressed="true" data-tip="What this model reads from and what reads it.">Lineage</button>
          <button data-mode="impact" aria-pressed="false" data-tip="If I change this, what might need a full refresh?">Impact</button>
          <button data-mode="columns" aria-pressed="false" data-tip="Where a column comes from and what derives from it.">Columns</button>
        </div>
      </div>
      <div class="ctl">
        <label for="dirPick">Direction</label>
        <select id="dirPick" data-tip="Which way to walk from the model. In Impact it filters the drop list to upstream, the target, downstream, or all.">
          <option value="both">both</option><option value="down">downstream</option><option value="up">upstream</option>
        </select>
      </div>
      <label class="switch" data-tip="Only ADDING columns? Incrementals set to append_new_columns or sync_all_columns absorb additive changes without a full refresh. Renames, drops and type changes never do.">
        <input type="checkbox" id="additive"> additive change
      </label>
    </div>
    <div class="tree-section" id="treeSection">
      <div class="pane-head clickable" id="treeHead" title="Collapse the model list"><h2>Models</h2>
        <button class="collapse" id="hideTree" title="Collapse" tabindex="-1">&#10094;</button></div>
      <div class="tree-filter"><input type="search" id="treeSearch" placeholder="filter models…" style="width:100%"></div>
      <div class="pane-body" id="treeBody"></div>
    </div>
  </aside>

  <main class="pane graph">
    <div class="graph-toolbar">
      <div class="ctl gb-cols">
        <label for="colBtn">Columns</label>
        <button id="colBtn" class="colbtn" data-tip="Narrow to specific columns &mdash; the result is everything affected by ANY of them. Opens a picker: click a column on the left to add it, click one on the right to remove it. Leave empty to treat it as a whole-model change.">+ add columns</button>
        <span id="colHint" class="info" hidden>i</span>
        <span class="chips" id="colChips"></span>
      </div>
      <div class="ctl gb-show">
        <label for="matPick">Show</label>
        <select id="matPick" data-tip="Filter the graph by materialization: how dbt persists a model. view = recomputed on query; table = rebuilt every run; incremental = only new rows added, so these need a full refresh when logic changes.">
          <option value="">all types</option><option value="incremental">incremental only</option>
          <option value="table">table only</option><option value="view">view only</option>
        </select>
      </div>
    </div>
    <div class="graph-area">
      <div id="graph"></div>
      <div id="legend" hidden></div>
      <div class="graph-note" id="graphNote"></div>
    </div>
  </main>

  <aside class="pane detail">
    <div class="splitter" id="splitDetail" title="Drag to resize"></div>
    <div class="pane-head clickable" id="detailHead" title="Collapse detail panel"><h2>Detail</h2>
      <button class="collapse" id="hideDetail" title="Collapse" tabindex="-1">&#10095;</button></div>
    <div class="pane-body">
      <section class="det-sec det-target" id="targetSec">
        <div class="det-shead" data-sec="target" title="Click to collapse"><span class="det-caret">&#9662;</span><span class="det-badge">TARGET DETAIL</span><span id="targetName"></span></div>
        <div class="det-body"><div id="results"></div></div>
      </section>
      <section class="det-sec det-inspect" id="inspectSec">
        <div class="det-shead" data-sec="inspect" title="Click to collapse"><span class="det-caret">&#9662;</span><span class="det-badge">INSPECTING</span><span id="inspectName"></span></div>
        <div class="det-body"><div id="sqlPanel"></div></div>
      </section>
    </div>
  </aside>
</div>

<button id="showDetail" title="Show detail">&#10094;</button>

<script>__MERMAID__</script>
<script>
const DATA = __DATA__;
const N = DATA.nodes, PAR = DATA.parents, CH = DATA.children, COLS = DATA.columns, SQL = DATA.sql;
const SAFE_OSC = new Set(["append_new_columns","sync_all_columns"]);
const key = (u,c) => u + "|" + c;
const sid = u => u.replace(/[^0-9A-Za-z_]/g,"_");
const SID = {}; for (const u in N) SID[sid(u)] = u;
const esc = s => String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));

/* ---------------- traversal (mirrors graph.py / columns.py) ---------------- */
function walk(uid, dir, depth){
  const edges = dir === "up" ? PAR : CH;
  const seen = {}; let frontier = [uid], d = 0;
  while (frontier.length && (depth == null || d < depth)){
    d++; const next = [];
    for (const n of frontier) for (const nb of (edges[n]||[]))
      if (!(nb in seen) && nb !== uid){ seen[nb] = d; next.push(nb); }
    frontier = next;
  }
  return seen;
}
function topoOrder(set){
  const out = [], visiting = new Set(), done = new Set();
  const visit = u => {
    if (done.has(u) || visiting.has(u)) return;
    visiting.add(u);
    for (const p of (PAR[u]||[])) if (set.has(p)) visit(p);
    visiting.delete(u); done.add(u); out.push(u);
  };
  for (const u of Array.from(set).sort()) visit(u);
  return out;
}
/* Does a column with these edges read a tainted (parent,col)? A genuine unknown
   leaf (parent null AND no external relation) fails closed; an external terminal
   (parent null but a real off-project relation) never carries an in-project change. */
function colReadsChange(edges, tainted){
  for (const e of (edges || [])){
    const p = e[0], pc = e[1], rel = e[3];
    if (p === null && (rel === null || rel === undefined)) return true;  // unknown -> fail closed
    if (p === null) continue;                                            // external terminal
    if (tainted.has(key(p,pc)) || tainted.has(key(p,"*"))) return true;
  }
  return false;
}
/* union taint from one or more changed columns; fail-closed on unknown lineage */
function taint(root, cols){
  const down = walk(root, "down");
  const scope = Object.keys(down).filter(u => N[u] && N[u].type === "model");
  const tainted = new Set(), affected = new Set(), unknown = new Set(), byNode = {};
  for (const c of cols){ tainted.add(key(root,c)); (byNode[root] = byNode[root]||new Set()).add(c); }
  for (const uid of topoOrder(new Set(scope))){
    const info = COLS[uid];
    if (!info || !info.resolved){
      unknown.add(uid); affected.add(uid); tainted.add(key(uid,"*")); continue;
    }
    let modelHit = false;
    for (const col in info.cols){        // explicit columns (all, or computed additions)
      if (colReadsChange(info.cols[col], tainted)){
        tainted.add(key(uid,col)); (byNode[uid] = byNode[uid]||new Set()).add(col); modelHit = true;
      }
    }
    if (info.passthrough && info.passthrough.parent){
      // every non-computed column passes through by name: inherit the terminal's
      // tainted columns (snapshot the set first; we mutate it in the loop)
      const src = info.passthrough.parent;
      for (const t of Array.from(tainted)){
        const i = t.indexOf("|"), u = t.slice(0,i), c = t.slice(i+1);
        if (u === src && !(c in info.cols)){
          tainted.add(key(uid,c)); (byNode[uid] = byNode[uid]||new Set()).add(c); modelHit = true;
        }
      }
    }
    if (modelHit) affected.add(uid);
  }
  return {affected, unknown, byNode};
}
/* target+downstream models -> full-refresh / absorbs (additive) / rebuild-free */
function classifyDownstream(uids, additive){
  const full = [], absorbs = [], rebuild = [];
  for (const uid of topoOrder(new Set(uids))){
    const n = N[uid];
    if (!n || n.mat !== "incremental") rebuild.push(uid);
    else if (additive && SAFE_OSC.has(n.osc || "ignore")) absorbs.push(uid);
    else full.push(uid);
  }
  return {full, absorbs, rebuild};
}
/* incremental ancestors on the columns' lineage (topo order). No columns -> every
   incremental ancestor. Fails closed: past an opaque model, keep all above it. */
function upstreamIncrementals(root, cols){
  if (!cols || !cols.length){
    const anc = Object.keys(walk(root,"up")).filter(u => N[u] && N[u].mat === "incremental");
    return topoOrder(new Set(anc));
  }
  const lineage = new Set(), opaque = new Set();
  for (const c of cols) for (const e of colUpstream(root, c)){
    if (e.parent) lineage.add(e.parent);
    else if (e.t === "unknown") opaque.add(e.uid);
  }
  for (const m of opaque) for (const a of Object.keys(walk(m,"up"))) lineage.add(a);
  lineage.delete(root);
  const incs = Array.from(lineage).filter(u => N[u] && N[u].mat === "incremental");
  return topoOrder(new Set(incs));
}
function relSchemaTable(uid){ return (N[uid] && N[uid].rel_st) || N[uid].relation; }
/* merge upstream + target + downstream incrementals into ONE topo-ordered drop
   list, tagged by position, with db-less DROP DDL (no CASCADE -- the database
   qualifier is stripped since you're already connected) */
function dropList(upstreamIncs, fullRefresh, root){
  const pos = {}; for (const u of upstreamIncs) pos[u] = "upstream";
  for (const u of fullRefresh) pos[u] = (u === root ? "target" : "downstream");
  return topoOrder(new Set(Object.keys(pos))).map(uid => ({
    model: uid, name: N[uid].name, position: pos[uid], relation: relSchemaTable(uid),
    statement: "DROP TABLE " + relSchemaTable(uid) + ";",
  }));
}
function colUpstream(uid, col, depth, seen, out){
  seen = seen || new Set(); out = out || []; depth = depth || 1;
  const info = COLS[uid];
  if (!info){
    // a non-model node (source/seed/snapshot) is a resolved leaf with no lineage
    // — exactly Python's columns_of; only a missing/unresolved MODEL is unknown
    if (N[uid] && N[uid].type !== "model") return out;
    out.push({uid, col, parent:null, pcol:null, rel:null, t:"unknown", d:depth}); return out;
  }
  if (!info.resolved){ out.push({uid, col, parent:null, pcol:null, rel:null, t:"unknown", d:depth}); return out; }
  let edges = info.cols[col];
  if (!edges && info.passthrough){  // passes through by name to the terminal
    const pt = info.passthrough;    // rel only when the terminal is external (no parent)
    edges = [[pt.parent, col, "passthrough", pt.parent ? null : pt.rel]];
  }
  for (const [p,pc,t,rel] of (edges || [])){
    const k = key(uid,col) + key(p,pc) + (rel||""); if (seen.has(k)) continue; seen.add(k);
    out.push({uid, col, parent:p, pcol:pc, rel: rel||null, t, d:depth});
    if (p && pc) colUpstream(p, pc, depth+1, seen, out);
  }
  return out;
}
/* Which SQL lines to highlight for a set of affected columns.
   Match where a column is PRODUCED (its output alias), not everywhere its name
   appears: "case when t3.col_2 > 50 ... end as col_0" merely READS col_2, and
   highlighting it claims blast radius the taint engine never claimed. */
/* Exact 1-based line ranges producing `cols`, from sqlglot token positions.
   Only valid for COMPILED sql (jinja line numbers don't survive compilation),
   and empty when the model's final projection is a star -- caller falls back. */
function hlLines(uid, cols, sqlMode){
  if (sqlMode !== "compiled") return null;
  const sp = (COLS[uid] || {}).spans;
  if (!sp) return null;
  const hit = new Set();
  let any = false;
  for (const c of (cols || [])){
    const ranges = sp[c];
    if (!ranges || !ranges.length) continue;
    any = true;
    // several ranges when parallel CTEs each produce the column
    for (const r of ranges) for (let i = r[0]; i <= r[1]; i++) hit.add(i);
  }
  return any ? hit : null;
}
function hlRegex(cols){
  const words = (cols||[]).map(c => String(c).replace(/[^0-9A-Za-z_]/g,"")).filter(Boolean);
  if (!words.length) return null;
  const alt = "(?:" + words.join("|") + ")";
  // "... as col_2," / 'as "col_2"'  |  a bare trailing select item: "t3.col_2,"
  return new RegExp("(?:\\bas\\s+\"?" + alt + "\"?|(?:^|[\\s.,(])" + alt + ")\\s*,?\\s*$", "i");
}
/* Columns we could NOT trace: they sit in the blast radius because fail-closed
   keeps them there, not because anything proved they derive from the change.
   A null parent is the unknown marker columns.py writes. Presenting these the
   same as proven derivations turns "we can't tell" into what reads as a finding. */
function unprovenCols(uid){
  const out = new Set(), info = COLS[uid];
  if (!info) return out;
  if (!info.resolved){                        // whole model unparseable
    for (const c in (info.cols || {})) out.add(c);
    return out;
  }
  for (const c in info.cols)
    // a genuine unknown leaf is parent null AND no external relation; an external
    // terminal (e[3] set) is PROVEN, just off-project
    if ((info.cols[c] || []).some(e => e[0] === null && (e[3] == null))) out.add(c);
  return out;
}
window.__api = {walk, topoOrder, taint, colUpstream, classifyDownstream,
                upstreamIncrementals, dropList, hlRegex, hlLines, unprovenCols};   // used by the parity test

/* ---------------- state ---------------- */
/* each mode has a natural direction: lineage/columns look both ways, impact only
   downstream (a change can't affect what feeds it). Switching modes RESTORES the
   new mode's default, so you can always get back to the view you started with. */
const MODE_DIR = {lineage:"both", impact:"both", columns:"both"};
const S = {model:null, cols:[], mode:"lineage", dir:MODE_DIR.lineage, additive:false, mat:"",
           detail:null, sqlMode:"compiled"};
const $ = id => document.getElementById(id);

/* ---------------- model tree ---------------- */
/* nested folder tree: models/staging/crm/x.sql nests under staging > crm */
const TREE = {kids:{}, items:[]};
for (const u in N){
  if (N[u].type === "exposure") continue;
  let node = TREE;
  for (const part of String(N[u].folder || "models").split("/").filter(Boolean)){
    node.kids[part] = node.kids[part] || {kids:{}, items:[]};
    node = node.kids[part];
  }
  node.items.push(u);
}
const openFolders = new Set();                 // folders start collapsed (Q2)
function countIn(node, q){
  let n = node.items.filter(u => !q || N[u].name.toLowerCase().includes(q)).length;
  for (const k in node.kids) n += countIn(node.kids[k], q);
  return n;
}
function renderBranch(node, path, depth, q, box){
  for (const name of Object.keys(node.kids).sort()){
    const kid = node.kids[name], full = path ? path + "/" + name : name;
    const n = countIn(kid, q);
    if (!n) continue;
    const open = q ? true : openFolders.has(full);
    const head = document.createElement("div");
    head.className = "folder" + (open ? "" : " closed");
    head.style.paddingLeft = (depth * 0.75) + "rem";
    head.innerHTML = '<span class="caret">&#9660;</span>' + esc(name) +
                     ' <span style="opacity:.6">(' + n + ')</span>';
    head.onclick = () => { open ? openFolders.delete(full) : openFolders.add(full); renderTree(); };
    box.appendChild(head);
    if (open) renderBranch(kid, full, depth + 1, q, box);
  }
  const items = node.items
    .filter(u => !q || N[u].name.toLowerCase().includes(q))
    .sort((a,b) => N[a].name.localeCompare(N[b].name));
  for (const u of items){
    const el = document.createElement("div");
    el.className = "tree-item t-" + N[u].mat; el.title = N[u].name;
    el.style.paddingLeft = (depth * 0.75 + 0.35) + "rem";
    el.setAttribute("aria-current", String(u === S.model));
    el.innerHTML = '<span class="dot ' + esc(N[u].mat) + '"></span>' + esc(N[u].name);
    el.onclick = () => selectModel(u);
    box.appendChild(el);
  }
}
function renderTree(){
  const q = ($("treeSearch").value || "").toLowerCase();
  const box = $("treeBody"); box.innerHTML = "";
  renderBranch(TREE, "", 0, q, box);
  if (!box.children.length) box.innerHTML = '<div class="empty">No models match that filter.</div>';
}

/* ---------------- controls ---------------- */
function fillModelPicker(){
  const sel = $("modelPick");
  sel.innerHTML = '<option value="">&mdash; pick a model &mdash;</option>';
  Object.keys(N).filter(u => N[u].type !== "exposure")
    .sort((a,b) => N[a].name.localeCompare(N[b].name))
    .forEach(u => { const o = document.createElement("option"); o.value = u; o.textContent = N[u].name; sel.appendChild(o); });
}
/* reason-specific nudge for a model whose column lineage didn't resolve.
   Only `select *` and missing-SQL have a clean remedy; be honest otherwise. */
function unresolvedTip(why){
  switch (why){
    case "star": return "This model is `select *` over its parents, so individual "
      + "columns can't be traced from its own SQL. Run `dbt docs generate` to create "
      + "catalog.json, then rebuild this app, and its columns will resolve.";
    case "python": return "This is a Python model — there's no SQL to trace columns "
      + "from, so column-level lineage isn't available. Model-level Impact still works.";
    case "nosql": return "No compiled SQL was found for this model. Run `dbt compile` "
      + "(or any dbt build) and rebuild this app.";
    case "parse": return "This model's SQL couldn't be parsed for column lineage "
      + "(an unusual construct or a dialect gap). Model-level Impact still works.";
    default: return "Column lineage couldn't be traced for this model. Model-level "
      + "Impact still works; try `dbt docs generate` if it's a `select *` model.";
  }
}
let colPopOpen = false, colFilter = "";
function fillColumnPicker(){
  const info = S.model && COLS[S.model];
  const resolved = !!(info && info.resolved);
  const btn = $("colBtn");
  btn.disabled = !resolved;
  btn.textContent = !S.model ? "pick a model first"
    : !resolved ? "lineage unresolved"
    : (S.cols.length ? "columns (" + S.cols.length + ")" : "+ add columns");
  // nudge: when a model's columns can't be traced, say WHY and how to fix it
  const hint = $("colHint");
  if (info && !info.resolved){ hint.hidden = false; hint.setAttribute("data-tip", unresolvedTip(info.why)); }
  else hint.hidden = true;
  if (!resolved) closeColPopover();
  // chips = compact selected view in the control bar
  const chips = $("colChips"); chips.innerHTML = "";
  S.cols.forEach(c => {
    const el = document.createElement("span"); el.className = "chip";
    el.innerHTML = esc(c) + '<button title="Remove">&times;</button>';
    el.querySelector("button").onclick = () => { S.cols = S.cols.filter(x => x !== c); fillColumnPicker(); run(); };
    chips.appendChild(el);
  });
  if (colPopOpen) renderColPopover();
}
/* two-pane column picker: click a column on the left to add, on the right to remove */
function renderColPopover(){
  const info = S.model && COLS[S.model];
  $("colPopModel").textContent = S.model ? N[S.model].name : "";
  const all = (info && info.resolved) ? Object.keys(info.cols).sort() : [];
  const unproven = unprovenCols(S.model);
  const f = colFilter.toLowerCase();
  const avail = all.filter(c => !S.cols.includes(c) && c.toLowerCase().includes(f));
  const item = (c, mark) => '<div class="colpop-item' + (unproven.has(c) ? " unproven" : "") +
    '" data-col="' + esc(c) + '" title="' + esc(c) + '"><span class="nm">' + esc(c) + "</span>" +
    (unproven.has(c) ? '<span class="pill unknown">unproven</span>' : "") +
    '<span class="mk">' + mark + "</span></div>";
  const A = $("colAvail"), Sl = $("colSel");
  A.innerHTML = avail.length ? avail.map(c => item(c, "+")).join("")
    : '<div class="colpop-empty">' + (all.length ? "no matches" : "no columns") + "</div>";
  Sl.innerHTML = S.cols.length ? S.cols.slice().sort().map(c => item(c, "&times;")).join("")
    : '<div class="colpop-empty">none selected &mdash; the whole model counts as changed</div>';
  A.querySelectorAll(".colpop-item").forEach(it => it.onclick = () => {
    S.cols.push(it.dataset.col); fillColumnPicker(); run(); });
  Sl.querySelectorAll(".colpop-item").forEach(it => it.onclick = () => {
    S.cols = S.cols.filter(x => x !== it.dataset.col); fillColumnPicker(); run(); });
}
function openColPopover(){
  const info = S.model && COLS[S.model];
  if (!info || !info.resolved) return;
  colPopOpen = true; colFilter = "";
  const pop = $("colPop"); pop.hidden = false;
  $("colBtn").setAttribute("aria-expanded", "true");
  // drop any prior manual resize so the box re-fits THIS model's column names
  pop.style.width = ""; pop.style.height = "";
  $("colPopFilter").value = ""; renderColPopover();   // fill first -> width fits content
  const r = $("colBtn").getBoundingClientRect();
  // keep the popover within the graph pane (not the whole viewport) so it never
  // spills over the detail pane on the right; cap its width to the pane too
  const gp = document.querySelector(".pane.graph").getBoundingClientRect();
  pop.style.maxWidth = Math.round(gp.width - 20) + "px";
  const w = pop.offsetWidth;
  pop.style.left = Math.round(Math.max(gp.left + 8,
    Math.min(r.left, gp.right - w - 12))) + "px";
  pop.style.top = (r.bottom + 6) + "px";
  $("colPopFilter").focus();
}
function closeColPopover(){
  colPopOpen = false; $("colPop").hidden = true;
  $("colBtn").setAttribute("aria-expanded", "false");
}
/* open every folder on the way to a model so the selection is actually visible */
function revealInTree(u){
  if (!u || !N[u]) return;
  let path = "";
  for (const part of String(N[u].folder || "models").split("/").filter(Boolean)){
    path = path ? path + "/" + part : part;
    openFolders.add(path);
  }
}
function selectModel(u){
  S.model = u; S.cols = []; S.detail = null;
  revealInTree(u);
  $("modelPick").value = u || "";
  $("dirPick").value = S.dir;   // mode switching owns the direction, not model choice
  fillColumnPicker(); renderTree(); run();
}

/* ---------------- graph ---------------- */
function palette(){
  const dark = (document.documentElement.dataset.theme || "") === "dark" ||
    (!document.documentElement.dataset.theme && matchMedia("(prefers-color-scheme: dark)").matches);
  return dark
    ? {view:"#2b3a4a",table:"#2f4a6b",incremental:"#5a4526",source:"#2c4632",seed:"#3a3450",
       snapshot:"#4a4026",stroke:"#8b95a6",text:"#e7eaf0",changed:"#ef2d56",inspect:"#22d3ee"}
    : {view:"#dce9f5",table:"#c3d9f0",incremental:"#f2e0c2",source:"#d7ecd9",seed:"#e2dcf0",
       snapshot:"#f2e7bf",stroke:"#66707f",text:"#1b1f27",changed:"#ef2d56",inspect:"#22d3ee"};
}
/* column lines shown inside a node, keyed to the selected column(s):
   - the changing model  -> the selected columns
   - a downstream node    -> each affected column and which selected col it came FROM
   - an upstream node      -> each column and which selected col it FEEDS
   ASCII only (mermaid double-escapes entities); column names are identifiers. */
function nodeColLines(u, root, annot){
  if (!annot) return null;
  if (u === root)
    return (annot.selected || []).map(c => c);
  const src = (m) => Array.from(m).sort().join(",");
  const dim = (t) => "<span style='opacity:.55'>" + t + "</span>";
  if (annot.downMap && annot.downMap[u]){
    const d = annot.downMap[u];
    return Object.keys(d).sort().map(dest => dest + dim("  from " + src(d[dest])));
  }
  if (annot.upMap && annot.upMap[u]){
    const um = annot.upMap[u];
    return Object.keys(um).sort().map(feed => feed + dim("  feeds " + src(um[feed])));
  }
  return null;
}
function mermaidFor(nodes, root, annot){
  const p = palette(), lines = ["graph LR"], used = new Set();
  const arr = Array.from(nodes).sort();
  for (const u of arr){
    const n = N[u]; const bucket = n.type === "model" ? n.mat : n.type;
    used.add(bucket);
    // labeled, left-aligned card: Model: / Type: / Columns:. The changed model is
    // marked by its red outline + the legend, not a banner. Everything that
    // affects width must live INSIDE the label with inline styles -- mermaid
    // measures the label to size the box, so a stylesheet ::after would clip.
    const lbl = "font-family:monospace;font-size:0.72em;opacity:.6";
    // inline label: value, one per line, never wrapped (the box grows instead) --
    // the TARGET / INSPECTING role badges are injected as SVG after render
    let card = '<div style=\'text-align:left;line-height:1.4\'>' +
      '<div style=\'white-space:nowrap\'><span style=\'' + lbl + '\'>Model:</span> <b>' + n.name + "</b></div>" +
      "<div style='white-space:nowrap'><span style='" + lbl + "'>Type:</span> " + n.mat + "</div>";
    const colLines = nodeColLines(u, root, annot);
    if (colLines && colLines.length){
      const shown = colLines.slice(0, 5)
        .concat(colLines.length > 5 ? ["+" + (colLines.length - 5) + " more"] : []);
      card += "<div style='white-space:nowrap'><span style='" + lbl + "'>Columns:</span></div>" +
        '<div style=\'font-family:monospace;font-size:0.85em;opacity:.9;margin-left:.6em;white-space:nowrap\'>' +
        shown.join("<br/>") + "</div>";
    }
    card += "</div>";
    lines.push('  ' + sid(u) + '["' + card + '"]:::' + bucket);
  }
  const inSet = new Set(arr);
  for (const u of arr) for (const p2 of (PAR[u]||[])) if (inSet.has(p2)) lines.push("  " + sid(p2) + " --> " + sid(u));
  for (const b of used) lines.push("  classDef " + b + " fill:" + (p[b]||p.view) + ",stroke:" + p.stroke + ",color:" + p.text + ";");
  if (root && inSet.has(root)){
    lines.push("  classDef focus stroke:" + p.changed + ",stroke-width:4px;");
    lines.push("  class " + sid(root) + " focus;");
  }
  return lines.join("\n");
}
let gid = 0, pan = {x:0,y:0,k:1};
let currentRoot = null;
const SVGNS = "http://www.w3.org/2000/svg";
/* role badge straddling a node's top border, as an SVG foreignObject so it
   renders styled HTML text yet still pans/zooms with the graph. dx offsets a
   second badge so TARGET + INSPECTING can sit side by side. Returns its width. */
function svgBadge(g, cls, txt, bg, fg, dx, align){
  const shape = g.querySelector("rect"); if (!shape) return 0;
  const x = parseFloat(shape.getAttribute("x")), y = parseFloat(shape.getAttribute("y"));
  const sw = parseFloat(shape.getAttribute("width")) || 0;
  const w = txt.length * 7.3 + 16;
  const fo = document.createElementNS(SVGNS, "foreignObject");
  fo.setAttribute("class", "rolebadge " + cls);
  const px = align === "right" ? x + sw - w - 10 - (dx || 0) : x + 10 + (dx || 0);
  fo.setAttribute("x", px); fo.setAttribute("y", y - 9);
  fo.setAttribute("width", w + 4); fo.setAttribute("height", 18);
  fo.setAttribute("overflow", "visible");
  const div = document.createElement("div");
  div.setAttribute("xmlns", "http://www.w3.org/1999/xhtml");
  div.style.cssText = "display:inline-block;background:" + bg + ";color:" + fg +
    ";font:700 10px var(--sans);letter-spacing:.5px;padding:1px 6px;border-radius:3px;white-space:nowrap";
  div.textContent = txt;
  fo.appendChild(div); g.appendChild(fo);
  return w;
}
async function drawGraph(nodes, root, annot){
  const box = $("graph");
  currentRoot = root;
  if (!nodes || !nodes.size){
    box.innerHTML = '<div class="empty" style="padding:2rem 1.5rem">' +
      (S.model ? "Nothing to draw for this selection. Try a different direction or clear the type filter."
               : "<b>Select a model</b> from the tree on the left (or the Model picker above) and its lineage will be drawn here.") +
      "</div>";
    return;
  }
  const {svg} = await mermaid.render("g" + (gid++), mermaidFor(nodes, root, annot));
  box.innerHTML = svg;
  const el = box.querySelector("svg");
  // small top/left offset so the role badges straddling top-row nodes aren't
  // clipped by the pane edge on first render
  pan = {x:6,y:16,k:1}; applyPan();
  const p = palette();
  box.querySelectorAll(".node").forEach(g => {
    const m = (g.id||"").match(/^flowchart-(.+)-\d+$/);
    if (!m || !SID[m[1]]) return;
    // click: inspect in the Detail pane. Ctrl/Cmd+click (or double click):
    // make it the target, exactly like picking it from the tree.
    const tip = document.createElementNS(SVGNS, "title");
    tip.textContent = "click: inspect in Detail  |  Ctrl+click: make this the target";
    g.appendChild(tip);
    if (SID[m[1]] === root) svgBadge(g, "rb-target", "TARGET", p.changed, "#fff");
    // mark drop-list members (not the target, which already carries TARGET) so the
    // graph matches the plan: an amber DROP tag at the node's top-right
    else if (window.__dropNodes && window.__dropNodes.has(SID[m[1]]))
      svgBadge(g, "rb-drop", "DROP", "#c47d2b", "#fff", 0, "right");
    g.addEventListener("click", e => {
      if (e.ctrlKey || e.metaKey){ selectModel(SID[m[1]]); return; }
      S.detail = SID[m[1]]; renderDetail(); markSelected();
    });
    g.addEventListener("dblclick", e => { e.preventDefault(); selectModel(SID[m[1]]); });
  });
  markSelected();
  return el;
}
function markSelected(){
  const p = palette();
  document.querySelectorAll("#graph .rb-inspect, #graph .selring, #graph .selhint")
    .forEach(el => el.remove());
  document.querySelectorAll("#graph .node").forEach(g => {
    const m = (g.id||"").match(/^flowchart-(.+)-\d+$/);
    const uid = m && SID[m[1]];
    const on = !!(uid && uid === S.detail);
    const isRoot = !!(uid && uid === currentRoot);
    g.classList.toggle("selected", on);
    // mermaid inlines each node's stroke colour with !important (from its
    // classDef), which even an external !important can't beat -- so set the
    // selection colour directly on the shape, saving/restoring the original.
    g.querySelectorAll("rect,polygon,path").forEach(shape => {
      if (shape.closest(".rolebadge")) return;
      if (on && !isRoot){
        if (shape.dataset.os === undefined) shape.dataset.os = shape.style.stroke || "";
        shape.style.setProperty("stroke", p.inspect, "important");
      } else if (shape.dataset.os !== undefined){
        shape.style.setProperty("stroke", shape.dataset.os);
        delete shape.dataset.os;
      }
    });
    if (!on) return;
    const shape = g.querySelector("rect");
    if (!shape) return;
    const x = parseFloat(shape.getAttribute("x")), y = parseFloat(shape.getAttribute("y"));
    const w = parseFloat(shape.getAttribute("width")), h = parseFloat(shape.getAttribute("height"));
    if (isRoot){
      // inspecting the TARGET itself: keep its crimson border and wrap a
      // light-blue selection ring around it; badges sit side by side
      const ring = document.createElementNS(SVGNS, "rect");
      ring.setAttribute("class", "selring");
      ring.setAttribute("x", x - 7); ring.setAttribute("y", y - 7);
      ring.setAttribute("width", w + 14); ring.setAttribute("height", h + 14);
      ring.setAttribute("rx", 9);
      // !important on BOTH fill and stroke: the node's bucket classDef (view/
      // table/...) sets fill:!important and the focus classDef sets stroke:
      // !important on every child rect -- without !important the ring would take
      // the node's fill and, painted on top, cover the label
      ring.setAttribute("style", "fill:none !important;stroke:" + p.inspect + " !important;" +
        "stroke-width:3.5px;filter:drop-shadow(0 0 6px rgba(34,211,238,.55))");
      g.appendChild(ring);
      svgBadge(g, "rb-inspect", "INSPECTING", p.inspect, "#14161b", "TARGET".length * 7.3 + 24);
    } else {
      svgBadge(g, "rb-inspect", "INSPECTING", p.inspect, "#14161b");
      // hint inside the node, bottom-right: this node can become the target
      const hint = document.createElementNS(SVGNS, "text");
      hint.setAttribute("class", "selhint");
      hint.textContent = "Ctrl+click → make target";
      hint.setAttribute("x", x + w - 7); hint.setAttribute("y", y + h - 6);
      hint.setAttribute("text-anchor", "end");
      g.appendChild(hint);
    }
  });
  markInspectEdges();
}
/* emphasize the arrows immediately touching the inspected node -- its direct
   parents (what feeds it) and direct children (what it feeds) -- so a click
   reads locally in the graph, not just in the Detail pane. mermaid names each
   edge `L_<fromSid>_<toSid>_<globalIndex>`; we strip that trailing index and
   match the from/to key against the node's PAR/CH. Edges to nodes not in the
   current graph simply aren't present. */
function markInspectEdges(){
  document.querySelectorAll("#graph .edge-inspect").forEach(e => {
    e.classList.remove("edge-inspect");
    e.style.removeProperty("stroke"); e.style.removeProperty("stroke-width");
    e.style.removeProperty("opacity");
  });
  const uid = S.detail; if (!uid) return;
  const p = palette();
  const targets = new Set();
  for (const par of (PAR[uid] || [])) targets.add(sid(par) + "_" + sid(uid));  // feeds it
  for (const ch of (CH[uid] || [])) targets.add(sid(uid) + "_" + sid(ch));      // it feeds
  if (!targets.size) return;
  document.querySelectorAll('#graph svg [id^="L_"]').forEach(e => {
    if (!targets.has(e.id.replace(/^L_/, "").replace(/_\d+$/, ""))) return;
    e.classList.add("edge-inspect");
    e.style.setProperty("stroke", p.inspect, "important");
    e.style.setProperty("stroke-width", "2.6px", "important");
    e.style.setProperty("opacity", "1", "important");
  });
}
function applyPan(){
  const el = document.querySelector("#graph svg");
  if (el) el.style.transform = "translate(" + pan.x + "px," + pan.y + "px) scale(" + pan.k + ")";
}

/* ---------------- results + sql ---------------- */
function nameRow(u, pill, cls){
  return '<div class="row" data-uid="' + u + '">' +
    (pill ? '<span class="pill ' + cls + '">' + pill + "</span>" : "") + esc(N[u].name) + "</div>";
}
function renderResults(res){
  const box = $("results");
  if (!S.model){
    box.innerHTML = '<div class="empty"><b>Pick a model</b> to see its refresh plan and affected columns here.</div>';
    return;
  }
  let h = "";
  if (S.mode === "impact"){
    if (S.cols.length) h += '<div class="summary"><b>' + res.affectedCount + "</b> of " +
      res.downCount + " downstream models read " +
      S.cols.map(c => "<code>" + esc(c) + "</code>").join(", ") + "</div>";
    if (res.unknownCount) h += '<div class="summary" style="color:var(--muted)">' + res.unknownCount +
      ' included conservatively &mdash; lineage unresolved. <span class="info" data-tip="This model\'s SQL couldn\'t be fully analyzed (select * over a join, dynamic macros, a Python model). It is never assumed safe.">i</span></div>';
    // the merged DROP list, filtered by the direction toggle
    const drop = res.dropAll.filter(e =>
      res.dir === "both" ? true : res.dir === "up" ? e.position !== "downstream"
                                                   : e.position !== "upstream");
    h += '<div class="section-label">Drop these (' + drop.length + ') <span class="info" ' +
      'data-tip="Every incremental on this change\'s lineage &mdash; upstream, the target, and downstream. A dropped incremental is rebuilt in full by the next scheduled run. Drop the ones you changed, or whose stored history you don\'t trust.">i</span></div>';
    if (!drop.length){
      h += '<div class="empty" style="padding:.2rem">Nothing to drop &mdash; nothing on this path is incremental.</div>';
    } else {
      // one colour-coded heading per position (upstream / target / downstream),
      // with that group's DROP statements listed underneath as one copyable block
      for (const pos of ["upstream","target","downstream"]){
        const grp = drop.filter(e => e.position === pos);
        if (!grp.length) continue;
        h += '<div class="drop-group"><div class="drop-ghead"><span class="pill pos-' + pos +
          '">' + pos + '</span><span class="drop-count">' + grp.length +
          (grp.length === 1 ? " model" : " models") + '</span></div>' +
          '<pre class="cmd">' + grp.map(e => esc(e.statement)).join("\n") + "</pre></div>";
      }
    }
    if (res.absorbs.length){
      h += '<div class="section-label">Absorbs schema change (' + res.absorbs.length + ') <span class="info" ' +
        'data-tip="With additive on, an append/sync incremental adds the column on its next normal run; existing rows get NULL. Full-refresh it anyway if you need the history backfilled.">i</span></div>';
      h += res.absorbs.map(u => '<div class="row" data-uid="' + u + '"><span class="pill">absorbs</span>' +
        esc(N[u].name) + ' <span style="color:var(--muted)">[' + esc(N[u].osc || "ignore") + "]</span></div>").join("");
    }
    h += '<div class="section-label">Rebuild normally (' + res.rebuild.length + ')</div>';
    h += res.rebuild.length ? res.rebuild.map(u => nameRow(u,"run","ok")).join("")
                            : '<div class="empty" style="padding:.2rem">None.</div>';
    const tests = new Set();
    for (const u of res.affected) for (const t of (DATA.tests[u]||[])) tests.add(t);
    if (tests.size) h += '<div class="section-label">Tests that re-run</div><div class="summary">' + tests.size + "</div>";
    h += '<div class="section-label">Or rebuild with dbt <span class="info" data-tip="The safe alternative to dropping: an atomic swap, but it needs ~2x storage during the rebuild.">i</span></div>';
    if (drop.length) h += '<pre class="cmd">dbt run --select ' + drop.map(e => e.name).join(" ") + " --full-refresh</pre>";
    h += '<pre class="cmd">dbt build --select ' + N[S.model].name + "+</pre>";
  } else if (S.mode === "columns"){
    if (!S.cols.length){
      h += '<div class="empty"><b>Add a column</b> above to trace where it comes from and what derives from it.</div>';
    } else {
      // one collapsible group per selected column (COMES FROM + DERIVED together).
      // Default collapsed at 2+ columns to keep a dense multi-select scannable.
      for (const c of S.cols){
        const up = colUpstream(S.model, c);
        const downNodes = Object.keys((res.byCol && res.byCol[c]) || {});
        const open = colGroupState(c);
        h += '<div class="colgroup"><div class="colgroup-head" data-col="' + esc(c) + '">' +
          '<span class="chev">' + (open ? "&#9662;" : "&#9656;") + "</span><b>" + esc(c) + "</b>" +
          '<span class="colgroup-sub">' + up.length + " upstream &middot; " + downNodes.length + " downstream</span></div>";
        if (open){
          h += '<div class="colgroup-body"><div class="section-label">comes from</div>';
          h += up.length ? up.map(e => '<div class="row"' +
                (e.parent ? ' data-uid="' + e.parent + '"' : "") + ">" + "&nbsp;".repeat((e.d-1)*2) +
                (e.parent ? esc(N[e.parent] ? N[e.parent].name : e.parent) + "." + esc(e.pcol) +
                  ' <span class="pill ok">' + esc(e.t) + "</span>"
                          : e.rel ? esc(e.rel) + "." + esc(e.pcol) + ' <span class="pill">external</span>'
                                  : '<span class="pill unknown">lineage unknown</span>') + "</div>").join("")
                        : '<div class="empty" style="padding:.2rem">No upstream columns (literal or count(*)).</div>';
          h += '<div class="section-label">feeds downstream</div>';
          const rows = downNodes.map(u => {
            const unp = unprovenCols(u), cols = res.byCol[c][u];
            const shown = cols.map(x => unp.has(x) ? esc(x) + "?" : esc(x)).join(", ");
            return '<div class="row" data-uid="' + u + '">&nbsp;&nbsp;' + esc(N[u].name) + "." +
              '<span style="color:var(--accent)">' + shown + "</span>" +
              (cols.every(x => unp.has(x)) ? ' <span class="pill unknown">unproven</span>' : "") + "</div>";
          });
          h += (rows.length ? rows.join("") : '<div class="empty" style="padding:.1rem .6rem">nothing downstream</div>') + "</div>";
        }
        h += "</div>";
      }
    }
  } else {
    const up = Object.keys(walk(S.model,"up")).length, down = Object.keys(walk(S.model,"down")).length;
    h += '<div class="summary"><b>' + esc(N[S.model].name) + "</b> · " + esc(N[S.model].mat) + "</div>";
    h += '<div class="summary">' + up + " upstream · " + down + " downstream</div>";
    h += '<div class="section-label">Direct parents</div>';
    const ps = (PAR[S.model]||[]);
    h += ps.length ? ps.map(u => nameRow(u)).join("") : '<div class="empty" style="padding:.2rem">None &mdash; this is a source or root.</div>';
    h += '<div class="section-label">Direct children</div>';
    const cs = (CH[S.model]||[]);
    h += cs.length ? cs.map(u => nameRow(u)).join("") : '<div class="empty" style="padding:.2rem">None &mdash; nothing reads this.</div>';
  }
  box.innerHTML = h;
  box.querySelectorAll(".row[data-uid]").forEach(r =>
    r.onclick = () => { S.detail = r.dataset.uid; renderDetail(); markSelected(); });
  // toggling a column group just re-renders the pane (no graph recompute)
  box.querySelectorAll(".colgroup-head").forEach(el =>
    el.onclick = () => { toggleColGroup(el.dataset.col); renderResults(res); });
}
/* per-column expand state in Columns mode (session-scoped, not persisted):
   default expanded for a single column, collapsed once several are selected */
const colGroupOpen = {};
function colGroupState(c){ return (c in colGroupOpen) ? colGroupOpen[c] : S.cols.length === 1; }
function toggleColGroup(c){ colGroupOpen[c] = !colGroupState(c); }
/* fill the two section headers: TARGET DETAIL echoes the picked model, INSPECTING
   the clicked node (and its section glows blue while a node is actually inspected) */
function updateDetailHead(){
  $("targetName").textContent = S.model ? N[S.model].name : "—";
  const on = S.detail && N[S.detail];
  $("inspectSec").classList.toggle("on", !!on);
  $("inspectName").textContent = on ? N[S.detail].name : "—";
}
function renderDetail(){
  updateDetailHead();
  const box = $("sqlPanel");
  if (!S.detail || !SQL[S.detail]){
    box.innerHTML = '<div class="section-label">SQL</div><div class="empty">' +
      (S.detail ? "This node has no SQL (sources and seeds aren't models)."
                : "<b>Click a node</b> in the graph &mdash; or a model in the list above &mdash; to read its SQL here, with the relevant lines highlighted.") + "</div>";
    return;
  }
  const cur = window.__last || {};
  // highlight the inspected node's relevant columns: downstream nodes -> the
  // affected columns (byNode); upstream nodes -> the columns that FEED the
  // selection (upMap). Either way, the lines that PRODUCE those columns light up.
  const downRel = (cur.byNode && cur.byNode[S.detail]) ? Array.from(cur.byNode[S.detail]) : [];
  const upRel = (cur.upMap && cur.upMap[S.detail]) ? Object.keys(cur.upMap[S.detail]) : [];
  const related = Array.from(new Set(downRel.concat(upRel))).sort();
  const sql = SQL[S.detail][S.sqlMode] || "";
  // split proven from fail-closed so the two never look alike
  const unproven = unprovenCols(S.detail);
  const provenCols = related.filter(c => !unproven.has(c));
  const unprovenRelated = related.filter(c => unproven.has(c));
  // exact spans when we have them; otherwise match the output alias textually
  const spanP = hlLines(S.detail, provenCols, S.sqlMode);
  const spanU = hlLines(S.detail, unprovenRelated, S.sqlMode);
  const reP = spanP ? null : hlRegex(provenCols);
  const reU = spanU ? null : hlRegex(unprovenRelated);
  const lit = (span, re, l, n) => span ? span.has(n) : (re ? re.test(l) : false);
  const lines = sql ? sql.split("\n") : [];
  const gutter = String(lines.length).length;
  const body = sql ? lines.map((l, i) => {
      // proven wins when a line is both -- never downgrade a real derivation
      const cls = lit(spanP, reP, l, i + 1) ? ' class="hl"'
                : lit(spanU, reU, l, i + 1) ? ' class="hl unproven"' : "";
      return "<span" + cls + '><i class="ln">' + (i + 1) + "</i>" + (esc(l) || " ") + "</span>";
    }).join("")
    : "<span>-- no " + S.sqlMode + " SQL --</span>";
  // an inspected node that isn't the current target can be promoted to target
  const canFocus = S.detail !== S.model && N[S.detail] && N[S.detail].type === "model";
  box.innerHTML = '<div class="section-label" style="display:flex;align-items:center;gap:.5rem">' +
    esc(N[S.detail].name) + " &mdash; SQL" +
    (canFocus ? ' <button id="focusBtn" class="focusbtn" title="Make this model the target ' +
      '(or Ctrl+click its node)">make target &#8635;</button>' : "") + "</div>" +
    (related.length ? "related columns:<ul class=\"cols\">" +
       related.map(c => "<li>" + esc(c) + (unproven.has(c)
         ? ' <span class="pill unknown">unproven</span>' : "")  + "</li>").join("") + "</ul>" : "") +
    (unprovenRelated.length ? '<div class="summary" style="color:var(--muted)"><b>' +
       unprovenRelated.length + "</b> of " + related.length +
       " could not be traced &mdash; listed because we can't prove they're unaffected, " +
       "not because they derive from the change. " +
       '<span class="info" data-tip="This model\'s SQL couldn\'t be analyzed for these columns ' +
       "(unqualified columns across a join, select * over a join, dynamic macros, a Python model). " +
       'Fail-closed keeps them in the blast radius rather than claiming they are safe.">i</span></div>' : "") +
    '<div class="tabs"><button data-sql="compiled" aria-pressed="' + (S.sqlMode==="compiled") +
      '">compiled</button><button data-sql="raw" aria-pressed="' + (S.sqlMode==="raw") + '">raw (jinja)</button></div>' +
    '<div class="sqlbox" style="--gutter:' + gutter + 'ch">' + body + "</div>";
  box.querySelectorAll("[data-sql]").forEach(b =>
    b.onclick = () => { S.sqlMode = b.dataset.sql; renderDetail(); });
  const fb = box.querySelector("#focusBtn");
  if (fb) fb.onclick = () => selectModel(S.detail);
  // real models run to hundreds of lines and the interesting expression is
  // rarely near the top -- bring the first highlighted line into view
  const sbox = box.querySelector(".sqlbox"), first = box.querySelector(".sqlbox span.hl");
  if (sbox && first) sbox.scrollTop = Math.max(0, first.offsetTop - sbox.clientHeight / 3);
}

/* ---------------- main query ---------------- */
async function run(){
  if (!S.model){
    await drawGraph(null); renderResults({}); renderDetail(); $("graphNote").textContent = "";
    return;
  }
  const dir = S.dir;   // impact now respects direction too (defaults to both)
  let nodes = new Set([S.model]), byNode = null, annot = null, res = {};
  const taintMode = S.mode === "impact" || (S.mode === "columns" && S.cols.length);
  if (taintMode){
    const downModels = Object.keys(walk(S.model,"down")).filter(u => N[u] && N[u].type === "model");
    res.downCount = downModels.length;
    if (S.cols.length){
      const t = taint(S.model, S.cols);
      byNode = t.byNode; res.affected = Array.from(t.affected);
      res.unknownCount = t.unknown.size; res.affectedCount = t.affected.size; res.byNode = t.byNode;
      // per-selected-column maps: which downstream cols each selected col reaches,
      // and which upstream cols feed it (for both the graph and the detail pane)
      const downMap = {}, upMap = {}, byCol = {};
      for (const c of S.cols){
        const tc = taint(S.model, [c]);
        byCol[c] = {};
        for (const u of tc.affected){
          if (!tc.byNode[u] || u === S.model) continue;
          byCol[c][u] = Array.from(tc.byNode[u]).sort();
          const dm = downMap[u] = downMap[u] || {};
          for (const dest of tc.byNode[u]) (dm[dest] = dm[dest] || new Set()).add(c);
        }
        for (const e of colUpstream(S.model, c)){
          if (!e.parent) continue;
          const um = upMap[e.parent] = upMap[e.parent] || {};
          (um[e.pcol] = um[e.pcol] || new Set()).add(c);
        }
      }
      res.byCol = byCol; res.upMap = upMap;
      annot = {downMap, upMap, selected: S.cols.slice()};
    } else {
      res.affected = downModels; res.affectedCount = downModels.length; res.unknownCount = 0; res.byNode = {};
    }
    // the plan (merged drop list + absorbs + free rebuilds) is always computed in
    // FULL; direction filters what's shown, not what's planned. The changed model
    // itself is in the plan: an incremental's plain run only appends old-logic rows.
    const planSet = new Set(res.affected);
    if (N[S.model] && N[S.model].type === "model") planSet.add(S.model);
    const cd = classifyDownstream(Array.from(planSet), S.additive);
    res.dropAll = dropList(upstreamIncrementals(S.model, S.cols), cd.full, S.model);
    res.absorbs = cd.absorbs; res.rebuild = cd.rebuild;
    // display nodes, filtered by direction
    if (dir === "down" || dir === "both") res.affected.forEach(u => nodes.add(u));
    if (dir === "up" || dir === "both"){
      if (S.cols.length){
        for (const c of S.cols) for (const e of colUpstream(S.model,c)) if (e.parent) nodes.add(e.parent);
      } else {
        Object.keys(walk(S.model,"up")).forEach(u => nodes.add(u));
      }
    }
  } else {
    if (dir === "up" || dir === "both") Object.keys(walk(S.model,"up")).forEach(u => nodes.add(u));
    if (dir === "down" || dir === "both") Object.keys(walk(S.model,"down")).forEach(u => nodes.add(u));
    res.affected = [];
  }
  res.dir = dir;
  if (S.mat) nodes = new Set(Array.from(nodes).filter(u => u === S.model || N[u].mat === S.mat));
  window.__last = {byNode, upMap: res.upMap || null};
  // graph badges: which rendered nodes are on the drop list (impact only)
  window.__dropNodes = (S.mode === "impact" && res.dropAll)
    ? new Set(res.dropAll.map(e => e.model)) : null;
  await drawGraph(nodes, S.model, annot);
  renderResults(res); renderDetail();
  $("graphNote").innerHTML =
    '<span><b style="color:var(--changed)">TARGET</b> = the model you picked (see legend)</span>' +
    "<span>" + nodes.size + " nodes</span><span>mode: " + S.mode + "</span>" +
    "<span>direction: " + dir + "</span>" +
    "<span style='color:var(--muted)'>scroll to zoom · drag to pan · click a node to inspect · Ctrl+click to make it the target</span>";
}

/* ---------------- legend (collapsible, parked top-right of the graph) ---------------- */
let legendOpen = true;
try { legendOpen = localStorage.getItem("dw-legend") !== "closed"; } catch (_) {}
function buildLegend(){
  const el = $("legend"); el.hidden = false;
  const p = palette();
  const counts = (DATA.project && DATA.project.counts) || {};
  const buckets = ["source","view","table","incremental","seed","snapshot"]
    .filter(b => counts[b]);
  const sw = (c, label) => '<div class="lg-row"><i style="background:' + c +
    ';border:1px solid ' + p.stroke + '"></i>' + label + "</div>";
  const ol = (c, label) => '<div class="lg-row"><i style="background:transparent;border:3px solid ' +
    c + '"></i>' + label + "</div>";
  if (legendOpen){
    el.innerHTML = '<div class="card"><div class="lg-head"><span class="lg-title">LEGEND</span>' +
      '<button class="lg-toggle" title="Collapse legend">&#9650;</button></div>' +
      buckets.map(b => sw(p[b], b)).join("") +
      '<div class="lg-sep"></div>' +
      ol(p.changed, "<b>target</b> &mdash; the model you picked") +
      ol(p.inspect, "<b>inspecting</b> &mdash; open in Detail &rarr;") +
      sw("#c47d2b", "<b>drop</b> &mdash; on the impact drop list") +
      '<div class="lg-hint">Ctrl+click a node to make it the target</div></div>';
    el.querySelector(".lg-toggle").onclick = () => {
      legendOpen = false; try { localStorage.setItem("dw-legend", "closed"); } catch (_) {}
      buildLegend();
    };
  } else {
    el.innerHTML = '<div class="pill" title="Show legend">' +
      '<i style="background:' + p.view + ';border:1px solid ' + p.stroke + '"></i>' +
      '<i style="background:transparent;border:2px solid ' + p.changed + '"></i>' +
      '<i style="background:transparent;border:2px solid ' + p.inspect + '"></i>' +
      "Legend &#9660;</div>";
    el.querySelector(".pill").onclick = () => {
      legendOpen = true; try { localStorage.setItem("dw-legend", "open"); } catch (_) {}
      buildLegend();
    };
  }
}

/* ---------------- wiring ---------------- */
$("modelPick").onchange = e => selectModel(e.target.value || null);
$("colBtn").onclick = () => { colPopOpen ? closeColPopover() : openColPopover(); };
$("colPopClose").onclick = closeColPopover;
$("colPopFilter").oninput = e => { colFilter = e.target.value; renderColPopover(); };
// keep clicks inside the popover from reaching the outside-click handler -- an
// item click rebuilds the list and detaches the target, which would otherwise
// read as an "outside" click and close the popover after the first pick
$("colPop").addEventListener("click", e => e.stopPropagation());
document.addEventListener("click", e => {
  if (colPopOpen && !$("colBtn").contains(e.target)) closeColPopover();
});
document.addEventListener("keydown", e => { if (e.key === "Escape" && colPopOpen) closeColPopover(); });
$("dirPick").onchange = e => { S.dir = e.target.value; run(); };
$("matPick").onchange = e => { S.mat = e.target.value; run(); };
$("additive").onchange = e => { S.additive = e.target.checked; run(); };
$("treeSearch").oninput = renderTree;
document.querySelectorAll("#modeSeg button").forEach(b => b.onclick = () => {
  S.mode = b.dataset.mode;
  document.querySelectorAll("#modeSeg button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
  S.dir = MODE_DIR[S.mode];           // restore this mode's direction, don't inherit the last one
  $("dirPick").value = S.dir;
  run();
});
/* help panel */
const helpBox = $("help");
const openHelp = () => { helpBox.hidden = false; $("helpClose").focus(); };
const closeHelp = () => { helpBox.hidden = true; };
$("helpBtn").onclick = openHelp;
$("helpClose").onclick = closeHelp;
helpBox.onclick = e => { if (e.target === helpBox) closeHelp(); };   // click the backdrop
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && !helpBox.hidden) closeHelp();
  else if (e.key === "?" && helpBox.hidden && e.target === document.body) openHelp();
});

// the Models header folds/unfolds the list (the rail above it stays put)
$("treeHead").onclick = () => $("body").classList.toggle("no-tree");
// the whole Detail header collapses the pane (the chevron just bubbles up to it)
$("detailHead").onclick = () => { $("body").classList.add("no-detail"); $("showDetail").style.display = "flex"; };
$("showDetail").onclick = () => { $("body").classList.remove("no-detail"); $("showDetail").style.display = "none"; };
/* collapse either detail section (TARGET DETAIL / INSPECTING) independently */
document.querySelectorAll(".det-shead[data-sec]").forEach(head => {
  const sec = head.closest(".det-sec"), keyName = "dw-det-" + head.dataset.sec;
  try { if (localStorage.getItem(keyName) === "1") sec.classList.add("collapsed"); } catch (_) {}
  head.onclick = () => {
    const c = sec.classList.toggle("collapsed");
    try { localStorage.setItem(keyName, c ? "1" : "0"); } catch (_) {}
  };
});

/* resizable panes: drag the edge between the graph and either side pane */
function makeSplitter(id, side){
  const el = $(id); if (!el) return;
  let on = false;
  el.addEventListener("mousedown", e => { on = true; el.classList.add("active");
    document.body.style.userSelect = "none"; e.preventDefault(); });
  window.addEventListener("mousemove", e => {
    if (!on) return;
    const w = side === "right" ? window.innerWidth - e.clientX : e.clientX;
    const clamped = Math.min(Math.max(w, 180), Math.round(window.innerWidth * 0.7));
    $("body").style.setProperty(side === "right" ? "--detail-w" : "--tree-w", clamped + "px");
  });
  window.addEventListener("mouseup", () => {
    if (!on) return;
    on = false; el.classList.remove("active"); document.body.style.userSelect = "";
    const svg = document.querySelector("#graph svg"); if (svg) applyPan();
  });
}
makeSplitter("splitDetail", "right");
makeSplitter("splitTree", "left");

/* pan + zoom */
(function(){
  const box = $("graph"); let drag = false, moved = false, sx = 0, sy = 0;
  box.addEventListener("wheel", e => { e.preventDefault();
    const r = box.getBoundingClientRect(), mx = e.clientX-r.left, my = e.clientY-r.top;
    const f = e.deltaY < 0 ? 1.1 : 1/1.1;
    pan.x = mx-(mx-pan.x)*f; pan.y = my-(my-pan.y)*f; pan.k *= f; applyPan();
  }, {passive:false});
  box.addEventListener("mousedown", e => { drag = true; moved = false; sx = e.clientX-pan.x; sy = e.clientY-pan.y; box.classList.add("grabbing"); });
  window.addEventListener("mousemove", e => { if (!drag) return; pan.x = e.clientX-sx; pan.y = e.clientY-sy; moved = true; applyPan(); });
  window.addEventListener("mouseup", () => { drag = false; box.classList.remove("grabbing"); });
  box.addEventListener("click", e => { if (moved){ e.stopPropagation(); e.preventDefault(); moved = false; } }, true);
})();

/* boot */
mermaid.initialize({startOnLoad:false, securityLevel:"loose", maxEdges:5000, maxTextSize:5000000,
  theme: matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "default",
  themeVariables:{fontSize:"15px"}, flowchart:{htmlLabels:true, nodeSpacing:45, rankSpacing:70}});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { buildLegend(); run(); });
(function boot(){
  const p = DATA.project;
  const cat0 = DATA.catalog;
  $("provenance").textContent = [p.branch ? p.branch + (p.sha ? " @ " + p.sha : "") : null,
    "built " + p.built_at, Object.entries(p.counts).map(([k,v]) => v + " " + k).join(" · "),
    (cat0 && cat0.present) ? "catalog: " + cat0.relations + " relations" : null]
    .filter(Boolean).join("  ·  ");
  const st = p.staleness, notes = [];
  if (st && st.stale)
    notes.push("<b>Out of date.</b> " + st.newer_count +
      " model file(s) changed after this manifest was built &mdash; rerun <code>dbt compile</code> and rebuild this app to trust these results.");
  // a stale catalog can mis-resolve columns against outdated column lists; a hint, not a block
  const cat = DATA.catalog;
  if (cat && cat.present && cat.stale)
    notes.push("<b>Catalog older than manifest.</b> Column lists from <code>catalog.json</code> " +
      "may be out of date &mdash; rerun <code>dbt docs generate</code> if column lineage looks wrong.");
  if (notes.length){
    $("staleBanner").hidden = false;
    $("staleBanner").innerHTML = notes.join("<br>");
  }
  buildLegend(); fillModelPicker(); fillColumnPicker(); renderTree(); run();
})();
</script>
</body>
</html>
"""


def render(payload: dict, mermaid_js: str) -> str:
    project = payload["project"]["name"]
    return (
        _TEMPLATE.replace("__TITLE__", f"{project} · dbt lineage explorer")
        .replace("__PROJECT__", project)
        .replace("__MERMAID__", mermaid_js.replace("</script", "<\\/script"))
        .replace("__DATA__", _embed(payload))
    )
