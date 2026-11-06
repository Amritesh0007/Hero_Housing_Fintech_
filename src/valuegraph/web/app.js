"use strict";
/* KSHETRA lender app — vanilla JS, talks only to the REST API (same origin). */

const API = "/api/v1";
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const ROUTE_LABEL = { auto: "Auto-eligible", desktop_review: "Desktop review", physical_inspection: "Site inspection" };
const GRADE_LABEL = { high: "High reliability", medium: "Medium reliability", low: "Low reliability",
  insufficient_evidence: "Insufficient evidence" };
const REASON_TEXT = {
  UNSUPPORTED_CITY: "City not covered yet", UNKNOWN_LOCALITY: "Locality not in our listings",
  OOD_INPUT: "Size or BHK outside what the model has seen", NO_COMPS: "No similar listings in this locality",
  FEW_COMPS: "Few similar listings in this locality", WIDE_INTERVAL: "Wide value range",
  GUIDELINE_DEVIATION: "Far from the guideline value", LTV_ABOVE_CAP: "Loan is above the LTV cap",
};
const AREA_LABEL = { built_up: "built-up", carpet: "carpet", super_built_up: "super built-up" };

let state = { token: sessionStorage.getItem("vg_token"), role: sessionStorage.getItem("vg_role"),
  email: sessionStorage.getItem("vg_email"), status: null };

/* ---------- tiny helpers ---------- */
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid instanceof Node ? kid : String(kid));
  return el;
}
const $ = (s, root = document) => root.querySelector(s);

function inr(n, short = true) {
  if (n == null) return "—";
  n = Math.round(n);
  const sign = n < 0 ? "−" : "", a = Math.abs(n);
  if (short && a >= 1e7) return `${sign}₹${(a / 1e7).toFixed(2)} Cr`;
  if (short && a >= 1e5) return `${sign}₹${(a / 1e5).toFixed(2)} L`;
  return sign + "₹" + a.toLocaleString("en-IN");
}

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), 3200);
}

async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let r;
  try {
    r = await fetch(API + path, { method, headers, body: body && JSON.stringify(body) });
  } catch {
    throw new Error("Can't reach the valuation service. Check that the API is running.");
  }
  if (r.status === 401 && !path.startsWith("/auth/")) { logout(); throw new Error("Your session expired. Sign in again."); }
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : null;
  if (!r.ok) {
    const d = data?.detail;
    throw new Error(Array.isArray(d) ? d.map((e) => `${e.loc.at(-1)}: ${e.msg}`).join("; ") : d || `Error ${r.status}`);
  }
  return data;
}

function countUp(el, to, fmt, ms = 1200) {
  if (REDUCED || to == null) { el.textContent = fmt(to); return; }
  const t0 = performance.now();
  const step = (t) => {
    const p = Math.min(1, (t - t0) / ms), e = 1 - Math.pow(1 - p, 4);
    el.textContent = fmt(to * e);
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
const live = (el) => requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add("live")));

/* ---------- auth ---------- */
function drawContours() {
  // Topographic lines: concentric wobbly rings around a few "hills".
  const g = $("#contour-lines");
  if (g.childElementCount) return;
  const hills = [[260, 220], [900, 560], [1050, 140], [480, 680]];
  for (const [cx, cy] of hills) {
    for (let r = 30; r < 420; r += 26) {
      let d = "";
      for (let a = 0; a <= 360; a += 10) {
        const rad = (a * Math.PI) / 180;
        const rr = r + 9 * Math.sin(rad * 3 + r / 40) + 6 * Math.cos(rad * 5 + cx);
        d += `${a ? "L" : "M"}${(cx + rr * Math.cos(rad)).toFixed(1)},${(cy + rr * Math.sin(rad)).toFixed(1)}`;
      }
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", d + "Z");
      g.append(p);
    }
  }
}

function showLogin() {
  $("#shell").hidden = true;
  $("#login").hidden = false;
  drawContours();
  setupGoogle();
  switchAuthTab("login");
}

/* ---------- auth tabs ---------- */
function switchAuthTab(which) {
  const isLogin = which === "login";
  const loginBtn = $("#tab-btn-login"), regBtn = $("#tab-btn-register");
  const loginForm = $("#login-form"), regForm = $("#register-form");

  loginBtn.classList.toggle("active", isLogin);
  regBtn.classList.toggle("active", !isLogin);
  loginBtn.setAttribute("aria-selected", isLogin);
  regBtn.setAttribute("aria-selected", !isLogin);

  loginForm.hidden = !isLogin;
  regForm.hidden = isLogin;

  // re-trigger panel animation
  const active = isLogin ? loginForm : regForm;
  active.style.animation = "none";
  requestAnimationFrame(() => { active.style.animation = ""; });

  $(".form-error").textContent = "";
  const firstInput = active.querySelector("input");
  if (firstInput) firstInput.focus();
}

