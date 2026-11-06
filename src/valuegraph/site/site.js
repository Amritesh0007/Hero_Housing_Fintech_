/* KSHETRA public site: live market data, the anonymous estimate, languages, and the hero's survey-plan drawing. */
const API = "/api/v1/public";
const DEFAULT_CITY = "Bangalore";
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const $ = (s, el = document) => el.querySelector(s);

/* ---------- small helpers ---------- */
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) el.append(k.nodeType ? k : String(k));
  return el;
}
const svg = (tag, attrs = {}) => {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
};
// Indian notation: ₹82.4 L, ₹1.24 Cr
function money(n) {
  if (n == null) return "—";
  const a = Math.abs(n), s = n < 0 ? "−" : "";
  if (a >= 1e7) return `${s}₹${(a / 1e7).toFixed(a >= 1e8 ? 1 : 2)} Cr`;
  if (a >= 1e5) return `${s}₹${(a / 1e5).toFixed(1)} L`;
  return `${s}₹${Math.round(a).toLocaleString("en-IN")}`;
}
const rate = (n) => `₹${Math.round(n).toLocaleString("en-IN")}`;
const pct = (x) => `${(x * 100).toFixed(1)}%`;
const count = (n) => n.toLocaleString("en-IN");
const titleCase = (s) => s.replace(/\w\S*/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase());
const store = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode: language just isn't remembered */ } } };

async function get(path) {
  const r = await fetch(API + path);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Error ${r.status}`);
  return r.json();
}

/* ---------- languages ----------
   English text lives in /site/i18n/en.json. Other languages come from the server, which translates en.json
   once with Gemini and caches the result. t() falls back to English for anything missing. */
let EN = {}, DICT = {}, LANG = "en";
const VARS = { cities: 45 }; // values shared by several strings, filled in as data arrives
function t(key, vars = {}) {
  const s = DICT[key] ?? EN[key] ?? key;
  return s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] ?? VARS[k] ?? m));
}
function applyI18n(root = document) {
  root.querySelectorAll("[data-i18n]").forEach((el) => (el.textContent = t(el.dataset.i18n)));
  root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => (el.placeholder = t(el.dataset.i18nPlaceholder)));
  root.querySelectorAll("[data-i18n-aria]").forEach((el) => el.setAttribute("aria-label", t(el.dataset.i18nAria)));
  $("#foot-lang").textContent = LANG === "en" ? "" : t("foot.translated");
}
const langSelect = $("#lang-select");
async function setLang(code, { quiet = false } = {}) {
  if (code === LANG) return;
  if (code === "en") DICT = {};
  else {
    langSelect.disabled = true;
    $("#foot-lang").textContent = t("js.lang.loading");
    try {
      DICT = await get(`/i18n/${encodeURIComponent(code)}`);
    } catch {
      langSelect.disabled = false;
      langSelect.value = LANG;
      if (!quiet) $("#foot-lang").textContent = t("js.lang.failed");
      return;
    }
    langSelect.disabled = false;
  }
  LANG = code;
  document.documentElement.lang = code;
  store.set("kshetra_lang", code);
  applyI18n();
  rerender(); // redraw the parts the script built, in the new language
}

/* ---------- smooth scrolling ----------
   Lenis eases wheel and trackpad scrolling and in-page links. Off for people who ask for reduced motion. */
const NAV_OFFSET = -88; // height of the sticky nav
const lenis = !REDUCED && window.Lenis ? new Lenis({ lerp: 0.1, anchors: { offset: NAV_OFFSET }, autoRaf: true }) : null;
const scrollToEl = (el) => (lenis ? lenis.scrollTo(el, { offset: NAV_OFFSET })
  : el.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth", block: "start" }));

/* ---------- navigation ---------- */
const nav = $(".nav");
addEventListener("scroll", () => nav.classList.toggle("scrolled", scrollY > 8), { passive: true });
const toggle = $(".nav-toggle"), menu = $("#mobile-menu");
toggle.addEventListener("click", () => {
  const open = toggle.getAttribute("aria-expanded") !== "true";
  toggle.setAttribute("aria-expanded", open);
  toggle.setAttribute("aria-label", t(open ? "nav.menuClose" : "nav.menu"));
  menu.hidden = !open;
});
menu.addEventListener("click", (e) => { if (e.target.tagName === "A") toggle.click(); });
// Highlight the nav pill for the section in view.
const links = [...document.querySelectorAll(".nav-links a")];
const spy = new IntersectionObserver((entries) => {
  for (const e of entries) if (e.isIntersecting) {
    links.forEach((a) => a.setAttribute("aria-current", a.getAttribute("href") === `#${e.target.id}` ? "true" : "false"));
  }
}, { rootMargin: "-45% 0px -50% 0px" });
["platform", "how", "insights", "about"].forEach((id) => spy.observe(document.getElementById(id)));

