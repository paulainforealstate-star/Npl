// NPLDesk · interfaz. Sin JavaScript en línea: la página se sirve con CSP script-src 'self'.
"use strict";

const $ = (id) => document.getElementById(id);
const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString("es-ES", { maximumFractionDigits: 0 }) + " €");
const pct = (n) => (n == null ? "—" : (n * 100).toFixed(2).replace(".", ",") + " %");
const pnum = (n) => (Math.round(n * 100) / 100).toLocaleString("es-ES", { maximumFractionDigits: 2 });
// Todo dato del servidor pasa por esc() antes de entrar en innerHTML.
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const round6 = (x) => Math.round(x * 1e6) / 1e6;

const FIELDS = { titular: "Titular", saldo_pendiente: "Saldo pendiente", fecha_nota: "Fecha de emisión", finca: "Finca" };
const CATS = { registral: "Registral", procesal: "Procesal", ocupacion: "Ocupación", proteccion_deudor: "Protección deudor", documental: "Documental" };
const LEVELS = { bajo: "Bajo", medio: "Medio", alto: "Alto", bloqueante: "Bloqueante" };
const PHASES = { precontenciosa: "Precontenciosa", judicial_sin_adjudicar: "Judicial sin adjudicar", judicial_adjudicada: "Judicial adjudicada" };
const ESTADOS = { bloqueado: "Bloqueado", listo_para_ofertar: "Listo para ofertar", en_curso: "En curso", cerrado: "Cerrado" };
const ESTADO_ICON = { bloqueado: "⛔", listo_para_ofertar: "✔", en_curso: "◐", cerrado: "✔✔" };
const STEP_ICON = { completado: "✔", pendiente: "○", bloqueado: "✕" };
const STEP_LABEL = { completado: "Completado", pendiente: "Pendiente", bloqueado: "Bloqueado" };
const CATEGORY_ORDER = ["registral", "procesal", "ocupacion", "proteccion_deudor", "documental"];

// La sesión vive en sessionStorage: se borra al cerrar la pestaña.
const store = {
  get() { try { return sessionStorage.getItem("npldesk_session") || ""; } catch (e) { return ""; } },
  set(v) { try { if (v) sessionStorage.setItem("npldesk_session", v); else sessionStorage.removeItem("npldesk_session"); } catch (e) { /* sin almacenamiento */ } },
};
let token = store.get();
let me = null;
let catalog = { risk_catalog: [] };
let currentId = null;
let currentCase = null;
let voidIdx = null;          // cobro cuya anulación está abierta para escribir el motivo
let previewTimer = null;
let pending = false;         // una acción en curso: no se lanza otra
let toastTimer = null;
let allCases = [];
let routes = [];             // modelo del formulario de valoración (probabilidades en %)

function headers() {
  const h = { "Content-Type": "application/json" };
  if (token) h["Authorization"] = "Bearer " + token;
  return h;
}

async function api(method, path, body) {
  let payload = body;
  // Las escrituras sobre un expediente llevan su versión: si otra sesión lo cambió antes, el servidor responde 409.
  if (method === "POST" && currentCase && path.startsWith(`/api/cases/${currentId}/`)) {
    payload = { ...(body || {}), version: currentCase.version };
  }
  const r = await fetch(path, { method, headers: headers(), body: payload ? JSON.stringify(payload) : undefined });
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (r.status === 401 && path !== "/api/login") {
    showLogin("Tu sesión ha caducado. Vuelve a entrar.");
    throw new Error("Sesión caducada");
  }
  if (!r.ok) throw new Error((data && data.error) || ("Error " + r.status));
  return data;
}

// ---- avisos ---------------------------------------------------------------------

function toast(msg, kind) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (kind === "err" ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.className = "toast"; }, kind === "err" ? 6000 : 3500);
}

function setBusy(el, on) { if (el) el.setAttribute("aria-busy", on ? "true" : "false"); }

// ---- acceso -----------------------------------------------------------------------

function showLogin(message) {
  token = ""; me = null; store.set("");
  $("appArea").hidden = true;
  $("session").hidden = true;
  $("loginCard").hidden = false;
  $("loginErr").textContent = message || "";
  $("detailCard").hidden = true;
  currentId = null; currentCase = null; voidIdx = null;
}

function showApp() {
  $("loginCard").hidden = true;
  $("loginErr").textContent = "";
  $("appArea").hidden = false;
  $("session").hidden = false;
  $("who").textContent = `${me.name} · ${me.role}`;
}

async function loadConfig() {
  try {
    const c = await api("GET", "/api/config");
    $("registerToggle").hidden = !c.registro;
  } catch (e) { $("registerToggle").hidden = true; }
}

async function start() {
  if (!token) { await loadConfig(); return showLogin(""); }
  try {
    me = await api("GET", "/api/me");
    showApp();
    await afterLogin();
  } catch (e) { showLogin(e.message === "Sesión caducada" ? "Tu sesión ha caducado. Vuelve a entrar." : ""); }
}

async function afterLogin() {
  catalog = await api("GET", "/api/catalog");
  fillPhaseSelects();
  renderRoutes();
  await loadCases();
}

$("loginForm").onsubmit = async (ev) => {
  ev.preventDefault();
  $("loginErr").textContent = "";
  try {
    const r = await api("POST", "/api/login", { user: $("loginUser").value.trim(), password: $("loginPass").value });
    token = r.token; store.set(token);
    $("loginPass").value = "";
    me = { name: r.name, role: r.role };
    showApp();
    await afterLogin();
  } catch (e) { $("loginErr").textContent = e.message; }
};

$("registerToggle").onclick = () => {
  const open = $("registerForm").hidden;
  $("registerForm").hidden = !open;
  $("loginForm").hidden = open;
  $("registerToggle").textContent = open ? "Volver a entrar" : "Crear cuenta nueva";
  $("loginErr").textContent = "";
};

$("registerForm").onsubmit = async (ev) => {
  ev.preventDefault();
  $("loginErr").textContent = "";
  const user = $("regUser").value.trim();
  const password = $("regPass").value;
  try {
    await api("POST", "/api/register", { user, name: $("regName").value.trim(), password });
    const r = await api("POST", "/api/login", { user, password });
    token = r.token; store.set(token);
    $("regPass").value = "";
    me = { name: r.name, role: r.role };
    showApp();
    await afterLogin();
  } catch (e) { $("loginErr").textContent = e.message; }
};

$("logout").onclick = async () => {
  try { await api("POST", "/api/logout", {}); } catch (e) { /* la sesión ya no es válida: basta con salir */ }
  showLogin("Has cerrado sesión.");
};

function fillPhaseSelects() {
  const opts = Object.entries(PHASES).map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
  $("phase").innerHTML = opts;
  $("fPhase").innerHTML = `<option value="">Todas</option>` + opts;
}