$("#tab-btn-login").addEventListener("click", () => switchAuthTab("login"));
$("#tab-btn-register").addEventListener("click", () => switchAuthTab("register"));

/* ---------- login form ---------- */
$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, btn = f.querySelector("button");
  btn.disabled = true;
  $(".form-error").textContent = "";
  try {
    const r = await api("POST", "/auth/token", { email: f.email.value, password: f.password.value });
    startSession(r, f.email.value);
    f.password.value = "";
    toast("Welcome back!");
    await boot();
    _runPendingAuth();
  } catch (err) {
    $(".form-error").textContent = err.message === "Invalid email or password" ? "That email and password don't match." : err.message;
  } finally { btn.disabled = false; }
});

/* ---------- register form ---------- */
$("#register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, btn = f.querySelector("button[type=submit]");
  $(".form-error").textContent = "";

  // client-side validation
  if (f.password.value.length < 8) {
    $(".form-error").textContent = "Password must be at least 8 characters.";
    f.password.focus();
    return;
  }
  if (f.password.value !== f.confirm.value) {
    $(".form-error").textContent = "Passwords don't match.";
    f.confirm.focus();
    return;
  }

  btn.disabled = true;
  btn.textContent = "Creating account…";
  try {
    const body = {
      full_name: f.full_name.value.trim(),
      email: f.email.value.trim(),
      phone: f.phone.value.trim() || null,
      password: f.password.value,
    };
    const r = await api("POST", "/auth/register", body);
    startSession(r, r.email);
    f.reset();
    toast("Account created — welcome to KSHETRA!");
    await boot();
    _runPendingAuth();
  } catch (err) {
    $(".form-error").textContent = err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Create account";
  }
});

/* ---------- password strength indicator ---------- */
(function () {
  const pw = document.querySelector("#register-form [name=password]");
  const indicator = $("#pw-strength");
  const fill = indicator.querySelector(".pw-fill");
  const label = indicator.querySelector(".pw-label");

  function score(v) {
    if (!v) return 0;
    let s = 0;
    if (v.length >= 8) s++;
    if (/[A-Z]/.test(v) && /[a-z]/.test(v)) s++;
    if (/[0-9]/.test(v) && /[^a-zA-Z0-9]/.test(v)) s++;
    return s;
  }
  const labels = ["", "Weak", "Fair", "Strong"];

  pw.addEventListener("input", () => {
    const s = score(pw.value);
    indicator.hidden = !pw.value;
    fill.setAttribute("data-level", s);
    label.textContent = labels[s] || "";
  });
})();

/* ---------- demo quick-fill tags ---------- */
document.querySelectorAll(".demo-tag").forEach((tag) => {
  tag.addEventListener("click", () => {
    const loginForm = $("#login-form");
    switchAuthTab("login");
    loginForm.email.value = tag.dataset.email;
    loginForm.password.value = tag.dataset.password;
    loginForm.email.focus();
  });
});

/* ---------- Sign in with Google ----------
   Google's button hands us a signed ID token; the server verifies it and returns our own session token. */
function startSession(r, email) {
  state = { ...state, token: r.access_token, role: r.role, email };
  sessionStorage.setItem("vg_token", r.access_token);
  sessionStorage.setItem("vg_role", r.role);
  sessionStorage.setItem("vg_email", email);
}

let googleReady = null;
function setupGoogle() {
  if (!googleReady) googleReady = (async () => {
    const { google_client_id: clientId } = await api("GET", "/config").catch(() => ({}));
    if (!clientId) return; // not configured: email sign-in only
    await new Promise((ok, fail) => document.head.append(Object.assign(document.createElement("script"),
      { src: "https://accounts.google.com/gsi/client", async: true, onload: ok, onerror: fail })));
    google.accounts.id.initialize({ client_id: clientId, callback: onGoogleCredential, ux_mode: "popup" });
    google.accounts.id.renderButton($("#google-button"),
      { theme: "outline", size: "large", shape: "pill", text: "signin_with", width: 316 });
    $("#google-signin").hidden = false;
  })().catch((e) => console.warn("Google sign-in unavailable:", e));
  return googleReady;
}

async function onGoogleCredential({ credential }) {
  $(".form-error").textContent = "";
  try {
    const r = await api("POST", "/auth/google", { credential });
    startSession(r, r.email);
    toast("Welcome back!");
    await boot();
    _runPendingAuth();
  } catch (err) {
    $(".form-error").textContent = err.message;
  }
}

function logout() {
  sessionStorage.clear();
  window.google?.accounts.id.disableAutoSelect(); // don't silently sign the same Google account back in
  state = { token: null, role: null, email: null, status: null };
  showLogin();
}
$("#logout").addEventListener("click", logout);

/* ---------- requireAuth pattern ----------
   window.AuthModal.requireAuth(callback): if signed in, runs callback immediately;
   otherwise shows the auth card, stores the callback, and runs it after successful auth. */