/* ---------- reveal on scroll ---------- */
const reveal = new IntersectionObserver((entries) => {
  for (const e of entries) if (e.isIntersecting) { e.target.classList.add("in"); reveal.unobserve(e.target); }
}, { threshold: 0.15 });
document.querySelectorAll(".reveal").forEach((el) => reveal.observe(el));

/* ---------- hero survey plan: contour lines and land plots filled with images ---------- */
(function drawPlat() {
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  const cont = $(".plat-contours"), plots = $(".plat-plots");
  const platSvg = $(".plat");
  // Ensure <defs> exists for clip-paths
  let defs = platSvg.querySelector("defs");
  if (!defs) { defs = svg("defs", {}); platSvg.prepend(defs); }
  // 6 property images, cycled across all plots
  const PLOT_IMAGES = [
    "/site/img/plot-1.png", "/site/img/plot-2.png", "/site/img/plot-3.png",
    "/site/img/plot-4.png", "/site/img/plot-5.png", "/site/img/plot-6.png"
  ];
  for (let k = 0; k < 14; k++) {
    const r0 = 60 + k * 34, cx = 430, cy = 210, pts = [];
    for (let a = 0; a <= 64; a++) {
      const th = (a / 64) * Math.PI * 2;
      const r = r0 + 18 * Math.sin(3 * th + k * 0.6) + 10 * Math.cos(5 * th - k);
      pts.push(`${(cx + r * Math.cos(th)).toFixed(1)},${(cy + r * 0.82 * Math.sin(th)).toFixed(1)}`);
    }
    cont.append(svg("path", { d: `M${pts.join("L")}Z` }));
  }
  // A few blocks of plots along two roads, slightly skewed like a real layout plan.
  const blocks = [[40, 50, 4, 3], [380, 40, 2, 2]];
  let i = 0;
  for (const [x0, y0, cols, rows] of blocks) {
    for (let c = 0; c < cols; c++) for (let r = 0; r < rows; r++) {
      const w = 54 + rnd() * 18, hgt = 62 + rnd() * 16, x = x0 + c * 76 + r * 6, y = y0 + r * 84 - c * 4;
      const d = `M${x},${y}L${x + w},${y - 3}L${x + w + 4},${y + hgt}L${x + 2},${y + hgt + 3}Z`;
      const clipId = `plot-clip-${i}`;
      // Create clipPath with the plot shape
      const clip = svg("clipPath", { id: clipId });
      clip.append(svg("path", { d }));
      defs.append(clip);
      // Create a group with the image clipped to the plot shape
      const g = svg("g", { "clip-path": `url(#${clipId})` });
      const img = document.createElementNS("http://www.w3.org/2000/svg", "image");
      img.setAttributeNS("http://www.w3.org/1999/xlink", "href", PLOT_IMAGES[i % PLOT_IMAGES.length]);
      img.setAttribute("x", x - 2);
      img.setAttribute("y", y - 3);
      img.setAttribute("width", w + 6);
      img.setAttribute("height", hgt + 6);
      img.setAttribute("preserveAspectRatio", "xMidYMid slice");
      g.append(img);
      plots.append(g);
      // Overlay the stroke outline on top
      const p = svg("path", { d, class: i === 9 ? "plot-hl" : "" });
      plots.append(p);
      const len = Math.ceil(p.getTotalLength());
      p.style.setProperty("--len", len);
      p.style.setProperty("--d", `${(REDUCED ? 0 : 0.2 + i * 0.05).toFixed(2)}s`);
      i++;
    }
  }
})();

