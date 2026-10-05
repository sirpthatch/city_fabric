/* City Fabric map explorer.
 *
 * State flows one way: user action -> update `state` -> render(). The map shows
 * X as a choropleth; the side panel profiles X and its relationship with Y.
 */
(() => {
  "use strict";

  const BASEMAP = "https://basemaps.cartocdn.com/gl/dark-matter-nolabels-gl-style/style.json";
  const LABELS = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";
  const N_CLASSES = 7;
  const PALETTE = d3.quantize(t => d3.interpolateViridis(0.08 + t * 0.92), N_CLASSES);
  const NODATA = getComputedStyle(document.documentElement).getPropertyValue("--nodata").trim();
  const DEFAULT_X = "acs_median_hh_income";
  const DEFAULT_Y = "trees_live_per_1k";

  const state = {
    manifest: null,
    catalog: new Map(),
    level: null,
    x: null,
    y: null,
    scale: "quantile",
    logAxes: false,
    kindFilter: "all",
    search: "",
    geo: null,        // GeoJSON for the current level
    rows: [],         // [{geo_id, name, x, y, idx}]
    hoverId: null,
  };

  const $ = sel => document.querySelector(sel);
  const api = path => fetch(path).then(r => {
    if (!r.ok) throw new Error(`${r.status} ${path}`);
    return r.json();
  });

  // ---------- formatting ----------
  function fmt(v, unit) {
    if (v == null || Number.isNaN(v)) return "—";
    if (unit === "fraction") return d3.format(".1%")(v);
    const a = Math.abs(v);
    if (a === 0) return "0";
    if (a >= 1e5) return d3.format(".3~s")(v);
    if (a >= 100) return d3.format(",.0f")(v);
    if (a >= 1) return d3.format(",.2f")(v);
    return d3.format(".3~g")(v);
  }
  const unitOf = id => state.catalog.get(id)?.unit;
  const titleOf = id => state.catalog.get(id)?.title ?? id;

  // ---------- stats ----------
  function pairs() {
    return state.rows.filter(r => r.x != null && r.y != null);
  }
  function pearson(xs, ys) {
    const n = xs.length;
    if (n < 3) return NaN;
    const mx = d3.mean(xs), my = d3.mean(ys);
    let sxy = 0, sxx = 0, syy = 0;
    for (let i = 0; i < n; i++) {
      const dx = xs[i] - mx, dy = ys[i] - my;
      sxy += dx * dy; sxx += dx * dx; syy += dy * dy;
    }
    return sxy / Math.sqrt(sxx * syy);
  }
  function ranks(arr) {
    const idx = arr.map((v, i) => [v, i]).sort((a, b) => a[0] - b[0]);
    const r = new Array(arr.length);
    for (let i = 0; i < idx.length;) {
      let j = i;
      while (j + 1 < idx.length && idx[j + 1][0] === idx[i][0]) j++;
      const avg = (i + j) / 2 + 1;
      for (let k = i; k <= j; k++) r[idx[k][1]] = avg;
      i = j + 1;
    }
    return r;
  }
  function ols(xs, ys) {
    const mx = d3.mean(xs), my = d3.mean(ys);
    let sxy = 0, sxx = 0;
    for (let i = 0; i < xs.length; i++) {
      sxy += (xs[i] - mx) * (ys[i] - my);
      sxx += (xs[i] - mx) ** 2;
    }
    const slope = sxy / sxx;
    return { slope, intercept: my - slope * mx };
  }

  // ---------- color scale ----------
  function classBreaks(values) {
    const v = values.filter(d => d != null).sort(d3.ascending);
    if (!v.length) return [];
    let breaks;
    if (state.scale === "quantile") {
      breaks = d3.range(1, N_CLASSES).map(i => d3.quantileSorted(v, i / N_CLASSES));
    } else if (state.scale === "log") {
      const pos = v.filter(d => d > 0);
      if (!pos.length) return [];
      const lo = Math.log10(pos[0]), hi = Math.log10(pos[pos.length - 1]);
      breaks = d3.range(1, N_CLASSES).map(i => 10 ** (lo + (hi - lo) * i / N_CLASSES));
    } else {
      const lo = v[0], hi = v[v.length - 1];
      breaks = d3.range(1, N_CLASSES).map(i => lo + (hi - lo) * i / N_CLASSES);
    }
    // MapLibre `step` needs strictly ascending stops.
    return breaks.filter((b, i) => i === 0 || b > breaks[i - 1]);
  }
  function colorFor(v, breaks) {
    if (v == null) return NODATA;
    let i = 0;
    while (i < breaks.length && v >= breaks[i]) i++;
    return PALETTE[Math.min(i, PALETTE.length - 1)];
  }

  // ---------- map ----------
  const map = new maplibregl.Map({
    container: "map",
    style: BASEMAP,
    center: [-73.94, 40.70],
    zoom: 9.6,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
  const mapReady = new Promise(res => map.on("load", res));
  // Resolves once the choropleth source/layers exist; labels load afterwards, above them.
  const geoLayersReady = mapReady.then(() => {
    setupGeoLayers();
    addLabelLayer();
  });

  async function addLabelLayer() {
    // Overlay place labels from the labeled Carto style above the choropleth.
    try {
      const style = await fetch(LABELS).then(r => r.json());
      const srcName = Object.keys(style.sources)[0];
      for (const layer of style.layers) {
        if (layer.type === "symbol" && /place|label/.test(layer.id) && layer.source === srcName) {
          if (!map.getLayer(layer.id)) map.addLayer(layer);
        }
      }
    } catch (e) { console.warn("labels unavailable", e); }
  }

  function setupGeoLayers() {
    map.addSource("geo", { type: "geojson", data: { type: "FeatureCollection", features: [] }, promoteId: "geo_id" });
    map.addLayer({ id: "geo-fill", type: "fill", source: "geo", paint: { "fill-color": NODATA, "fill-opacity": 0.82 } });
    map.addLayer({ id: "geo-line", type: "line", source: "geo", paint: { "line-color": "#0a0d10", "line-width": 0.4 } });
    map.addLayer({
      id: "geo-hover", type: "line", source: "geo",
      paint: { "line-color": "#f2b33d", "line-width": 2 },
      filter: ["==", ["get", "geo_id"], ""],
    });

    const tt = $("#tooltip");
    map.on("mousemove", "geo-fill", e => {
      const f = e.features[0];
      if (!f) return;
      map.getCanvas().style.cursor = "crosshair";
      setHover(f.properties.geo_id);
      const row = state.rows.find(r => r.geo_id === f.properties.geo_id);
      tt.hidden = false;
      tt.innerHTML = tooltipHtml(row, f.properties);
      const { x, y } = e.point;
      const w = map.getCanvas().clientWidth;
      tt.style.left = `${x + 14 + 200 > w ? x - 214 : x + 14}px`;
      tt.style.top = `${y + 14}px`;
    });
    map.on("mouseleave", "geo-fill", () => {
      map.getCanvas().style.cursor = "";
      tt.hidden = true;
      setHover(null);
    });
  }

  function tooltipHtml(row, props) {
    if (!row) return `<div class="tt-name">${props.name}</div>`;
    const pct = v => {
      const vals = state.rows.map(r => r[v]).filter(d => d != null).sort(d3.ascending);
      const val = row[v];
      if (val == null || !vals.length) return "";
      return `p${Math.round(100 * d3.bisectRight(vals, val) / vals.length)}`;
    };
    return `
      <div class="tt-name">${row.name ?? row.geo_id}</div>
      <div class="tt-row muted"><span>${state.level} ${row.geo_id}</span><span>${fmt(props.area_km2)} km²</span></div>
      <div class="tt-row"><span class="x-tag">X</span><span>${fmt(row.x, unitOf(state.x))} <span class="muted">${pct("x")}</span></span></div>
      <div class="tt-row"><span class="y-tag">Y</span><span>${fmt(row.y, unitOf(state.y))} <span class="muted">${pct("y")}</span></span></div>`;
  }

  function setHover(id) {
    if (state.hoverId === id) return;
    state.hoverId = id;
    if (map.getLayer("geo-hover")) map.setFilter("geo-hover", ["==", ["get", "geo_id"], id ?? ""]);
    d3.select("#scatter").selectAll(".dot").classed("hl", d => d.geo_id === id)
      .filter(d => d.geo_id === id).raise();
  }

  function renderMap() {
    const breaks = classBreaks(state.rows.map(r => r.x));
    const byId = new Map(state.rows.map(r => [r.geo_id, r]));
    const data = {
      ...state.geo,
      features: state.geo.features.map(f => ({
        ...f,
        properties: { ...f.properties, x: byId.get(f.properties.geo_id)?.x ?? null },
      })),
    };
    map.getSource("geo").setData(data);
    const step = ["step", ["get", "x"], PALETTE[0]];
    breaks.forEach((b, i) => step.push(b, PALETTE[i + 1]));
    map.setPaintProperty("geo-fill", "fill-color",
      ["case", ["==", ["typeof", ["get", "x"]], "number"], step, NODATA]);

    $("#map-title").innerHTML = `${titleOf(state.x)}<span class="sub">${state.catalog.get(state.x)?.dataset ?? ""} · ${state.level} · ${state.scale}</span>`;
    const unit = unitOf(state.x);
    const lo = d3.min(state.rows, r => r.x), hi = d3.max(state.rows, r => r.x);
    const edges = [lo, ...breaks, hi];
    $("#legend").innerHTML = PALETTE.slice(0, breaks.length + 1).map((c, i) => `
      <div class="legend-row"><span class="legend-swatch" style="background:${c}"></span>
      ${fmt(edges[i], unit)} – ${fmt(edges[i + 1], unit)}</div>`).join("")
      + `<div class="legend-row"><span class="legend-swatch" style="background:${NODATA};border:1px solid #2c3640"></span>no data</div>`;
  }

  // ---------- side panel ----------
  function renderXStats() {
    const v = state.rows.map(r => r.x).filter(d => d != null).sort(d3.ascending);
    const unit = unitOf(state.x);
    const stats = [
      ["n", v.length, null], ["mean", d3.mean(v), unit], ["std", d3.deviation(v), unit], ["null", state.rows.length - v.length, null],
      ["min", v[0], unit], ["p25", d3.quantileSorted(v, .25), unit], ["median", d3.quantileSorted(v, .5), unit], ["max", v[v.length - 1], unit],
    ];
    $("#x-title").textContent = titleOf(state.x);
    $("#x-stats").innerHTML = stats.map(([k, val, u]) =>
      `<div class="stat"><div class="k">${k}</div><div class="v">${u === null && Number.isInteger(val) ? val : fmt(val, u)}</div></div>`).join("");
    renderHistogram(v);
  }

  function renderHistogram(v) {
    const svg = d3.select("#histogram");
    svg.selectAll("*").remove();
    if (!v.length) return;
    const W = svg.node().clientWidth, H = +svg.attr("height");
    const m = { t: 4, r: 4, b: 18, l: 34 };
    const x = d3.scaleLinear().domain(d3.extent(v)).nice().range([m.l, W - m.r]);
    const bins = d3.bin().domain(x.domain()).thresholds(40)(v);
    const y = d3.scaleLinear().domain([0, d3.max(bins, b => b.length)]).nice().range([H - m.b, m.t]);
    const breaks = classBreaks(v);
    svg.append("g").selectAll("rect").data(bins).join("rect")
      .attr("x", b => x(b.x0) + 0.5).attr("width", b => Math.max(0, x(b.x1) - x(b.x0) - 1))
      .attr("y", b => y(b.length)).attr("height", b => y(0) - y(b.length))
      .attr("fill", b => colorFor((b.x0 + b.x1) / 2, breaks));
    svg.append("g").attr("transform", `translate(0,${H - m.b})`)
      .call(d3.axisBottom(x).ticks(5).tickFormat(d => fmt(d, unitOf(state.x))).tickSizeOuter(0));
    svg.append("g").attr("transform", `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(3).tickSizeOuter(0));
  }

  function renderScatter() {
    const p = pairs();
    const ux = unitOf(state.x), uy = unitOf(state.y);
    $("#y-title").textContent = titleOf(state.y);
    const usable = state.logAxes ? p.filter(d => d.x > 0 && d.y > 0) : p;
    const tx = state.logAxes ? Math.log10 : d => d;
    const xs = usable.map(d => tx(d.x)), ys = usable.map(d => tx(d.y));
    const r = pearson(xs, ys);
    const rho = pearson(ranks(xs), ranks(ys));
    const fit = usable.length >= 3 ? ols(xs, ys) : null;
    const stat = (k, v) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div></div>`;
    $("#xy-stats").innerHTML = [
      stat("n", usable.length),
      stat("pearson r", Number.isFinite(r) ? r.toFixed(3) : "—"),
      stat("spearman ρ", Number.isFinite(rho) ? rho.toFixed(3) : "—"),
      stat("r²", Number.isFinite(r) ? (r * r).toFixed(3) : "—"),
      stat(state.logAxes ? "elasticity" : "slope", fit ? d3.format(".3~g")(fit.slope) : "—"),
    ].join("");

    const svg = d3.select("#scatter");
    svg.selectAll("*").remove();
    if (!usable.length) return;
    const W = svg.node().clientWidth, H = +svg.attr("height");
    const m = { t: 6, r: 8, b: 20, l: 40 };
    const S = state.logAxes ? d3.scaleLog : d3.scaleLinear;
    const x = S().domain(d3.extent(usable, d => d.x)).nice().range([m.l, W - m.r]);
    const y = S().domain(d3.extent(usable, d => d.y)).nice().range([H - m.b, m.t]);
    svg.append("g").attr("class", "grid").attr("transform", `translate(${m.l},0)`)
      .call(d3.axisLeft(y).ticks(4).tickSize(-(W - m.l - m.r)).tickFormat("")).select(".domain").remove();
    svg.append("g").attr("transform", `translate(0,${H - m.b})`)
      .call(d3.axisBottom(x).ticks(5, state.logAxes ? "~s" : undefined).tickFormat(d => fmt(d, ux)).tickSizeOuter(0));
    svg.append("g").attr("transform", `translate(${m.l},0)`)
      .call(d3.axisLeft(y).ticks(4).tickFormat(d => fmt(d, uy)).tickSizeOuter(0));
    const radius = usable.length > 1000 ? 1.6 : usable.length > 200 ? 2.4 : 3.5;
    svg.append("g").selectAll("circle").data(usable).join("circle")
      .attr("class", "dot").attr("r", radius)
      .attr("cx", d => x(d.x)).attr("cy", d => y(d.y))
      .on("mouseenter", (_, d) => setHover(d.geo_id))
      .on("mouseleave", () => setHover(null))
      .append("title").text(d => `${d.name}\nX ${fmt(d.x, ux)}\nY ${fmt(d.y, uy)}`);
    if (fit) {
      const [x0, x1] = x.domain();
      const line = state.logAxes
        ? d3.range(0, 1.0001, 0.05).map(t => {
            const lx = Math.log10(x0) + t * (Math.log10(x1) - Math.log10(x0));
            return [10 ** lx, 10 ** (fit.intercept + fit.slope * lx)];
          })
        : [[x0, fit.intercept + fit.slope * x0], [x1, fit.intercept + fit.slope * x1]];
      const [ylo, yhi] = y.domain();
      svg.append("path").attr("class", "fit")
        .attr("d", d3.line().x(d => x(d[0])).y(d => y(Math.min(Math.max(d[1], ylo), yhi)))(line));
    }
  }

  async function renderCorrelates() {
    const el = $("#correlates");
    el.innerHTML = `<div class="muted">computing…</div>`;
    const rows = await api(`/api/correlates/${state.level}/${state.x}?limit=12`);
    el.innerHTML = "";
    for (const row of rows) {
      const div = document.createElement("div");
      div.className = "corr-row";
      const w = Math.abs(row.r) * 50;
      const color = row.r >= 0 ? "var(--pos)" : "var(--neg)";
      const left = row.r >= 0 ? 50 : 50 - w;
      div.innerHTML = `<span class="title" title="${row.title} (n=${row.n})">${row.title}</span>
        <span class="corr-bar"><span style="left:${left}%;width:${w}%;background:${color}"></span></span>
        <span class="r" style="color:${color}">${row.r >= 0 ? "+" : ""}${row.r.toFixed(2)}</span>`;
      div.onclick = () => setAxes({ y: row.feature });
      el.appendChild(div);
    }
    if (!rows.length) el.innerHTML = `<div class="muted">no correlates</div>`;
  }

  function renderRankTable() {
    const sorted = state.rows.filter(r => r.x != null).sort((a, b) => b.x - a.x);
    const ux = unitOf(state.x), uy = unitOf(state.y);
    const row = (r, i) => `<tr data-id="${r.geo_id}"><td class="muted">${i + 1}</td><td title="${r.name}">${r.name ?? r.geo_id}</td>
      <td class="x-tag">${fmt(r.x, ux)}</td><td class="y-tag">${fmt(r.y, uy)}</td></tr>`;
    const top = sorted.slice(0, 8).map((r, i) => row(r, i));
    const bottom = sorted.length > 16
      ? sorted.slice(-8).map((r, i) => row(r, sorted.length - 8 + i))
      : sorted.slice(8).map((r, i) => row(r, i + 8));
    const sep = sorted.length > 16 ? `<tr class="sep"><td colspan="4">⋯ ${sorted.length - 16} more ⋯</td></tr>` : "";
    $("#rank-table").innerHTML = `<thead><tr><th>#</th><th>${state.level}</th><th>X</th><th>Y</th></tr></thead>
      <tbody>${top.join("")}${sep}${bottom.join("")}</tbody>`;
    $("#rank-table").querySelectorAll("tbody tr[data-id]").forEach(tr => {
      tr.onmouseenter = () => setHover(tr.dataset.id);
      tr.onmouseleave = () => setHover(null);
      tr.onclick = () => zoomTo(tr.dataset.id);
    });
  }

  function zoomTo(id) {
    const f = state.geo.features.find(f => f.properties.geo_id === id);
    if (!f) return;
    const b = new maplibregl.LngLatBounds();
    const walk = c => (typeof c[0] === "number" ? b.extend(c) : c.forEach(walk));
    walk(f.geometry.coordinates);
    map.fitBounds(b, { padding: 80, maxZoom: 14, duration: 600 });
  }

  // ---------- feature list ----------
  function renderFeatureList() {
    const list = $("#feature-list");
    const q = state.search.toLowerCase();
    const feats = state.manifest.features.filter(f =>
      (state.kindFilter === "all" || f.kind === state.kindFilter) &&
      (!q || f.title.toLowerCase().includes(q) || f.feature.includes(q)));
    $("#feature-count").textContent = `${feats.length}/${state.manifest.features.length}`;
    const groups = d3.group(feats, f => f.dataset);
    list.innerHTML = "";
    for (const [dataset, fs] of groups) {
      const head = document.createElement("div");
      head.className = "feature-group-head";
      head.textContent = dataset;
      list.appendChild(head);
      for (const f of fs) {
        const row = document.createElement("div");
        row.className = "feature-row" + (f.feature === state.x ? " is-x" : "") + (f.feature === state.y ? " is-y" : "");
        row.title = `${f.feature}\nunit: ${f.unit ?? "—"}`;
        const kind = { raw: "", per_km2: "/km²", per_capita: "/1k", share: "share" }[f.kind] ?? f.kind;
        const tag = f.feature === state.x ? `<b class="x-tag">X</b>` : f.feature === state.y ? `<b class="y-tag">Y</b>` : kind;
        row.innerHTML = `<span class="title">${f.title}</span><span class="kind">${tag}</span>`;
        // ⌘/Ctrl-click or right-click sets Y. On macOS, Ctrl-click arrives as contextmenu.
        row.onclick = e => setAxes(e.metaKey || e.ctrlKey ? { y: f.feature } : { x: f.feature });
        row.oncontextmenu = e => { e.preventDefault(); setAxes({ y: f.feature }); };
        list.appendChild(row);
      }
    }
    if (!feats.length) list.innerHTML = `<div class="empty">no matching features</div>`;
  }

  // ---------- data loading ----------
  async function loadData() {
    const [geo, feats] = await Promise.all([
      state.geo?.level === state.level ? state.geo : api(`/api/geo/${state.level}`).then(g => ({ ...g, level: state.level })),
      api(`/api/features/${state.level}?f=${state.x},${state.y}`),
    ]);
    state.geo = geo;
    state.rows = feats.geo_id.map((id, i) => ({
      geo_id: id, name: feats.name[i], x: feats.values[state.x][i], y: feats.values[state.y][i],
    }));
  }

  async function render() {
    writeHash();
    renderFeatureList();
    await loadData();
    // Panels don't depend on the basemap; a slow tile CDN shouldn't block analysis.
    renderXStats();
    renderScatter();
    renderRankTable();
    renderCorrelates().catch(e => { $("#correlates").innerHTML = `<div class="muted">${e.message}</div>`; });
    await geoLayersReady;
    renderMap();
  }

  function setAxes({ x, y }) {
    if (x) state.x = x;
    if (y) state.y = y;
    render();
  }

  // ---------- URL state ----------
  function writeHash() {
    const p = new URLSearchParams({ level: state.level, x: state.x, y: state.y, scale: state.scale });
    history.replaceState(null, "", `#${p}`);
  }
  function readHash() {
    const p = new URLSearchParams(location.hash.slice(1));
    return Object.fromEntries(p.entries());
  }

  // ---------- controls ----------
  function bindSeg(sel, attr, onChange) {
    $(sel).addEventListener("click", e => {
      const b = e.target.closest("button");
      if (!b) return;
      $(sel).querySelectorAll("button").forEach(x => x.classList.toggle("active", x === b));
      onChange(b.dataset[attr]);
    });
  }
  function setActive(sel, attr, value) {
    $(sel).querySelectorAll("button").forEach(b => b.classList.toggle("active", b.dataset[attr] === value));
  }

  const Y_SHORTCUT = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘-click" : "ctrl-click";

  async function init() {
    try {
      state.manifest = await api("/api/manifest");
    } catch (e) {
      document.querySelector(".layout").innerHTML =
        `<div class="empty" style="grid-column:1/-1">No published data found (${e.message}).<br>Run <b>cf run</b> to collect and build features.</div>`;
      return;
    }
    state.manifest.features.forEach(f => state.catalog.set(f.feature, f));
    const ids = new Set(state.catalog.keys());
    const h = readHash();
    const levels = state.manifest.levels.map(l => l.level);
    state.level = levels.includes(h.level) ? h.level : (levels.includes("nta") ? "nta" : levels[0]);
    state.x = ids.has(h.x) ? h.x : (ids.has(DEFAULT_X) ? DEFAULT_X : state.manifest.features[0].feature);
    state.y = ids.has(h.y) ? h.y : (ids.has(DEFAULT_Y) ? DEFAULT_Y : state.manifest.features[1]?.feature ?? state.x);
    if (["quantile", "linear", "log"].includes(h.scale)) state.scale = h.scale;

    $("#y-shortcut").textContent = Y_SHORTCUT;
    $("#levels").innerHTML = state.manifest.levels.map(l =>
      `<button data-level="${l.level}" title="${l.title}">${l.level.replace("_", " ")}</button>`).join("");
    setActive("#levels", "level", state.level);
    setActive("#scale-mode", "scale", state.scale);
    $("#meta").textContent = `${state.manifest.features.length} features · ${state.manifest.datasets.length} datasets · built ${state.manifest.built_at.replace("T", " ").slice(0, 16)}Z`;

    bindSeg("#levels", "level", v => { state.level = v; render(); });
    bindSeg("#scale-mode", "scale", v => {
      state.scale = v; writeHash(); renderXStats(); geoLayersReady.then(renderMap);
    });
    bindSeg("#kind-filter", "kind", v => { state.kindFilter = v; renderFeatureList(); });
    $("#feature-search").addEventListener("input", e => { state.search = e.target.value; renderFeatureList(); });
    $("#swap").onclick = () => setAxes({ x: state.y, y: state.x });
    $("#log-axes").onclick = e => {
      state.logAxes = !state.logAxes;
      e.target.classList.toggle("active", state.logAxes);
      renderScatter();
    };
    window.addEventListener("resize", () => { renderScatter(); renderXStats(); });

    await render();
  }

  init();
})();