let _pendingAuthCallback = null;
function _runPendingAuth() {
  if (_pendingAuthCallback && state.token) {
    const cb = _pendingAuthCallback;
    _pendingAuthCallback = null;
    try { cb(); } catch (e) { console.error("requireAuth callback error:", e); }
  }
}

window.AuthModal = {
  requireAuth(callback) {
    if (state.token) { callback(); return; }
    _pendingAuthCallback = callback;
    showLogin();
    toast("Please sign in to continue", true);
  },
  showLogin() { switchAuthTab("login"); showLogin(); },
  showRegister() { switchAuthTab("register"); showLogin(); },
};

const isStaff = () => ["reviewer", "risk", "admin"].includes(state.role);

async function boot() {
  if (!state.token) return showLogin();
  try { state.status = await api("GET", "/models/status"); }
  catch (e) { if (!state.token) return; toast(e.message, true); }
  $("#login").hidden = true;
  $("#shell").hidden = false;
  $("#who").textContent = `${state.email} · ${state.role}`;
  document.querySelectorAll("[data-staff]").forEach((a) => (a.hidden = !isStaff()));
  if (!location.hash) location.hash = state.role === "underwriter" || state.role === "admin" ? "#/value" : "#/history";
  route();
}

/* ---------- router ---------- */
const VIEWS = { value: viewValue, history: viewHistory, v: viewReport, reviews: viewReviews, market: viewMarket, model: viewModel };
async function route() {
  if (!state.token) return;
  const [, name = "value", arg] = location.hash.split("/");
  document.querySelectorAll(".rail a").forEach((a) => a.classList.toggle("active", a.dataset.route === name));
  const view = $("#view");
  view.replaceChildren();
  try { await (VIEWS[name] || viewValue)(view, arg); }
  catch (e) { view.replaceChildren(h("div", { class: "panel" }, h("h3", {}, "Couldn't load this page"), h("p", { class: "muted" }, e.message))); }
  view.focus({ preventScroll: true });
}
addEventListener("hashchange", route);