/* ---------- market data, cached per city ---------- */
const summaries = new Map();
const summary = (city) => {
  if (!summaries.has(city)) summaries.set(city, get(`/markets/${encodeURIComponent(city)}/summary`));
  return summaries.get(city);
};

let heroSummary = null;
function heroCaption() {
  const s = heroSummary;
  if (!s) return;
  $("#hero-caption").replaceChildren(
    h("span", { class: "cap-city" }, t("js.cap.city", { city: s.city })),
    h("span", { class: "cap-meta" }, t("js.cap.meta", { listings: count(s.listings), rate: rate(s.median_rate_per_sqft) })),
    h("span", { class: "cap-scale" }, t("js.cap.lower"), h("i"), t("js.cap.higher")));
}
async function heroMap(city) {
  heroSummary = await summary(city);
  heroCaption();
  VGMaps.draw($("#hero-map"), heroSummary.localities.filter((l) => l.lat != null), null, { interactive: false });
}

/* ---------- insights charts ---------- */
let highlight = null; // locality from the latest estimate, marked on the chart
let insightsCity = null;
async function insights(city) {
  insightsCity = city;
  const s = await summary(city);
  const locs = s.localities;
  $("#ins-lead").textContent = locs.length
    ? t("js.ins.lead", { city: s.city, listings: count(s.listings), count: locs.length, rate: rate(s.median_rate_per_sqft) })
    : t("js.ins.few", { city: s.city });

  // Bars: top 6 and bottom 4, scaled to the dearest.
  const top = locs.slice(0, 6), bottom = locs.length > 10 ? locs.slice(-4) : [];
  const max = Math.max(...locs.map((l) => l.median_rate_per_sqft), 1);
  const row = (l, i, low) => h("div", { class: "bar-row" },
    h("span", { class: "name", title: l.locality }, l.locality),
    h("div", {}, h("div", { class: `bar${low ? " low" : ""}`, style: { width: `${(l.median_rate_per_sqft / max) * 100}%`, "--d": `${i * 0.06}s` } })),
    h("span", { class: "num" }, rate(l.median_rate_per_sqft)));
  const bars = h("div", { class: "bars" }, top.map((l, i) => row(l, i, false)),
    bottom.length ? h("div", { class: "bars-gap", role: "presentation" }) : null, bottom.map((l, i) => row(l, i + 6, true)));
  $("#ins-bars").replaceChildren(bars);
  requestAnimationFrame(() => requestAnimationFrame(() => bars.classList.add("in")));

  // Dot histogram: every locality stacked in its ₹/sq ft band, city median marked.
  const rates = locs.map((l) => l.median_rate_per_sqft);
  const lo = Math.min(...rates), hi = Math.max(...rates), bins = 36, step = Math.max(1, (hi - lo) / bins);
  const W = 560, pad = 14, R = 5, gap = 2 * R + 1.5, stack = new Array(bins + 1).fill(0);
  const x = (r) => pad + ((r - lo) / Math.max(1, hi - lo)) * (W - 2 * pad);
  const placed = locs.map((l) => {
    const b = Math.min(bins, Math.floor((l.median_rate_per_sqft - lo) / step));
    return { l, b, k: stack[b]++ };
  });
  const tall = Math.max(...stack), base = 40 + tall * gap, H = base + 34;
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
    "aria-label": `${t("ins.strip.title")}: ${rate(lo)}–${rate(hi)}; ${t("js.ins.median", { rate: rate(s.median_rate_per_sqft) })}` });
  root.append(svg("line", { class: "axis", x1: pad, x2: W - pad, y1: base + 4, y2: base + 4 }));
  for (const { l, b, k } of placed) {
    const c = svg("circle", { cx: (pad + (b + 0.5) * ((W - 2 * pad) / (bins + 1))).toFixed(1), cy: (base - R - k * gap).toFixed(1), r: R });
    if (highlight && highlight.city === s.city && highlight.locality.toLowerCase() === l.locality.toLowerCase()) c.setAttribute("class", "hl");
    const tip = svg("title", {});
    tip.textContent = t("js.ins.tip", { locality: l.locality, rate: rate(l.median_rate_per_sqft), listings: l.listings });
    c.append(tip);
    root.append(c);
  }
  root.querySelectorAll("circle.hl").forEach((c) => root.append(c)); // draw the highlight on top
  const mx = x(Math.min(hi, Math.max(lo, s.median_rate_per_sqft)));
  root.append(svg("line", { class: "med", x1: mx, x2: mx, y1: 14, y2: base + 8 }));
  const ml = svg("text", { class: "med-lbl", x: mx + 6, y: 22 });
  ml.textContent = t("js.ins.median", { rate: rate(s.median_rate_per_sqft) });
  root.append(ml);
  for (const [r, anchor] of [[lo, "start"], [hi, "end"]]) {
    const tx = svg("text", { class: "lbl", x: x(r), y: base + 26, "text-anchor": anchor }); tx.textContent = rate(r); root.append(tx);
  }
  $("#ins-strip").replaceChildren(h("div", { class: "strip" }, root));
}