// ---- valoración: formulario de vías y escenarios ----------------------------------

const DEFAULT_ROUTES = [
  { name: "negociación", scenarios: [
    { name: "base", probability: 1, sale_price: 90000, time_years: 0.5, source: "tasación", reviewed_at: "2026-10-01" } ] },
  { name: "venta ordenada", scenarios: [
    { name: "favorable", probability: 0.7, sale_price: 110000, costs: 8000, time_years: 1, source: "mercado", reviewed_at: "2026-10-01" },
    { name: "desfavorable", probability: 0.3, sale_price: 70000, costs: 8000, time_years: 1.5, source: "mercado", reviewed_at: "2026-10-01" } ] },
];

// Del formato del servidor (probabilidad 0-1) al del formulario (0-100).
function toUiRoute(r) {
  return {
    name: String(r.name ?? ""),
    scenarios: (r.scenarios || []).map(s => ({
      ...s,
      name: String(s.name ?? ""),
      probability: s.probability == null ? "" : round6(Number(s.probability) * 100),
      sale_price: s.sale_price ?? "",
      costs: s.costs ?? 0,
      charges: s.charges ?? 0,
      time_years: s.time_years ?? "",
      source: s.source ?? "",
      reviewed_at: s.reviewed_at ?? "",
    })),
  };
}

// Del formulario al formato del servidor. No valida: para validar está validate().
function toServerScenario(s) {
  const out = { ...s,
    name: String(s.name || "").trim() || "escenario",
    probability: Number(s.probability) / 100,
    sale_price: Number(s.sale_price),
    costs: s.costs === "" ? 0 : Number(s.costs),
    charges: s.charges === "" ? 0 : Number(s.charges),
    time_years: Number(s.time_years),
  };
  if (String(s.source || "").trim()) out.source = String(s.source).trim(); else delete out.source;
  if (s.reviewed_at) out.reviewed_at = s.reviewed_at; else delete out.reviewed_at;
  return out;
}

function serverRoutes() {
  return routes.map(r => ({ name: r.name.trim(), scenarios: r.scenarios.map(toServerScenario) }));
}

function routeSum(r) {
  return r.scenarios.reduce((a, s) => a + (Number.isFinite(Number(s.probability)) ? Number(s.probability) : 0), 0);
}

// Errores que impiden calcular. Se comprueban aquí para no ir al servidor a por un error evidente.
function validate() {
  const errs = [];
  const rate = parseFloat($("rate").value), margin = parseFloat($("margin").value);
  if (!Number.isFinite(rate) || rate < 0 || rate > 1) errs.push("La tasa de descuento debe estar entre 0 y 1");
  if (!Number.isFinite(margin) || margin < 0 || margin > 0.99) errs.push("El margen de seguridad debe estar entre 0 y 0,99");
  if (!routes.length) errs.push("Añade al menos una vía");
  routes.forEach((r, i) => {
    const label = r.name.trim() || `vía ${i + 1}`;
    if (!r.name.trim()) errs.push(`La vía ${i + 1} necesita un nombre`);
    if (!r.scenarios.length) errs.push(`La vía "${label}" no tiene escenarios`);
    r.scenarios.forEach((s, j) => {
      const p = Number(s.probability), price = Number(s.sale_price), t = Number(s.time_years);
      const where = `escenario ${j + 1} de "${label}"`;
      if (!Number.isFinite(p) || p < 0 || p > 100) errs.push(`La probabilidad del ${where} debe estar entre 0 y 100`);
      if (!Number.isFinite(price) || price < 0) errs.push(`Falta el precio de venta del ${where}`);
      if (!Number.isFinite(t) || t <= 0) errs.push(`Falta el plazo (en años) del ${where}`);
      if (s.costs !== "" && (!Number.isFinite(Number(s.costs)) || Number(s.costs) < 0)) errs.push(`Los costes del ${where} no son válidos`);
      if (s.charges !== "" && (!Number.isFinite(Number(s.charges)) || Number(s.charges) < 0)) errs.push(`Las cargas del ${where} no son válidas`);
    });
    const sum = round6(routeSum(r));
    if (r.scenarios.length && Math.abs(sum - 100) > 0.001) {
      errs.push(`Las probabilidades de "${label}" suman ${pnum(sum)} %; deben sumar 100 %`);
    }
  });
  return errs;
}

function sumText(r) {
  const sum = round6(routeSum(r));
  const diff = round6(100 - sum);
  if (Math.abs(diff) < 0.001) return { cls: "ok", text: "✔ Suma 100 %" };
  return diff > 0
    ? { cls: "bad", text: `✕ Suma ${pnum(sum)} % · faltan ${pnum(diff)} puntos` }
    : { cls: "bad", text: `✕ Suma ${pnum(sum)} % · sobran ${pnum(-diff)} puntos` };
}

function updateSums() {
  routes.forEach((r, i) => {
    const el = document.querySelector(`[data-sum="${i}"]`);
    if (!el) return;
    const s = sumText(r);
    el.className = `sum ${s.cls}`;
    el.textContent = s.text;
  });
  const errs = validate();
  $("calc").disabled = errs.length > 0;
  $("save").disabled = errs.length > 0;
  $("formMsg").textContent = errs.length ? `Revisa el formulario: ${errs[0]}${errs.length > 1 ? ` (y ${errs.length - 1} más)` : ""}` : "Formulario correcto.";
  $("formMsg").className = errs.length ? "bad" : "ok";
  syncJson();
}

const inputCell = (label, attrs) => `<td><input aria-label="${esc(label)}" ${attrs}></td>`;