/* ---------- New valuation ---------- */
async function viewValue(view) {
  const cities = state.status?.cities || [];
  const start = cities.includes("Hyderabad") ? "Hyderabad" : cities[0];
  const canValue = ["underwriter", "admin"].includes(state.role);
  const opt = (vals, blank = "Not known") => [h("option", { value: "" }, blank), ...vals.map((v) => h("option", { value: v }, v))];

  const report = h("div", { class: "panel" },
    h("div", { class: "empty" }, h("h3", {}, "Describe the flat and value it"),
      h("p", {}, "You'll see the estimate, how sure the model is, and the listings it compared against.")));

  const form = h("form", { class: "panel form-grid", novalidate: true },
    h("label", {}, "City", h("select", { name: "city" }, cities.map((c) => h("option", { value: c, selected: c === start }, c)))),
    h("label", {}, "Locality", h("input", { name: "locality", list: "localities", required: true, autocomplete: "off", placeholder: "Start typing…" })),
    h("label", {}, "Society / project", h("input", { name: "society", maxlength: 120, autocomplete: "off", placeholder: "Optional, e.g. Prestige High Fields" })),
    h("datalist", { id: "localities" }),
    h("p", { class: "coords full", id: "loc-hint" }),
    h("label", {}, "Area (sqft)", h("input", { name: "area", type: "number", min: 150, max: 20000, value: 1100, required: true })),
    h("label", {}, "Area type", h("select", { name: "area_type" },
      Object.entries(AREA_LABEL).map(([v, t]) => h("option", { value: v }, t[0].toUpperCase() + t.slice(1))))),
    h("label", {}, "Bedrooms", h("input", { name: "bhk", type: "number", min: 1, max: 10, value: 2, required: true })),
    h("label", {}, "Bathrooms", h("input", { name: "bathrooms", type: "number", min: 1, max: 8, placeholder: "Optional" })),
    h("label", {}, "Floor", h("input", { name: "floor", type: "number", min: -2, max: 200, placeholder: "0 = ground" })),
    h("label", {}, "Floors in building", h("input", { name: "total_floors", type: "number", min: 1, max: 200, placeholder: "Optional" })),
    h("label", {}, "Sale type", h("select", { name: "transaction" }, h("option", { value: "Resale" }, "Resale"), h("option", { value: "New Property" }, "New property"))),
    h("label", {}, "Furnishing", h("select", { name: "furnishing" }, opt(["Unfurnished", "Semi-Furnished", "Furnished"]))),
    h("label", {}, "Car parking", h("select", { name: "parking" }, opt(["1 Covered", "2 Covered", "1 Open", "2 Open"], "None / not known"))),
    h("label", {}, "Facing", h("select", { name: "facing" }, opt(["North", "South", "East", "West", "North - East", "North - West", "South - East", "South - West"]))),
    h("fieldset", { class: "full views" }, h("legend", {}, "Overlooks"),
      ["Garden/Park", "Pool", "Main Road"].map((v) => h("label", { class: "check" }, h("input", { type: "checkbox", name: "view", value: v }), v))),
    h("label", {}, "Loan amount (₹)", h("input", { name: "loan", type: "number", min: 0, step: 100000, placeholder: "Optional" })),
    h("label", {}, "Guideline rate (₹/sqft)", h("input", { name: "guideline", type: "number", min: 100, step: 100, placeholder: "Optional" })),
    h("button", { class: "btn primary full", type: "submit", disabled: !canValue }, canValue ? "Value property" : "Only underwriters can request valuations"),
  );

  view.append(
    h("div", { class: "page-head" }, h("div", {}, h("h2", {}, "New valuation"),
      h("p", { class: "muted" }, "Pick the locality as it appears in listings; the more details you add, the sharper the estimate."))),
    h("div", { class: "value-layout" }, form, report));

  let known = new Map();
  async function loadLocalities() {
    const list = await api("GET", `/markets/${encodeURIComponent(form.city.value)}/localities`).catch(() => []);
    known = new Map(list.map((l) => [l.locality.toLowerCase(), l]));
    $("#localities").replaceChildren(...list.slice(0, 600).map((l) => h("option", { value: l.locality })));
    form.locality.value = list[0]?.locality || "";
    hint();
  }
  function hint() {
    const l = known.get(form.locality.value.trim().toLowerCase());
    $("#loc-hint").textContent = l ? `${l.listings.toLocaleString("en-IN")} listings on file for ${l.locality}`
      : form.locality.value ? "Not a locality we have listings for — the valuation will be declined." : "";
  }
  form.city.addEventListener("change", loadLocalities);
  form.locality.addEventListener("input", hint);
  await loadLocalities();

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!form.reportValidity()) return;
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    btn.textContent = "Valuing…";
    report.replaceChildren(
      h("div", { class: "skeleton", style: { height: "68px", width: "55%" } }),
      h("div", { class: "skeleton", style: { height: "120px", marginTop: "20px" } }),
      h("div", { class: "skeleton", style: { height: "180px", marginTop: "20px" } }));
    const num = (name) => (form[name].value === "" ? null : +form[name].value);
    const [pCount, pType] = form.parking.value ? form.parking.value.split(" ") : [null, null];
    try {
      const v = await api("POST", "/valuations", {
        city: form.city.value, locality: form.locality.value.trim(), society: form.society.value.trim() || null, area_sqft: +form.area.value, area_type: form.area_type.value,
        bhk: +form.bhk.value, bathrooms: num("bathrooms"), floor: num("floor"), total_floors: num("total_floors"),
        transaction: form.transaction.value, furnishing: form.furnishing.value || null, facing: form.facing.value || null,
        parking: pCount ? +pCount : null, parking_type: pType, overlooking: [...form.querySelectorAll("[name=view]:checked")].map((c) => c.value),
        loan_amount_inr: num("loan"), guideline_rate_sqft: num("guideline"),
      });
      report.replaceChildren(renderReport(v));
    } catch (err) {
      report.replaceChildren(h("div", { class: "empty" }, h("h3", {}, "Valuation failed"), h("p", {}, err.message)));
    } finally { btn.disabled = false; btn.textContent = "Value property"; }
  });
}