/* ---------- valuation form ---------- */
const form = $("#vform"), err = $("#form-error"), out = $("#result");
let errKey = ""; // remember which message is showing so a language switch can re-translate it
const showErr = (key, raw = "") => { errKey = key; err.textContent = key ? t(key) : raw; };
async function loadLocalities(city) {
  const list = await get(`/markets/${encodeURIComponent(city)}/localities`).catch(() => []);
  $("#localities").replaceChildren(...list.slice(0, 800).map((l) => h("option", { value: l.locality })));
}

const num = (v) => (v === "" || v == null ? null : Number(v));
function payload() {
  const f = form.elements;
  const body = {
    city: f.city.value, locality: f.locality.value.trim(), area_sqft: num(f.area_sqft.value), area_type: f.area_type.value,
    bhk: Number(f.bhk.value), transaction: f.transaction.value, society: f.society.value.trim() || null,
    bathrooms: num(f.bathrooms.value), balconies: num(f.balconies.value), floor: num(f.floor.value), total_floors: num(f.total_floors.value),
    furnishing: f.furnishing.value || null, facing: f.facing.value || null, parking_type: f.parking_type.value || null,
  };
  if (body.parking_type) body.parking = 1;
  return Object.fromEntries(Object.entries(body).filter(([, v]) => v !== null));
}

function check(body) {
  const f = form.elements;
  [f.locality, f.area_sqft].forEach((el) => el.removeAttribute("aria-invalid"));
  if (!body.locality) { f.locality.setAttribute("aria-invalid", "true"); f.locality.focus(); return "js.err.locality"; }
  if (!body.area_sqft || body.area_sqft <= 100 || body.area_sqft > 20000) {
    f.area_sqft.setAttribute("aria-invalid", "true"); f.area_sqft.focus(); return "js.err.area";
  }
  if (body.floor != null && body.total_floors != null && body.floor > body.total_floors) return "js.err.floor";
  return "";
}

const GRADE_KEY = { high: "high", medium: "medium", low: "low", insufficient_evidence: "none" };

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = payload(), msgKey = check(body);
  showErr(msgKey);
  if (msgKey) return;
  const btn = form.querySelector("button[type=submit]"), label = btn.querySelector("[data-i18n]");
  btn.disabled = true; label.textContent = t("js.estimating");
  try {
    const r = await fetch(`${API}/estimate`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (r.status === 429) { showErr("js.err.tooMany"); return; }
    if (!r.ok) {
      showErr("", Array.isArray(data.detail) ? data.detail.map((d) => `${d.loc.at(-1)}: ${d.msg}`).join("; ") : data.detail || `Error ${r.status}`);
      return;
    }
    lastResult = { req: body, v: data };
    render(body, data);
    scrollToEl(out);
  } catch {
    showErr("js.err.offline");
  } finally {
    btn.disabled = false; label.textContent = t("f.submit");
  }
});