function renderRoutes() {
  if (!routes.length) routes = DEFAULT_ROUTES.map(toUiRoute);
  $("routes").innerHTML = routes.map((r, i) => {
    const rows = r.scenarios.map((s, j) => {
      const where = `de la vía ${r.name || i + 1}, escenario ${j + 1}`;
      const dataAttrs = `data-r="${i}" data-s="${j}"`;
      return `<tr>` +
        inputCell(`Nombre ${where}`, `type="text" size="10" value="${esc(s.name)}" ${dataAttrs} data-f="name"`) +
        inputCell(`Probabilidad (%) ${where}`, `type="number" step="any" min="0" max="100" class="num" value="${esc(s.probability)}" ${dataAttrs} data-f="probability"`) +
        inputCell(`Precio de venta (€) ${where}`, `type="number" step="any" min="0" class="num" value="${esc(s.sale_price)}" ${dataAttrs} data-f="sale_price"`) +
        inputCell(`Costes (€) ${where}`, `type="number" step="any" min="0" class="num narrow" value="${esc(s.costs)}" ${dataAttrs} data-f="costs"`) +
        inputCell(`Cargas (€) ${where}`, `type="number" step="any" min="0" class="num narrow" value="${esc(s.charges)}" ${dataAttrs} data-f="charges"`) +
        inputCell(`Plazo (años) ${where}`, `type="number" step="any" min="0" class="num narrow" value="${esc(s.time_years)}" ${dataAttrs} data-f="time_years"`) +
        inputCell(`Fuente ${where}`, `type="text" size="10" value="${esc(s.source)}" ${dataAttrs} data-f="source"`) +
        inputCell(`Fecha de revisión ${where}`, `type="date" value="${esc(s.reviewed_at)}" ${dataAttrs} data-f="reviewed_at"`) +
        `<td><button type="button" class="quiet" data-remove-scenario="${i}" data-s="${j}" aria-label="Quitar escenario ${j + 1} de ${esc(r.name || "la vía")}">Quitar</button></td>` +
        `</tr>`;
    }).join("");
    return `<div class="route" data-route="${i}">` +
      `<div class="route-head">` +
        `<label>Nombre de la vía <input type="text" size="20" value="${esc(r.name)}" data-r="${i}" data-f="name"></label>` +
        `<span class="sum" data-sum="${i}"></span>` +
        `<button type="button" class="quiet" data-remove-route="${i}">Quitar vía</button>` +
      `</div>` +
      `<div class="scroll"><table class="routes-table"><thead><tr>` +
        `<th>Escenario</th><th class="num">Probabilidad (%)</th><th class="num">Precio de venta (€)</th>` +
        `<th class="num">Costes (€)</th><th class="num">Cargas (€)</th><th class="num">Plazo (años)</th>` +
        `<th>Fuente</th><th>Fecha de revisión</th><th><span class="sr">Acciones</span></th>` +
        `</tr></thead><tbody>${rows}</tbody></table></div>` +
      `<button type="button" data-add-scenario="${i}">Añadir escenario</button>` +
    `</div>`;
  }).join("");
  updateSums();
}

function addScenario(i) {
  const r = routes[i];
  const left = Math.max(0, round6(100 - routeSum(r)));
  r.scenarios.push({ name: `escenario ${r.scenarios.length + 1}`, probability: left, sale_price: "", costs: 0,
    charges: 0, time_years: "", source: "", reviewed_at: "" });
}

$("routes").addEventListener("input", (ev) => {
  const t = ev.target;
  if (t.dataset.r === undefined) return;
  const i = Number(t.dataset.r);
  if (t.dataset.s === undefined) { routes[i].name = t.value; updateSums(); return; }
  routes[i].scenarios[Number(t.dataset.s)][t.dataset.f] = t.value;
  updateSums();
});

$("routes").addEventListener("click", (ev) => {
  const b = ev.target.closest("button");
  if (!b) return;
  if (b.dataset.addScenario !== undefined) { addScenario(Number(b.dataset.addScenario)); renderRoutes(); }
  else if (b.dataset.removeScenario !== undefined) {
    const i = Number(b.dataset.removeScenario);
    if (routes[i].scenarios.length === 1) return showCalcErr("Una vía necesita al menos un escenario");
    routes[i].scenarios.splice(Number(b.dataset.s), 1);
    renderRoutes();
  } else if (b.dataset.removeRoute !== undefined) {
    if (routes.length === 1) return showCalcErr("Debe quedar al menos una vía");
    routes.splice(Number(b.dataset.removeRoute), 1);
    renderRoutes();
  }
});

$("addRoute").onclick = () => {
  routes.push({ name: `vía ${routes.length + 1}`, scenarios: [
    { name: "base", probability: 100, sale_price: "", costs: 0, charges: 0, time_years: "", source: "", reviewed_at: "" } ] });
  renderRoutes();
};

// JSON para usuarios avanzados: refleja el formulario salvo mientras se edita a mano.
function syncJson() {
  const box = $("json");
  if (document.activeElement === box) return;
  box.value = JSON.stringify({ routes: serverRoutes() }, null, 2);
}

$("applyJson").onclick = () => {
  try {
    const obj = JSON.parse($("json").value);
    if (!obj || !Array.isArray(obj.routes) || !obj.routes.length) throw new Error("El JSON debe tener una lista 'routes' con al menos una vía");
    routes = obj.routes.map(toUiRoute);
    $("jsonErr").textContent = "";
    renderRoutes();
  } catch (e) { $("jsonErr").textContent = "JSON no válido: " + e.message; }
};

function buildPayload() {
  const errs = validate();
  if (errs.length) throw new Error(errs.join("\n"));
  const body = {
    phase: $("phase").value,
    rate: parseFloat($("rate").value),
    safety_margin: parseFloat($("margin").value),
    routes: serverRoutes(),
  };
  const iters = parseInt($("iters").value, 10);
  if (iters > 0) body.montecarlo_iterations = iters;
  return body;
}

function showCalcErr(msg) { $("calcErr").textContent = msg; }
function showCalcWarn(msg) { $("calcErr").innerHTML = `<span class="warn">${esc(msg)}</span>`; }

function renderValuation(res) {
  let html = `<div class="scroll"><table><thead><tr><th>Vía</th><th class="num"><abbr title="Valor esperado: media de los flujos netos descontados, ponderada por la probabilidad de cada escenario">VE</abbr></th><th class="num">Precio máximo</th></tr></thead><tbody>`;
  for (const r of res.ranking) {
    const cls = r.route === res.best.route ? ' class="best"' : "";
    const max = r.max_price_floored ? "0 € (no genera valor)" : fmt(r.max_price);
    html += `<tr${cls}><td>${esc(r.route)}${r.route === res.best.route ? " · recomendada" : ""}</td><td class="num">${fmt(r.expected_value)}</td><td class="num">${esc(max)}</td></tr>`;
  }
  html += "</tbody></table></div>";
  if (res.montecarlo) {
    html += `<h4>Simulación Monte Carlo</h4><div class="scroll"><table><thead><tr><th>Vía</th><th class="num"><abbr title="Percentil 10: en el 10 % de las simulaciones el resultado es igual o inferior">P10</abbr></th><th class="num"><abbr title="Mediana: el resultado que sale en la mitad de las simulaciones">P50</abbr></th><th class="num"><abbr title="Percentil 90: en el 90 % de las simulaciones el resultado es igual o inferior">P90</abbr></th></tr></thead><tbody>`;
    for (const [n, p] of Object.entries(res.montecarlo)) {
      html += `<tr><td>${esc(n)}</td><td class="num">${fmt(p.p10)}</td><td class="num">${fmt(p.p50)}</td><td class="num">${fmt(p.p90)}</td></tr>`;
    }
    html += "</tbody></table></div>";
  }
  $("result").innerHTML = html;
}

