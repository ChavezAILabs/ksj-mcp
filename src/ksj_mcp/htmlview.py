"""
Self-contained HTML view of the knowledge base (§2.4a).

Overview modes ship in stages, gated only by the data each needs:
  Mode 1 — Timeline: chronological captures, client-side search + filters.
  Mode 2 — Index: tags grouped by semantic role, plus the entity register.
  Mode 3 — Connections: every capture card lists its typed, directional
           edges as clickable links (no spatial layout, just adjacency).
  Mode 4 — Graph: a rotating globe, gated on edge quality and typed edges
           (server 3.0/3.1) so it never renders a hairball. Each capture
           sits at a fixed point on a sphere — the most-connected capture
           dead center of the equator, the rest spiraling outward by
           connection rank. Dream Capture entries get their own smaller,
           nested inner sphere; any connection reaching from a dream out to
           the main sphere renders as a distinct "rod". The globe does one
           bounded, decaying spin on first activation (never a perpetual
           animation) and then rests; drag to spin it manually, click a
           node to inspect it. Personalization controls (globe size, show
           superseded, journal/AI source, auto-rotate on/off, force
           labels) plus Reset Layout all live in the graph toolbar — Reset
           returns every one of these to its default and snaps the view
           (never the data) back to rotation 0, without replaying the
           intro spin. A plain-language summary above the graph, including
           the time span of what's shown, describes what's currently
           visible and updates live with every control.

One .html file, all data inlined as JSON, vanilla JS/CSS, no network access,
no build step, opens in any browser. Everything user-authored is escaped
client-side; the embedded JSON escapes '</' so page text can never terminate
the script block.
"""

import json
import sqlite3
from datetime import datetime, timezone

from .database import get_active_volumes, get_current_volume


def collect_view_data(con: sqlite3.Connection) -> dict:
    """Everything modes 1–4 need, in one JSON-serializable dict."""
    captures = []
    for r in con.execute(
        """SELECT id, type, template_id, page_suffix, volume, summary, confidence,
                  content_json, raw_ocr, corrected_ocr, source, valid_until, created_at
           FROM captures ORDER BY created_at DESC"""
    ).fetchall():
        tags = [
            {"prefix": t["prefix"], "value": t["value"],
             "display": t["display"] or t["value"], "role": t["role"] or ""}
            for t in con.execute(
                "SELECT prefix, value, display, role FROM tags WHERE capture_id=?",
                (r["id"],),
            ).fetchall()
        ]
        captures.append({
            "id": r["id"],
            "type": r["type"],
            "template_id": r["template_id"] or "",
            "page_suffix": r["page_suffix"] or "",
            "volume": r["volume"],
            "summary": r["summary"] or "",
            "confidence": r["confidence"],
            "fields": {
                k: v for k, v in json.loads(r["content_json"]).items()
                if v and k != "tags_raw"
            },
            "text": r["corrected_ocr"] or r["raw_ocr"] or "",
            "corrected": bool(r["corrected_ocr"]),
            "source": r["source"],
            "superseded": bool(r["valid_until"]),
            "date": (r["created_at"] or "")[:10],
            "tags": tags,
        })

    entities = []
    for e in con.execute(
        "SELECT id, name, kind FROM entities ORDER BY name COLLATE NOCASE"
    ).fetchall():
        cap_ids = [
            row["capture_id"] for row in con.execute(
                "SELECT capture_id FROM capture_entities WHERE entity_id=?", (e["id"],)
            ).fetchall()
        ]
        entities.append({
            "id": e["id"], "name": e["name"], "kind": e["kind"],
            "capture_ids": cap_ids,
        })

    # One row per edge (not per capture-perspective) — modes 3 and 4 both
    # derive what they need from this single list client-side, so the
    # payload never duplicates edge data per endpoint.
    edges = [
        {
            "source": row["source_id"],
            "target": row["target_id"],
            "type": row["type"],
            "relation": row["relation"],
            "strength": round(row["strength"], 2) if row["strength"] is not None else 1.0,
            "note": row["note"],
        }
        for row in con.execute(
            "SELECT source_id, target_id, type, relation, strength, note "
            "FROM connections ORDER BY id"
        ).fetchall()
    ]

    vols = get_active_volumes(con)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active_volumes": vols,          # None = all
        "current_volume": get_current_volume(con),
        "captures": captures,
        "entities": entities,
        "edges": edges,
    }


