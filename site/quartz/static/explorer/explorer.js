/* Community graph explorer: filters, search-to-zoom, role highlighting.
 * Data: graph.json (written by scripts/build.py) + Quartz's contentIndex.json (note URLs). */
(() => {
  "use strict";

  const TYPES = [
    { key: "person", label: "People", token: "--t-person" },
    { key: "place", label: "Places", token: "--t-place" },
    { key: "project", label: "Projects", token: "--t-project" },
    { key: "group", label: "Groups", token: "--t-group" },
  ];
  const ROLES = [
    { key: "PI", label: "PI", token: "--r-pi" },
    { key: "Postdoc", label: "Postdocs", token: "--r-postdoc" },
    { key: "PhD student", label: "PhD students", token: "--r-phd" },
    { key: "Undergrad", label: "Undergrads & post-baccs", token: "--r-undergrad" },
    { key: "Other member", label: "Other members", token: "--r-other" },
  ];
  const KINDS = [
    { key: "affiliation", label: "Affiliations (person–place)", on: true },
    { key: "project", label: "Project work (person–project)", on: true },
    { key: "advisor", label: "Advisor → student", on: true },
    { key: "member", label: "Group membership", on: true },
    { key: "based", label: "Group location", on: true },
    { key: "coauthor", label: "Coauthors", on: false },
  ];
  const TYPE_LABEL = { person: "Person", place: "Place", project: "Project", group: "Group" };

  const $ = (s) => document.querySelector(s);
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v;
      else if (k === "style") n.style.cssText = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
    for (const k of kids.flat()) if (k != null) n.append(k);
    return n;
  };

  // ---------------------------------------------------------------- theme (follows the parent Quartz page when embedded)
  let colors = {};
  function syncTheme() {
    let theme = null;
    try { theme = window.parent !== window ? window.parent.document.documentElement.getAttribute("saved-theme") : null; } catch (e) {}
    if (theme) document.documentElement.setAttribute("data-theme", theme);
    const cs = getComputedStyle(document.documentElement);
    const v = (t) => cs.getPropertyValue(t).trim();
    colors = {
      text: v("--text"), muted: v("--muted"), dim: v("--dim"), surface: v("--surface"),
      link: v("--link"), linkStrong: v("--link-strong"),
      type: Object.fromEntries(TYPES.map((t) => [t.key, v(t.token)])),
      role: Object.fromEntries(ROLES.map((r) => [r.key, v(r.token)])),
    };
  }
  syncTheme();
  try { window.parent.document.addEventListener("themechange", () => { syncTheme(); refresh(false); }); } catch (e) {}
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { syncTheme(); refresh(false); });

  // ---------------------------------------------------------------- state
  const state = {
    types: new Set(["person", "place", "project", "group"]),
    roles: new Set(),
    kinds: new Set(KINDS.filter((k) => k.on).map((k) => k.key)),
    status: new Set(["current", "alumni"]),
    groups: new Set(),
    since: null, onlyActive: false, hideEnded: false, onlyDM: false,
    selected: null, hover: null,
  };

  let DATA, nodeById, slugByFile = {}, graph, visible = { nodes: [], links: [] }, neighbors = new Map(), minYear;

  // ---------------------------------------------------------------- load
  Promise.all([
    fetch("graph.json").then((r) => r.json()),
    fetch("../contentIndex.json").then((r) => (r.ok ? r.json() : {})).catch(() => ({})),
  ]).then(([data, index]) => {
    DATA = data;
    for (const [slug, entry] of Object.entries(index)) {
      const fp = (entry.filePath || "").replace(/^(\.\.\/)+/, "");
      if (fp) slugByFile[fp] = slug;
    }
    nodeById = new Map(data.nodes.map((n) => [n.id, n]));
    data.links.forEach((l) => { l.sourceId = l.source; l.targetId = l.target; });
    // degree over all links (stable node sizes that don't jump while filtering)
    data.nodes.forEach((n) => (n.degree = 0));
    data.links.forEach((l) => { if (l.kind !== "coauthor") { nodeById.get(l.sourceId).degree++; nodeById.get(l.targetId).degree++; } });
    minYear = Math.min(...data.nodes.map((n) => n.first).filter(Boolean), data.thisYear - 10);
    buildControls();
    buildGraph();
    refresh(true);
    const fromHash = decodeURIComponent(location.hash.replace(/^#select=/, ""));
    if (fromHash && nodeById.has(fromHash)) setTimeout(() => select(fromHash, true), 600);
    $("#status-msg").textContent = `Data from INSPIRE-HEP, generated ${data.generated}. Scroll to zoom, drag to pan, click a node for details.`;
  }).catch((e) => {
    $("#status-msg").textContent = "Couldn't load graph data: " + e.message;
  });

  // ---------------------------------------------------------------- controls
  function buildControls() {
    const count = (pred) => DATA.nodes.filter(pred).length;

    $("#types").append(...TYPES.map((t) => el("label", {},
      el("input", { type: "checkbox", checked: "", "data-type": t.key, onchange: (e) => { toggle(state.types, t.key, e.target.checked); refresh(); } }),
      el("span", { class: `swatch ${t.key}`, style: `--c: var(${t.token})` }), t.label,
      el("span", { class: "n", "data-count-type": t.key }, String(count((n) => n.type === t.key))))));

    const groupNodes = DATA.nodes.filter((n) => n.type === "group");
    groupNodes.forEach((g) => state.groups.add(g.id.slice(6)));
    $("#groups").append(...groupNodes.map((g) => el("label", {},
      el("input", { type: "checkbox", checked: "", "data-group": g.id.slice(6), onchange: (e) => { toggle(state.groups, g.id.slice(6), e.target.checked); refresh(); } }),
      el("span", { class: "swatch group", style: "--c: var(--t-group)" }), g.label,
      el("span", { class: "n" }, String(count((n) => (n.groups || []).includes(g.id.slice(6))))))));

    $("#roles").append(...ROLES.map((r) => el("label", {},
      el("input", { type: "checkbox", "data-role": r.key, onchange: (e) => { toggle(state.roles, r.key, e.target.checked); refresh(false); } }),
      el("span", { class: "swatch role", style: `--c: var(${r.token})` }), r.label,
      el("span", { class: "n" }, String(count((n) => n.role === r.key))))));

    $("#kinds").append(...KINDS.map((k) => el("label", {},
      el("input", Object.assign({ type: "checkbox", "data-kind": k.key, onchange: (e) => { toggle(state.kinds, k.key, e.target.checked); refresh(); } }, k.on ? { checked: "" } : {})),
      el("span", { class: "swatch line", style: `--c: ${k.key === "advisor" ? "var(--link-strong)" : "var(--muted)"}` }), k.label)));

    const since = $("#since");
    since.min = minYear; since.max = DATA.thisYear; since.value = minYear;
    since.addEventListener("input", () => {
      state.since = +since.value <= minYear ? null : +since.value;
      $("#since-label").textContent = state.since ? String(state.since) : "any time";
      refresh();
    });
    $("#only-active").addEventListener("change", (e) => { state.onlyActive = e.target.checked; refresh(); });
    $("#hide-ended").addEventListener("change", (e) => { state.hideEnded = e.target.checked; refresh(); });
    $("#only-dm").addEventListener("change", (e) => { state.onlyDM = e.target.checked; refresh(); });
    document.querySelectorAll("#status input").forEach((i) =>
      i.addEventListener("change", () => { toggle(state.status, i.value, i.checked); refresh(); }));

    $("#fit").addEventListener("click", () => { userMoved = false; graph.zoomToFit(500, 40); });
    $("#reset").addEventListener("click", reset);
    $("#open-panel").addEventListener("click", () => $("#panel").classList.add("open"));
    $("#close-panel").addEventListener("click", () => $("#panel").classList.remove("open"));

    setupSearch();
  }

  function toggle(set, key, on) { on ? set.add(key) : set.delete(key); }

  function reset() {
    state.types = new Set(TYPES.map((t) => t.key));
    state.roles.clear();
    state.kinds = new Set(KINDS.filter((k) => k.on).map((k) => k.key));
    state.status = new Set(["current", "alumni"]);
    state.groups = new Set(DATA.nodes.filter((n) => n.type === "group").map((n) => n.id.slice(6)));
    state.since = null; state.onlyActive = state.hideEnded = state.onlyDM = false;
    document.querySelectorAll("#types input, #status input, #groups input").forEach((i) => (i.checked = true));
    document.querySelectorAll("#roles input").forEach((i) => (i.checked = false));
    document.querySelectorAll("#kinds input").forEach((i) => (i.checked = state.kinds.has(i.dataset.kind)));
    $("#only-active").checked = $("#hide-ended").checked = $("#only-dm").checked = false;
    $("#since").value = minYear; $("#since-label").textContent = "any time";
    userMoved = false;
    searchInput.value = "";
    deselect();
    refresh(true);
    graph.zoomToFit(500, 40);
  }

  // ---------------------------------------------------------------- filtering
  function nodePasses(n) {
    if (!state.types.has(n.type)) return false;
    if (state.since && n.last != null && n.last < state.since) return false;
    if (n.type === "group" && !state.groups.has(n.id.slice(6))) return false;
    if (n.type === "person") {
      if (n.groups && n.groups.length && !n.groups.some((g) => state.groups.has(g))) return false;
      if (state.onlyActive && !n.inField) return false;
      if (n.status && !state.status.has(n.status)) return false;
    }
    if (n.type === "project") {
      if (state.hideEnded && !n.inField) return false;
      if (state.onlyDM && !n.darkMatter) return false;
    }
    return true;
  }

  function computeVisible() {
    const pass = new Set(DATA.nodes.filter(nodePasses).map((n) => n.id));
    const links = DATA.links.filter((l) =>
      state.kinds.has(l.kind) && pass.has(l.sourceId) && pass.has(l.targetId) &&
      !(state.since && l.last != null && l.last < state.since));
    // places, projects and groups only make sense with at least one visible connection
    const linked = new Set(links.flatMap((l) => [l.sourceId, l.targetId]));
    const nodes = DATA.nodes.filter((n) => pass.has(n.id) && (n.type === "person" || linked.has(n.id)));
    const keep = new Set(nodes.map((n) => n.id));
    return {
      nodes,
      links: links.filter((l) => keep.has(l.sourceId) && keep.has(l.targetId))
        .map((l) => ({ ...l, source: l.sourceId, target: l.targetId })),
    };
  }

  function refresh(reheat = true) {
    if (!graph) return;
    if (reheat) {
      visible = computeVisible();
      neighbors = new Map(visible.nodes.map((n) => [n.id, new Set()]));
      visible.links.forEach((l) => { neighbors.get(l.sourceId).add(l.targetId); neighbors.get(l.targetId).add(l.sourceId); });
      graph.graphData(visible);
      if (state.selected && !neighbors.has(state.selected)) deselect();
      TYPES.forEach((t) => {
        const c = document.querySelector(`[data-count-type="${t.key}"]`);
        if (c) c.textContent = visible.nodes.filter((n) => n.type === t.key).length + "/" + DATA.nodes.filter((n) => n.type === t.key).length;
      });
      $("#count").textContent = `${visible.nodes.length} of ${DATA.nodes.length} shown`;
    }
    graph.linkColor(graph.linkColor()).nodeCanvasObject(graph.nodeCanvasObject()); // repaint with current colours
    if (searchInput && !$("#results").hidden) renderResults();
  }

  // ---------------------------------------------------------------- graph drawing
  const radius = (n) => (n.type === "group" ? 9 : 3 + Math.sqrt(n.degree) * 1.4);

  function nodeColor(n) {
    if (state.roles.size && n.type === "person") return state.roles.has(n.role) ? colors.role[n.role] : colors.dim;
    return colors.type[n.type];
  }

  function focusSet() {
    const id = state.hover || state.selected;
    if (!id || !neighbors.has(id)) return null;
    return new Set([id, ...neighbors.get(id)]);
  }

  function drawShape(ctx, n, r) {
    ctx.beginPath();
    if (n.type === "person") ctx.arc(n.x, n.y, r, 0, 2 * Math.PI);
    else if (n.type === "place") ctx.rect(n.x - r, n.y - r, 2 * r, 2 * r);
    else if (n.type === "project") {
      const h = r * 1.25;
      ctx.moveTo(n.x, n.y - h); ctx.lineTo(n.x + h, n.y + h * 0.8); ctx.lineTo(n.x - h, n.y + h * 0.8); ctx.closePath();
    } else {
      ctx.moveTo(n.x, n.y - r * 1.3); ctx.lineTo(n.x + r * 1.3, n.y); ctx.lineTo(n.x, n.y + r * 1.3); ctx.lineTo(n.x - r * 1.3, n.y); ctx.closePath();
    }
  }

  function drawNode(n, ctx, scale) {
    const focus = focusSet();
    const faded = focus && !focus.has(n.id);
    const r = radius(n);
    ctx.globalAlpha = faded ? 0.12 : 1;
    drawShape(ctx, n, r);
    ctx.fillStyle = nodeColor(n);
    ctx.fill();
    ctx.lineWidth = 1.5 / scale;
    ctx.strokeStyle = colors.surface; // surface ring separates overlapping marks
    ctx.stroke();
    if (n.id === state.selected) {
      drawShape(ctx, n, r + 3 / scale);
      ctx.lineWidth = 2 / scale; ctx.strokeStyle = colors.text; ctx.stroke();
    }
    const roleHit = state.roles.size && n.type === "person" && state.roles.has(n.role);
    const showLabel = !faded && (scale > 2.6 || n.type === "group" || roleHit || (focus && focus.has(n.id)) || (scale > 1.2 && n.degree >= 10));
    if (showLabel) {
      const fs = Math.max(10 / scale, 2.2);
      ctx.font = `${n.id === state.selected ? 700 : 400} ${fs}px "Roboto Mono", monospace`;
      ctx.textAlign = "center"; ctx.textBaseline = "top";
      const y = n.y + r + 2 / scale + (n.type === "project" ? r * 0.3 : 0);
      ctx.lineWidth = 3 / scale; ctx.strokeStyle = colors.surface; ctx.lineJoin = "round";
      ctx.strokeText(n.label, n.x, y);
      ctx.fillStyle = colors.text;
      ctx.fillText(n.label, n.x, y);
    }
    ctx.globalAlpha = 1;
  }

  function linkColor(l) {
    const focus = focusSet();
    const s = l.source.id ?? l.source, t = l.target.id ?? l.target;
    const id = state.hover || state.selected;
    if (focus) return s === id || t === id ? colors.linkStrong : "rgba(0,0,0,0)";
    return l.kind === "advisor" ? colors.linkStrong : colors.link;
  }

  function tooltip(n) {
    const bits = [TYPE_LABEL[n.type]];
    if (n.role) bits.push(`${n.role}${n.status === "alumni" ? " (alumni)" : ""}`);
    if (n.first || n.last) bits.push(years(n));
    return `<b>${escapeHtml(n.label)}</b><br><span style="opacity:.75">${escapeHtml(bits.join(" · "))}</span>`;
  }

  function buildGraph() {
    const box = $("#graph");
    graph = ForceGraph()(box)
      .backgroundColor("rgba(0,0,0,0)")
      .nodeId("id")
      .nodeRelSize(1)
      .nodeVal((n) => radius(n) ** 2)
      .nodeLabel(tooltip)
      .nodeCanvasObject(drawNode)
      .nodePointerAreaPaint((n, color, ctx) => { ctx.fillStyle = color; ctx.beginPath(); ctx.arc(n.x, n.y, radius(n) + 3, 0, 2 * Math.PI); ctx.fill(); })
      .linkColor(linkColor)
      .linkWidth((l) => (l.kind === "advisor" ? 1.6 : Math.min(0.6 + Math.log2(1 + (l.weight || 1)) * 0.35, 2.4)))
      .linkDirectionalArrowLength((l) => (l.kind === "advisor" ? 5 : 0))
      .linkDirectionalArrowRelPos(0.85)
      .linkLineDash((l) => (l.kind === "coauthor" ? [2, 2] : null))
      .onNodeHover((n) => { state.hover = n ? n.id : null; box.style.cursor = n ? "pointer" : ""; })
      .onNodeClick((n) => select(n.id, true))
      .onBackgroundClick(() => deselect())
      .cooldownTicks(200)
      .onEngineStop(() => { settled = true; if (!userMoved && !state.selected) graph.zoomToFit(400, 40); });
    graph.d3Force("charge").strength(-70);
    graph.d3Force("link").distance((l) => (l.kind === "coauthor" ? 60 : 38));
    const resize = () => {
      if (!box.clientWidth || !box.clientHeight) return;
      graph.width(box.clientWidth).height(box.clientHeight);
      if (settled && !userMoved && !state.selected && visible.nodes.length) graph.zoomToFit(0, 40);
    };
    new ResizeObserver(resize).observe(box);
    window.__graph = graph; // handy for debugging from the console
    resize();
    // once the user pans or zooms, stop re-fitting automatically
    ["wheel", "pointerdown", "touchstart"].forEach((ev) => box.addEventListener(ev, () => (userMoved = true), { passive: true }));
  }
  let userMoved = false, settled = false;

  // ---------------------------------------------------------------- selection + details
  function select(id, zoom) {
    const n = nodeById.get(id);
    if (!n) return;
    if (!neighbors.has(id)) {
      // selected something currently filtered out: relax just enough to show it
      state.types.add(n.type);
      document.querySelector(`[data-type="${n.type}"]`).checked = true;
      for (const g of n.groups || (n.type === "group" ? [n.id.slice(6)] : [])) {
        state.groups.add(g);
        const box = document.querySelector(`[data-group="${g}"]`);
        if (box) box.checked = true;
      }
      refresh();
      if (!neighbors.has(id)) { reset(); }
    }
    state.selected = id;
    history.replaceState(null, "", "#select=" + encodeURIComponent(id));
    renderDetails(n);
    if (zoom) {
      const go = () => {
        const live = visible.nodes.find((x) => x.id === id);
        if (!live || live.x == null) return;
        const k = Math.max(graph.zoom(), 3);
        // keep the node in the middle of the area the details card doesn't cover
        const card = $("#details");
        const shift = card.hidden || innerWidth <= 760 ? 0 : (card.offsetWidth + 12) / 2;
        graph.centerAt(live.x + shift / k, live.y, 700);
        graph.zoom(k, 700);
      };
      setTimeout(go, 50);
    }
    refresh(false);
  }

  function deselect() {
    state.selected = null;
    $("#details").hidden = true;
    history.replaceState(null, "", location.pathname + location.search);
    if (graph) refresh(false);
  }

  function years(n) {
    if (n.first && n.last) return n.first === n.last ? String(n.first) : `${n.first}–${n.last}`;
    return String(n.first || n.last || "");
  }

  function noteHref(n) {
    const slug = slugByFile[n.file];
    return slug ? "../../" + slug : null;
  }

  function renderDetails(n) {
    const d = $("#details");
    d.replaceChildren();
    const rows = [];
    if (n.type === "person") {
      for (const m of n.memberships || []) rows.push(["Group", `${m.role}, ${m.group}${m.status === "alumni" ? " (alumni)" : ""}`]);
      if (n.destination) rows.push(["Went on to", n.destination]);
      if (n.first) rows.push(["Publishing", years(n) + ` · ${n.papers} papers`]);
      rows.push(["Status", n.inField ? "Active in research" : "Inactive or left the field"]);
    } else if (n.type === "project") {
      if (n.longName) rows.push(["Name", n.longName]);
      if (n.first || n.last) rows.push(["Years", years(n)]);
      rows.push(["Status", n.completed ? "Completed" : n.inField ? "Active" : "Quiet (no recent papers here)"]);
    } else if (n.type === "place") {
      if (n.location) rows.push(["Location", n.location]);
      if (n.first) rows.push(["Activity", years(n)]);
    } else if (n.longName) rows.push(["Name", n.longName]);

    const href = noteHref(n);
    d.append(
      el("button", { class: "close", "aria-label": "Close", onclick: deselect }, "×"),
      el("h3", {}, n.label),
      el("div", { class: "pill" }, el("span", { class: `swatch ${n.type}`, style: `--c: ${colors.type[n.type]}` }), TYPE_LABEL[n.type]),
      el("dl", {}, rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])),
      el("div", { class: "actions" },
        href ? el("a", { href, target: "_top" }, "Open note →") : null,
        n.inspire ? el("a", { href: n.inspire, target: "_blank", rel: "noopener" }, "INSPIRE ↗") : null),
    );

    // connections, grouped by type (all links, not just visible ones)
    const conns = DATA.links.filter((l) => l.sourceId === n.id || l.targetId === n.id);
    const byType = {};
    for (const l of conns) {
      const other = nodeById.get(l.sourceId === n.id ? l.targetId : l.sourceId);
      let note = "";
      if (l.kind === "advisor") note = l.sourceId === n.id ? "student" : "advisor";
      else if (l.kind === "coauthor") note = `${l.weight} joint`;
      else if (l.kind === "project" && l.weight) note = `${l.weight} papers`;
      else if (l.first) note = l.first === l.last ? String(l.first) : `${l.first}–${l.last ?? ""}`;
      (byType[other.type] ||= new Map());
      const prev = byType[other.type].get(other.id);
      if (!prev || l.kind === "advisor") byType[other.type].set(other.id, { other, note });
    }
    for (const t of TYPES) {
      const list = byType[t.key];
      if (!list) continue;
      const items = [...list.values()].sort((a, b) => a.other.label.localeCompare(b.other.label));
      d.append(el("h4", {}, `${t.label} (${items.length})`),
        el("ul", {}, items.map(({ other, note }) => el("li", {},
          el("button", { onclick: () => select(other.id, true) },
            el("span", { class: `swatch ${other.type}`, style: `--c: ${colors.type[other.type]}` }),
            other.label, el("span", { class: "meta" }, note))))));
    }
    d.hidden = false;
  }

  // ---------------------------------------------------------------- search
  let searchInput, active = -1, matches = [];
  function setupSearch() {
    searchInput = $("#search");
    const results = $("#results");
    searchInput.addEventListener("input", () => { active = 0; renderResults(); });
    searchInput.addEventListener("focus", () => { if (searchInput.value) renderResults(); });
    searchInput.addEventListener("blur", () => setTimeout(() => (results.hidden = true), 150));
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { active = Math.min(active + 1, matches.length - 1); renderResults(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); renderResults(); e.preventDefault(); }
      else if (e.key === "Enter" && matches[active]) { choose(matches[active]); }
      else if (e.key === "Escape") { searchInput.value = ""; results.hidden = true; searchInput.blur(); }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "/" && document.activeElement !== searchInput) { e.preventDefault(); searchInput.focus(); }
      else if (e.key === "Escape" && document.activeElement !== searchInput) deselect();
    });
  }

  function renderResults() {
    const results = $("#results");
    const q = searchInput.value.trim().toLowerCase();
    if (!q) { results.hidden = true; return; }
    const score = (n) => {
      const l = n.label.toLowerCase(), ln = (n.longName || "").toLowerCase();
      if (l.startsWith(q)) return 0;
      if (l.split(/[\s\-,.]+/).some((w) => w.startsWith(q))) return 1;
      if (l.includes(q)) return 2;
      if (ln.includes(q)) return 3;
      return -1;
    };
    matches = DATA.nodes.filter((n) => state.types.has(n.type))
      .map((n) => [n, score(n)]).filter(([, s]) => s >= 0)
      .sort((a, b) => a[1] - b[1] || a[0].label.localeCompare(b[0].label))
      .slice(0, 12).map(([n]) => n);
    active = Math.min(Math.max(active, 0), matches.length - 1);
    results.replaceChildren(...(matches.length ? matches.map((n, i) => el("li", {
      role: "option", "aria-selected": String(i === active),
      onmousedown: (e) => { e.preventDefault(); choose(n); },
    }, el("span", { class: `swatch ${n.type}`, style: `--c: ${colors.type[n.type]}` }), n.label,
      el("span", { class: "meta" }, neighbors.has(n.id) ? TYPE_LABEL[n.type] : TYPE_LABEL[n.type] + " · filtered")))
      : [el("li", { class: "empty" }, "No matches in the selected types")]));
    results.hidden = false;
  }

  function choose(n) {
    $("#results").hidden = true;
    searchInput.value = n.label;
    searchInput.blur();
    $("#panel").classList.remove("open");
    select(n.id, true);
  }

  function escapeHtml(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
})();