$("calc").onclick = async () => {
  showCalcErr("");
  try {
    const res = await api("POST", "/api/valuation", buildPayload());
    renderValuation(res);
    if (res.warnings && res.warnings.length) showCalcWarn(res.warnings.join(" · "));
  } catch (e) { showCalcErr(e.message); }
};

$("save").onclick = async () => {
  showCalcErr("");
  const name = $("caseName").value.trim();
  if (!name) return showCalcErr("Escribe un nombre para el expediente");
  try {
    const payload = buildPayload();
    const r = await api("POST", "/api/cases", { name, phase: payload.phase, input: payload });
    toast("Expediente creado.");
    if (r.warnings && r.warnings.length) showCalcWarn(r.warnings.join(" · "));
    $("caseName").value = "";
    await loadCases();
  } catch (e) { showCalcErr(e.message); }
};

// ---- lista de expedientes -------------------------------------------------------

function chip(estado) {
  return `<span class="chip chip-${esc(estado)}"><span aria-hidden="true">${ESTADO_ICON[estado] || ""}</span>${esc(ESTADOS[estado] || estado)}</span>`;
}

async function loadCases() {
  setBusy($("cases"), true);
  try {
    const { cases } = await api("GET", "/api/cases");
    allCases = cases;
    renderCases();
  } catch (e) { $("cases").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
  finally { setBusy($("cases"), false); }
}

function renderCases() {
  const q = $("fSearch").value.trim().toLowerCase();
  const ph = $("fPhase").value, est = $("fEstado").value;
  const list = allCases.filter(c =>
    (!q || c.name.toLowerCase().includes(q)) && (!ph || c.phase === ph) && (!est || c.estado === est));
  $("casesCount").textContent = allCases.length ? `${list.length} de ${allCases.length} expedientes` : "";
  if (!allCases.length) { $("cases").innerHTML = `<p class="muted">Todavía no hay expedientes.</p>`; return; }
  if (!list.length) { $("cases").innerHTML = `<p class="muted">Ningún expediente coincide con los filtros.</p>`; return; }
  $("cases").innerHTML = `<div class="scroll"><table><thead><tr><th>Nombre</th><th>Fase</th><th>Estado</th><th class="num">Oferta</th><th><span class="sr">Acciones</span></th></tr></thead><tbody>` +
    list.map(c => `<tr><td>${esc(c.name)}</td><td>${esc(PHASES[c.phase] || c.phase)}</td><td>${chip(c.estado)}</td>` +
      `<td class="num">${c.oferta == null ? "—" : fmt(c.oferta)}</td>` +
      `<td><button type="button" data-open="${esc(c.id)}">Abrir</button></td></tr>`).join("") +
    `</tbody></table></div>`;
}

["fSearch", "fPhase", "fEstado"].forEach(id => {
  $(id).addEventListener("input", renderCases);
  $(id).addEventListener("change", renderCases);
});
$("cases").onclick = (ev) => {
  const b = ev.target.closest("[data-open]");
  if (b) openCase(b.dataset.open);
};

// ---- ficha del expediente -------------------------------------------------------

function captureDrafts() {
  const drafts = {};
  document.querySelectorAll("#detail input[id], #detail textarea[id], #detail select[id]").forEach(el => {
    if (el.type !== "file") drafts[el.id] = el.value;
  });
  return drafts;
}

function restoreDrafts(drafts) {
  for (const [id, value] of Object.entries(drafts)) {
    const el = $(id);
    if (el) el.value = value;
  }
}

function rerender() {
  const drafts = captureDrafts();
  const active = document.activeElement && $("detailCard").contains(document.activeElement) ? document.activeElement.id : "";
  renderSteps();
  renderDetail();
  restoreDrafts(drafts);
  if (active && $(active)) $(active).focus({ preventScroll: true });
  refreshPreview();
}

function setDetailErr(msg) { $("detailErr").textContent = msg || ""; }

async function openCase(id) {
  if (String(id) !== String(currentId)) voidIdx = null;
  currentId = id;
  currentCase = null;
  $("detailCard").hidden = false;
  $("detail").innerHTML = `<p class="muted">Cargando expediente…</p>`;
  $("steps").innerHTML = "";
  await refreshDetail();
  $("detailCard").scrollIntoView({ behavior: "smooth", block: "start" });
}

// Recarga el expediente desde el servidor y lo pinta conservando lo que la persona estaba escribiendo.
async function refreshDetail(okMsg) {
  setBusy($("detail"), true);
  try {
    currentCase = await api("GET", `/api/cases/${currentId}`);
    $("detailTitle").innerHTML = `${esc(currentCase.name)} · ${esc(PHASES[currentCase.phase] || currentCase.phase)} ${chip(currentCase.estado)}`;
    rerender();
    setDetailErr("");
    if (okMsg) toast(okMsg);
    loadCases();
  } catch (e) { setDetailErr(e.message); }
  finally { setBusy($("detail"), false); }
}

function renderSteps() {
  const steps = currentCase.steps || [];
  $("steps").innerHTML = `<ol class="steps">` + steps.map((s, i) =>
    `<li class="st-${esc(s.status)}${s.next ? " next" : ""}">` +
      `<span class="dot" aria-hidden="true">${STEP_ICON[s.status] || ""}</span>` +
      `<span class="name">${esc(s.label)}<span class="sr"> (paso ${i + 1} de ${steps.length})</span></span>` +
      `<span class="st-label">${esc(STEP_LABEL[s.status] || s.status)}</span>` +
      `<p class="reason">${esc(s.reason)}</p>` +
      `<button type="button" data-target="${esc(s.target)}">${esc(s.action)}</button>` +
    `</li>`).join("") + `</ol>`;
}

function focusSection(id) {
  const sec = $(id);
  if (!sec) return;
  sec.scrollIntoView({ behavior: "smooth", block: "start" });
  const first = sec.querySelector("input, select, textarea, button");
  if (first) first.focus({ preventScroll: true });
}

function sec(id, title, body) {
  return `<section class="sec" id="${id}" tabindex="-1" aria-labelledby="${id}-t"><h3 id="${id}-t">${esc(title)}</h3>${body}</section>`;
}

function listOrNone(items, render, none) {
  return items.length ? `<ul>${items.map(render).join("")}</ul>` : `<p class="muted">${esc(none)}</p>`;
}

function fieldValue(field, value) {
  return field === "saldo_pendiente" ? fmt(value) : String(value);
}

function renderDetail() {
  const c = currentCase;
  $("detail").innerHTML = `<div class="sections">${[
    sectionCribado(c.state.screening),
    sectionDiligencia(c),
    sectionRiesgos(c.state.risks || []),
    sectionDecision(c),
    sectionOferta(c),
    sectionEjecucion(c),
  ].join("")}</div>`;
}

function sectionCribado(sc) {
  let body;
  if (!sc) body = `<p class="muted">Todavía no hay cribado.</p>`;
  else {
    body = `<p>Estado: <b>${esc(sc.status)}</b>` +
      (sc.ltv != null ? ` · LTV ${(sc.ltv * 100).toFixed(1).replace(".", ",")} %` : "") + `</p>` +
      listOrNone(sc.reasons || [], r => `<li>${esc(r)}</li>`, "Sin motivos de descarte.");
  }
  return sec("sec-cribado", "Cribado", body + `<div class="row"><button type="button" data-act="screen">Recalcular cribado</button></div>`);
}

function sectionDiligencia(c) {
  const d = c.state.diligence || { candidates: {}, confirmed: {} };
  const cand = d.candidates || {}, conf = d.confirmed || {};
  const alerts = c.alerts || [];
  const discrepancies = alerts.filter(a => a.type === "discrepancia");
  const others = alerts.filter(a => a.type !== "discrepancia");

  let must = "";
  if (discrepancies.length) {
    must = `<div class="must" role="region" aria-labelledby="mustTitle"><h4 id="mustTitle">Discrepancias: resuélvelas antes de seguir</h4>` +
      discrepancies.map(a => {
        const items = cand[a.field] || [];
        return `<div class="item"><b>${esc(FIELDS[a.field] || a.field)}</b>: ${esc(a.detail)}` +
          `<div class="row">` + items.map((it, idx) =>
            `<button type="button" data-confirm="${esc(a.field)}" data-idx="${idx}">Usar ${esc(fieldValue(a.field, it.value))} <span class="muted">(${esc(it.document)})</span></button>`
          ).join(" ") + `</div></div>`;
      }).join("") + `</div>`;
  }
  const otherList = others.length ? `<ul class="warn">${others.map(a => `<li>${esc(a.detail)}</li>`).join("")}</ul>` : "";

  const rows = Object.entries(FIELDS).map(([field, label]) => {
    const items = cand[field] || [];
    const ok = conf[field];
    const candHtml = items.length ? items.map((it, idx) => {
      const isOk = ok && String(ok.value) === String(it.value);
      return `<div class="cand"><div><b>${esc(fieldValue(field, it.value))}</b> <span class="muted">· ${esc(it.document)}</span>` +
        (isOk ? ` <span class="chip chip-cerrado">✔ confirmado</span>` : "") +
        ` <button type="button" data-confirm="${esc(field)}" data-idx="${idx}">Usar este valor</button></div>` +
        (it.source_fragment ? `<blockquote class="frag">${esc(it.source_fragment)}</blockquote>` : "") + `</div>`;
    }).join("") : `<span class="muted">No extraído</span>`;
    const placeholder = field === "fecha_nota" ? "DD/MM/AAAA" : field === "saldo_pendiente" ? "0,00" : "";
    const inputMode = field === "saldo_pendiente" ? ` inputmode="decimal"` : "";
    return `<tr><th scope="row">${esc(label)}</th>` +
      `<td>${ok ? `<b class="ok">✔ ${esc(fieldValue(field, ok.value))}</b>` : `<span class="muted">○ pendiente</span>`}</td>` +
      `<td>${candHtml}</td>` +
      `<td><form data-manual="${esc(field)}" class="manual"><label>Valor corregido` +
        ` <input id="man-${esc(field)}" size="12" placeholder="${esc(placeholder)}"${inputMode}></label>` +
        ` <button type="submit">Confirmar corrección</button></form></td></tr>`;
  }).join("");

  const body = must + otherList +
    `<div class="drop" id="drop">` +
      `<label for="docFile">Arrastra aquí un documento de texto (.txt) o elígelo desde tu equipo</label>` +
      `<input type="file" id="docFile" accept=".txt,text/plain">` +
      `<p class="muted">Los PDF deben tener texto seleccionable (no escaneados). Máximo 5 MB.</p>` +
    `</div>` +
    `<div class="row"><label>Nombre del documento <input id="docName" size="20" value="nota-simple.txt"></label></div>` +
    `<label class="block-label">Texto del documento <textarea id="docText"></textarea></label>` +
    `<div class="row"><button type="button" data-act="extract">Extraer datos</button></div>` +
    `<h4>Datos del expediente</h4>` +
    `<div class="scroll"><table><thead><tr><th>Dato</th><th>Confirmado</th><th>Extraído (usa el valor correcto)</th><th>Corregir a mano</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  return sec("sec-diligencia", "Diligencia documental", body);
}

function sectionRiesgos(risks) {
  const rows = risks.map((r, i) => {
    const approval = r.level !== "bloqueante" ? ""
      : (r.approved_by ? `<span class="ok">✔ aprobado por ${esc(r.approved_by)}</span>`
        : `<button type="button" data-act="approve" data-idx="${i}">Aprobar</button>`);
    const levelCls = r.level === "bloqueante" ? "bad" : "";
    return `<tr><td>${esc(CATS[r.category] || r.category)}</td><td class="${levelCls}">${esc(LEVELS[r.level] || r.level)}</td>` +
      `<td>${esc(r.description)}${r.norm_ref ? `<div class="muted">${esc(r.norm_ref)}</div>` : ""}</td>` +
      `<td>${approval}</td></tr>`;
  }).join("");
  const table = rows
    ? `<div class="scroll"><table><thead><tr><th>Categoría</th><th>Nivel</th><th>Descripción</th><th><span class="sr">Acciones</span></th></tr></thead><tbody>${rows}</tbody></table></div>`
    : `<p class="muted">Sin riesgos registrados.</p>`;
  const body = table +
    `<h4>Registrar riesgo</h4>` +
    `<div class="row"><label>Plantilla` +
      ` <select id="rTemplate">${templateOptions()}</select></label>` +
      `<span class="muted">Elige una plantilla para rellenar categoría, nivel, descripción y norma. Puedes ajustarlos.</span></div>` +
    `<div class="row">` +
      `<label>Categoría <select id="rCat">${optionsOf(CATS, "registral")}</select></label>` +
      `<label>Nivel <select id="rLevel">${optionsOf(LEVELS, "medio")}</select></label>` +
    `</div>` +
    `<label class="block-label">Descripción <textarea id="rDesc"></textarea></label>` +
    `<div class="row"><label>Norma de referencia <input id="rNorm" size="40"></label>` +
      `<button type="button" class="primary" data-act="risk-add">Añadir riesgo</button></div>`;
  return sec("sec-riesgos", "Riesgos", body);
}

function optionsOf(map, selected) {
  return Object.entries(map).map(([v, l]) => `<option value="${esc(v)}"${v === selected ? " selected" : ""}>${esc(l)}</option>`).join("");
}

function templateOptions() {
  const groups = CATEGORY_ORDER.map(cat => {
    const items = (catalog.risk_catalog || []).filter(t => t.category === cat);
    if (!items.length) return "";
    return `<optgroup label="${esc(CATS[cat])}">` +
      items.map(t => `<option value="${esc(t.id)}">${esc(t.title)}</option>`).join("") + `</optgroup>`;
  }).join("");
  return `<option value="">Texto libre</option>` + groups;
}

function sectionDecision(c) {
  const ranking = (c.result && c.result.ranking) || [];
  const recommended = c.result && c.result.best ? c.result.best.route : (ranking[0] || {}).route;
  const d = c.state.decision;
  const current = d
    ? `<p>Elegida: <b>${esc(d.chosen)}</b> · recomendada: ${esc(d.recommended)}` +
      (d.justification ? ` · <span class="muted">${esc(d.justification)}</span>` : "") + `</p>`
    : `<p class="muted">Sin decisión registrada.</p>`;
  const table = ranking.length
    ? `<div class="scroll"><table><thead><tr><th>Vía</th><th class="num"><abbr title="Valor esperado: media de los flujos netos descontados, ponderada por la probabilidad">VE</abbr></th><th class="num">Precio máximo</th></tr></thead><tbody>` +
      ranking.map(r => `<tr${r.route === recommended ? ' class="best"' : ""}><td>${esc(r.route)}${r.route === recommended ? " (recomendada)" : ""}</td>` +
        `<td class="num">${fmt(r.expected_value)}</td><td class="num">${r.max_price_floored ? "0 € (no genera valor)" : fmt(r.max_price)}</td></tr>`).join("") +
      `</tbody></table></div>`
    : "";
  let body = current + table;
  if (c.state.offer) {
    body += `<p class="muted">Hay oferta registrada: la decisión ya no se puede cambiar.</p>`;
  } else if (ranking.length) {
    const options = ranking.map(r => `<option value="${esc(r.route)}"${(d ? d.chosen : recommended) === r.route ? " selected" : ""}>${esc(r.route)}</option>`).join("");
    body += `<div class="row"><label>Vía elegida <select id="dChosen">${options}</select></label></div>` +
      `<label class="block-label">Justificación <textarea id="dJust" placeholder="Obligatoria si no eliges la vía recomendada"></textarea></label>` +
      `<div class="row"><button type="button" class="primary" data-act="decision">Registrar decisión</button></div>`;
  } else {
    body += `<p class="muted">No hay vías valoradas.</p>`;
  }
  return sec("sec-decision", "Decisión", body);
}

function sectionOferta(c) {
  const s = c.state, offer = s.offer, recoveries = s.recoveries || [];
  const info = offer
    ? `<p>Oferta vigente (v${esc(offer.version || 1)}): <b class="num">${fmt(offer.amount)}</b> · vía ${esc(offer.route)} · ` +
      `<abbr title="Tasa interna de retorno: rentabilidad anual de la operación si se cumplen los flujos previstos">TIR</abbr> prevista ` +
      `${offer.planned_irr == null ? "no calculable" : pct(offer.planned_irr)} · ${esc(offer.by)} (${esc(offer.date)})</p>` +
      (offer.over_max_price ? `<p class="warn">⚠ Superaba el precio máximo de la vía (${fmt(offer.max_price)}) al registrarla.</p>` : "") +
      (offer.motivo ? `<p class="muted">Motivo del último cambio: ${esc(offer.motivo)}</p>` : "")
    : `<p class="muted">Sin oferta registrada.</p>`;
  if (recoveries.length) {
    return sec("sec-oferta", "Oferta", info + `<p class="muted">Hay cobros registrados: la oferta ya no se puede cambiar.</p>`);
  }
  const live = `<div class="live" id="offerLive"><p class="muted">Escribe un importe para ver la TIR prevista y la distancia al precio máximo. No se guarda nada hasta que registres la oferta.</p></div>`;
  const form = `<div class="row">` +
    `<label>Importe de la oferta (€) <input id="oAmount" type="number" min="1" step="100" class="num-input" inputmode="decimal"></label>` +
    (offer ? `<label>Motivo del cambio (obligatorio) <input id="oMotivo" size="28"></label>` : "") +
    `<button type="button" class="primary" data-act="offer">${offer ? "Registrar nueva versión" : "Registrar oferta"}</button></div>`;
  return sec("sec-oferta", "Oferta", info + live + form);
}

function sectionEjecucion(c) {
  const s = c.state, offer = s.offer, recoveries = s.recoveries || [];
  const cobrado = recoveries.filter(r => !r.anulado).reduce((a, r) => a + r.amount, 0);
  const history = (s.phase_history || []).map(h =>
    `${esc(PHASES[h.phase] || h.phase)} (${esc(h.date)})${h.retroceso ? ` <span class="warn">retroceso: ${esc(h.motivo || "")}</span>` : ""}`).join(" → ");
  const phaseOptions = optionsOf(PHASES, c.phase);
  const phaseBox = `<h4>Fase procesal</h4>` +
    `<div class="row"><label>Fase <select id="pPhase">${phaseOptions}</select></label>` +
    `<label>Motivo (obligatorio si retrocedes) <input id="pMotivo" size="24"></label>` +
    `<button type="button" data-act="phase">Cambiar fase</button></div>` +
    `<p class="muted">Recorrido: ${history || "—"}</p>`;

  const recRows = recoveries.map((r, i) => {
    if (r.anulado) {
      return `<tr><td>${esc(r.date)}</td><td class="num">${fmt(r.amount)}</td><td><span class="muted">anulado: ${esc(r.motivo_anulacion || "")}</span></td></tr>`;
    }
    const open = voidIdx === i
      ? `<tr><td colspan="4"><div class="confirm-box"><label class="block-label">Motivo de la anulación (obligatorio; el cobro queda en el historial)` +
        ` <textarea id="vMotivo"></textarea></label>` +
        `<div class="row"><button type="button" class="primary" data-act="void" data-idx="${i}">Confirmar anulación</button>` +
        ` <button type="button" data-void-cancel="1">No anular</button></div></div></td></tr>`
      : "";
    return `<tr><td>${esc(r.date)}</td><td class="num">${fmt(r.amount)}</td><td>válido</td>` +
      `<td><button type="button" data-void-open="${i}" aria-label="Anular el cobro de ${esc(fmt(r.amount))} del ${esc(r.date)}">Anular…</button></td></tr>${open}`;
  }).join("");
  const recTable = recoveries.length
    ? `<div class="scroll"><table><thead><tr><th>Fecha</th><th class="num">Importe</th><th>Estado</th><th><span class="sr">Acciones</span></th></tr></thead><tbody>${recRows}</tbody></table></div>` +
      `<p class="muted">Cobrado (sin anulados): <b class="num">${fmt(cobrado)}</b>${offer ? ` de ${fmt(offer.amount)}` : ""}</p>`
    : `<p class="muted">Sin cobros.</p>`;
  const recForm = offer
    ? `<div class="row"><label>Importe cobrado (€) <input id="rAmount" type="number" min="1" step="100" class="num-input"></label>` +
      `<label>Fecha <input id="rDate" type="date"></label>` +
      `<button type="button" data-act="recovery">Registrar cobro</button></div>`
    : `<p class="muted">Registra antes la oferta para poder registrar cobros.</p>`;
  const recBox = `<h4>Cobros</h4>` + recTable + recForm;

  const taskRows = (c.tasks || []).map(t => {
    const action = t.status === "pendiente" ? `<button type="button" data-act="task-done" data-id="${esc(t.id)}">Completar</button>` : "";
    return `<tr><td>${esc(t.title)}</td><td>${esc(t.owner || "—")}</td><td>${esc(t.due_date || "—")}</td>` +
      `<td>${t.status === "completada" ? "✔ " : "○ "}${esc(t.status)}</td><td>${action}</td></tr>`;
  }).join("");
  const taskBox = `<h4>Tareas</h4>` +
    (taskRows ? `<div class="scroll"><table><thead><tr><th>Tarea</th><th>Responsable</th><th>Vence</th><th>Estado</th><th><span class="sr">Acciones</span></th></tr></thead><tbody>${taskRows}</tbody></table></div>`
              : `<p class="muted">Sin tareas.</p>`) +
    `<div class="row"><label>Nueva tarea <input id="tTitle" size="26"></label>` +
    `<label>Responsable <input id="tOwner" size="12"></label>` +
    `<label>Vence <input id="tDue" type="date"></label>` +
    `<button type="button" data-act="task-add">Añadir tarea</button></div>`;

  return sec("sec-ejecucion", "Ejecución", phaseBox + recBox + taskBox);
}

// ---- simulador de oferta -----------------------------------------------------------

function refreshPreview() {
  clearTimeout(previewTimer);
  const el = $("offerLive");
  if (!el || !$("oAmount")) return;
  const amount = parseFloat($("oAmount").value);
  if (!Number.isFinite(amount) || amount <= 0) return;
  previewTimer = setTimeout(() => previewOffer(amount), 250);
}

async function previewOffer(amount) {
  try {
    const p = await api("POST", `/api/cases/${currentId}/offer/preview`, { amount });
    const input = $("oAmount");
    if (!input || parseFloat(input.value) !== amount) return;   // llegó tarde: ya hay otro importe
    $("offerLive").innerHTML = renderPreview(p);
  } catch (e) {
    if ($("offerLive")) $("offerLive").innerHTML = `<p class="err">${esc(e.message)}</p>`;
  }
}

function renderPreview(p) {
  if (p.message) return `<p class="muted">${esc(p.message)}</p>`;
  let html = "";
  if (p.max_price != null) {
    const over = p.over_max_price;
    const fill = p.max_price > 0 ? Math.min(p.amount / p.max_price, 1) : 1;
    html += `<div class="bar${over ? " over" : ""}" role="img" aria-label="${over ? "La oferta supera" : "La oferta está dentro de"} el precio máximo"><span style="width:${(fill * 100).toFixed(1)}%"></span></div>`;
    if (p.max_price_floored) html += `<p class="state over">⚠ La vía no genera valor: cualquier oferta supera su precio máximo (0 €).</p>`;
    else if (over) html += `<p class="state over">⚠ Supera el precio máximo en ${esc(fmt(p.amount - p.max_price))}</p>`;
    else html += `<p class="state under">✔ Dentro del precio máximo: quedan ${esc(fmt(p.max_price - p.amount))}</p>`;
  } else {
    html += `<p class="muted">No hay precio máximo para esta vía.</p>`;
  }
  html += `<dl>` +
    `<dt><abbr title="Tasa interna de retorno: rentabilidad anual de la operación si se cumplen los flujos previstos">TIR</abbr> prevista</dt><dd>${p.planned_irr == null ? "no calculable" : esc(pct(p.planned_irr))}</dd>` +
    `<dt>Precio máximo${p.route ? ` (${esc(p.route)})` : ""}</dt><dd>${p.max_price == null ? "—" : esc(p.max_price_floored ? "0 €" : fmt(p.max_price))}</dd>` +
    `</dl>`;
  if (!p.approval_ok) html += `<p class="bad">✕ Supera tu límite de aprobación: no puedes registrar esta oferta.</p>`;
  if (p.blockers && p.blockers.length) html += `<p class="warn">Antes de ofertar: ${esc(p.blockers.join("; "))}</p>`;
  return html;
}

// ---- acciones -------------------------------------------------------------------

async function run(action) {
  if (pending) return;
  pending = true;
  $("busyMsg").textContent = "Guardando…";
  setDetailErr("");
  try {
    const msg = await action();
    await refreshDetail(msg || "Hecho.");
  } catch (e) {
    await refreshDetail();
    setDetailErr(e.message);
    toast(e.message, "err");
  } finally {
    pending = false;
    $("busyMsg").textContent = "";
  }
}

function text(id) { return $(id) ? $(id).value.trim() : ""; }

function num(id, label) {
  const v = parseFloat($(id).value);
  if (!Number.isFinite(v) || v <= 0) throw new Error(`${label} debe ser un número positivo`);
  return v;
}

async function confirmField(field, idx) {
  const it = currentCase.state.diligence.candidates[field][idx];
  await api("POST", `/api/cases/${currentId}/diligence/confirm`, { field, value: it.value });
  return `${FIELDS[field]} confirmado.`;
}

async function manualConfirm(field) {
  const value = text(`man-${field}`);
  if (!value) throw new Error(`Escribe el valor correcto de "${FIELDS[field]}"`);
  await api("POST", `/api/cases/${currentId}/diligence/confirm`, { field, value, manual: true });
  return `${FIELDS[field]} corregido y confirmado.`;
}

// Convierte un archivo en base64 por trozos (evita apilar demasiados argumentos al convertirlo).
async function fileToBase64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

const MAX_UPLOAD_BYTES = 5000000;

// Carga un documento: un PDF se lee en el servidor (con su texto a la vista); un .txt se lee aquí.
function readDocument(file) {
  if (!file) return;
  setDetailErr("");
  if (/\.pdf$/i.test(file.name) || file.type === "application/pdf") {
    if (file.size > MAX_UPLOAD_BYTES) { setDetailErr("El PDF supera el tamaño máximo (5 MB)."); return; }
    run(async () => {
      const pdf_base64 = await fileToBase64(file);
      const res = await api("POST", `/api/cases/${currentId}/diligence/extract-pdf`, { document: file.name, pdf_base64 });
      $("docText").value = res.text;
      $("docName").value = file.name;
      return res.found.length
        ? `PDF leído: ${res.found.length} datos extraídos. Confírmalos uno a uno.`
        : "PDF leído, pero no se encontró ningún dato reconocible.";
    });
    return;
  }
  if (!/\.txt$/i.test(file.name) && file.type !== "text/plain") {
    setDetailErr("Solo se admiten archivos .pdf o .txt.");
    return;
  }
  if (file.size > 900000) { setDetailErr("El archivo es demasiado grande (máximo 900 KB)."); return; }
  const reader = new FileReader();
  reader.onload = () => {
    $("docText").value = String(reader.result || "");
    $("docName").value = file.name;
    toast("Documento cargado. Revisa el texto y pulsa Extraer datos.");
  };
  reader.onerror = () => setDetailErr("No se pudo leer el archivo.");
  reader.readAsText(file, "utf-8");
}

const ACTIONS = {
  "screen": () => api("POST", `/api/cases/${currentId}/screen`, {}).then(() => "Cribado recalculado."),

  "extract": async () => {
    const t = $("docText").value;
    if (!t.trim()) throw new Error("Carga un documento o pega su texto antes de extraer");
    const res = await api("POST", `/api/cases/${currentId}/diligence/extract`,
      { document: $("docName").value || "documento", text: t });
    return res.found.length
      ? `Extraídos ${res.found.length} datos. Confírmalos uno a uno.`
      : "No se encontró ningún dato reconocible en el texto.";
  },

  "risk-add": async () => {
    const description = text("rDesc");
    if (!description) throw new Error("Escribe la descripción del riesgo");
    await api("POST", `/api/cases/${currentId}/risks`, {
      category: $("rCat").value, level: $("rLevel").value, description,
      norm_ref: text("rNorm") || null,
    });
    return "Riesgo añadido.";
  },

  "approve": (btn) => api("POST", `/api/cases/${currentId}/risks/${btn.dataset.idx}/approve`)
    .then(() => "Riesgo aprobado."),

  "decision": async () => {
    await api("POST", `/api/cases/${currentId}/decision`, { chosen: $("dChosen").value, justification: text("dJust") });
    return "Decisión registrada.";
  },

  "offer": async () => {
    const body = { amount: num("oAmount", "El importe") };
    if ($("oMotivo")) body.motivo = text("oMotivo");
    const res = await api("POST", `/api/cases/${currentId}/offer`, body);
    return res.warnings && res.warnings.length ? res.warnings.join(" ") : "Oferta registrada.";
  },

  "recovery": async () => {
    const body = { amount: num("rAmount", "El cobro") };
    if ($("rDate").value) body.date = $("rDate").value;
    await api("POST", `/api/cases/${currentId}/recoveries`, body);
    return "Cobro registrado.";
  },

  "void": async (btn) => {
    const motivo = text("vMotivo");
    if (!motivo) throw new Error("Escribe el motivo de la anulación");
    await api("POST", `/api/cases/${currentId}/recoveries/${btn.dataset.idx}/void`, { motivo });
    voidIdx = null;
    return "Cobro anulado. Queda en el historial.";
  },

  "task-add": async () => {
    const title = text("tTitle");
    if (!title) throw new Error("Escribe el título de la tarea");
    const body = { title, owner: text("tOwner") || null };
    if ($("tDue").value) body.due_date = $("tDue").value;
    await api("POST", `/api/cases/${currentId}/tasks`, body);
    return "Tarea añadida.";
  },

  "task-done": (btn) => api("POST", `/api/cases/${currentId}/tasks/${btn.dataset.id}/status`, { status: "completada" })
    .then(() => "Tarea completada."),

  "phase": () => {
    const body = { phase: $("pPhase").value };
    const motivo = text("pMotivo");
    if (motivo) body.motivo = motivo;
    return api("POST", `/api/cases/${currentId}/phase`, body).then(() => "Fase actualizada.");
  },
};

async function openReport() {
  setDetailErr("");
  try {
    const html = await api("GET", `/api/cases/${currentId}/report`);
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    window.open(url, "_blank", "noopener");
  } catch (e) { setDetailErr(e.message); }
}

// ---- eventos --------------------------------------------------------------------

$("closeDetail").onclick = () => {
  $("detailCard").hidden = true;
  currentId = null; currentCase = null; voidIdx = null;
};

$("detailCard").addEventListener("click", (ev) => {
  const t = ev.target.closest("button, [data-target]");
  if (!t || !$("detailCard").contains(t)) return;
  if (t.dataset.target) return focusSection(t.dataset.target);
  if (t.dataset.confirm) return run(() => confirmField(t.dataset.confirm, Number(t.dataset.idx)));
  if (t.dataset.voidOpen !== undefined) {
    voidIdx = Number(t.dataset.voidOpen);
    rerender();
    if ($("vMotivo")) $("vMotivo").focus();
    return;
  }
  if (t.dataset.voidCancel !== undefined) { voidIdx = null; return rerender(); }
  if (t.dataset.act === "report") return openReport();
  const fn = ACTIONS[t.dataset.act];
  if (fn) run(() => fn(t));
});

$("detailCard").addEventListener("submit", (ev) => {
  const form = ev.target.closest("[data-manual]");
  if (!form) return;
  ev.preventDefault();
  run(() => manualConfirm(form.dataset.manual));
});

$("detailCard").addEventListener("change", (ev) => {
  if (ev.target.id === "rTemplate") {
    const t = (catalog.risk_catalog || []).find(x => x.id === ev.target.value);
    if (!t) return;
    $("rCat").value = t.category;
    $("rLevel").value = t.level;
    $("rDesc").value = t.description;
    $("rNorm").value = t.norm_ref;
  } else if (ev.target.id === "docFile") {
    readDocument(ev.target.files && ev.target.files[0]);
  }
});

$("detailCard").addEventListener("input", (ev) => {
  if (ev.target.id === "oAmount") refreshPreview();
});

["dragenter", "dragover"].forEach(type => $("detailCard").addEventListener(type, (ev) => {
  const drop = ev.target.closest && ev.target.closest("#drop");
  if (!drop) return;
  ev.preventDefault();
  drop.classList.add("over");
}));
["dragleave", "drop"].forEach(type => $("detailCard").addEventListener(type, (ev) => {
  const drop = ev.target.closest && ev.target.closest("#drop");
  if (!drop) return;
  ev.preventDefault();
  drop.classList.remove("over");
  if (type === "drop") readDocument(ev.dataTransfer.files && ev.dataTransfer.files[0]);
}));

start();
