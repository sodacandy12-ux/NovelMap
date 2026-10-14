const REL = {"가족": "var(--rel-family)", "연애·혼인": "var(--rel-love)", "협력·동료": "var(--rel-coop)",
  "위계·소속": "var(--rel-rank)", "적대·갈등": "var(--rel-foe)", "기타·중립": "var(--rel-etc)"};
const CAT = {"인물": "var(--cat-char)", "장소": "var(--cat-place)", "조직": "var(--cat-org)", "물건": "var(--cat-item)", "용어": "var(--cat-term)"};
const $ = s => document.querySelector(s);
let NID = null, DATA = {}, LORE = [], loreCat = "", hl = {characters: [], events: [], lore: []}, LLM = {configured: false};

const api = async (path, opt) => {
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const legend = map => Object.entries(map).map(([k, v]) => `<span><i style="background:${v}"></i>${k}</span>`).join("");

document.querySelectorAll("#tabs button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#tabs button").forEach(x => x.classList.toggle("on", x === b));
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("on", v.id === "v-" + b.dataset.tab));
  if (b.dataset.tab === "graph") renderGraph();
  if (b.dataset.tab === "timeline") drawTimeline();
});

/* ---------- LLM 설정 바 ---------- */
let PROVS = [];
function fillModels() {
  const p = PROVS.find(x => x.id === $("#provSel").value);
  $("#modelSel").innerHTML = p && p.ready ? p.models.map(m => `<option value="${esc(m.id)}">${esc(m.label)}</option>`).join("") : '<option value="">준비 중</option>';
  $("#btnKey").disabled = !(p && p.ready);
}
function showStatus(cur, msg, cls) {
  const el = $("#llmStatus");
  el.className = "badge " + (cls || "");
  el.textContent = msg || (cur.configured ? `연결됨 (${cur.key_source === "env" ? "환경 변수 키" : "저장된 키"})` : "API 키 필요 — 키가 없으면 분석이 시작되지 않습니다");
  LLM = cur;
  $("#btnAnalyze").disabled = !cur.configured || $("#btnAnalyze").textContent === "분석 중…";
  $("#apiKey").placeholder = cur.configured ? "••••••••••••  (저장됨)" : "API 키 (서버 메모리에만 저장)";
}
async function loadProviders() {
  const d = await api("/llm/providers");
  PROVS = d.providers;
  $("#provSel").innerHTML = PROVS.map(p => `<option value="${p.id}" ${p.ready ? "" : "disabled"}>${esc(p.name)}${p.ready ? "" : " (준비 중)"}</option>`).join("");
  $("#provSel").value = d.current.provider;
  fillModels();
  if (d.current.model) $("#modelSel").value = d.current.model;
  showStatus(d.current);
}
$("#provSel").onchange = fillModels;
$("#btnKey").onclick = async () => {
  const body = {provider: $("#provSel").value, model: $("#modelSel").value, api_key: $("#apiKey").value || null};
  $("#btnKey").disabled = true; $("#llmStatus").className = "badge"; $("#llmStatus").textContent = "연결 테스트 중…";
  try {
    const r = await api("/llm/config", {method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    $("#apiKey").value = "";   // 저장 후 키를 화면에 남기지 않는다
    if (r.ok) showStatus({configured: true, key_source: "memory"}, `연결 성공 · ${body.model}`, "ok");
    else showStatus({configured: !!r.configured, key_source: "memory"}, "실패: " + r.error, "err");
  } catch (e) { showStatus(LLM, "실패: " + e.message, "err"); }
  $("#btnKey").disabled = false;
};

/* ---------- 근거 장면 패널 ---------- */
function markQuote(text, quote) {
  let html = esc(text);
  if (!quote) return html;
  const words = quote.trim().split(/\s+/).map(w => esc(w).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  try { return html.replace(new RegExp(words.join("\\s+"), "i"), m => `<mark>${m}</mark>`); } catch (e) { return html; }
}
async function sceneCard(sid, quote, label, cmp) {
  const d = await api(`/novels/${NID}/scenes/${sid}`);
  const s = d.scene;
  const div = document.createElement("div");
  div.className = "scene" + (cmp ? " cmp" : "");
  div.innerHTML = `<h3>${esc(label || "")} ${s.scene_id} · ${s.chapter}장${s.chapter_title ? " " + esc(s.chapter_title) : ""}</h3>
    <p>${markQuote(s.text, quote)}</p>
    <div class="nav">${d.prev ? `<button data-s="${d.prev.scene_id}">◀ 이전 장면</button>` : ""}${d.next ? `<button data-s="${d.next.scene_id}">다음 장면 ▶</button>` : ""}</div>`;
  div.querySelectorAll("button").forEach(b => b.onclick = () => openEvidence([{sid: b.dataset.s}]));
  return div;
}
async function openEvidence(items, title, headNode) {
  const box = $("#evidence");
  box.innerHTML = title ? `<p><b>${esc(title)}</b></p>` : "";
  if (headNode) box.appendChild(headNode);
  const area = document.createElement("div"); area.id = "sceneArea"; box.appendChild(area);
  for (const it of items) area.appendChild(await sceneCard(it.sid, it.quote, it.label, items.length > 1));
  box.scrollTop = 0;
}
async function openSceneInArea(sid, quote, label) {
  const area = $("#sceneArea"); if (!area) return openEvidence([{sid, quote, label}]);
  area.innerHTML = ""; area.appendChild(await sceneCard(sid, quote, label));
}

/* ---------- 하이라이트 ---------- */
function applyHighlight() {
  const any = hl.characters.length || hl.events.length || hl.lore.length;
  d3.selectAll(".node").classed("hl", d => hl.characters.includes(d.id)).classed("dim", d => any && hl.characters.length && !hl.characters.includes(d.id));
  d3.selectAll(".linkg").classed("dim", d => any && hl.characters.length && !(hl.characters.includes(d.source.id ?? d.source) && hl.characters.includes(d.target.id ?? d.target)));
  document.querySelectorAll(".ev").forEach(e => { e.classList.toggle("hl", hl.events.includes(e.dataset.id)); e.classList.toggle("dim", any && hl.events.length && !hl.events.includes(e.dataset.id)); });
  document.querySelectorAll(".lcard").forEach(e => { e.classList.toggle("hl", hl.lore.includes(e.dataset.id)); e.classList.toggle("dim", any && hl.lore.length && !hl.lore.includes(e.dataset.id)); });
  d3.selectAll(".node.hl,.ev.hl,.lcard.hl").classed("blink", true);
}

/* ---------- 인물 관계도 ---------- */
// 관계 선 표현: 색 + 선 모양 + 중앙 기호 (색만으로 구분하지 않는다)
const REL_STYLE = {"가족": {dash: "", w: 2}, "연애·혼인": {dash: "", w: 2, mark: "♥"}, "협력·동료": {dash: "", w: 2},
  "위계·소속": {dash: "", w: 2}, "적대·갈등": {dash: "7 4", w: 3.6, mark: "✕"}, "기타·중립": {dash: "", w: 1.1}};
let sim = null, zoomB = null, curK = 1, graphCtl = null;
const relHidden = new Set();
const NEO = {ok: false, why: ""};

function buildLegend() {
  $("#relLegend").innerHTML = Object.entries(REL).map(([t, c]) => {
    const s = REL_STYLE[t];
    return `<button data-t="${t}" class="${relHidden.has(t) ? "off" : ""}" title="누르면 이 관계 선을 켜고 끕니다"><svg viewBox="0 0 30 12">
      <line x1="1" y1="6" x2="29" y2="6" stroke="${c}" stroke-width="${Math.max(1.5, s.w)}" stroke-dasharray="${s.dash}"/>${s.mark ? `<text x="15" y="10" font-size="11" text-anchor="middle" fill="${c}">${s.mark}</text>` : ""}</svg>${t}</button>`;
  }).join("") + '<span class="muted">→ 방향 있는 관계</span>';
  $("#relLegend").querySelectorAll("button").forEach(b => b.onclick = () => {
    const t = b.dataset.t; relHidden.has(t) ? relHidden.delete(t) : relHidden.add(t);
    buildLegend(); d3.selectAll(".linkg").style("display", d => relHidden.has(d.type) ? "none" : null);
  });
}
buildLegend();

function labelLimit(k) { return k < 0.5 ? 6 : k < 0.9 ? 12 : k < 1.4 ? 25 : Infinity; }   // 축소하면 상위 인물만, 확대하면 나머지도 표시
function updateLabels() {
  const lim = labelLimit(curK);
  d3.selectAll(".node text").style("display", d => (d.rank < lim || d3.select(d._el).classed("pathnode") || d3.select(d._el).classed("hl")) ? null : "none");
}

async function renderGraph() {
  if (!NID) return;
  let g;
  try { g = await api(`/novels/${NID}/graph`); } catch (e) { $("#svg").innerHTML = ""; return; }
  const showAll = $("#showAll").checked;
  fillPathSelects(g.nodes);
  const keep = new Set((showAll ? g.nodes : g.nodes.slice(0, 12)).map(n => n.id));
  const svg = d3.select("#svg"); svg.selectAll("*").remove();
  const box = $("#svg").getBoundingClientRect(), W = box.width || 800, H = box.height || 500;
  const defs = svg.append("defs");
  Object.entries(REL).forEach(([t, c], i) => defs.append("marker").attr("id", "arr" + i).attr("viewBox", "0 -5 10 10").attr("refX", 10).attr("refY", 0)
    .attr("markerUnits", "userSpaceOnUse").attr("markerWidth", 12).attr("markerHeight", 12).attr("orient", "auto").append("path").attr("d", "M0,-5L10,0L0,5").attr("fill", c));
  const relIdx = Object.fromEntries(Object.keys(REL).map((t, i) => [t, i]));
  const nodes = g.nodes.filter(n => keep.has(n.id)).map((d, i) => ({...d, rank: i}));
  const links = g.links.filter(l => keep.has(l.source) && keep.has(l.target)).map(d => ({...d}));
  const r = d3.scaleSqrt().domain([1, d3.max(nodes, d => d.scene_count) || 1]).range([8, 28]);
  const rad = id => r(nodes.find(n => n.id === id)?.scene_count || 1);
  if (sim) sim.stop();
  const root = svg.append("g").attr("class", "viewport");   // 확대·이동은 이 그룹에 적용
  let userMoved = false;
  zoomB = d3.zoom().scaleExtent([0.2, 4]).on("zoom", e => {
    root.attr("transform", e.transform); curK = e.transform.k; if (e.sourceEvent) userMoved = true; updateLabels();
  });
  svg.call(zoomB).on("dblclick.zoom", null);
  sim = d3.forceSimulation(nodes).force("link", d3.forceLink(links).id(d => d.id).distance(190).strength(0.15))
    .force("charge", d3.forceManyBody().strength(-1100)).force("x", d3.forceX(W / 2).strength(0.06)).force("y", d3.forceY(H / 2).strength(0.09))
    .force("collide", d3.forceCollide(d => r(d.scene_count) + 34));
  const lg = root.append("g").selectAll("g").data(links).join("g").attr("class", "linkg").style("display", d => relHidden.has(d.type) ? "none" : null);
  lg.append("line").attr("class", "link").attr("stroke", d => REL[d.type]).attr("stroke-dasharray", d => REL_STYLE[d.type].dash)
    .attr("stroke-width", d => REL_STYLE[d.type].w + Math.min(3, d.evidence.length * 0.4))
    .attr("marker-end", d => d.directed ? `url(#arr${relIdx[d.type]})` : null);
  lg.append("line").attr("class", "link-hit")
    .on("click", (e, d) => openEvidence(d.evidence.map(s => ({sid: s, quote: d.quotes[s], label: d.label || d.type})),
      `${d.source.name} ${d.directed ? "→" : "—"} ${d.target.name} · ${d.type}${d.label ? " / " + d.label : ""} (${d.since_scene || d.since_chapter + "장"}~)`))
    .append("title").text(d => `${d.type}${d.label ? " · " + d.label : ""}${d.directed ? " (방향 있음)" : ""}`);
  lg.append("text").attr("class", "midmark").attr("fill", d => REL[d.type]).text(d => REL_STYLE[d.type].mark || "");
  const ng = root.append("g").selectAll("g").data(nodes).join("g").attr("class", "node")
    .call(d3.drag().on("start", (e, d) => { if (!e.active) sim.alphaTarget(.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on("drag", (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on("end", (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = d.fy = null; }));
  ng.each(function (d) { d._el = this; });
  ng.append("circle").attr("r", d => r(d.scene_count));
  ng.append("text").attr("text-anchor", "middle").attr("dy", d => r(d.scene_count) + 14).text(d => d.name);
  ng.append("title").text(d => `${d.name} · 등장 ${d.scene_count}장면` + (d.aliases.length ? `\n별칭: ${d.aliases.join(", ")}` : ""));
  ng.on("click", (e, d) => {
    if (e.shiftKey) return pickForPath(d.id);           // Shift+클릭: 경로 찾기의 출발/도착 인물 선택
    const ids = new Set([d.id]);
    links.forEach(l => { if (l.source.id === d.id) ids.add(l.target.id); if (l.target.id === d.id) ids.add(l.source.id); });
    ng.classed("dim", n => !ids.has(n.id)); lg.classed("dim", l => l.source.id !== d.id && l.target.id !== d.id);
    const card = LORE.find(x => x.character_id === d.id);
    if (card) showLore(card); else openEvidence(d.scene_ids.slice(0, 3).map(s => ({sid: s, quote: d.name.split(" ").pop(), label: d.name})), `${d.name} — 등장 ${d.scene_count}장면`);
  });
  svg.on("click", e => { if (e.target.tagName === "svg") { ng.classed("dim", false); lg.classed("dim", false); clearPath(true); } });
  sim.on("tick", () => {
    lg.each(function (d) {
      const dx = d.target.x - d.source.x, dy = d.target.y - d.source.y, len = Math.hypot(dx, dy) || 1;
      const tr = d.directed ? rad(d.target.id) + 3 : 0;   // 화살표가 노드 가장자리에서 끝나게 함
      const s = d3.select(this);
      s.selectAll("line").attr("x1", d.source.x).attr("y1", d.source.y).attr("x2", d.target.x - dx / len * tr).attr("y2", d.target.y - dy / len * tr);
      s.select("text").attr("x", (d.source.x + d.target.x) / 2).attr("y", (d.source.y + d.target.y) / 2);
    });
    ng.attr("transform", d => `translate(${d.x},${d.y})`);
  });
  const fit = (animate = true) => {
    const vis = nodes.filter(n => n.x != null);
    if (!vis.length) return;
    const x0 = d3.min(vis, n => n.x - r(n.scene_count)), x1 = d3.max(vis, n => n.x + r(n.scene_count)),
      y0 = d3.min(vis, n => n.y - r(n.scene_count)), y1 = d3.max(vis, n => n.y + r(n.scene_count) + 18);
    const w = $("#svg").clientWidth || W, h = $("#svg").clientHeight || H, pad = 36;
    const k = Math.max(0.2, Math.min(4, Math.min((w - pad * 2) / Math.max(1, x1 - x0), (h - pad * 2) / Math.max(1, y1 - y0))));
    const t = d3.zoomIdentity.translate(w / 2 - k * (x0 + x1) / 2, h / 2 - k * (y0 + y1) / 2).scale(k);
    (animate ? svg.transition().duration(450) : svg).call(zoomB.transform, t);
  };
  // 전체 보기를 켜면 모든 인물이 한 화면에 들어오도록 자동으로 맞추고, 이후엔 직접 확대·이동할 수 있다
  let endFitted = false;
  sim.on("end.fit", () => { if (!userMoved && !endFitted) { endFitted = true; fit(); } });
  setTimeout(() => { if (!userMoved && sim.alpha() < 0.3) fit(); }, 900);
  graphCtl = {
    fit, svg, nodes, ng, lg, W, H,
    zoomBy: f => svg.transition().duration(200).call(zoomB.scaleBy, f),
    focus: id => {
      const n = nodes.find(x => x.id === id); if (!n) return false;
      const k = Math.max(curK, 1.4), w = $("#svg").clientWidth || W, h = $("#svg").clientHeight || H;
      userMoved = true;
      svg.transition().duration(500).call(zoomB.transform, d3.zoomIdentity.translate(w / 2 - k * n.x, h / 2 - k * n.y).scale(k));
      ng.classed("pulse", x => x.id === id).classed("hl", x => x.id === id); setTimeout(() => ng.classed("pulse", false).classed("hl", x => hl.characters.includes(x.id)), 2500);
      return true;
    },
  };
  applyHighlight(); updateLabels();
  if (PATH) drawPath(PATH);
}
$("#showAll").onchange = renderGraph;
$("#zIn").onclick = () => graphCtl && graphCtl.zoomBy(1.4);
$("#zOut").onclick = () => graphCtl && graphCtl.zoomBy(1 / 1.4);
$("#zFit").onclick = () => graphCtl && graphCtl.fit();
$("#findChar").onchange = e => {
  const nm = e.target.value.trim(); if (!nm || !graphCtl) return;
  const n = (DATA.chars || []).find(c => c.name === nm || c.aliases.includes(nm));
  if (!n) return;
  const go = () => graphCtl.focus(n.id);
  if (graphCtl.nodes.some(x => x.id === n.id)) go();
  else { $("#showAll").checked = true; renderGraph().then(() => setTimeout(go, 1000)); }   // 상위 12명 밖의 인물이면 전체 보기로 전환
};
window.addEventListener("resize", () => $("#v-graph").classList.contains("on") && renderGraph());

/* ---------- 관계 경로 찾기 (Neo4j) ---------- */
let PATH = null, pathPick = null;
function fillPathSelects(chars) {
  DATA.chars = chars;
  const opt = '<option value="">인물 선택</option>' + chars.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  const a = $("#pathFrom").value, b = $("#pathTo").value;
  $("#pathFrom").innerHTML = opt; $("#pathTo").innerHTML = opt; $("#pathFrom").value = a; $("#pathTo").value = b;
  $("#charList").innerHTML = chars.map(c => `<option value="${esc(c.name)}">`).join("");
  $("#btnPath").disabled = !NEO.ok;
  $("#btnPath").title = NEO.ok ? "두 인물 사이의 최단 관계 경로" : "Neo4j에 연결되지 않아 사용할 수 없습니다: " + NEO.why;
}
function pickForPath(id) {      // Shift+클릭으로 출발 -> 도착 순서로 고른다
  if (pathPick === null) { $("#pathFrom").value = id; pathPick = id; $("#pathMsg").textContent = "도착 인물을 Shift+클릭하세요"; }
  else { $("#pathTo").value = id; pathPick = null; $("#btnPath").click(); }
}
function clearPath(keepSel) {
  PATH = null; pathPick = null; $("#btnPathClear").hidden = true; $("#pathMsg").textContent = "";
  d3.selectAll(".node").classed("pathnode", false); d3.selectAll(".linkg").classed("pathlink", false);
  if (!keepSel) { $("#pathFrom").value = ""; $("#pathTo").value = ""; }
  d3.selectAll(".node,.linkg").classed("dim", false); updateLabels();
}
function drawPath(p) {
  const ids = new Set(p.nodes.map(n => n.id));
  const key = (a, b) => [a, b].sort().join("|");
  const edges = new Set(p.links.map(l => key(l.source, l.target)));
  d3.selectAll(".node").classed("pathnode", d => ids.has(d.id)).classed("dim", d => !ids.has(d.id));
  d3.selectAll(".linkg").classed("pathlink", d => edges.has(key(d.source.id, d.target.id))).classed("dim", d => !edges.has(key(d.source.id, d.target.id)));
  updateLabels();
}
$("#btnPath").onclick = async () => {
  const a = $("#pathFrom").value, b = $("#pathTo").value, msg = $("#pathMsg");
  if (!a || !b || a === b) { msg.textContent = "서로 다른 두 인물을 고르세요"; return; }
  msg.textContent = "조회 중…";
  try {
    const r = await api(`/novels/${NID}/graph/path?from=${encodeURIComponent(a)}&to=${encodeURIComponent(b)}`);
    if (!r.found) { msg.textContent = "두 인물을 잇는 관계 경로가 없습니다(6단계 이내)"; return; }
    if (r.nodes.some(n => !graphCtl.nodes.some(x => x.id === n.id))) {      // 경로가 상위 12명 밖의 인물을 지나면 전체 보기로 전환
      $("#showAll").checked = true; PATH = r; await renderGraph();
    } else { PATH = r; drawPath(r); }
    $("#btnPathClear").hidden = false;
    msg.textContent = r.nodes.map(n => n.name).join(" → ") + ` (${r.links.length}단계: ${r.links.map(l => l.label || l.type).join(" · ")})`;
  } catch (e) { msg.textContent = e.message; }
};
$("#btnPathClear").onclick = () => clearPath();
fetch("/info").then(r => r.json()).then(i => { Object.assign(NEO, i.neo4j || {}); $("#btnPath").disabled = !NEO.ok; }).catch(() => {});

/* ---------- 사건 타임라인 (세로 = 장면 순서, 가로 = 장소 레인) ---------- */
const lanesOff = new Set();
async function renderTimeline() {
  let t; try { t = await api(`/novels/${NID}/timeline`); } catch (e) { $("#tl").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  DATA.tl = t;
  const cur = $("#fChar").value;
  $("#fChar").innerHTML = '<option value="">전체</option>' + t.characters.map(c => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  $("#fChar").value = t.characters.some(c => c.id === cur) ? cur : "";
  drawTimeline();
}
function drawTimeline() {
  const t = DATA.tl; if (!t) return;
  const fc = $("#fChar").value;
  const lanes = t.lanes;
  // 행 = 사건이 있는 장면 (장면 순서 그대로). 장 구분이 없는 원고도 order만 있으면 그려진다.
  const rows = [...new Map(t.events.map(e => [e.scene_id, e.order])).entries()].sort((a, b) => a[1] - b[1]).map(x => x[0]);
  const rowOf = Object.fromEntries(rows.map((s, i) => [s, i + 2]));
  const colOf = Object.fromEntries(lanes.map((l, i) => [l.id, i + 2]));
  const chOf = {}; t.events.forEach(e => chOf[e.scene_id] = e.chapter);
  $("#tlCount").textContent = fc ? `${t.events.filter(e => e.characters.includes(fc)).length} / ${t.events.length}개 사건` : `${t.events.length}개 사건 · 레인 ${lanes.length}개`;
  const cols = ["64px", ...lanes.map(l => lanesOff.has(l.id) ? "34px" : "minmax(190px,230px)")].join(" ");
  let html = `<div id="tlGrid" style="grid-template-columns:${cols}"><div class="lanehead" style="grid-column:1;grid-row:1;left:0;z-index:5">장면</div>`;
  lanes.forEach(l => html += `<div class="lanehead ${lanesOff.has(l.id) ? "collapsed" : ""}" data-lane="${esc(l.id)}" style="grid-column:${colOf[l.id]};grid-row:1" title="${esc(l.name)} (${l.count}건) — 눌러서 접기/펴기">${esc(l.name)} <small>${l.count}</small></div>`);
  rows.forEach(s => {
    html += `<div class="rowhead" style="grid-column:1;grid-row:${rowOf[s]}">${s}<br>${chOf[s]}장</div>`;
    lanes.forEach(l => html += `<div class="cell ${lanesOff.has(l.id) ? "collapsed" : ""}" style="grid-column:${colOf[l.id]};grid-row:${rowOf[s]}" data-r="${s}|${esc(l.id)}"></div>`);
  });
  html += '</div><svg id="tlSvg"></svg>';
  $("#tl").innerHTML = html;
  const cell = {}; document.querySelectorAll("#tlGrid .cell").forEach(c => cell[c.dataset.r] = c);
  t.events.forEach(e => {
    const c = cell[e.scene_id + "|" + e.lane_id]; if (!c || lanesOff.has(e.lane_id)) return;
    const d = document.createElement("div"); d.className = "ev"; d.dataset.id = e.id;
    d.innerHTML = `<small>${e.scene_id}${e.location ? " · " + esc(e.location) : ""}</small>${esc(e.summary)}<small>${esc(e.character_names.join(", "))}</small>`;
    d.onclick = () => openEvidence([{sid: e.scene_id, quote: e.quote, label: "사건"}], e.summary);
    if (fc) d.classList.add(e.characters.includes(fc) ? "pick" : "faded");
    c.appendChild(d);
  });
  document.querySelectorAll("#tlGrid .lanehead[data-lane]").forEach(h => h.onclick = () => {
    const id = h.dataset.lane; lanesOff.has(id) ? lanesOff.delete(id) : lanesOff.add(id); drawTimeline();
  });
  drawRoute(fc);
  applyHighlight();
}
function drawRoute(fc) {
  const svg = d3.select("#tlSvg"); svg.selectAll("*").remove();
  const grid = $("#tlGrid"); if (!grid) return;
  svg.attr("width", grid.scrollWidth).attr("height", grid.scrollHeight);
  if (!fc) return;
  const o = grid.getBoundingClientRect();
  const pts = [...document.querySelectorAll("#tlGrid .ev.pick")].map(el => {      // 장면 순서대로 이어진 이동 경로
    const b = el.getBoundingClientRect(); return [b.left - o.left + b.width / 2, b.top - o.top + b.height / 2];
  });
  if (pts.length < 2) return;
  svg.append("defs").append("marker").attr("id", "tlArr").attr("viewBox", "0 -5 10 10").attr("refX", 9).attr("markerWidth", 7).attr("markerHeight", 7).attr("orient", "auto")
    .append("path").attr("d", "M0,-5L10,0L0,5").attr("fill", "var(--accent)");
  svg.append("path").attr("d", d3.line().curve(d3.curveMonotoneY)(pts)).attr("fill", "none").attr("stroke", "var(--accent)")
    .attr("stroke-width", 2.5).attr("stroke-opacity", .75).attr("stroke-linejoin", "round").attr("marker-end", "url(#tlArr)");
  pts.forEach(p => svg.append("circle").attr("cx", p[0]).attr("cy", p[1]).attr("r", 4).attr("fill", "var(--accent)"));
}
$("#fChar").onchange = drawTimeline;
window.addEventListener("resize", () => $("#v-timeline").classList.contains("on") && drawTimeline());

/* ---------- 로어북 ---------- */
async function renderLore() {
  let d; try { d = await api(`/novels/${NID}/lore`); } catch (e) { LORE = []; $("#loreGrid").innerHTML = `<p class="muted">${esc(e.message)}</p>`; return; }
  LORE = d.entries;
  $("#catTabs").innerHTML = ['<button data-c="">전체 ' + LORE.length + "</button>"].concat(d.categories.map(c =>
    `<button data-c="${c}"><i style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${CAT[c]};margin-right:4px"></i>${c} ${d.counts[c] || 0}</button>`)).join("");
  $("#catTabs").querySelectorAll("button").forEach(b => b.onclick = () => { loreCat = b.dataset.c; drawLore(); });
  $("#btnExport").href = `/novels/${NID}/lore/export`;
  drawLore();
}
function drawLore() {
  document.querySelectorAll("#catTabs button").forEach(b => b.classList.toggle("on", b.dataset.c === loreCat));
  const q = $("#loreQ").value.trim().toLowerCase();
  const items = LORE.filter(e => (!loreCat || e.category === loreCat) &&
    (!q || [e.name, e.summary, ...e.aliases, ...e.attributes.map(a => a.value + " " + a.key)].some(s => s.toLowerCase().includes(q))));
  $("#loreCount").textContent = `${items.length}개`;
  $("#loreGrid").innerHTML = items.map(e => `<div class="lcard" data-id="${e.id}" style="border-top-color:${CAT[e.category]}">
    <h3>${esc(e.name)}${e.flags.map(f => `<span class="flag">${esc(f)}</span>`).join("")}</h3>
    <div class="cat">${e.category} · 등장 ${e.scene_count}장면 · 첫 등장 ${e.first_scene}</div>
    <div class="sum">${esc(e.summary || e.attributes.slice(0, 3).map(a => a.key + ": " + a.value).join(" / "))}</div></div>`).join("")
    || '<p class="muted">표시할 항목이 없습니다.</p>';
  document.querySelectorAll(".lcard").forEach(el => el.onclick = () => showLore(LORE.find(x => x.id === el.dataset.id)));
  applyHighlight();
}
$("#loreQ").oninput = drawLore;
async function showLore(e) {
  const head = document.createElement("div"); head.className = "detail";
  const chips = ids => ids.map(s => `<span class="chip" data-s="${s}">${s}</span>`).join("");
  head.innerHTML = `<h3 style="margin:0">${esc(e.name)} <small class="muted">${e.category}</small></h3>
    ${e.aliases.length ? `<div class="muted">별칭: ${esc(e.aliases.join(", "))}</div>` : ""}
    ${e.summary ? `<div class="sum">${esc(e.summary)}</div>` : ""}
    <dl>${e.attributes.map(a => `<dt>${esc(a.key)}</dt><dd>${esc(a.value)} ${a.evidence_scene_ids.map(s =>
      `<span class="attr" data-s="${s}" data-q="${esc(a.evidence_quotes[s] || "")}">${s}</span>`).join("")}</dd>`).join("")}</dl>
    <div class="muted">첫 등장 ${e.first_scene} · 등장 ${e.scene_count}장면</div>
    ${e.related.length ? `<div style="margin-top:6px">함께 나온 항목: ${e.related.map(id => { const o = LORE.find(x => x.id === id); return o ? `<span class="chip rel" data-id="${id}">${esc(o.name)}</span>` : ""; }).join("")}</div>` : ""}`;
  if (e.category === "인물") {   // 수동 보정: 인물 병합/제외 -> overrides 저장 후 재처리(추출 캐시 재사용)
    const fix = document.createElement("div"); fix.className = "fix";
    const others = LORE.filter(x => x.category === "인물" && x.id !== e.id);
    fix.innerHTML = `<b>인물 보정</b><div><select class="mergeTo"><option value="">합칠 대상 선택…</option>${others.map(o => `<option value="${esc(o.name)}">${esc(o.name)}</option>`).join("")}</select>
      <button class="btnMerge">선택한 인물로 합치기</button> <button class="btnDrop">인물에서 제외</button></div>
      <div class="muted">보정은 저장되어 재처리에 반영됩니다. 추출 결과를 재사용하므로 빠릅니다.</div>`;
    const apply = async body => {
      await api(`/novels/${NID}/overrides`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
      if (LLM.configured) $("#btnAnalyze").click();
      else alert("보정을 저장했습니다. API 키를 저장한 뒤 ‘분석 실행’을 누르면 반영됩니다(추출 결과는 재사용).");
    };
    fix.querySelector(".btnMerge").onclick = () => {
      const t = fix.querySelector(".mergeTo").value; if (!t) return alert("합칠 대상을 고르세요.");
      const m = {}; [e.name, ...e.aliases].forEach(n => m[n] = t); apply({merge: m});
    };
    fix.querySelector(".btnDrop").onclick = () => confirm(`'${e.name}'을(를) 인물에서 제외할까요?`) && apply({drop: [e.name, ...e.aliases]});
    head.appendChild(fix);
  }
  head.querySelectorAll(".attr").forEach(el => el.onclick = () => openSceneInArea(el.dataset.s, el.dataset.q, esc(e.name)));
  head.querySelectorAll(".chip.rel").forEach(el => el.onclick = () => showLore(LORE.find(x => x.id === el.dataset.id)));
  const first = e.attributes[0];
  await openEvidence([{sid: first ? first.evidence_scene_ids[0] : e.first_scene, quote: first ? first.evidence_quotes[first.evidence_scene_ids[0]] : e.name, label: e.name}], null, head);
}

/* ---------- 질의응답 ---------- */
$("#chatForm").onsubmit = async ev => {
  ev.preventDefault();
  const q = $("#q").value.trim(); if (!q || !NID) return;
  $("#q").value = "";
  const log = $("#chatLog");
  log.insertAdjacentHTML("beforeend", `<div class="q">Q. ${esc(q)}</div><div class="a" id="pending">…</div>`);
  try {
    const r = await api(`/novels/${NID}/ask`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({question: q})});
    const p = $("#pending"); p.removeAttribute("id");
    p.innerHTML = esc(r.answer) + "<br>" + r.citations.map(c => `<span class="chip" data-s="${c}">${c}</span>`).join("");
    p.querySelectorAll(".chip").forEach(c => c.onclick = () => openEvidence([{sid: c.dataset.s}]));
    hl = r.highlight; applyHighlight(); renderGraph();
    if (r.citations[0]) openEvidence(r.citations.slice(0, 2).map(s => ({sid: s})), "답변 근거");
  } catch (err) { $("#pending").textContent = "오류: " + err.message; $("#pending").removeAttribute("id"); }
  log.scrollTop = log.scrollHeight;
};

/* ---------- 작품 선택 / 업로드 / 분석 ---------- */
async function loadAll() {
  if (!NID) return;
  await renderLore();           // 관계도 노드 클릭 시 로어북 카드를 열 수 있도록 먼저 로드
  await Promise.all([renderGraph(), renderTimeline()]);
}
async function loadNovels(select) {
  const list = await api("/novels");
  $("#novelSel").innerHTML = list.map(n => `<option value="${n.novel_id}">${n.novel_id}${n.state === "running" ? " (분석 중)" : n.analyzed ? "" : " (미분석)"}</option>`).join("");
  const running = list.find(n => n.state === "running");
  if (select && !(running && select === "baskervilles")) $("#novelSel").value = select;   // 첫 진입 시 진행 중인 분석이 있으면 그 작품을 보여준다
  else if (running) $("#novelSel").value = running.novel_id;
  NID = $("#novelSel").value || null;
  await loadAll();
  pollStatus();
}
$("#novelSel").onchange = () => { NID = $("#novelSel").value; hl = {characters: [], events: [], lore: []}; loadAll(); pollStatus(); };
$("#file").onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  const lab = e.target.parentElement.firstChild; const orig = lab.textContent; lab.textContent = "업로드 중… ";
  try {
    const r = await api("/novels", {method: "POST", body: fd});
    lab.textContent = orig; e.target.value = "";
    await loadNovels(r.novel_id);
    $("#evidence").innerHTML = `<p>업로드 완료: ${r.chapters}개 장 / ${r.scenes}개 장면. 바로 “분석 실행”을 누를 수 있습니다(키 저장 필요). 질문용 임베딩은 백그라운드에서 진행됩니다.</p>`;
  } catch (err) { lab.textContent = orig; e.target.value = ""; alert(err.message); }
};
/* ---------- 분석 상태 표시 (폴링: 새로고침해도 진행 중인 분석에 다시 붙는다) ---------- */
let pollTimer = null, wasRunning = false;
const fmtT = sec => sec == null ? "" : (sec >= 60 ? `${Math.floor(sec / 60)}분 ${Math.round(sec % 60)}초` : `${Math.round(sec)}초`);
const STATE_TXT = {idle: "대기", running: "분석 중", done: "분석 완료", error: "오류", cancelled: "중지됨"};
function renderStatus(st, analyzed) {
  const el = $("#stState");
  const key = st.state === "idle" && analyzed ? "done" : st.state;
  el.className = "st " + key;
  el.textContent = st.state === "idle" ? (analyzed ? "저장된 분석 결과 있음" : "분석 전") : STATE_TXT[st.state];
  $("#stSteps").innerHTML = st.state === "idle" ? "" : st.steps.map(s => {
    const prog = s.total > 1 ? ` ${s.done}/${s.total}${s.failed ? ` (실패 ${s.failed})` : ""}` : "";
    return `<span class="step ${s.state}">${esc(s.label || ({load_scenes: "장면 로드", extract: "추출", normalize: "정규화", lore: "로어북 생성", validate: "검증", save: "저장"})[s.node])}${s.state === "wait" ? "" : prog}</span>`;
  }).join("");
  $("#stTime").textContent = st.elapsed != null && st.state !== "idle" ? `경과 ${fmtT(st.elapsed)}${st.model ? " · 사용 모델 " + st.model : ""}` : "";
  const msg = $("#stMsg");
  msg.className = st.state === "error" ? "err" : "muted";
  msg.textContent = st.state === "error" ? st.error || "알 수 없는 오류" :
    st.state === "cancelled" ? "사용자가 중지했습니다. 끝난 장면은 저장돼 있어 다시 실행하면 이어서 진행합니다." :
    st.state === "running" && st.rounds ? `일부 장면 추출에 실패해 ${st.rounds}/2회차 재시도 중입니다. 실패가 계속되면 중지하고 오류 원인(API 한도 등)을 확인하세요.` :
    st.state === "running" ? "추출 단계는 장면 수에 따라 수 분 걸립니다. 창을 닫아도 서버에서 계속 진행됩니다." :
    st.state === "done" && (st.steps[1].failed || 0) > 0 ? `완료했지만 ${st.steps[1].failed}개 장면은 추출에 실패해 결과에서 빠졌습니다. 다시 실행하면 실패한 장면만 이어서 시도합니다.` :
    st.state === "idle" && !analyzed ? "API 키를 저장한 뒤 “분석 실행”을 누르세요." : "";
  const ix = st.index || {};
  if (ix.state === "running") msg.textContent = (msg.textContent ? msg.textContent + " · " : "") + `질문(RAG)용 임베딩 인덱싱 중 ${ix.done}/${ix.total} (분석과는 별개로 진행됩니다)`;
  else if (ix.state === "error") { msg.className = "err"; msg.textContent = (msg.textContent ? msg.textContent + " · " : "") + "임베딩 인덱싱 실패: " + (ix.message || ""); }
  $("#btnCancel").hidden = st.state !== "running";
  const btn = $("#btnAnalyze");
  btn.textContent = st.state === "running" ? "분석 중…" : "분석 실행";
  btn.disabled = st.state === "running" || !LLM.configured;
}
async function pollStatus() {
  clearTimeout(pollTimer);
  if (!NID) return;
  let st;
  try { st = await api(`/novels/${NID}/analyze/status`); } catch (e) { return; }
  const opt = $("#novelSel").selectedOptions[0];
  renderStatus(st, opt && !opt.text.includes("미분석"));
  if (st.state === "running" || (st.index && st.index.state === "running")) { wasRunning = wasRunning || st.state === "running"; pollTimer = setTimeout(pollStatus, 1000); }
  else if (wasRunning) { wasRunning = false; if (st.state === "done") await loadNovels(NID); }
}
$("#btnCancel").onclick = async () => {
  if (!NID || !confirm("진행 중인 분석을 중지할까요? 끝난 장면은 저장돼 있어 나중에 이어서 할 수 있습니다.")) return;
  await api(`/novels/${NID}/analyze/cancel`, {method: "POST"});
  $("#stMsg").textContent = "중지 요청됨 — 진행 중인 호출이 끝나는 대로 멈춥니다…";
};
$("#btnAnalyze").onclick = async () => {
  if (!NID || !LLM.configured) return;
  try { await api(`/novels/${NID}/analyze`, {method: "POST"}); } catch (err) { $("#stMsg").className = "err"; $("#stMsg").textContent = err.message; return; }
  wasRunning = true;
  pollStatus();
};

loadProviders().catch(() => {});
loadNovels("baskervilles").catch(() => loadNovels());