let lastResult = null;
function render(req, v) {
  out.hidden = false;
  out.classList.remove("in"); void out.offsetWidth; out.classList.add("in");
  const locality = titleCase(req.locality);
  if (v.value == null) {
    const why = v.reason_codes.map((c) => EN[`js.reason.${c}`] && t(`js.reason.${c}`)).filter(Boolean);
    out.replaceChildren(h("div", { class: "res-value" },
      h("p", { class: "eyebrow" }, t("js.res.none.label")),
      h("p", { class: "res-amount", style: { fontSize: "48px" } }, t("js.res.none.title")),
      h("p", { class: "res-range" }, why.join(" ") || t("js.res.none.body"))));
    return;
  }
  const g = GRADE_KEY[v.reliability] || "low";
  const head = h("div", { class: "res-head" },
    h("div", { class: "res-value" },
      h("p", { class: "eyebrow" }, t("js.res.label", { bhk: req.bhk, locality, city: req.city })),
      h("p", { class: "res-amount" }, money(v.value)),
      h("p", { class: "res-range" }, t("js.res.range", { low: money(v.low), high: money(v.high) })),
      h("dl", { class: "res-stats" },
        h("div", {}, h("dt", {}, t("js.res.perSqft", { area: t(`js.area.${req.area_type}`) })), h("dd", {}, rate(v.rate_per_sqft))),
        h("div", {}, h("dt", {}, t("js.res.confidence")), h("dd", {}, h("span", { class: `grade ${v.reliability}` }, t(`js.grade.${g}`)))),
        h("div", {}, h("dt", {}, t("js.res.comps")), h("dd", {}, v.comp_count))),
      h("p", { class: "res-note" }, `${t(`js.grade.${g}.note`)} ${t("js.res.rangeNote", { pct: Math.round(v.interval_level * 100) })}`)),
    h("div", { class: "res-map-wrap" }, h("div", { class: "map res-map", role: "img", "aria-label": t("js.res.mapAria") })));

  // Factors: rupee effect of each group, scaled to the largest.
  const fs = v.factors.slice(0, 7), fmax = Math.max(...fs.map((f) => Math.abs(f.contribution_inr)), 1);
  const factors = h("section", {},
    h("h3", {}, t("js.res.factors")),
    h("p", { class: "sub" }, t("js.res.factorsSub", { base: money(v.base_value), city: req.city })),
    h("ul", { class: "factors" }, fs.map((f) => {
      const up = f.contribution_inr >= 0, w = `${(Math.abs(f.contribution_inr) / fmax) * 50}%`;
      const label = EN[`factor.${f.label}`] ? t(`factor.${f.label}`) : f.label;
      return h("li", {}, h("span", {}, label),
        h("span", { class: "f-track", "aria-hidden": "true" }, h("i", { class: up ? "up" : "down", style: { width: w } })),
        h("span", { class: `f-amt ${up ? "up" : "down"}` }, `${up ? "+" : ""}${money(f.contribution_inr)}`));
    })));

  const comps = v.comparables.slice(0, 6);
  const compTable = h("section", {},
    h("h3", {}, t("js.res.compsTitle")),
    h("p", { class: "sub" }, t("js.res.compsSub")),
    h("div", { class: "table-scroll" }, h("table", { class: "comps" },
      h("thead", {}, h("tr", {}, h("th", {}, t("js.res.where")), h("th", { class: "num" }, "BHK"),
        h("th", { class: "num" }, t("js.res.sqft")), h("th", { class: "num" }, t("js.res.asking")))),
      h("tbody", {}, comps.map((c) => h("tr", {},
        h("td", {}, c.society ? `${titleCase(c.society)}, ${c.locality}` : c.locality),
        h("td", { class: "num" }, c.bhk), h("td", { class: "num" }, count(Math.round(c.sqft))),
        h("td", { class: "num" }, money(c.price_inr))))))));

  const insight = h("section", { class: "res-insight" }, h("h3", {}, t("js.res.insight")), h("p", {}, t("js.res.insightLoading")));
  out.replaceChildren(head, h("div", { class: "res-body" }, factors, compTable, insight));

  const mapEl = $(".res-map", out);
  if (v.location) VGMaps.draw(mapEl, v.neighbourhood, { lat: v.location.lat, lon: v.location.lon, label: t("js.res.thisProperty", { locality }) });
  else mapEl.replaceChildren(h("p", { class: "map-fallback" }, t("js.res.noCoords")));

  summary(req.city).then((s) => {
    const loc = s.localities.find((l) => l.locality.toLowerCase() === req.locality.toLowerCase());
    const vars = { locality, city: s.city, cityRate: rate(s.median_rate_per_sqft) };
    let line;
    if (loc) {
      const cmp = loc.median_rate_per_sqft / s.median_rate_per_sqft - 1;
      line = t(cmp >= 0 ? "js.res.insightAbove" : "js.res.insightBelow", { ...vars, rate: rate(loc.median_rate_per_sqft),
        pct: Math.abs(cmp * 100).toFixed(0), rank: s.localities.indexOf(loc) + 1, count: s.localities.length });
    } else line = t("js.res.insightFew", vars);
    insight.replaceChildren(h("h3", {}, t("js.res.insight")), h("p", {}, line), h("p", { class: "res-note" }, t("js.res.disclaimer")));
    if (!highlight || highlight.locality !== req.locality) {
      highlight = { city: s.city, locality: req.locality };
      $("#ins-city").value = s.city;
      insights(s.city);
    }
  }).catch(() => insight.remove());
}