/* ---------- the report (the orchestrated reveal) ---------- */
function renderReport(v) {
  const wrap = h("div", { class: "report" });
  const fig = h("div", { class: "figure" }, v.value ? "₹0" : "No value issued");
  const basisSqft = v.area?.listing_basis_sqft || v.area?.value;

  wrap.append(h("div", { class: "headline" },
    h("div", {},
      fig,
      v.value
        ? h("div", { class: "figure-sub" }, `Likely between ${inr(v.low)} and ${inr(v.high)} (${Math.round(v.interval_level * 100)}% range) · ${inr(v.rate_per_sqft, false)}/sqft ${AREA_LABEL[v.area?.type] || ""}`)
        : h("div", { class: "figure-sub" }, "There isn't enough evidence for an automated estimate, so this goes to a site inspection."),
      h("span", { class: "basis" }, v.basis === "asking_price_proxy" ? "Trained on asking prices, not registered sales" : v.basis)),
    h("div", { class: "verdict" },
      h("span", { class: `grade ${v.reliability}` }, GRADE_LABEL[v.reliability] || v.reliability),
      h("span", { class: "route" }, ROUTE_LABEL[v.route] || v.route),
      gradePromise(v.reliability),
      v.reason_codes?.length ? h("div", { class: "flags" }, v.reason_codes.map((c) => h("span", { class: "flag" }, REASON_TEXT[c] || c))) : null)));
  if (v.value) countUp(fig, v.value, (x) => inr(x));

  const facts = [];
  if (v.lending) facts.push([`${v.lending.ltv_pct}%`, `LTV on the low estimate (cap ${v.lending.ltv_cap_pct}%)`]);
  if (v.guideline) facts.push([`${v.guideline.deviation_pct > 0 ? "+" : ""}${v.guideline.deviation_pct}%`, `vs guideline value ${inr(v.guideline.value)}`]);
  facts.push([String(v.comp_count ?? 0), "Similar listings in this locality"]);
  wrap.append(h("div", { class: "facts" }, facts.map(([b, s]) => h("div", { class: "fact" }, h("b", {}, b), h("span", {}, s)))));

  if (v.value) wrap.append(h("div", { class: "panel" }, h("h3", {}, "Where it sits among similar listings"), strip(v, basisSqft)));
  if (v.factors?.length) wrap.append(h("div", { class: "panel" }, h("h3", { style: { marginBottom: "14px" } }, "What moved the value"), waterfall(v)));

  if (v.location)
    wrap.append(h("div", { class: "panel" }, h("h3", { style: { marginBottom: "12px" } },
      v.neighbourhood?.length ? `Neighbourhood: ${v.neighbourhood.length} localities within 3 km` : "Location"),
      priceMap(v.neighbourhood || [], { lat: v.location.lat, lon: v.location.lon, label: "This locality" })));
  if (v.comparables?.length)
    wrap.append(h("div", { class: "panel" }, h("h3", { style: { marginBottom: "8px" } }, "Listings compared (asking prices)"), compsTable(v.comparables)));
  wrap.append(h("p", { class: "small-print" }, `${v.valuation_id} · model ${v.model_version}${v.created_at ? " · " + new Date(v.created_at).toLocaleString("en-IN") : ""}`));
  return wrap;
}

function gradePromise(grade) {
  const g = state.status?.evaluation?.results?.test?.by_grade?.find((x) => x.grade === grade);
  return g ? h("span", { class: "small-print" }, `Held-out cases graded like this: ${(g.mdape * 100).toFixed(1)}% median error`) : null;
}

function strip(v, basisSqft) {
  // One ₹/sqft axis on the user's area basis; comparables are stored super built-up, so scale them across.
  const userSqft = v.area?.value || basisSqft, toUser = basisSqft / userSqft;
  const est = v.value / userSqft, lo = v.low / userSqft, hi = v.high / userSqft;
  const rates = v.comparables.map((c) => c.rate_per_sqft * toUser);
  const min = Math.min(lo, ...rates) * 0.95, max = Math.max(hi, ...rates) * 1.05;
  const x = (r) => `${((r - min) / (max - min)) * 100}%`;
  const s = h("div", { class: "strip", role: "img",
    "aria-label": `Estimate ${inr(est, false)} per sqft; range ${inr(lo, false)} to ${inr(hi, false)}; ${rates.length} comparable listings plotted.` },
    h("div", { class: "strip-axis" }),
    h("div", { class: "strip-range", style: { left: x(lo), width: `calc(${x(hi)} - ${x(lo)})` } }),
    v.comparables.map((c, i) => h("div", { class: "strip-dot", title: `${c.society || c.locality}: ${c.bhk} BHK, ${inr(rates[i], false)}/sqft`,
      style: { left: x(rates[i]), transitionDelay: `${0.3 + i * 0.07}s`, "--jitter": `${((i * 7) % 5 - 2) * 5}px` } })),
    h("div", { class: "strip-pin", style: { left: x(est) } }),
    h("span", { class: "strip-label pin", style: { left: x(est) } }, `${inr(est, false)}/sqft`),
    h("span", { class: "strip-label lo", style: { left: x(lo) } }, inr(lo, false)),
    h("span", { class: "strip-label hi", style: { left: x(hi) } }, inr(hi, false)));
  live(s);
  return h("div", {}, s, h("div", { class: "strip-legend" },
    h("span", {}, h("i", { style: { background: "var(--blush)" } }), "Estimate"),
    h("span", {}, h("i", { style: { background: "var(--sage)", border: "1.5px solid var(--forest)" } }), "Likely range"),
    h("span", {}, h("i", { style: { border: "2px solid var(--forest)" } }), `Similar listing (asking ₹/sqft, ${AREA_LABEL[v.area?.type] || "listing"} basis)`)));
}

/* ---------- maps: shared with the public site, see maps.js ---------- */
function priceMap(points, subject) {
  // points: [{locality, lat, lon, median_rate_per_sqft, listings}], subject: {lat, lon, label} | null
  const el = h("div", { class: "map", role: "img", "aria-label": `Map of ${points.length} localities coloured by median asking price per sqft` });
  requestAnimationFrame(() => VGMaps.draw(el, points, subject));
  const legend = h("div", { class: "strip-legend" },
    subject && h("span", {}, h("i", { style: { background: "var(--blush)", border: "2px solid var(--forest)" } }), subject.label),
    h("span", {}, h("i", { style: { background: "var(--rate-lo)" } }), "Lower asking ₹/sqft"),
    h("span", {}, h("i", { style: { background: "var(--rate-hi)" } }), "Higher asking ₹/sqft"),
    h("span", {}, "Positions are approximate locality centres from OpenStreetMap"));
  return h("div", {}, el, legend);
}