_ROLE_SECTIONS = [
    ("topic",    "Topics"),
    ("theme",    "Dream Themes"),
    ("question", "Open Questions"),
    ("insight",  "Key Insights"),
    ("priority", "Priorities"),
    ("motif",    "Recurring Motifs (writer-flagged)"),
    ("sensory",  "Sensory Details"),
    ("causal",   "Cause → Effect"),
]

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KSJ Knowledge Base</title>
<style>
:root {
  --bg: #f7f7f5; --card: #ffffff; --ink: #1c2427; --muted: #67737a;
  --line: #e2e2de; --accent: #0e7c92; --accent-soft: #e3f2f5; --chip: #eef0ee;
  --type-rc: #b7791f; --type-syn: #7c5cbf; --type-rev: #4a6f96; --type-dc: #96477c;
  --edge-negative: #b23a4a; --edge-positive: #3f8f6b;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #10181b; --card: #182327; --ink: #e4e9eb; --muted: #8fa0a8;
    --line: #253238; --accent: #4cc3d9; --accent-soft: #143540; --chip: #223035;
    --type-rc: #e0ab52; --type-syn: #a68ee6; --type-rev: #82a8cf; --type-dc: #d391b8;
    --edge-negative: #e08795; --edge-positive: #6bc79a;
  }
}
:root[data-theme="dark"] {
  --bg: #10181b; --card: #182327; --ink: #e4e9eb; --muted: #8fa0a8;
  --line: #253238; --accent: #4cc3d9; --accent-soft: #143540; --chip: #223035;
  --type-rc: #e0ab52; --type-syn: #a68ee6; --type-rev: #82a8cf; --type-dc: #d391b8;
  --edge-negative: #e08795; --edge-positive: #6bc79a;
}
:root[data-theme="light"] {
  --bg: #f7f7f5; --card: #ffffff; --ink: #1c2427; --muted: #67737a;
  --line: #e2e2de; --accent: #0e7c92; --accent-soft: #e3f2f5; --chip: #eef0ee;
  --type-rc: #b7791f; --type-syn: #7c5cbf; --type-rev: #4a6f96; --type-dc: #96477c;
  --edge-negative: #b23a4a; --edge-positive: #3f8f6b;
}
* { box-sizing: border-box; margin: 0; }
body { background: var(--bg); color: var(--ink);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
.wrap { max-width: 960px; margin: 0 auto; padding: 24px 16px 80px; }
header h1 { font-size: 1.35rem; letter-spacing: .01em; }
header .meta { color: var(--muted); font-size: .82rem; margin-top: 4px; }
.scope { margin-top: 6px; font-size: .82rem; color: var(--accent); }
.tabs { display: flex; gap: 8px; margin: 18px 0 12px; }
.tabs button { border: 1px solid var(--line); background: var(--card); color: var(--ink);
  padding: 7px 18px; border-radius: 999px; cursor: pointer; font-size: .9rem; }
.tabs button.active { background: var(--accent); border-color: var(--accent); color: #fff; }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; align-items: center; }
.toolbar input[type=search], .toolbar select { border: 1px solid var(--line);
  background: var(--card); color: var(--ink); padding: 7px 10px; border-radius: 8px;
  font-size: .88rem; }
.toolbar input[type=search] { flex: 1 1 200px; }
.toolbar label { font-size: .82rem; color: var(--muted); display: flex; gap: 6px; align-items: center; }
.toolbar input[type=range] { width: 110px; accent-color: var(--accent); }
.btn { border: 1px solid var(--line); background: var(--card); color: var(--ink);
  padding: 7px 14px; border-radius: 8px; cursor: pointer; font-size: .85rem; }
.btn:hover { border-color: var(--accent); color: var(--accent); }
.activetag { display: none; margin-bottom: 12px; font-size: .86rem; }
.activetag span { background: var(--accent-soft); color: var(--accent);
  padding: 4px 12px; border-radius: 999px; }
.activetag button { border: none; background: none; color: var(--accent);
  cursor: pointer; font-size: .86rem; text-decoration: underline; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 14px 16px; margin-bottom: 12px; }
.card.superseded { opacity: .55; }
.card .top { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; }
.tid { font-weight: 600; color: var(--accent); }
.badge { font-size: .7rem; padding: 2px 8px; border-radius: 999px;
  background: var(--chip); color: var(--muted); }
.badge.src-ai { background: var(--accent-soft); color: var(--accent); }
.badge.sup { background: #8b2635; color: #fff; }
.date { color: var(--muted); font-size: .8rem; margin-left: auto; }
.summary { margin-top: 6px; }
.chips { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font-size: .78rem; background: var(--chip); border-radius: 999px;
  padding: 2px 10px; cursor: pointer; border: none; color: var(--ink); }
.chip:hover { background: var(--accent-soft); color: var(--accent); }
details { margin-top: 10px; }
details summary { cursor: pointer; color: var(--muted); font-size: .82rem; }
.fields { margin-top: 8px; }
.fields h4 { font-size: .78rem; text-transform: uppercase; letter-spacing: .05em;
  color: var(--muted); margin: 10px 0 2px; }
.fields div, .rawtext { white-space: pre-wrap; overflow-wrap: anywhere; font-size: .88rem; }
.rawtext { background: var(--bg); border-radius: 8px; padding: 10px; margin-top: 8px; }
.idx-section { margin-bottom: 26px; }
.idx-section h2 { font-size: 1rem; margin-bottom: 8px; }
.idx-section h2 small { color: var(--muted); font-weight: 400; }
.idx-list { display: flex; flex-wrap: wrap; gap: 8px; }
.idx-item { border: 1px solid var(--line); background: var(--card); border-radius: 8px;
  padding: 6px 12px; cursor: pointer; font-size: .86rem; color: var(--ink); }
.idx-item:hover { border-color: var(--accent); color: var(--accent); }
.idx-item .n { color: var(--muted); font-size: .76rem; margin-left: 6px; }
.idx-item .kind { color: var(--muted); font-size: .74rem; margin-left: 6px; font-style: italic; }
.empty { color: var(--muted); font-style: italic; padding: 24px 0; }
footer { margin-top: 40px; color: var(--muted); font-size: .78rem; }

/* mode 3 — connections */
.connections { margin-top: 10px; }
.connections h4 { font-size: .72rem; text-transform: uppercase; letter-spacing: .05em;
  color: var(--muted); margin-bottom: 6px; }
.conn-row { display: flex; align-items: baseline; gap: 6px; width: 100%; text-align: left;
  background: none; border: none; border-top: 1px solid var(--line); padding: 6px 0;
  cursor: pointer; color: var(--ink); font-size: .84rem; }
.conn-row:first-child { border-top: none; }
.conn-row:hover { color: var(--accent); }
.conn-arrow { color: var(--muted); }
.conn-verb { color: var(--muted); font-style: italic; white-space: nowrap; }
.conn-target { font-weight: 600; white-space: nowrap; }
.conn-summary { color: var(--muted); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; flex: 1; }
.conn-note { font-size: .78rem; color: var(--muted); padding-left: 20px; margin-bottom: 4px; }
.conn-more { font-size: .78rem; color: var(--muted); padding-top: 6px; }
.conn-empty { font-size: .82rem; color: var(--muted); font-style: italic; }
@keyframes ksjFlash { 0% { box-shadow: 0 0 0 3px var(--accent); } 100% { box-shadow: 0 0 0 3px transparent; } }
.card.flash { animation: ksjFlash 1.1s ease-out; }
@media (prefers-reduced-motion: reduce) {
  .card.flash { animation: none; box-shadow: 0 0 0 3px var(--accent); }
}

/* mode 4 — graph */
.legend { display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  font-size: .76rem; color: var(--muted); margin-left: auto; }
.legend .sep { color: var(--line); }
.legend .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%;
  margin-right: 4px; vertical-align: middle; }
.legend .line { display: inline-block; width: 16px; height: 2px; margin-right: 4px;
  vertical-align: middle; background: var(--muted); }
.dot.t-RC { background: var(--type-rc); }
.dot.t-SYN { background: var(--type-syn); }
.dot.t-REV { background: var(--type-rev); }
.dot.t-DC { background: var(--type-dc); }
.dot.t-AIEX { background: var(--accent); }
.line.e-reference { background: var(--accent); }
.line.e-asserted { background: var(--edge-negative); }
.line.e-entity_overlap { background: var(--accent); opacity: .5; }
.line.e-tag_overlap { background: var(--muted); opacity: .5; }
.line.e-rod { background: var(--type-dc); }
#view-graph { position: relative; }
#graph-svg { width: 100%; height: 64vh; min-height: 360px; display: block;
  border: 1px solid var(--line); border-radius: 12px; background: var(--card); touch-action: none;
  cursor: grab; }
#graph-svg:active { cursor: grabbing; }
/* While an actual drag is in progress, force grabbing everywhere — without
   this, passing over a node mid-drag would flicker the cursor to pointer
   (its own rule) even though the user is mid-gesture, not about to click. */
#graph-svg.dragging, #graph-svg.dragging * { cursor: grabbing !important; }
.globe-outline { fill: none; stroke: var(--line); stroke-width: 1; opacity: .6; }
.globe-equator { stroke: var(--accent); stroke-width: 1; opacity: .28; stroke-dasharray: 2 4; }
.dc-globe-outline { fill: none; stroke: var(--type-dc); stroke-width: 1; opacity: .5; stroke-dasharray: 3 3; }
.arrowhead { fill: var(--muted); }
/* cursor lives on the whole node group, not just the circle, so hovering
   any part of a node (including its label) reliably shows pointer instead
   of occasionally falling through to the background's grab cursor. */
.node { cursor: pointer; }
.node circle.body { stroke: var(--card); stroke-width: 1.5; }
.node.t-RC circle.body { fill: var(--type-rc); }
.node.t-SYN circle.body { fill: var(--type-syn); }
.node.t-REV circle.body { fill: var(--type-rev); }
.node.t-DC circle.body { fill: var(--type-dc); }
.node.t-AIEX circle.body { fill: var(--accent); }
.node.superseded circle.body { opacity: .5; stroke-dasharray: 2 2; }
.node.src-ai circle.body { stroke: var(--accent); stroke-width: 2; }
.node .node-label { font-size: 9px; fill: var(--ink); pointer-events: none; user-select: none; }
/* !important: nodes/edges get a per-frame inline opacity from the globe's
   depth cue (renderGraphPositions), which — being an inline style — would
   otherwise always beat a plain class rule in the cascade and silently
   mask the ego-highlight dimming on every animation frame. */
.node.dimmed { opacity: .15 !important; }
.node:hover circle.body { stroke: var(--ink); stroke-width: 2.5; }
.edge { fill: none; stroke: var(--muted); stroke-opacity: .3; stroke-width: 1; }
.edge.e-reference { stroke: var(--accent); stroke-opacity: .8; stroke-width: 1.6; }
.edge.e-asserted { stroke-width: 2; stroke: var(--muted); }
.edge.e-asserted.e-rel-supersedes { stroke: var(--edge-negative); stroke-dasharray: 6 3; }
.edge.e-asserted.e-rel-refutes { stroke: var(--edge-negative); stroke-dasharray: 2 3; stroke-width: 1.4; }
.edge.e-asserted.e-rel-supports { stroke: var(--edge-positive); }
.edge.e-asserted.e-rel-narrows { stroke: var(--muted); }
.edge.e-entity_overlap { stroke: var(--accent); stroke-opacity: .45; stroke-width: 1.3; }
.edge.e-tag_overlap { stroke: var(--muted); stroke-opacity: .3; stroke-width: 1; }
/* A rod (a connection crossing from the inner DC/dream sphere out to the
   main sphere) always gets the same solid strut look, regardless of the
   underlying tag/reference/relation type it's also styled as — !important
   because a same-specificity type/relation combo (e.g. .e-asserted.e-rel-
   supports) would otherwise win on source order or out-specificity a plain
   two-class .edge.e-rod rule. */
.edge.e-rod { stroke: var(--type-dc) !important; stroke-width: 1.8 !important;
  stroke-opacity: .55 !important; stroke-dasharray: none !important; }
.edge.dimmed { stroke-opacity: .08 !important; }
.graph-caption { color: var(--muted); font-size: .8rem; line-height: 1.5; margin-bottom: 8px; }
.graph-caption strong { color: var(--ink); }
.graph-summary { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 10px 14px; margin-bottom: 10px; font-size: .86rem; color: var(--ink); line-height: 1.6; }
.graph-tooltip { position: absolute; pointer-events: none; background: var(--ink); color: var(--bg);
  font-size: .76rem; padding: 4px 8px; border-radius: 6px; transform: translate(-50%, -130%);
  white-space: nowrap; z-index: 5; }
.graph-panel { position: absolute; top: 12px; right: 12px; max-width: 260px; background: var(--card);
  border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px;
  box-shadow: 0 4px 18px rgba(0,0,0,.16); font-size: .85rem; z-index: 6; }
.graph-panel h3 { font-size: .92rem; margin-bottom: 4px; }
.graph-panel .meta2 { color: var(--muted); font-size: .76rem; margin-bottom: 6px; }
.graph-panel button { margin-top: 8px; width: 100%; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Knowledge Synthesis Journal</h1>
    <div class="meta" id="meta"></div>
    <div class="scope" id="scope"></div>
  </header>
  <div class="tabs">
    <button id="tab-timeline" class="active" onclick="setMode('timeline')">Timeline</button>
    <button id="tab-index" onclick="setMode('index')">Index</button>
    <button id="tab-graph" onclick="setMode('graph')">Graph</button>
  </div>
  <div class="toolbar" id="toolbar">
    <input type="search" id="q" placeholder="Search captures…" oninput="render()">
    <select id="ftype" onchange="render()"><option value="">All types</option></select>
    <select id="fvol" onchange="render()"><option value="">All volumes</option></select>
    <select id="fsrc" onchange="render()">
      <option value="">Journal + AI</option>
      <option value="journal">Journal only</option>
      <option value="ai_extract">AI-extracted only</option>
    </select>
    <label><input type="checkbox" id="fsup" onchange="render()"> show superseded</label>
    <label>from <input type="date" id="fdatefrom" onchange="render()"></label>
    <label>to <input type="date" id="fdateto" onchange="render()"></label>
  </div>
  <div class="toolbar" id="graph-toolbar" style="display:none">
    <label>min tag/entity strength
      <input type="range" id="gminstrength" min="0" max="7" step="0.5" value="3.5"
        oninput="onGraphStrengthChange()">
      <span id="gminstrength-val">3.5</span>
    </label>
    <label>globe size
      <input type="range" id="gsize" min="80" max="260" step="10" value="200"
        oninput="onGlobeSizeChange()">
      <span id="gsize-val">200</span>
    </label>
    <label><input type="checkbox" id="gshowsup" onchange="onGraphSupersededToggle()"> show superseded</label>
    <select id="gsource" onchange="onGraphSourceChange()">
      <option value="">Journal + AI</option>
      <option value="journal">Journal only</option>
      <option value="ai_extract">AI-extracted only</option>
    </select>
    <label><input type="checkbox" id="gautorotate" checked onchange="onGraphAutoRotateToggle()"> auto-rotate</label>
    <label><input type="checkbox" id="gforcelabels" onchange="onGraphLabelsToggle()"> always show labels</label>
    <button class="btn" onclick="resetGraphLayout()">Reset layout</button>
    <div class="legend">
      <span><i class="dot t-RC"></i>RC</span>
      <span><i class="dot t-SYN"></i>SYN</span>
      <span><i class="dot t-REV"></i>REV</span>
      <span><i class="dot t-DC"></i>DC</span>
      <span><i class="dot t-AIEX"></i>AIEX</span>
      <span class="sep">|</span>
      <span><i class="line e-reference"></i>reference</span>
      <span><i class="line e-asserted"></i>asserted</span>
      <span><i class="line e-entity_overlap"></i>entity</span>
      <span><i class="line e-tag_overlap"></i>tag overlap</span>
      <span><i class="line e-rod"></i>dream connection (rod)</span>
    </div>
  </div>
  <div class="activetag" id="activetag"></div>
  <main>
    <div id="view-timeline"></div>
    <div id="view-index" style="display:none"></div>
    <div id="view-graph" style="display:none">
      <div class="graph-caption">
        <strong>How to read this:</strong> position shows how connected a capture is —
        the most-connected capture sits front-and-center on the equator; everyone else
        spirals outward from there. Bigger nodes also mean more connections. Drag
        anywhere to spin the globe, click a node for details.
      </div>
      <div id="graph-summary" class="graph-summary"></div>
      <svg id="graph-svg" viewBox="0 0 900 560" preserveAspectRatio="xMidYMid meet"
        aria-label="Connection graph">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0 L10,5 L0,10 z" class="arrowhead"></path>
          </marker>
        </defs>
        <circle id="globe-outline" class="globe-outline" cx="450" cy="280" r="200"></circle>
        <line id="globe-equator" class="globe-equator" x1="250" y1="280" x2="650" y2="280"></line>
        <circle id="dc-globe-outline" class="dc-globe-outline" cx="450" cy="280" r="70"></circle>
        <g id="graph-edges"></g>
        <g id="graph-nodes"></g>
      </svg>
      <div id="graph-tooltip" class="graph-tooltip" hidden></div>
      <div id="graph-panel" class="graph-panel" hidden></div>
    </div>
  </main>
  <footer>Generated by ksj-mcp · local file, no network · data as of <span id="gen"></span></footer>
</div>
<script id="ksj-data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('ksj-data').textContent);
const ROLE_SECTIONS = __ROLE_SECTIONS__;
const byId = new Map(DATA.captures.map(c => [c.id, c]));
const prefersReducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
let state = { mode: 'timeline', tag: null, entity: null };

const esc = s => String(s).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label = c => c.template_id ? c.template_id + (c.page_suffix || '') : 'UNIDENTIFIED #' + c.id;

function setMode(m) {
  state.mode = m;
  for (const id of ['timeline', 'index', 'graph']) {
    document.getElementById('tab-' + id).classList.toggle('active', m === id);
  }
  document.getElementById('toolbar').style.display = m === 'timeline' ? 'flex' : 'none';
  document.getElementById('graph-toolbar').style.display = m === 'graph' ? 'flex' : 'none';
  document.getElementById('view-timeline').style.display = m === 'timeline' ? '' : 'none';
  document.getElementById('view-index').style.display = m === 'index' ? '' : 'none';
  document.getElementById('view-graph').style.display = m === 'graph' ? '' : 'none';
  if (m !== 'timeline') document.getElementById('activetag').style.display = 'none';
  render();
  if (m === 'graph') {
    // ensureGraphInit() only ever runs its body once (first activation) and
    // stages its own delayed spin-start. A RETURN visit later resumes the
    // rotation loop ONLY if a spin was genuinely still in flight when the
    // user left (spinVelocity above the stop threshold) — a settled,
    // static globe should stay exactly as still as it was, not restart
    // spinning just because the tab was revisited.
    const wasInitialized = graphInitialized;
    ensureGraphInit();
    if (wasInitialized && !globeLoopRunning && Math.abs(spinVelocity) > SPIN_STOP_THRESHOLD) {
      globeLoopRunning = true;
      requestAnimationFrame(stepGlobe);
    }
  }
}

function setTag(value, roleName) {
  state.tag = { value, roleName };
  state.entity = null;
  setMode('timeline');
}
function setEntity(id) {
  state.entity = DATA.entities.find(e => e.id === id) || null;
  state.tag = null;
  setMode('timeline');
}
function clearTag() { state.tag = null; state.entity = null; render(); }

// Jump to a capture from anywhere (a connection link, a graph node): reset
// the timeline filters so the target is guaranteed visible, then scroll to
// and briefly highlight its card.
function gotoCapture(id) {
  document.getElementById('q').value = '';
  document.getElementById('ftype').value = '';
  document.getElementById('fvol').value = '';
  document.getElementById('fsrc').value = '';
  document.getElementById('fdatefrom').value = '';
  document.getElementById('fdateto').value = '';
  const cap = byId.get(id);
  if (cap && cap.superseded) document.getElementById('fsup').checked = true;
  state.tag = null; state.entity = null;
  setMode('timeline');
  requestAnimationFrame(() => {
    const el = document.getElementById('cap-' + id);
    if (el) {
      el.scrollIntoView({ behavior: prefersReducedMotion ? 'auto' : 'smooth', block: 'center' });
      el.classList.add('flash');
      setTimeout(() => el.classList.remove('flash'), 1200);
    }
  });
}

function filtered() {
  const q = document.getElementById('q').value.trim().toLowerCase();
  const ftype = document.getElementById('ftype').value;
  const fvol = document.getElementById('fvol').value;
  const fsrc = document.getElementById('fsrc').value;
  const fsup = document.getElementById('fsup').checked;
  const dfrom = document.getElementById('fdatefrom').value;
  const dto = document.getElementById('fdateto').value;
  return DATA.captures.filter(c => {
    if (!fsup && c.superseded) return false;
    if (ftype && c.type !== ftype) return false;
    if (fvol && String(c.volume) !== fvol) return false;
    if (fsrc && c.source !== fsrc) return false;
    if (dfrom && c.date < dfrom) return false;
    if (dto && c.date > dto) return false;
    if (state.tag && !c.tags.some(t => t.value === state.tag.value)) return false;
    if (state.entity && !state.entity.capture_ids.includes(c.id)) return false;
    if (q) {
      const hay = (label(c) + ' ' + c.summary + ' ' + c.text + ' '
        + c.tags.map(t => t.prefix + t.value + ' ' + t.display).join(' ')).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

// ---- mode 3: connection lists — pure helpers (no DOM), then rendering ----

function edgeDirection(e, capId) {
  if (e.type !== 'reference' && e.type !== 'asserted') return 'shared';
  return e.source === capId ? 'cites' : 'cited_by';
}
function otherEnd(e, capId) { return e.source === capId ? e.target : e.source; }
function edgesFor(capId) {
  return DATA.edges.filter(e => e.source === capId || e.target === capId);
}

const STRONG_OVERLAP = 2.0;
const RELATION_PASSIVE = {
  supersedes: 'superseded by', refutes: 'refuted by',
  narrows: 'narrowed by', supports: 'supported by',
};

function connectionRows(capId) {
  const all = edgesFor(capId);
  const priority = all.filter(e => e.type === 'asserted' || e.type === 'reference');
  const overlap = all.filter(e => e.type !== 'asserted' && e.type !== 'reference');
  const strongOverlap = overlap.filter(e => e.strength >= STRONG_OVERLAP);
  const weakCount = overlap.length - strongOverlap.length;
  const rank = e => e.type === 'asserted' ? 0 : e.type === 'reference' ? 1
              : e.type === 'entity_overlap' ? 2 : 3;
  const shown = priority.concat(strongOverlap)
    .sort((a, b) => rank(a) - rank(b) || b.strength - a.strength)
    .slice(0, 12);
  return { shown, weakCount };
}

function connectionRowHtml(e, capId) {
  const otherId = otherEnd(e, capId);
  const other = byId.get(otherId);
  if (!other) return '';
  const dir = edgeDirection(e, capId);
  let verb;
  if (e.type === 'asserted') {
    verb = dir === 'cited_by' ? (RELATION_PASSIVE[e.relation] || `${e.relation} by`) : e.relation;
  } else if (e.type === 'reference') {
    verb = dir === 'cited_by' ? 'cited by' : 'cites';
  } else if (e.type === 'entity_overlap') {
    verb = 'shares entities with';
  } else {
    verb = 'shares tags with';
  }
  const arrow = dir === 'cited_by' ? '←' : dir === 'cites' ? '→' : '↔';
  const sup = other.superseded ? '<span class="badge sup">superseded</span>' : '';
  const noteRow = e.note ? `<div class="conn-note">note: ${esc(e.note)}</div>` : '';
  return `<button class="conn-row" onclick="gotoCapture(${otherId})">
      <span class="conn-arrow">${arrow}</span>
      <span class="conn-verb">${esc(verb)}</span>
      <span class="conn-target">${esc(label(other))}</span>${sup}
      <span class="conn-summary">${esc((other.summary || '').slice(0, 70))}</span>
    </button>${noteRow}`;
}

function connectionsSectionHtml(capId) {
  const { shown, weakCount } = connectionRows(capId);
  if (!shown.length && !weakCount) return '<div class="conn-empty">No connections yet.</div>';
  let html = shown.map(e => connectionRowHtml(e, capId)).join('');
  if (weakCount > 0) {
    html += `<div class="conn-more">+ ${weakCount} weaker tag/entity connection(s) `
      + `below strength ${STRONG_OVERLAP.toFixed(1)}</div>`;
  }
  return html;
}

function captureCard(c) {
  const badges = [
    `<span class="badge">${esc(c.type)}</span>`,
    `<span class="badge">vol ${c.volume}</span>`,
    c.source === 'ai_extract' ? '<span class="badge src-ai">AI-extracted</span>' : '',
    c.superseded ? '<span class="badge sup">superseded</span>' : '',
  ].join(' ');
  const chips = c.tags.map(t =>
    `<button class="chip" title="${esc(t.role || 'tag')}"
      onclick="setTag('${esc(t.value)}', '${esc(t.role)}')">${esc(t.prefix + t.display)}</button>`
  ).join('');
  const fields = Object.entries(c.fields).map(([k, v]) =>
    `<h4>${esc(k.replace(/_/g, ' '))}</h4><div>${esc(v)}</div>`).join('');
  const textLabel = c.corrected ? 'corrected transcription' : 'raw text';
  return `<article class="card${c.superseded ? ' superseded' : ''}" id="cap-${c.id}">
    <div class="top"><span class="tid">${esc(label(c))}</span>${badges}
      <span class="date">${esc(c.date)}</span></div>
    <div class="summary">${esc(c.summary) || '<i>(no summary)</i>'}</div>
    <div class="chips">${chips}</div>
    <div class="connections">
      <h4>Connections</h4>
      ${connectionsSectionHtml(c.id)}
    </div>
    <details><summary>fields &amp; ${textLabel}</summary>
      <div class="fields">${fields || '<i>(no parsed fields)</i>'}</div>
      <div class="rawtext">${esc(c.text) || '(empty)'}</div>
    </details>
  </article>`;
}

function renderTimeline(el) {
  const caps = filtered();
  const at = document.getElementById('activetag');
  if (state.tag) {
    at.style.display = 'block';
    at.innerHTML = `<span>filtered to tag: ${esc(state.tag.value)}` +
      (state.tag.roleName ? ` (${esc(state.tag.roleName)})` : '') +
      `</span> <button onclick="clearTag()">clear</button>`;
  } else if (state.entity) {
    at.style.display = 'block';
    at.innerHTML = `<span>filtered to entity: ${esc(state.entity.name)} (${esc(state.entity.kind)})</span>
      <button onclick="clearTag()">clear</button>`;
  } else { at.style.display = 'none'; }
  el.innerHTML = caps.length
    ? caps.map(captureCard).join('')
    : '<div class="empty">No captures match the current filters.</div>';
}

function renderIndex(el) {
  let html = '';
  const ents = DATA.entities.filter(e => e.capture_ids.length > 0);
  if (ents.length) {
    html += `<div class="idx-section"><h2>Entities <small>· people, places, works, symbols</small></h2>
      <div class="idx-list">` + ents.map(e =>
        `<button class="idx-item" onclick="setEntity(${e.id})">
          ${esc(e.name)}<span class="kind">${esc(e.kind)}</span>
          <span class="n">×${e.capture_ids.length}</span></button>`).join('') +
      '</div></div>';
  }
  for (const [role, title] of ROLE_SECTIONS) {
    const counts = {};
    for (const c of DATA.captures) {
      if (c.superseded) continue;
      for (const t of c.tags) {
        if (t.role !== role) continue;
        (counts[t.value] ||= { display: t.prefix + t.display, n: 0 }).n++;
      }
    }
    const items = Object.entries(counts).sort((a, b) => b[1].n - a[1].n);
    if (!items.length) continue;
    html += `<div class="idx-section"><h2>${esc(title)} <small>· ${items.length}</small></h2>
      <div class="idx-list">` + items.map(([v, d]) =>
        `<button class="idx-item" onclick="setTag('${esc(v)}', '${esc(role)}')">
          ${esc(d.display)}<span class="n">×${d.n}</span></button>`).join('') +
      '</div></div>';
  }
  el.innerHTML = html || '<div class="empty">Nothing indexed yet — upload some captures.</div>';
}

function render() {
  if (state.mode === 'timeline') renderTimeline(document.getElementById('view-timeline'));
  else if (state.mode === 'index') renderIndex(document.getElementById('view-index'));
  // graph mode keeps its own persistent DOM (see ensureGraphInit) — no
  // innerHTML replacement here, that would blow away node positions.
}

// ---- mode 4: rotating globe, with a nested inner globe for dreams ----
//
// A small hand-written spherical projection (no CDN dependency — "opens in
// any browser, no network" rules that out). Each capture sits at a FIXED
// point on a sphere — the most-connected capture is placed dead center of
// the equator, the rest spiral outward from there by connection rank.
// Dream Capture (DC) entries get their OWN smaller, concentric inner
// sphere — dreams are a distinct, more personal category — and any
// connection reaching from a DC capture out to the main sphere renders as
// a visually distinct "rod" strut between the two nested globes.
//
// The globe does one bounded, decaying spin (like a coin settling, not a
// perpetual motion machine) on first activation, then comes to rest.
// Reset Layout, the strength slider, and every personalization toggle
// below all explicitly halt any spin in progress rather than letting one
// keep running underneath a control the user just touched.
//
// Weak tag-overlap edges are excluded by default (adjustable via the
// strength slider), matching the server's own traversal rule
// (connections.py _edge_map) — a graph where every capture connects to
// hundreds of others is a hairball, not a graph.

function filterGraphEdges(edges, minStrength) {
  return edges.filter(e => e.type !== 'tag_overlap' || e.strength >= minStrength);
}

function computeDegrees(nodeIds, edges) {
  const deg = new Map(nodeIds.map(id => [id, 0]));
  for (const e of edges) {
    deg.set(e.source, (deg.get(e.source) || 0) + 1);
    deg.set(e.target, (deg.get(e.target) || 0) + 1);
  }
  return deg;
}

const GRAPH_W = 900, GRAPH_H = 560, GRAPH_CX = GRAPH_W / 2, GRAPH_CY = GRAPH_H / 2;
const GLOBE_SIZE_DEFAULT = 200, GLOBE_SIZE_MIN = 80, GLOBE_SIZE_MAX = 260;
const DC_GLOBE_RATIO = 0.35; // inner sphere radius, as a fraction of the outer one
const GRAPH_DEFAULT_STRENGTH = 3.5;
const SPIN_INITIAL_VELOCITY = 0.05; // radians/frame at the start of the reveal spin
const SPIN_DECAY = 0.985;           // multiplicative decay per frame — a graceful slow-down
const SPIN_STOP_THRESHOLD = 0.00005;

let graphInitialized = false;
let graphNodes = [], graphSimLinks = [];
let graphSelected = null;
let currentMinStrength = GRAPH_DEFAULT_STRENGTH;
let globePositions = new Map();   // outer-sphere capture id -> {lat, lon}
let dcGlobePositions = new Map(); // inner-sphere (DC) capture id -> {lat, lon}
let globeRotation = 0;
let globeRadius = GLOBE_SIZE_DEFAULT;
let globeLoopRunning = false;
let spinVelocity = 0;
let dragActive = false, dragStartX = 0, dragMoved = false, dragStartRotation = 0, dragNodeHit = null;
let graphShowSuperseded = false;
let graphSourceFilter = '';
let graphAutoRotate = true;
let graphForceLabels = false;

function svgEl(tag, attrs) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}

function dcRadius() { return globeRadius * DC_GLOBE_RATIO; }
function labelsVisible() { return graphForceLabels || graphNodes.length <= 40; }

// Deterministic sphere placement, ranked by connection count — NOT random.
// The single most-connected capture in the given set sits at (lat=0,
// lon=0): dead center of the equator, front-and-center at rotation=0. The
// rest spiral outward by rank: remaining nodes alternate north/south
// hemispheres with latitude magnitude growing with rank (so "distance from
// the equator" visually encodes "how connected"), longitude spreads via
// the golden angle for even coverage as the globe turns. Called once for
// the outer (non-DC) captures and once for the DC captures — each sphere
// gets its own independent equator-center. Computed once per sphere and
// cached — the strength slider changes which EDGES are visible, never
// where a capture physically sits, so each sphere's shape stays a stable
// reference frame across every interaction, matching Reset Layout's
// "resets the view, not the data" principle.
function computeGlobePositions(captures, degreeMap) {
  const ranked = [...captures].sort((a, b) => (degreeMap.get(b.id) || 0) - (degreeMap.get(a.id) || 0));
  const n = ranked.length;
  const positions = new Map();
  if (!n) return positions;
  positions.set(ranked[0].id, { lat: 0, lon: 0 }); // most-connected: exact equator center
  if (n === 1) return positions;

  const golden = Math.PI * (3 - Math.sqrt(5));
  const m = n - 1; // remaining captures to spiral outward from the equator
  ranked.slice(1).forEach((c, idx) => {
    const rank = idx + 1; // 1..m
    const hemisphere = rank % 2 === 1 ? 1 : -1;
    // A SMOOTH (not discretized) progression from just-off-equator to
    // near-pole as rank increases: t = rank/m grows continuously, so every
    // rank gets a distinct latitude magnitude. The earlier version used
    // Math.ceil(rank/2), which groups PAIRS of ranks onto the exact same
    // latitude band (differing only by hemisphere) — a coarse
    // approximation that visibly clustered nodes together at some viewing
    // angles. The golden-angle longitude spiral is unchanged.
    const t = rank / m;
    const lat = hemisphere * t * (Math.PI / 2) * 0.94;
    const lon = golden * rank;
    positions.set(c.id, { lat, lon });
  });
  return positions;
}

// Orthographic sphere -> 2D projection at an arbitrary radius (the outer
// main sphere and the inner DC sphere share the same center and rotation,
// differing only in radius). depth is 0 (far side) .. 1 (near side); used
// to scale/dim/reorder elements for the 3D illusion.
function projectOnSphere(pos, rotation, radius) {
  const lon = pos.lon + rotation;
  const cosLat = Math.cos(pos.lat);
  const x3d = radius * cosLat * Math.sin(lon);
  const y3d = radius * Math.sin(pos.lat);
  const z3d = radius * cosLat * Math.cos(lon);
  return { x: GRAPH_CX + x3d, y: GRAPH_CY - y3d, depth: (z3d / radius + 1) / 2 };
}

// Personalization: which captures are currently eligible to appear at all
// (independent of the strength slider, which only ever filters EDGES).
function graphVisibleCaptures() {
  return DATA.captures.filter(c =>
    (graphShowSuperseded || !c.superseded) &&
    (!graphSourceFilter || c.source === graphSourceFilter)
  );
}

function buildGraphData(minStrength) {
  currentMinStrength = minStrength;
  const visible = graphVisibleCaptures();
  const filteredEdges = filterGraphEdges(DATA.edges, minStrength);
  const degrees = computeDegrees(visible.map(c => c.id), filteredEdges);

  const outerCaptures = visible.filter(c => c.type !== 'DC');
  const dcCaptures = visible.filter(c => c.type === 'DC');
  const fullDegrees = computeDegrees(visible.map(c => c.id), DATA.edges);
  if (globePositions.size === 0 && outerCaptures.length) {
    globePositions = computeGlobePositions(outerCaptures, fullDegrees);
  }
  if (dcGlobePositions.size === 0 && dcCaptures.length) {
    dcGlobePositions = computeGlobePositions(dcCaptures, fullDegrees);
  }

  graphNodes = visible.map(c => {
    const deg = degrees.get(c.id) || 0;
    const r = Math.max(5, Math.min(16, 5 + deg * 1.3));
    const isDC = c.type === 'DC';
    const pos = (isDC ? dcGlobePositions : globePositions).get(c.id);
    if (!pos) return null; // defensive — shouldn't happen
    return { id: c.id, lat: pos.lat, lon: pos.lon, r, deg, cap: c, isDC };
  }).filter(Boolean);

  const nodeById = new Map(graphNodes.map(n => [n.id, n]));
  graphSimLinks = filteredEdges
    .map(e => ({ ...e, _s: nodeById.get(e.source), _t: nodeById.get(e.target) }))
    .filter(l => l._s && l._t);
}

// ---- graph summary text: a plain-language description of what's on
// screen right now, recomputed whenever the strength threshold changes ----

function computeComponents(nodeIds, edges) {
  const parent = new Map(nodeIds.map(id => [id, id]));
  function find(x) {
    while (parent.get(x) !== x) { parent.set(x, parent.get(parent.get(x))); x = parent.get(x); }
    return x;
  }
  function union(a, b) { const ra = find(a), rb = find(b); if (ra !== rb) parent.set(ra, rb); }
  for (const e of edges) union(e.source, e.target);
  const groups = new Map();
  for (const id of nodeIds) {
    const root = find(id);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(id);
  }
  return [...groups.values()];
}

function summarizeGraph(nodes, links) {
  const counts = { reference: 0, entity_overlap: 0, tag_overlap: 0,
    supersedes: 0, refutes: 0, narrows: 0, supports: 0 };
  for (const l of links) {
    if (l.type === 'asserted') counts[l.relation] = (counts[l.relation] || 0) + 1;
    else counts[l.type] = (counts[l.type] || 0) + 1;
  }
  const degreeMap = computeDegrees(nodes.map(n => n.id), links);
  const isolated = nodes.filter(n => (degreeMap.get(n.id) || 0) === 0);
  const components = computeComponents(nodes.map(n => n.id), links);
  const largest = components.reduce((a, b) => (b.length > a.length ? b : a), []);
  const topNode = nodes.reduce(
    (best, n) => (degreeMap.get(n.id) || 0) > (best ? (degreeMap.get(best.id) || 0) : -1) ? n : best,
    null
  );
  return { counts, isolated, components, largest, topNode, degreeMap };
}

function graphSummaryHtml() {
  const nodes = graphNodes, links = graphSimLinks;
  const total = nodes.length;
  if (!total) return 'No captures to show.';
  const s = summarizeGraph(nodes, links);
  const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;

  const dates = nodes.map(n => n.cap.date).filter(Boolean).sort();
  const timeSpan = dates.length
    ? (dates[0] === dates[dates.length - 1]
        ? `All from ${dates[0]}.`
        : `Captures span ${dates[0]} to ${dates[dates.length - 1]}.`)
    : '';

  const sentences = [
    `${plural(total, 'capture')}, ${plural(links.length, 'connection')} shown at strength ≥ ${currentMinStrength.toFixed(1)}.`,
  ];
  if (timeSpan) sentences.push(timeSpan);

  const typeBits = [];
  if (s.counts.reference) typeBits.push(plural(s.counts.reference, 'reference'));
  if (s.counts.entity_overlap) typeBits.push(plural(s.counts.entity_overlap, 'entity overlap'));
  if (s.counts.tag_overlap) typeBits.push(plural(s.counts.tag_overlap, 'tag overlap'));
  const relBits = ['supersedes', 'refutes', 'narrows', 'supports']
    .filter(r => s.counts[r]).map(r => `${s.counts[r]} ${r}`);
  if (relBits.length) typeBits.push(`asserted (${relBits.join(', ')})`);
  if (typeBits.length) sentences.push(typeBits.join(' · ') + '.');

  if (links.length) {
    sentences.push(s.components.length > 1
      ? `Forms ${s.components.length} separate clusters — largest has ${plural(s.largest.length, 'capture')}.`
      : 'All connected captures form a single cluster.');
  }
  if (s.isolated.length) {
    sentences.push(`${plural(s.isolated.length, 'capture')} isolated at this threshold.`);
  }
  if (s.topNode && (s.degreeMap.get(s.topNode.id) || 0) > 0) {
    sentences.push(`Most connected (equator center): <span class="tid">${esc(label(s.topNode.cap))}</span> `
      + `(${plural(s.degreeMap.get(s.topNode.id), 'connection')}).`);
  }
  return sentences.join(' ');
}

function renderGraphSummary() {
  const el = document.getElementById('graph-summary');
  if (el) el.innerHTML = graphSummaryHtml();
}

function relationClass(e) {
  return e.type === 'asserted' && e.relation ? ` e-rel-${e.relation}` : '';
}

function renderGraphStructure() {
  const edgesG = document.getElementById('graph-edges');
  const nodesG = document.getElementById('graph-nodes');
  edgesG.innerHTML = '';
  for (const l of graphSimLinks) {
    const directional = l.type === 'reference' || l.type === 'asserted';
    const isRod = l._s.isDC !== l._t.isDC;
    const line = svgEl('line', { class: `edge e-${l.type}${relationClass(l)}${isRod ? ' e-rod' : ''}` });
    if (directional) line.setAttribute('marker-end', 'url(#arrow)');
    line._link = l;
    edgesG.appendChild(line);
  }
  nodesG.innerHTML = '';
  const showLabels = labelsVisible();
  for (const n of graphNodes) {
    const c = n.cap;
    const g = svgEl('g', {
      class: `node t-${c.type}${c.superseded ? ' superseded' : ''}${c.source === 'ai_extract' ? ' src-ai' : ''}`,
      'data-id': c.id,
    });
    g.appendChild(svgEl('circle', { class: 'body', r: n.r }));
    if (showLabels) {
      const text = svgEl('text', { class: 'node-label', x: n.r + 3, y: 3 });
      text.textContent = label(c);
      g.appendChild(text);
      g._label = text;
    }
    g._node = n;
    g.addEventListener('pointerenter', () => showGraphTooltip(n));
    g.addEventListener('pointerleave', hideGraphTooltip);
    nodesG.appendChild(g);
  }
  updateGlobeGuides();
  renderGraphPositions();
  renderGraphSummary();
}

// Keeps the static outline/equator/inner-sphere guide shapes in sync with
// the current (user-adjustable) globe size, and hides the inner-sphere
// guide entirely when there are no DC captures to nest inside it.
function updateGlobeGuides() {
  document.getElementById('globe-outline').setAttribute('r', globeRadius);
  document.getElementById('globe-equator').setAttribute('x1', GRAPH_CX - globeRadius);
  document.getElementById('globe-equator').setAttribute('x2', GRAPH_CX + globeRadius);
  const dcOutline = document.getElementById('dc-globe-outline');
  dcOutline.setAttribute('r', dcRadius());
  dcOutline.style.display = graphNodes.some(n => n.isDC) ? '' : 'none';
}

// Projects every node/edge to its current screen position and re-orders the
// DOM back-to-front (appendChild on an already-attached element moves it,
// no removal/recreation needed) so nearer elements draw over farther ones —
// without this the sphere illusion breaks the moment two elements overlap
// in the wrong z-order. Each element carries its data via a direct property
// (_node / _link) rather than array-index correspondence, because the
// reordering itself changes DOM order every single frame. DC nodes project
// against the smaller inner-sphere radius; everything else against the
// outer one — both share the same center and rotation angle.
function renderGraphPositions() {
  const nodesG = document.getElementById('graph-nodes');
  const edgesG = document.getElementById('graph-edges');
  if (!nodesG || !edgesG) return; // graph host was replaced (empty-state message)

  const dcR = dcRadius();
  const proj = new Map(graphNodes.map(n =>
    [n.id, projectOnSphere({ lat: n.lat, lon: n.lon }, globeRotation, n.isDC ? dcR : globeRadius)]));

  const nodeOrder = [...nodesG.children]
    .map(el => ({ el, p: proj.get(el._node.id) }))
    .sort((a, b) => a.p.depth - b.p.depth);
  for (const { el, p } of nodeOrder) {
    const scale = 0.5 + p.depth * 0.6;
    el.setAttribute('transform', `translate(${p.x},${p.y}) scale(${scale})`);
    el.style.opacity = String(0.3 + p.depth * 0.7);
    // Only the near (front-facing) half of the sphere keeps its label
    // visible each frame — with every node labeled all the time, several
    // captures land close together on screen at some rotation angles and
    // their text piles up illegibly. Hiding far-side labels roughly halves
    // how many are shown at any moment and drops exactly the ones too
    // small/faint to read anyway. "Always show labels" means always,
    // including the far side, so it overrides this.
    if (el._label) el._label.style.display = (graphForceLabels || p.depth > 0.5) ? '' : 'none';
    nodesG.appendChild(el);
  }

  const edgeOrder = [...edgesG.children]
    .map(el => {
      const l = el._link;
      const ps = proj.get(l._s.id), pt = proj.get(l._t.id);
      return { el, ps, pt, depth: (ps.depth + pt.depth) / 2 };
    })
    .sort((a, b) => a.depth - b.depth);
  for (const { el, ps, pt, depth } of edgeOrder) {
    el.setAttribute('x1', ps.x); el.setAttribute('y1', ps.y);
    el.setAttribute('x2', pt.x); el.setAttribute('y2', pt.y);
    el.style.opacity = String(0.1 + depth * 0.7);
    edgesG.appendChild(el);
  }
}

// ---- rotation: a BOUNDED, decaying spin (like a coin settling), never a
// perpetual animation. spinVelocity decays toward 0 every tick it's active;
// once it crosses the stop threshold the loop fully stops rescheduling
// itself (globeLoopRunning=false) rather than idling forever. Reset, the
// strength slider, and every personalization toggle explicitly zero
// spinVelocity, so nothing can keep "continuing" underneath a control the
// user just touched — that was the exact bug reported against the
// previous (perpetual, never-stopping) version. Dragging or selecting a
// node PAUSES rotation (freezes it, keeps the loop hot to resume the
// instant the pause clears) — a materially different state from actually
// having settled to a stop.
function startRevealSpin() {
  if (!graphAutoRotate || prefersReducedMotion) return;
  spinVelocity = SPIN_INITIAL_VELOCITY;
  if (!globeLoopRunning) { globeLoopRunning = true; requestAnimationFrame(stepGlobe); }
}
function stepGlobe() {
  if (state.mode !== 'graph') { globeLoopRunning = false; return; }
  const paused = dragActive || graphSelected !== null;
  const spinEnabled = graphAutoRotate && !prefersReducedMotion;
  let stillSpinning = false;
  if (!paused) {
    if (spinEnabled && Math.abs(spinVelocity) > SPIN_STOP_THRESHOLD) {
      globeRotation += spinVelocity;
      spinVelocity *= SPIN_DECAY;
      stillSpinning = Math.abs(spinVelocity) > SPIN_STOP_THRESHOLD;
      if (!stillSpinning) spinVelocity = 0; // clamp the final decayed residual to an exact, clean stop
    } else {
      spinVelocity = 0;
    }
  }
  renderGraphPositions();
  if (paused || stillSpinning) {
    requestAnimationFrame(stepGlobe); // still spinning, or a temporary pause that will resume
  } else {
    globeLoopRunning = false; // settled (or spin disabled) — fully stop until something restarts it
  }
}

function ensureGraphInit() {
  if (graphInitialized) return;
  graphInitialized = true;
  const host = document.getElementById('view-graph');
  if (!DATA.captures.length) {
    host.innerHTML = '<div class="empty">No captures yet — nothing to graph.</div>';
    return;
  }
  buildGraphData(GRAPH_DEFAULT_STRENGTH);
  wireGraphInteraction();
  renderGraphStructure();
  // Hold the initial static view — most-connected capture front and center
  // on the equator — for a beat before the spin begins, so the viewer
  // registers "here is my data" before watching it turn.
  setTimeout(startRevealSpin, 400);
}

function onGraphStrengthChange() {
  const v = parseFloat(document.getElementById('gminstrength').value);
  document.getElementById('gminstrength-val').textContent = v.toFixed(1);
  spinVelocity = 0; // halt any in-progress spin the moment the user adjusts anything
  buildGraphData(v);
  clearGraphSelection();
  renderGraphStructure();
}

function onGlobeSizeChange() {
  globeRadius = parseFloat(document.getElementById('gsize').value);
  document.getElementById('gsize-val').textContent = String(globeRadius);
  spinVelocity = 0;
  updateGlobeGuides();
  renderGraphPositions();
}

function onGraphSupersededToggle() {
  graphShowSuperseded = document.getElementById('gshowsup').checked;
  spinVelocity = 0;
  globePositions = new Map(); dcGlobePositions = new Map(); // the visible pool changed
  buildGraphData(currentMinStrength);
  clearGraphSelection();
  renderGraphStructure();
}

function onGraphSourceChange() {
  graphSourceFilter = document.getElementById('gsource').value;
  spinVelocity = 0;
  globePositions = new Map(); dcGlobePositions = new Map();
  buildGraphData(currentMinStrength);
  clearGraphSelection();
  renderGraphStructure();
}

function onGraphAutoRotateToggle() {
  graphAutoRotate = document.getElementById('gautorotate').checked;
  if (graphAutoRotate) startRevealSpin(); else spinVelocity = 0;
}

function onGraphLabelsToggle() {
  graphForceLabels = document.getElementById('gforcelabels').checked;
  renderGraphStructure();
}

function resetGraphLayout() {
  // Resets the VIEW, not the data: rotation, size, and every
  // personalization toggle return to their defaults, and any spin in
  // progress is explicitly halted — Reset snaps directly to a static
  // view, it does not replay the intro spin. globePositions/
  // dcGlobePositions ARE recomputed here (the visible pool may have
  // changed via the toggles being reset), but which capture sits where
  // on either sphere is otherwise a stable structural fact this action
  // does not reshuffle.
  document.getElementById('gminstrength').value = String(GRAPH_DEFAULT_STRENGTH);
  document.getElementById('gminstrength-val').textContent = GRAPH_DEFAULT_STRENGTH.toFixed(1);
  document.getElementById('gsize').value = String(GLOBE_SIZE_DEFAULT);
  document.getElementById('gsize-val').textContent = String(GLOBE_SIZE_DEFAULT);
  document.getElementById('gshowsup').checked = false;
  document.getElementById('gsource').value = '';
  document.getElementById('gautorotate').checked = true;
  document.getElementById('gforcelabels').checked = false;

  graphShowSuperseded = false;
  graphSourceFilter = '';
  graphAutoRotate = true;
  graphForceLabels = false;
  globeRadius = GLOBE_SIZE_DEFAULT;
  globeRotation = 0;
  spinVelocity = 0;
  globePositions = new Map();
  dcGlobePositions = new Map();

  buildGraphData(GRAPH_DEFAULT_STRENGTH);
  clearGraphSelection();
  renderGraphStructure();
}

// ---- graph interaction: drag anywhere to spin the globe; a stationary
// click on a node selects it, on empty space deselects ----

function svgPoint(evt) {
  const svg = document.getElementById('graph-svg');
  const pt = svg.createSVGPoint();
  pt.x = evt.clientX; pt.y = evt.clientY;
  const ctm = svg.getScreenCTM();
  if (!ctm) return { x: 0, y: 0 };
  const p = pt.matrixTransform(ctm.inverse());
  return { x: p.x, y: p.y };
}

function findNodeAt(target) {
  let el = target;
  while (el && !el._node) el = el.parentNode;
  return el ? el._node : null;
}

function onSvgPointerDown(ev) {
  dragActive = true; dragMoved = false;
  dragNodeHit = findNodeAt(ev.target);
  dragStartX = svgPoint(ev).x;
  dragStartRotation = globeRotation;
  document.getElementById('graph-svg').setPointerCapture(ev.pointerId);
}
function onSvgPointerMove(ev) {
  if (!dragActive) return;
  const x = svgPoint(ev).x;
  const dx = x - dragStartX;
  if (!dragMoved && Math.abs(dx) > 2) {
    dragMoved = true;
    document.getElementById('graph-svg').classList.add('dragging');
  }
  if (dragMoved) {
    globeRotation = dragStartRotation + dx * 0.01;
    renderGraphPositions();
  }
}
function onSvgPointerUp() {
  if (!dragActive) return;
  dragActive = false;
  document.getElementById('graph-svg').classList.remove('dragging');
  if (!dragMoved) {
    if (dragNodeHit) selectGraphNode(dragNodeHit);
    else clearGraphSelection();
  } else {
    spinVelocity = 0; // a manual rotation ends any leftover reveal-spin momentum
  }
  dragNodeHit = null;
}

function wireGraphInteraction() {
  const svg = document.getElementById('graph-svg');
  svg.addEventListener('pointerdown', onSvgPointerDown);
  svg.addEventListener('pointermove', onSvgPointerMove);
  svg.addEventListener('pointerup', onSvgPointerUp);
}

function selectGraphNode(n) {
  graphSelected = n.id;
  const neighborIds = new Set([n.id]);
  for (const l of graphSimLinks) {
    if (l._s.id === n.id) neighborIds.add(l._t.id);
    if (l._t.id === n.id) neighborIds.add(l._s.id);
  }
  for (const el of document.getElementById('graph-nodes').children) {
    el.classList.toggle('dimmed', !neighborIds.has(el._node.id));
  }
  for (const el of document.getElementById('graph-edges').children) {
    const l = el._link;
    el.classList.toggle('dimmed', l._s.id !== n.id && l._t.id !== n.id);
  }
  showGraphPanel(n);
}
function clearGraphSelection() {
  graphSelected = null;
  for (const el of document.getElementById('graph-nodes').children) el.classList.remove('dimmed');
  for (const el of document.getElementById('graph-edges').children) el.classList.remove('dimmed');
  hideGraphPanel();
}

function showGraphPanel(n) {
  const c = n.cap;
  const panel = document.getElementById('graph-panel');
  panel.hidden = false;
  panel.innerHTML = `
    <h3>${esc(label(c))}</h3>
    <div class="meta2">${esc(c.type)} · vol ${c.volume} · ${esc(c.date)} · ${n.deg} connection(s)</div>
    <div>${esc((c.summary || '').slice(0, 140)) || '<i>(no summary)</i>'}</div>
    <button class="btn" onclick="gotoCapture(${c.id})">Open in Timeline →</button>
  `;
}
function hideGraphPanel() { document.getElementById('graph-panel').hidden = true; }

function showGraphTooltip(n) {
  const p = projectOnSphere({ lat: n.lat, lon: n.lon }, globeRotation, n.isDC ? dcRadius() : globeRadius);
  // A static label is only actually legible for THIS node right now if
  // labels are rendered at all AND (force-on, or it's currently far-side
  // enough to have been hidden per the same rule renderGraphPositions
  // uses) — checking only the blanket node-count gate would skip the
  // tooltip for a node whose own label is invisible at this rotation,
  // leaving it with no identification at all.
  if (labelsVisible() && (graphForceLabels || p.depth > 0.5)) return;
  const tip = document.getElementById('graph-tooltip');
  tip.hidden = false;
  tip.textContent = label(n.cap);
  const svg = document.getElementById('graph-svg');
  const rect = svg.getBoundingClientRect();
  tip.style.left = (p.x * (rect.width / GRAPH_W)) + 'px';
  tip.style.top = (p.y * (rect.height / GRAPH_H)) + 'px';
}
function hideGraphTooltip() { document.getElementById('graph-tooltip').hidden = true; }

(function init() {
  const caps = DATA.captures;
  document.getElementById('meta').textContent =
    `${caps.length} capture(s) · ${DATA.entities.length} entit(ies) · current writing volume: ${DATA.current_volume}`;
  document.getElementById('gen').textContent = DATA.generated_at.slice(0, 16).replace('T', ' ');
  const scope = document.getElementById('scope');
  scope.textContent = DATA.active_volumes === null
    ? ''
    : `Server read scope is volume(s) ${DATA.active_volumes.join(', ')} — this file contains ALL volumes; use the volume filter below.`;
  const types = [...new Set(caps.map(c => c.type))].sort();
  const ftype = document.getElementById('ftype');
  for (const t of types) ftype.add(new Option(t, t));
  const vols = [...new Set(caps.map(c => c.volume))].sort((a, b) => a - b);
  const fvol = document.getElementById('fvol');
  for (const v of vols) fvol.add(new Option('volume ' + v, String(v)));
  render();
})();
</script>
</body>
</html>
"""


def render_html(data: dict) -> str:
    """Render collected view data into the self-contained HTML page."""
    # '</' must never appear literally inside the JSON script block — page
    # text containing '</script>' would otherwise terminate it.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    sections = json.dumps(_ROLE_SECTIONS)
    return _TEMPLATE.replace("__DATA__", payload).replace("__ROLE_SECTIONS__", sections)