// After a language switch: rebuild everything the script drew, without new requests where cached.
function rerender() {
  heroCaption();
  if (insightsCity) insights(insightsCity).catch(() => {});
  if (lastResult) render(lastResult.req, lastResult.v);
  if (errKey) err.textContent = t(errKey);
}

/* ---------- boot ---------- */
(async function boot() {
  // English strings first: everything else uses them as the fallback.
  EN = await fetch("/site/i18n/en.json").then((r) => r.json()).catch(() => ({}));

  // Language picker: names in their own script so people can find theirs.
  const langs = await get("/i18n").catch(() => [{ code: "en", name: "English" }]);
  langSelect.replaceChildren(...langs.map((l) => h("option", { value: l.code, lang: l.code }, l.name)));
  langSelect.addEventListener("change", (e) => setLang(e.target.value));
  const saved = store.get("kshetra_lang");
  if (saved && saved !== "en" && langs.some((l) => l.code === saved)) { langSelect.value = saved; setLang(saved, { quiet: true }); }

  get("/model").then((m) => {
    const fill = (key, text) => document.querySelectorAll(`[data-fact="${key}"]`).forEach((el) => (el.textContent = text));
    if (m.median_error != null) fill("median_error", pct(m.median_error));
    if (m.within_10pct != null) fill("within_10pct", `${Math.round(m.within_10pct * 100)}%`);
    fill("cities", m.cities);
    VARS.cities = m.cities;
    applyI18n();
  }).catch(() => {});

  let cities = [];
  try { cities = await get("/cities"); } catch {
    $("#hero-caption").replaceChildren(h("span", { class: "cap-city" }, t("js.err.marketDown")), h("span", { class: "cap-meta" }, t("js.err.down")));
    showErr("js.err.down");
    return;
  }
  const start = cities.includes(DEFAULT_CITY) ? DEFAULT_CITY : cities[0];
  const opts = () => cities.map((c) => h("option", { value: c, selected: c === start }, c));
  form.elements.city.replaceChildren(...opts());
  $("#ins-city").replaceChildren(...opts());
  form.elements.city.addEventListener("change", (e) => { form.elements.locality.value = ""; loadLocalities(e.target.value); });
  $("#ins-city").addEventListener("change", (e) => insights(e.target.value));
  loadLocalities(start);
  heroMap(start).catch(() => {});
  insights(start).catch(() => {});
})();