function waterfall(v) {
  const rows = [{ label: "Typical flat this size", amt: v.base_value, kind: "base" },
    ...v.factors.slice(0, 7).map((f) => ({ label: f.label, amt: f.contribution_inr, kind: f.contribution_inr >= 0 ? "up" : "down" }))];
  let run = 0;
  const steps = rows.map((r) => { const from = r.kind === "base" ? 0 : run; run = r.kind === "base" ? r.amt : run + r.amt; return { ...r, from, to: run }; });
  const max = Math.max(...steps.map((s) => Math.max(s.from, s.to)), v.value) * 1.02;
  const pct = (n) => `${(n / max) * 100}%`;
  const el = h("div", { class: "waterfall" });
  steps.forEach((s, i) => {
    const left = Math.min(s.from, s.to), width = Math.abs(s.to - s.from);
    el.append(h("div", { class: "wf-row" }, h("span", {}, s.label),
      h("div", { class: "wf-track" }, h("div", { class: `wf-bar ${s.kind}`, style: { left: pct(left), width: pct(width), transitionDelay: `${0.4 + i * 0.12}s` } })),
      h("span", { class: `wf-amt ${s.kind === "base" ? "" : s.kind}` }, s.kind === "base" ? inr(s.amt) : (s.amt >= 0 ? "+" : "") + inr(s.amt))));
  });
  el.append(h("div", { class: "wf-row total" }, h("span", {}, "Estimate"),
    h("div", { class: "wf-track" }, h("div", { class: "wf-bar", style: { left: 0, width: pct(v.value), transitionDelay: `${0.4 + steps.length * 0.12}s` } })),
    h("span", { class: "wf-amt" }, inr(v.value))));
  live(el);
  return el;
}

function compsTable(comps) {
  const hasSociety = comps.some((c) => c.society);
  return h("div", { class: "table-scroll" }, h("table", {}, h("thead", {}, h("tr", {}, hasSociety && h("th", {}, "Society"),
    h("th", { class: "num" }, "BHK"), h("th", { class: "num", title: "Super built-up equivalent" }, "Area (sqft)"),
    h("th", { class: "num" }, "Floor"), h("th", {}, "Furnishing"), h("th", { class: "num" }, "Asking"),
    h("th", { class: "num" }, "₹/sqft"), h("th", { class: "num" }, "Similarity"))),
  h("tbody", {}, comps.map((c) => h("tr", {}, hasSociety && h("td", {}, c.society || "—"), h("td", { class: "num" }, c.bhk),
    h("td", { class: "num" }, c.sqft.toLocaleString("en-IN")), h("td", { class: "num" }, c.floor ?? "—"),
    h("td", {}, c.furnishing === "__missing__" ? "—" : c.furnishing), h("td", { class: "num" }, inr(c.price_inr)),
    h("td", { class: "num" }, inr(c.rate_per_sqft, false)), h("td", { class: "num" }, `${Math.round(c.weight * 100)}%`))))));
}

/* ---------- history & single report ---------- */
async function viewHistory(view) {
  const rows = await api("GET", "/valuations?limit=100");
  view.append(h("div", { class: "page-head" }, h("h2", {}, "Valuations")));
  if (!rows.length) {
    view.append(h("div", { class: "panel empty" }, h("h3", {}, "No valuations yet"), h("p", {}, "Value a property and it will show up here."),
      h("a", { class: "btn primary", href: "#/value" }, "New valuation")));
    return;
  }
  view.append(h("div", { class: "panel table-scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "When"), h("th", {}, "City"), h("th", { class: "num" }, "Value"), h("th", {}, "Reliability"), h("th", {}, "Route"))),
    h("tbody", {}, rows.map((r) => h("tr", { class: "clickable", tabindex: 0, onclick: () => (location.hash = `#/v/${r.valuation_id}`),
      onkeydown: (e) => e.key === "Enter" && (location.hash = `#/v/${r.valuation_id}`) },
      h("td", {}, new Date(r.created_at).toLocaleString("en-IN")), h("td", {}, r.city), h("td", { class: "num" }, inr(r.value)),
      h("td", {}, h("span", { class: `grade ${r.reliability}` }, GRADE_LABEL[r.reliability])), h("td", {}, ROUTE_LABEL[r.route])))))));
}

async function viewReport(view, id) {
  const v = await api("GET", `/valuations/${encodeURIComponent(id)}`);
  view.append(h("div", { class: "page-head" }, h("h2", {}, "Valuation report"), h("a", { class: "btn", href: "#/history" }, "All valuations")));
  if (v.review && v.review.status !== "open")
    view.append(h("p", { class: "notice", style: { marginBottom: "16px" } }, `Reviewed: ${v.review.status}${v.review.override_value_inr ? " to " + inr(v.review.override_value_inr) : ""}${v.review.reason ? " — " + v.review.reason : ""}`));
  view.append(h("div", { class: "panel" }, renderReport(v)));
}

/* ---------- review queue ---------- */
async function viewReviews(view) {
  const rows = await api("GET", "/reviews?status=open");
  view.append(h("div", { class: "page-head" }, h("div", {}, h("h2", {}, "Review queue"), h("p", { class: "muted", id: "queue-count" }, `${rows.length} waiting`))));
  if (!rows.length) { view.append(h("div", { class: "panel empty" }, h("h3", {}, "Nothing waiting for review"))); return; }
  const canAct = ["reviewer", "admin"].includes(state.role);
  const list = h("div", { class: "queue" });
  rows.forEach((r) => {
    const card = h("div", { class: "panel case" },
      h("div", {}, h("h3", {}, `${r.city} · ${inr(r.value)}`),
        h("p", { class: "small-print" }, `${r.valuation_id} · ${new Date(r.created_at).toLocaleString("en-IN")}`),
        h("div", { class: "flags", style: { marginTop: "6px" } }, h("span", { class: `grade ${r.reliability}` }, GRADE_LABEL[r.reliability]),
          r.reason_codes.map((c) => h("span", { class: "flag" }, REASON_TEXT[c] || c)))),
      h("div", { class: "case-actions" },
        h("a", { class: "btn small", href: `#/v/${r.valuation_id}` }, "Open report"),
        canAct && h("button", { class: "btn small", onclick: () => act(r, "accept", card) }, "Accept value"),
        canAct && h("button", { class: "btn small", onclick: () => toggleOverride(r, card) }, "Override"),
        canAct && h("button", { class: "btn small", onclick: () => act(r, "site_visit", card) }, "Send for site visit")));
    list.append(card);
  });
  view.append(list);
}

function toggleOverride(r, card) {
  const existing = card.querySelector(".override");
  if (existing) return existing.remove();
  const f = h("form", { class: "override" },
    h("input", { name: "val", type: "number", min: 1, required: true, placeholder: "Corrected value (₹)", "aria-label": "Corrected value in rupees" }),
    h("input", { name: "reason", required: true, maxlength: 1000, placeholder: "Reason (required)", "aria-label": "Reason for override" }),
    h("button", { class: "btn primary small", type: "submit" }, "Save override"));
  f.addEventListener("submit", (e) => { e.preventDefault(); act(r, "override", card, { override_value_inr: +f.val.value, reason: f.reason.value }); });
  card.append(f);
  f.val.focus();
}

async function act(r, action, card, extra = {}) {
  try {
    await api("PATCH", `/reviews/${r.review_id}`, { action, ...extra });
    const done = { accept: "Value accepted", override: "Override saved", site_visit: "Sent for site visit" }[action];
    toast(done);
    card.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateX(40px)" }], { duration: REDUCED ? 0 : 350, easing: "ease-in" })
      .onfinish = () => {
        card.remove();
        const left = document.querySelectorAll("#view .queue .case").length;
        if ($("#queue-count")) $("#queue-count").textContent = `${left} waiting`;
      };
  } catch (e) { toast(e.message, true); }
}

/* ---------- market ---------- */
async function viewMarket(view, city) {
  const cities = state.status?.cities || [];
  city = decodeURIComponent(city || (cities.includes("Hyderabad") ? "Hyderabad" : cities[0]));
  const m = await api("GET", `/markets/${encodeURIComponent(city)}/summary`);
  const sel = h("select", { "aria-label": "City", onchange: (e) => (location.hash = `#/market/${encodeURIComponent(e.target.value)}`) },
    cities.map((c) => h("option", { value: c, selected: c === city }, c)));
  view.append(h("div", { class: "page-head" }, h("div", {}, h("h2", {}, `${m.city} market`),
    h("p", { class: "muted" }, `Median asking price ${inr(m.median_rate_per_sqft, false)}/sqft across ${m.listings.toLocaleString("en-IN")} listings`)), sel));
  const mapped = m.localities.filter((l) => l.lat != null);
  if (mapped.length) view.append(h("div", { class: "panel", style: { marginBottom: "20px" } },
    h("h3", { style: { marginBottom: "12px" } }, `Asking price map: ${mapped.length} localities`), priceMap(mapped, null)));
  const top = m.localities.slice(0, 25);
  const max = Math.max(...top.map((l) => l.median_rate_per_sqft), 1);
  const bars = h("div", { class: "bars" }, top.map((l, i) => h("div", { class: "bar-row" }, h("span", {}, l.locality),
    h("div", {}, h("div", { class: "bar", style: { width: `${(l.median_rate_per_sqft / max) * 100}%`, transitionDelay: `${i * 0.04}s` } })),
    h("span", { class: "num" }, `${inr(l.median_rate_per_sqft, false)}`))));
  view.append(h("div", { class: "panel" }, h("h3", { style: { marginBottom: "14px" } }, "Priciest localities (median asking ₹/sqft, 5+ listings)"),
    m.localities.length ? bars : h("p", { class: "muted" }, "Not enough listings per locality yet.")));
  live(bars);
}

/* ---------- model ---------- */
async function viewModel(view) {
  const ev = state.status?.evaluation;
  view.append(h("div", { class: "page-head" }, h("div", {}, h("h2", {}, "How accurate is it?"),
    h("p", { class: "muted" }, `Model ${state.status?.model_version} · trained on ${ev?.data_source} data`))));
  if (!ev) return;
  view.append(h("p", { class: "notice", style: { marginBottom: "20px" } }, ev.ground_truth_note));
  const names = { valuegraph: "KSHETRA", linear_hedonic: "Linear model", comparables_only: "Similar listings only", city_median_rate_x_area: "City average × area" };
  for (const [key, title] of [["test", "Held-out listings"], ["geo_holdout", "Areas the model never saw"]]) {
    const res = ev.results[key], vg = res.valuegraph;
    const stats = h("div", { class: "stat-row" },
      [[vg.mdape, "Median error", (x) => `${(x * 100).toFixed(1)}%`], [vg.within_10pct, "Within ±10%", (x) => `${Math.round(x * 100)}%`],
        [vg.within_20pct, "Within ±20%", (x) => `${Math.round(x * 100)}%`], [vg.interval_coverage, `Inside the ${Math.round(ev.interval_level * 100)}% range`, (x) => `${Math.round(x * 100)}%`]]
        .map(([val, lab, fmt]) => { const b = h("b", {}); countUp(b, val, fmt, 900); return h("div", { class: "stat" }, b, h("span", {}, lab)); }));
    const models = Object.keys(names).filter((k) => res[k]);
    const worst = Math.max(...models.map((k) => res[k].mdape));
    const best = Math.min(...models.map((k) => res[k].mdape));
    const bars = h("div", { class: "bars" }, models.map((k, i) => h("div", { class: "bar-row" }, h("span", {}, names[k]),
      h("div", {}, h("div", { class: `bar ${res[k].mdape === best ? "best" : "muted-bar"}`, style: { width: `${(res[k].mdape / worst) * 100}%`, transitionDelay: `${i * 0.1}s` } })),
      h("span", { class: "num" }, `${(res[k].mdape * 100).toFixed(1)}%`))));
    const byGrade = (res.by_grade || []).slice().sort((a, b) => ["high", "medium", "low"].indexOf(a.grade) - ["high", "medium", "low"].indexOf(b.grade));
    const gradeTable = byGrade.length ? h("div", { class: "table-scroll" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Reliability"),
      h("th", { class: "num" }, "Share of cases"), h("th", { class: "num" }, "Median error"), h("th", { class: "num" }, "Within ±10%"),
      h("th", { class: "num" }, "Inside range"), h("th", {}, "Route"))),
      h("tbody", {}, byGrade.map((g) => h("tr", {}, h("td", {}, h("span", { class: `grade ${g.grade}` }, GRADE_LABEL[g.grade])),
        h("td", { class: "num" }, `${Math.round(g.share * 100)}%`), h("td", { class: "num" }, `${(g.mdape * 100).toFixed(1)}%`),
        h("td", { class: "num" }, `${Math.round(g.within_10pct * 100)}%`), h("td", { class: "num" }, `${Math.round(g.interval_coverage * 100)}%`),
        h("td", {}, { high: "Auto-eligible", medium: "Desktop review", low: "Site inspection" }[g.grade])))))) : null;
    view.append(h("div", { class: "panel", style: { marginBottom: "20px" } }, h("h3", { style: { marginBottom: "16px" } }, title), stats,
      gradeTable && h("h3", { style: { margin: "24px 0 4px" } }, "Accuracy by reliability grade"),
      gradeTable && h("p", { class: "small-print", style: { marginBottom: "8px" } }, "Grade cut-offs are set on a calibration split (High: median error ≤10%, Medium: ≤15%) and checked here on listings the calibration never saw."),
      gradeTable,
      h("h3", { style: { margin: "24px 0 12px" } }, "Median error against simpler methods (shorter is better)"), bars,
      h("p", { class: "small-print", style: { marginTop: "10px" } }, res.beats_all_baselines_mdape ? "KSHETRA beats every simpler method here." : "A simpler method matches or beats KSHETRA here.")));
    live(bars);
  }
}

boot();
