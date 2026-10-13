// RecallIndia web app. Plain JavaScript, no build step: served by the FastAPI app at /app/.
const view = document.getElementById("view");
const sheetRoot = document.getElementById("sheet-root");
const ZXING_URL = "https://cdn.jsdelivr.net/npm/@zxing/library@0.21.3/umd/index.min.js";
const CATEGORIES = [
  ["", "Not sure"], ["1", "Dairy"], ["2", "Fats and oils"], ["5", "Sweets and confectionery"], ["6", "Cereals and grains"],
  ["7", "Bakery (bread, biscuits)"], ["12", "Spices, salt, sauces"], ["13.1", "Infant formula"], ["13.2", "Baby food"],
  ["14", "Beverages"], ["15", "Savoury snacks"],
];
const GSI_URL = "https://accounts.google.com/gsi/client";
const state = { user: null, token: null, auth: null, summary: null, lastResult: null, openAlert: null, productFilter: "all", scanMode: "barcode" };
let stopCamera = () => {};

// ---------- helpers ----------
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const icon = {
  shield: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/><path d="M9 12l2 2 4-4"/></svg>',
  alert: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16.5v.5"/></svg>',
  check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12l5 5 9-10"/></svg>',
  search: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>',
  chev: '<svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>',
  back: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>',
  mail: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>',
  receipt: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6M9 12h6"/></svg>',
  barcode: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 8V5h3M17 5h3v3M20 16v3h-3M7 19H4v-3M8 9v6M11 9v6M14 9v6M17 9v6"/></svg>',
  plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
  camera: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="6" width="18" height="14" rx="3"/><circle cx="12" cy="13" r="3.5"/><path d="M8 6l1.5-2h5L16 6"/></svg>',
};

function toast(msg, ms = 3500) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), ms);
}

async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  if (state.token) init.headers.Authorization = `Bearer ${state.token}`;
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers["Content-Type"] = "application/json";
    delete init.json;
  }
  const res = await fetch(path, init);
  const body = res.headers.get("content-type")?.includes("json") ? await res.json() : await res.text();
  if (res.status === 401 && state.user && !path.startsWith("/auth/")) {
    signOut();
    toast("Please sign in again.");
  }
  if (!res.ok) {
    const detail = typeof body === "object" && body ? body.detail : body;
    const err = new Error(typeof detail === "string" ? detail : `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return body;
}
const userPath = (p = "") => `/users/${encodeURIComponent(state.user.user_id)}${p}`;

function fmtDate(d) {
  if (!d) return "";
  const dt = new Date(String(d).length <= 10 ? `${d}T00:00:00` : d);
  if (isNaN(dt)) return String(d);
  return dt.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: dt.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
}
const loading = () => (view.innerHTML = '<div class="spinner" role="img" aria-label="Loading"></div>');
const errorBox = (e, retry) => `<div class="card pad stack"><b>Something went wrong</b><div class="small muted">${esc(e.message)}</div>
  ${retry ? '<button class="btn ghost" data-act="retry">Try again</button>' : ""}</div>`;

function openSheet(html, onMount) {
  sheetRoot.innerHTML = `<div class="sheet-bg" data-close="1"><div class="sheet" role="dialog" aria-modal="true"><div class="grab"></div>${html}</div></div>`;
  const bg = sheetRoot.firstElementChild;
  bg.addEventListener("click", (e) => { if (e.target.dataset.close) closeSheet(); });
  onMount?.(bg.querySelector(".sheet"));
}
const closeSheet = () => (sheetRoot.innerHTML = "");

function pickFile(accept, capture) {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    if (capture) input.capture = capture;
    input.onchange = () => resolve(input.files[0] || null);
    input.click();
  });
}

function header(title, { back } = {}) {
  const initial = esc((state.user?.name || state.user?.user_id || "?").trim().charAt(0).toUpperCase());
  return `<div class="top">
    <div class="row">${back ? `<a class="back" href="${back}" aria-label="Back">${icon.back}</a>` : ""}${title}</div>
    <button class="avatar" data-act="account" aria-label="Account">${initial}</button></div>`;
}

async function refreshBadge() {
  try {
    state.summary = await api(userPath("/summary"));
    const b = document.getElementById("alert-badge");
    b.textContent = state.summary.open_alerts;
    b.hidden = !state.summary.open_alerts;
  } catch { /* badge is optional */ }
}

// ---------- notifications ----------
const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
function b64ToBytes(b64) {
  const s = atob(b64.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (b64.length % 4)) % 4));
  return Uint8Array.from(s, (c) => c.charCodeAt(0));
}
async function pushState() {
  if (!pushSupported()) return { available: false, why: "This browser doesn't support notifications. On iPhone, add RecallIndia to your Home Screen first." };
  if (!window.isSecureContext) return { available: false, why: "Notifications need the app to be opened over https." };
  const cfg = await api("/push/config").catch(() => ({ enabled: false }));
  if (!cfg.enabled) return { available: false, why: "Notifications aren't set up on this server yet." };
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  return { available: true, on: !!sub && Notification.permission === "granted", denied: Notification.permission === "denied", key: cfg.public_key, reg, sub };
}
async function enablePush() {
  const st = await pushState();
  if (!st.available) return toast(st.why, 6000);
  if ((await Notification.requestPermission()) !== "granted") return toast("Notifications are blocked. Allow them for this site in your browser settings.", 6000);
  try {
    const sub = st.sub || await st.reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(st.key) });
    await api(userPath("/push"), { method: "POST", json: sub.toJSON() });
    await api(userPath("/push/test"), { method: "POST" });
    toast("Notifications are on. We sent you a test one.");
    route();
  } catch (e) { toast(e.message, 6000); }
}
async function disablePush() {
  const st = await pushState();
  if (st.sub) {
    await api(userPath("/push/remove"), { method: "POST", json: st.sub.toJSON() }).catch(() => {});
    await st.sub.unsubscribe().catch(() => {});
  }
  toast("Notifications are off on this device.");
  route();
}
async function pushCard(el) {
  const st = await pushState().catch(() => ({ available: false }));
  if (!el.isConnected || !st.available || st.on || localStorage.getItem("ri_push_dismissed")) return;
  el.innerHTML = `<div class="card pad row" style="align-items:flex-start">
    <svg viewBox="0 0 24 24" aria-hidden="true" style="color:var(--accent)"><path d="M6 16V11a6 6 0 0112 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 004 0"/></svg>
    <div style="flex:1" class="stack"><div><b class="small">Get a notification if something you bought is recalled</b>
      <div class="tiny muted">${st.denied ? "Notifications are blocked for this site. Allow them in your browser settings." : "One tap. You can turn it off any time."}</div></div>
      ${st.denied ? "" : '<div class="row"><button class="btn primary" id="push-on" style="min-height:40px;padding:0 14px;white-space:nowrap">Turn on</button><button class="btn ghost" id="push-later" style="min-height:40px;padding:0 14px;white-space:nowrap">Not now</button></div>'}</div></div>`;
  el.querySelector("#push-on")?.addEventListener("click", enablePush);
  el.querySelector("#push-later")?.addEventListener("click", () => { try { localStorage.setItem("ri_push_dismissed", "1"); } catch {} el.innerHTML = ""; });
}

// ---------- router ----------
const routes = { welcome: viewWelcome, home: viewHome, scan: viewScan, result: viewResult, products: viewProducts, alerts: viewAlerts, lookup: viewLookup };

function route() {
  stopCamera();
  closeSheet();
  const [path, query] = (location.hash.replace(/^#\/?/, "") || "home").split("?");
  const name = state.user ? (routes[path] ? path : "home") : "welcome";
  document.getElementById("tabbar").hidden = name === "welcome";
  document.querySelectorAll(".tabbar a").forEach((a) => a.classList.toggle("active", a.dataset.tab === name || (name === "result" && a.dataset.tab === "scan") || (name === "lookup" && a.dataset.tab === "home")));
  window.scrollTo(0, 0);
  routes[name](new URLSearchParams(query || ""));
}

view.addEventListener("click", (e) => {
  const el = e.target.closest("[data-act]");
  if (el && el.dataset.act === "account") openAccount();
  if (el && el.dataset.act === "retry") route();
});

// ---------- welcome ----------
function saveLogin(user, token) {
  state.user = { user_id: user.user_id, name: user.name || "" };
  state.token = token || null;
  try {
    localStorage.setItem("ri_user", JSON.stringify(state.user));
    if (token) localStorage.setItem("ri_token", token); else localStorage.removeItem("ri_token");
  } catch { /* private mode: stays signed in for this tab only */ }
  location.hash = "#/home";
  route();
  refreshBadge();
}
function signOut() {
  state.user = null; state.token = null;
  try { localStorage.removeItem("ri_user"); localStorage.removeItem("ri_token"); } catch { /* ignore */ }
  window.google?.accounts?.id?.disableAutoSelect?.();
  closeSheet();
  location.hash = "#/welcome";
  route();
}
async function authConfig() {
  if (!state.auth) state.auth = await api("/auth/config").catch(() => ({ auth_enabled: false }));
  return state.auth;
}

async function viewWelcome() {
  const cfg = await authConfig();
  if (cfg.auth_enabled) return viewGoogleWelcome(cfg);
  view.innerHTML = `
    <div class="brand" style="margin-top:24px"><span class="logo">${icon.shield}</span>RecallIndia</div>
    <div><div class="big">Know when something you bought is recalled</div>
    <p class="muted small">RecallIndia checks official FSSAI recall notices against the products you buy, and tells you if one matches.</p></div>
    <form class="stack" id="welcome">
      <label class="field">Your name<input class="input" name="name" required autocomplete="name" placeholder="e.g. Ritika"></label>
      <label class="field">Email for alerts (optional)<input class="input" name="email" type="email" autocomplete="email" placeholder="you@example.com"></label>
      <button class="btn primary block">Get started</button>
      <p class="note">Already set up on another device? Use the same name and the code shown in your account there.</p>
      <label class="field">Account code (optional)<input class="input" name="code" placeholder="e.g. ritika-4821"></label>
    </form>`;
  view.querySelector("#welcome").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const name = f.get("name").trim();
    const code = f.get("code").trim();
    try {
      let user;
      if (code) {
        user = await api(`/users/${encodeURIComponent(code)}`);
      } else {
        const id = `${name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "user"}-${Math.floor(1000 + Math.random() * 9000)}`;
        user = await api("/users", { method: "POST", json: { user_id: id, name, email: f.get("email").trim() || null } });
      }
      saveLogin({ user_id: user.user_id, name: user.name || name });
    } catch (err) {
      toast(err.status === 404 ? "No account with that code." : err.message);
    }
  });
}

function viewGoogleWelcome(cfg) {
  view.innerHTML = `
    <div class="brand" style="margin-top:24px"><span class="logo">${icon.shield}</span>RecallIndia</div>
    <div><div class="big">Know when something you bought is recalled</div>
    <p class="muted small">RecallIndia checks official FSSAI recall notices against the products you buy, and tells you if one matches.</p></div>
    <div class="card pad stack" style="align-items:center"><div id="gbtn" style="min-height:44px"></div>
      <div class="note" style="text-align:center">We use your Google name and email only to keep your products and alerts private to you.</div></div>`;
  const render = () => {
    window.google.accounts.id.initialize({
      client_id: cfg.google_client_id,
      callback: async ({ credential }) => {
        try {
          const out = await api("/auth/google", { method: "POST", json: { credential } });
          saveLogin(out.user, out.token);
        } catch (e) { toast(e.message, 6000); }
      },
    });
    window.google.accounts.id.renderButton(view.querySelector("#gbtn"), { theme: "outline", size: "large", text: "continue_with", shape: "pill", width: 300 });
  };
  if (window.google?.accounts?.id) return render();
  const sc = document.createElement("script");
  sc.src = GSI_URL; sc.async = true; sc.onload = render;
  sc.onerror = () => toast("Couldn't load Google sign-in. Check your internet connection.", 6000);
  document.head.appendChild(sc);
}

function openAccount() {
  openSheet(`<h2>${esc(state.user.name || "Your account")}</h2>
    ${state.token ? '<div class="card pad small">Signed in with Google. Sign in the same way on any device to see the same products.</div>' : `<div class="card pad stack small"><div class="between"><span class="muted">Account code</span><b>${esc(state.user.user_id)}</b></div>
    <div class="note">Use this code to open the same account on another device.</div></div>`}
    <button class="btn ghost block" id="push-toggle">Notifications…</button>
    <a class="btn ghost block" href="/admin/review">Review queue (admin)</a>
    <button class="btn ghost block" id="signout">Sign out on this device</button>`, (s) => {
    s.querySelector("#signout").onclick = signOut;
    const pt = s.querySelector("#push-toggle");
    pushState().then((st) => {
      if (!st.available) { pt.textContent = "Notifications unavailable"; pt.disabled = true; pt.title = st.why; return; }
      pt.textContent = st.on ? "Turn off notifications" : "Turn on notifications";
      pt.onclick = () => { closeSheet(); st.on ? disablePush() : enablePush(); };
    }).catch(() => { pt.hidden = true; });
  });
}

// ---------- home ----------
async function viewHome() {
  loading();
  try {
    const s = await api(userPath("/summary"));
    state.summary = s;
    refreshBadgeFrom(s);
    const n = s.affected.length;
    const names = s.affected.slice(0, 4).map((a) => a.product_name);
    const statusCard = s.watched === 0 ? `
      <div class="status good"><div class="head"><span class="ic">${icon.plus}</span>Add your first products</div>
        <div class="small">Import your online orders, scan a shop receipt, or scan a barcode. RecallIndia then watches them for recalls.</div>
        <a class="btn primary" style="align-self:flex-start" href="#/products">Add products</a></div>`
      : n ? `
      <div class="status bad"><div class="head"><span class="ic">${icon.alert}</span>${n} of your products ${n === 1 ? "matches" : "match"} FSSAI recalls</div>
        <div class="small" style="line-height:1.45">${esc(names.join(", "))}${n > 4 ? ` and ${n - 4} more` : ""}. Check the batch number on your pack.</div>
        <a class="btn danger" style="align-self:flex-start" href="#/alerts">See what to do</a></div>`
      : `
      <div class="status good"><div class="head"><span class="ic">${icon.check}</span>None of your products match a recall</div>
        <div class="small">We check every new FSSAI recall against your ${s.watched} products.</div></div>`;
    view.innerHTML = `
      ${header(`<div class="brand"><span class="logo">${icon.shield}</span>RecallIndia</div>`)}
      <div><div class="muted small">Hi ${esc(state.user.name || "")}</div><div class="big">Is anything you bought recalled?</div></div>
      ${statusCard}
      <div id="push-card"></div>
      <div class="grid2">
        <div class="card pad stat"><div class="n">${s.watched}</div><div class="l">products watched</div></div>
        <div class="card pad stat"><div class="n">${s.recalls}</div><div class="l">FSSAI recalls checked${s.last_checked ? ` · ${esc(fmtDate(s.last_checked))}` : ""}</div></div>
      </div>
      <a class="search" href="#/lookup">${icon.search}Search an ingredient or E-number</a>
      <div class="stack">
        <h2>Latest FSSAI recalls</h2>
        <div class="card list">${s.latest_recalls.map((r) => `
          <div class="item" style="cursor:default"><div style="flex:1;min-width:0">
            <div class="t">${esc([r.brand, r.product_name].filter(Boolean).join(" · "))}</div>
            <div class="s">${r.batch_no ? `Batch ${esc(r.batch_no)}` : "No batch listed"} · Recall ${esc(r.recall_id)}${r.recall_type ? ` · ${esc(r.recall_type)}` : ""}</div></div>
            <div class="tiny muted">${esc(fmtDate(r.recall_date))}</div></div>`).join("") || '<div class="empty">No recalls loaded yet.</div>'}</div>
      </div>`;
    pushCard(view.querySelector("#push-card"));
  } catch (e) {
    view.innerHTML = header(`<div class="brand"><span class="logo">${icon.shield}</span>RecallIndia</div>`) + errorBox(e, true);
  }
}
function refreshBadgeFrom(s) {
  const b = document.getElementById("alert-badge");
  b.textContent = s.open_alerts;
  b.hidden = !s.open_alerts;
}

// ---------- scan ----------
function viewScan() {
  const mode = state.scanMode;
  const catOptions = CATEGORIES.map(([v, l]) => `<option value="${v}">${esc(l)}</option>`).join("");
  view.innerHTML = `
    ${header("<h1>Scan a product</h1>")}
    <div class="seg" role="tablist">
      <button role="tab" data-mode="barcode" class="${mode === "barcode" ? "on" : ""}">Barcode</button>
      <button role="tab" data-mode="photo" class="${mode === "photo" ? "on" : ""}">Label photo</button>
      <button role="tab" data-mode="type" class="${mode === "type" ? "on" : ""}">Type it</button>
    </div>
    <div id="mode"></div>
    <a class="card pad row" href="#/products" style="text-decoration:none;color:inherit">${icon.receipt}
      <div style="flex:1"><b class="small">Have a shop receipt?</b><div class="tiny muted">Save everything on it in My Products</div></div>${icon.chev}</a>`;
  view.querySelectorAll(".seg button").forEach((b) => (b.onclick = () => { state.scanMode = b.dataset.mode; stopCamera(); viewScan(); }));
  const box = view.querySelector("#mode");

  if (mode === "barcode") {
    const live = window.isSecureContext && navigator.mediaDevices?.getUserMedia;
    box.innerHTML = `
      <div class="stack">
        <div class="viewfinder" id="vf"><div class="idle">${icon.camera}
          ${live ? '<button class="btn primary" id="start">Start camera</button>' : '<span>Live camera needs a secure (https) connection. Take a photo of the barcode instead.</span>'}</div></div>
        <button class="btn ghost block" id="snap">${icon.camera}Take a photo of the barcode</button>
        <form class="row" id="manual"><input class="input" name="code" inputmode="numeric" pattern="[0-9]{8,14}" placeholder="Or type the digits under the barcode" aria-label="Barcode digits">
          <button class="btn primary">Check</button></form>
      </div>`;
    if (live) box.querySelector("#start").onclick = () => startCamera(box.querySelector("#vf"));
    box.querySelector("#snap").onclick = async () => {
      const file = await pickFile("image/*", "environment");
      if (!file) return;
      toast("Reading barcode…");
      const code = await decodeImage(file).catch(() => null);
      if (code) checkBarcode(code);
      else toast("Couldn't read a barcode in that photo. Try again closer, or type the digits.");
    };
    box.querySelector("#manual").addEventListener("submit", (e) => { e.preventDefault(); checkBarcode(new FormData(e.target).get("code").trim()); });
  } else if (mode === "photo") {
    box.innerHTML = `
      <div class="card pad stack">
        <div class="small">Take a clear photo of the <b>ingredients list</b> on the pack. RecallIndia reads it and checks each ingredient against FSSAI rules.</div>
        <label class="field">Type of food<select class="input" id="cat">${catOptions}</select></label>
        <button class="btn primary block" id="photo">${icon.camera}Take or choose a photo</button>
      </div>`;
    box.querySelector("#photo").onclick = async () => {
      const file = await pickFile("image/*", "environment");
      if (!file) return;
      const fd = new FormData();
      fd.append("file", file);
      fd.append("category", box.querySelector("#cat").value);
      await runCheck(() => api("/check/photo", { method: "POST", body: fd }), "Reading the label…");
    };
  } else {
    box.innerHTML = `
      <form class="card pad stack" id="typed">
        <label class="field">Ingredients from the label<textarea class="input" name="text" required placeholder="Wheat flour, Sugar, Palm oil, Colours (INS 102, 124), Salt"></textarea></label>
        <label class="field">Type of food<select class="input" name="category">${catOptions}</select></label>
        <div class="grid2"><label class="field">Product (optional)<input class="input" name="name"></label>
          <label class="field">Brand (optional)<input class="input" name="brand"></label></div>
        <button class="btn primary block">Check ingredients</button>
      </form>`;
    box.querySelector("#typed").addEventListener("submit", (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      runCheck(() => api("/check", { method: "POST", json: { ingredients_text: f.get("text"), category: f.get("category") || null,
        product_name: f.get("name") || null, brand: f.get("brand") || null } }), "Checking…");
    });
  }
}

async function runCheck(fn, msg) {
  toast(msg, 20000);
  try {
    state.lastResult = await fn();
    document.getElementById("toast").hidden = true;
    location.hash = "#/result";
  } catch (e) {
    toast(e.message, 6000);
  }
}
function checkBarcode(code) {
  if (!/^\d{8,14}$/.test(code)) return toast("A barcode has 8 to 14 digits.");
  stopCamera();
  runCheck(async () => ({ ...(await api("/check", { method: "POST", json: { barcode: code } })), barcode: code }), `Looking up ${code}…`)
    .then(() => {});
}

function loadZxing() {
  if (window.ZXing) return Promise.resolve(window.ZXing);
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = ZXING_URL;
    s.onload = () => resolve(window.ZXing);
    s.onerror = () => reject(new Error("Couldn't load the barcode reader. Check your internet connection."));
    document.head.appendChild(s);
  });
}
const nativeDetector = () => ("BarcodeDetector" in window ? new window.BarcodeDetector({ formats: ["ean_13", "ean_8", "upc_a", "upc_e"] }) : null);

async function decodeImage(file) {
  const det = nativeDetector();
  if (det) {
    const found = await det.detect(await createImageBitmap(file));
    if (found[0]) return found[0].rawValue;
  }
  const ZX = await loadZxing();
  const url = URL.createObjectURL(file);
  try {
    return (await new ZX.BrowserMultiFormatReader().decodeFromImageUrl(url)).getText();
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function startCamera(vf) {
  vf.innerHTML = '<video playsinline muted></video><div class="frame"></div><div class="hint">Point at the barcode on the pack</div>';
  const video = vf.querySelector("video");
  let stopped = false;
  try {
    const det = nativeDetector();
    if (det) {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
      video.srcObject = stream;
      await video.play();
      stopCamera = () => { stopped = true; stream.getTracks().forEach((t) => t.stop()); stopCamera = () => {}; };
      const tick = async () => {
        if (stopped) return;
        try {
          const found = await det.detect(video);
          if (found[0]) return checkBarcode(found[0].rawValue);
        } catch { /* frame not ready */ }
        setTimeout(tick, 250);
      };
      tick();
    } else {
      const ZX = await loadZxing();
      const reader = new ZX.BrowserMultiFormatReader();
      stopCamera = () => { stopped = true; reader.reset(); stopCamera = () => {}; };
      reader.decodeFromVideoDevice(undefined, video, (result) => { if (result && !stopped) checkBarcode(result.getText()); });
    }
  } catch (e) {
    vf.innerHTML = `<div class="idle">${icon.camera}<span>${esc(e.name === "NotAllowedError" ? "Camera permission was refused. Allow it in your browser settings, or take a photo instead." : e.message)}</span></div>`;
  }
}

// ---------- result ----------
const VERDICT = {
  avoid: ["Avoid", "An ingredient is not allowed in this food"],
  caution: ["Caution", "Something needs a closer look"],
  check: ["Check", "Some ingredients are allowed only in certain foods"],
  "no issues found": ["No issues", "No FSSAI restriction found for these ingredients"],
};
const ABROAD = { not_authorised: "not authorised in food", being_withdrawn: "authorisation revoked, being phased out", restricted: "allowed only in some foods", warning_label: "warning label required" };

function viewResult() {
  const r = state.lastResult;
  if (!r) { location.hash = "#/scan"; return; }
  const [label, line] = VERDICT[r.verdict] || [r.verdict, ""];
  const cls = { "no issues found": "ok" }[r.verdict] || r.verdict;
  const flagged = r.findings.filter((f) => f.status !== "ok");
  const ok = r.findings.filter((f) => f.status === "ok").map((f) => f.substance).concat(r.other_ingredients || []);
  const p = r.product || {};
  view.innerHTML = `
    ${header("<h1>Scan result</h1>", { back: "#/scan" })}
    <div class="card pad row">
      ${p.image_url ? `<img src="${esc(p.image_url)}" alt="" style="width:56px;height:56px;object-fit:cover;border-radius:12px">` : ""}
      <div style="min-width:0"><div style="font-weight:800">${esc(p.product_name || "Product")}</div>
      <div class="small muted">${esc([p.brand, r.category_name, `from ${p.source}`].filter(Boolean).join(" · "))}</div></div></div>
    <div class="verdict ${cls}"><div class="row"><span class="pill">${esc(label)}</span><b>${esc(flagged.length ? `${flagged.length} ingredient${flagged.length > 1 ? "s" : ""} to note` : line)}</b></div>
      <div class="small" style="margin-top:6px">${esc(line)}. ${r.recalls.length ? "This product matches an FSSAI recall." : "No FSSAI recall matches this product."}</div></div>
    ${r.recalls.length ? `<div class="card list">${r.recalls.map((x) => `<div class="item" style="cursor:default"><span class="dot red"></span><div style="flex:1">
      <div class="t">Recall ${esc(x.recall_id)}: ${esc(x.product_name)}</div><div class="s">${x.batch_no ? `Batch ${esc(x.batch_no)} · ` : ""}${esc(x.reason || "")}</div></div></div>`).join("")}</div>` : ""}
    <div class="card list">
      ${flagged.map((f) => `<div class="find"><div class="between"><b class="small">${esc(f.substance)}${f.ins ? ` (INS ${esc(f.ins)})` : ""}</b><span class="st ${esc(f.status)}">${esc(f.status.replace("_", " "))}</span></div>
        <div class="tiny muted" style="margin-top:3px;line-height:1.45">${esc(f.message)}${(f.abroad || []).filter((a) => ABROAD[a.status]).map((a) => ` · ${esc(a.jurisdiction)}: ${esc(ABROAD[a.status])}`).join("")}</div></div>`).join("")}
      ${(r.label_notes || []).filter((n) => n.ingredient).map((n) => `<div class="find"><b class="small">${esc(n.ingredient)}</b><div class="tiny muted">${esc(n.message)}</div></div>`).join("")}
      <div class="find between"><span class="small muted">${ok.length ? esc(ok.slice(0, 4).join(", ")) + (ok.length > 4 ? ` +${ok.length - 4} more` : "") : `${r.ingredients_parsed} ingredients read`}</span><span class="tiny" style="color:var(--accent);font-weight:700">No issues</span></div>
    </div>
    <button class="btn primary block" id="save">Save to My Products</button>
    ${r.barcode && flagged.length ? `<div class="stack" id="alts"><div><h2 style="font-size:15px">Similar products that don't list ${esc(flagged[0].substance)}${flagged.length > 1 ? ` or ${flagged.length - 1} other` : ""}</h2>
      <div class="tiny muted">Matched by category and listed ingredients. Not a safety rating.</div></div><div class="spinner" style="margin:12px auto"></div></div>` : ""}
    <p class="note">${esc(r.disclaimer || "")}</p>`;
  view.querySelector("#save").onclick = () => saveScanned(r);
  if (r.barcode && flagged.length) loadAlternatives(r);
}

async function loadAlternatives(r) {
  const box = view.querySelector("#alts");
  const spot = box?.querySelector(".spinner");
  if (!box) return;
  try {
    const out = await api(`/alternatives?barcode=${encodeURIComponent(r.barcode)}${r.category ? `&category=${encodeURIComponent(r.category)}` : ""}`);
    if (!view.contains(box)) return;
    if (!out.items.length) { spot.outerHTML = '<div class="card pad small muted">No similar products with ingredient details found yet.</div>'; return; }
    spot.outerHTML = `<div class="grid2">${out.items.map((a) => `
      <button class="card pad stack" data-alt="${esc(a.barcode)}" style="gap:6px;text-align:left;cursor:pointer;font:inherit">
        ${a.image_url ? `<img src="${esc(a.image_url)}" alt="" loading="lazy" style="width:100%;height:84px;object-fit:contain;border-radius:10px;background:#fff">` : '<div style="height:84px;border-radius:10px;background:#EDEFE9"></div>'}
        <div style="font-weight:700;font-size:13px;line-height:1.3">${esc(a.product_name)}</div>
        ${a.brand ? `<div class="tiny muted">${esc(a.brand)}</div>` : ""}
        ${a.notes.map((n) => `<div class="tiny" style="color:var(--accent);font-weight:600">${esc(n)}</div>`).join("")}
      </button>`).join("")}</div>`;
    box.querySelectorAll("[data-alt]").forEach((b) => (b.onclick = () => checkBarcode(b.dataset.alt)));
  } catch {
    if (spot && view.contains(box)) spot.outerHTML = '<div class="card pad small muted">Suggestions are unavailable right now.</div>';
  }
}

function saveScanned(r) {
  const p = r.product || {};
  openSheet(`<h2>Save to My Products</h2>
    <form class="stack" id="save-form">
      <label class="field">Product name<input class="input" name="product_name" required value="${esc(p.product_name || "")}"></label>
      <label class="field">Brand<input class="input" name="brand" value="${esc(p.brand || "")}"></label>
      <label class="field">Batch number (from the pack, makes alerts precise)<input class="input" name="batch_no" placeholder="e.g. B2409"></label>
      <button class="btn primary block">Save</button></form>`, (s) => {
    s.querySelector("#save-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target));
      try {
        const out = await api(userPath("/purchases"), { method: "POST", json: [{ ...f, brand: f.brand || null, batch_no: f.batch_no || null,
          barcode: r.barcode || p.barcode || null, ingredients_text: r.ingredients_text || null, store: "Scanned", purchase_date: new Date().toISOString().slice(0, 10) }] });
        closeSheet();
        toast(out.auto_published ? "Saved. It matches a recall — see Alerts." : "Saved. We'll tell you if it's ever recalled.");
        location.hash = "#/products";
      } catch (err) { toast(err.message); }
    });
  });
}

// ---------- products ----------
const SOURCE_TAG = {
  gmail: ["blue", icon.mail, "Gmail order"], receipt: ["green", icon.receipt, "Receipt photo"],
  manual: ["grey", icon.plus, "Added"], csv: ["grey", icon.plus, "Imported"],
};
function sourceTag(p) {
  const [c, ic, l] = p.barcode ? ["purple", icon.barcode, "Barcode"] : SOURCE_TAG[p.source] || ["grey", icon.plus, "Added"];
  return `<span class="tag ${c}">${ic}${l}</span>`;
}
const activeRecalls = (p) => (p.recalls || []).filter((r) => r.feedback !== "not_my_batch");

async function viewProducts() {
  loading();
  let items;
  try {
    items = await api(userPath("/purchases"));
  } catch (e) {
    view.innerHTML = header("<h1>My Products</h1>") + errorBox(e, true);
    return;
  }
  const active = items.filter((p) => p.status !== "finished");
  const recalled = active.filter((p) => activeRecalls(p).length);
  const counts = { all: active.length, recalled: recalled.length, ok: active.length - recalled.length, finished: items.length - active.length };
  const f = state.productFilter;
  const shown = f === "finished" ? items.filter((p) => p.status === "finished") : f === "recalled" ? recalled : f === "ok" ? active.filter((p) => !activeRecalls(p).length) : active;
  const groups = [];
  for (const p of shown) {
    const key = `${p.store || "Added"}|${p.purchase_date || ""}`;
    let g = groups.find((x) => x.key === key);
    if (!g) groups.push((g = { key, store: p.store || "Added", date: p.purchase_date, first: p, items: [] }));
    g.items.push(p);
  }
  view.innerHTML = `
    ${header(`<h1>My Products</h1>`)}
    <div class="grid4">
      <button class="tile" data-imp="gmail">${icon.mail}Gmail</button>
      <button class="tile" data-imp="receipt">${icon.receipt}Receipt</button>
      <a class="tile" href="#/scan">${icon.barcode}Barcode</a>
      <button class="tile" data-imp="add">${icon.plus}Add</button>
    </div>
    <div class="chips">
      ${[["all", "All"], ["recalled", "Recalled"], ["ok", "OK"], ["finished", "Finished"]].filter(([k]) => k !== "finished" || counts.finished)
        .map(([k, l]) => `<button class="chip ${k === "recalled" ? "red" : ""} ${f === k ? "on" : ""}" data-filter="${k}">${l} ${counts[k]}</button>`).join("")}
    </div>
    ${groups.map((g) => `
      <div class="group-h"><span>${esc(g.store)}${g.date ? ` · ${esc(fmtDate(g.date))}` : ""}</span>${sourceTag(g.first)}</div>
      <div class="card list">${g.items.map((p) => {
        const rc = activeRecalls(p);
        const dot = p.status === "finished" ? "grey" : rc.length ? "red" : "";
        const sub = p.status === "finished" ? "Finished · not watched" : rc.length ? (rc.length === 1 ? `Matches recall ${esc(rc[0].recall_id)}` : `Matches ${rc.length} recalls`) + (p.batch_no ? "" : " · add batch no.")
          : `${p.batch_no ? `Batch ${esc(p.batch_no)} · ` : ""}No recall`;
        return `<button class="item" data-pid="${esc(p.purchase_id)}"><span class="dot ${dot}"></span><div style="flex:1;min-width:0">
          <div class="t">${esc(p.product_name)}</div><div class="s ${rc.length && p.status !== "finished" ? "bad" : ""}">${sub}</div></div>${icon.chev}</button>`;
      }).join("")}</div>`).join("") || `<div class="card empty">${icon.plus}<b>No products here yet</b><span>Import an order email, scan a receipt, or add one by hand.</span></div>`}`;
  view.querySelectorAll("[data-filter]").forEach((b) => (b.onclick = () => { state.productFilter = b.dataset.filter; viewProducts(); }));
  view.querySelectorAll("[data-pid]").forEach((b) => (b.onclick = () => productSheet(items.find((p) => p.purchase_id === b.dataset.pid))));
  view.querySelector('[data-imp="gmail"]').onclick = importEmails;
  view.querySelector('[data-imp="receipt"]').onclick = importReceipt;
  view.querySelector('[data-imp="add"]').onclick = addManual;
}

function importSummary(out, what) {
  const parts = [`Added ${out.purchases} product${out.purchases === 1 ? "" : "s"} from ${what}`];
  if (out.auto_published) parts.push(`${out.auto_published} match a recall`);
  if (out.not_tracked?.length) parts.push(`not tracked (loose items): ${out.not_tracked.slice(0, 3).join(", ")}`);
  toast(parts.join(" · "), 7000);
}
async function importEmails() {
  openSheet(`<h2>Import online orders</h2>
    <div class="small" style="line-height:1.5">Export your Gmail from <b>takeout.google.com</b> (choose Mail only), then pick the <b>.mbox</b> file here. You can also pick a single saved order email (<b>.eml</b>).</div>
    <div class="note">Works with Amazon, Flipkart, BigBasket, Blinkit, Zepto, Swiggy Instamart, JioMart, DMart Ready and more. Promotional emails are skipped.</div>
    <button class="btn primary block" id="pick">${icon.mail}Choose file</button>`, (s) => {
    s.querySelector("#pick").onclick = async () => {
      const file = await pickFile(".mbox,.eml,message/rfc822,application/mbox");
      if (!file) return;
      closeSheet();
      toast("Reading your order emails…", 60000);
      const fd = new FormData();
      fd.append("file", file);
      try { importSummary(await api(userPath("/order-emails"), { method: "POST", body: fd }), `${file.name}`); viewProducts(); refreshBadge(); }
      catch (e) { toast(e.message, 6000); }
    };
  });
}
async function importReceipt() {
  const file = await pickFile("image/*,.txt", "environment");
  if (!file) return;
  toast("Reading the receipt…", 60000);
  const fd = new FormData();
  fd.append("file", file);
  try {
    const out = await api(userPath("/receipts"), { method: "POST", body: fd });
    if (!out.purchases && !out.not_tracked?.length) toast("Couldn't find product lines on that receipt. Try a flatter, sharper photo.", 6000);
    else importSummary(out, out.receipt?.store || "the receipt");
    viewProducts(); refreshBadge();
  } catch (e) { toast(e.message, 6000); }
}
function addManual() {
  openSheet(`<h2>Add a product</h2>
    <p class="note" style="margin:0">For branded, packed products. Loose items like vegetables or sweets can't be matched to recalls.</p>
    <form class="stack" id="add-form">
      <label class="field">Product name<input class="input" name="product_name" required placeholder="e.g. Cow Ghee 500 ml"></label>
      <label class="field">Brand<input class="input" name="brand" required placeholder="e.g. Patanjali"></label>
      <label class="field">Batch number (optional)<input class="input" name="batch_no" placeholder="Printed near the expiry date"></label>
      <div class="grid2"><label class="field">Shop (optional)<input class="input" name="store"></label>
        <label class="field">Date bought<input class="input" name="purchase_date" type="date" value="${new Date().toISOString().slice(0, 10)}"></label></div>
      <button class="btn primary block">Add product</button></form>`, (s) => {
    s.querySelector("#add-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target));
      for (const k of ["batch_no", "store", "purchase_date"]) if (!f[k]) f[k] = null;
      try {
        const out = await api(userPath("/purchases"), { method: "POST", json: [f] });
        closeSheet();
        toast(out.auto_published ? "Added. It matches a recall — see Alerts." : "Added. We'll watch it for recalls.");
        viewProducts(); refreshBadge();
      } catch (err) { toast(err.message); }
    });
  });
}
function productSheet(p) {
  const rc = p.recalls || [];
  openSheet(`
    <div><h2>${esc(p.product_name)}</h2><div class="small muted">${esc([p.brand, p.store, fmtDate(p.purchase_date)].filter(Boolean).join(" · "))}</div></div>
    ${rc.length ? `<div class="card list">${rc.map((r) => `<div class="item" style="cursor:default"><span class="dot ${r.feedback === "not_my_batch" ? "grey" : "red"}"></span><div style="flex:1">
      <div class="t">Recall ${esc(r.recall_id)}: ${esc([r.brand, r.product].filter(Boolean).join(" · "))}</div>
      <div class="s">${r.batch ? `Recalled batch ${esc(r.batch)}` : "No batch listed"}${r.feedback === "not_my_batch" ? " · you said not your batch" : ""}</div></div></div>`).join("")}</div>
      <a class="btn danger block" href="#/alerts">Open the alert</a>` : '<div class="card pad small">No recall matches this product.</div>'}
    <form class="stack" id="batch"><label class="field">Batch number<input class="input" name="batch_no" value="${esc(p.batch_no || "")}" placeholder="Printed near the expiry date"></label>
      <button class="btn ghost block">Save batch number</button></form>
    <div class="grid2">
      <button class="btn ghost" id="finish">${p.status === "finished" ? "Watch again" : "Finished it"}</button>
      <button class="btn ghost" id="del" style="color:var(--red)">Delete</button></div>`, (s) => {
    const path = userPath(`/purchases/${encodeURIComponent(p.purchase_id)}`);
    s.querySelector("#batch").addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        const out = await api(path, { method: "PATCH", json: { batch_no: new FormData(e.target).get("batch_no").trim() || null } });
        closeSheet(); toast(out.alerts_sent ? "Saved and rechecked — a recall matches." : "Batch saved and rechecked."); viewProducts(); refreshBadge();
      } catch (err) { toast(err.message); }
    });
    s.querySelector("#finish").onclick = async () => {
      try { await api(path, { method: "PATCH", json: { status: p.status === "finished" ? "active" : "finished" } }); closeSheet(); viewProducts(); refreshBadge(); }
      catch (err) { toast(err.message); }
    };
    s.querySelector("#del").onclick = async () => {
      if (!confirm(`Delete ${p.product_name}?`)) return;
      try { await api(path, { method: "DELETE" }); closeSheet(); toast("Deleted."); viewProducts(); refreshBadge(); }
      catch (err) { toast(err.message); }
    };
  });
}

// ---------- alerts ----------
async function viewAlerts() {
  loading();
  let rows;
  try { rows = await api(userPath("/alerts")); } catch (e) { view.innerHTML = header("<h1>Alerts</h1>") + errorBox(e, true); return; }
  if (state.openAlert == null || !rows.some((r) => r.id === state.openAlert)) state.openAlert = rows.find((r) => !r.feedback)?.id ?? null;
  const card = (r) => {
    const answered = !!r.feedback;
    if (r.id !== state.openAlert) {
      return `<button class="card item" data-open="${r.id}"><span class="dot ${answered ? "grey" : "red"}"></span><div style="flex:1;min-width:0">
        <div class="t">${esc(r.purchase_product)}</div><div class="s">Recall ${esc(r.recall_id)}${answered ? (r.feedback === "has_batch" ? " · you have this batch" : " · not your batch") : r.recall_batch ? ` · check batch ${esc(r.recall_batch)}` : ""}</div></div>
        <span class="tiny muted">${esc(fmtDate(r.created_at))}</span></button>`;
    }
    return `<div class="card alert-card ${answered ? "answered" : ""}">
      <div class="band"><span>${esc(r.recall_source || "FSSAI")} RECALL ${esc(r.recall_id)}${r.recall_type ? ` · ${esc(r.recall_type.toUpperCase())}` : ""}</span><span>${esc(fmtDate(r.created_at))}</span></div>
      <div class="pad stack">
        <div style="font-weight:800;font-size:17px;line-height:1.3">${esc(r.purchase_product)} matches a recall</div>
        <div class="kv">
          <span>You bought</span><span>${esc([r.purchase_product, r.purchase_store, fmtDate(r.purchase_date)].filter(Boolean).join(" · "))}</span>
          <span>Recalled</span><span>${esc([r.recall_product, r.recall_brand].filter(Boolean).join(", "))}${r.recall_fbo ? ` (${esc(r.recall_fbo)})` : ""}</span>
          <span>Started</span><span>${esc(fmtDate(r.recall_date))}${r.recall_status ? ` · ${esc(r.recall_status)}` : ""}</span>
          <span>Reason</span><span>${esc(r.reason_text)}</span>
        </div>
        <div class="todo"><b>What to do</b><div style="margin-top:4px">${esc(r.batch_advice)} If it is the recalled batch, stop using it and return it to the shop or contact the company.</div></div>
        ${answered ? `<div class="tag ${r.feedback === "has_batch" ? "red" : "grey"}" style="align-self:flex-start">${r.feedback === "has_batch" ? "You have this batch" : "Not your batch"}</div>` : `
        <div class="grid2"><button class="btn danger" data-fb="has_batch" data-id="${r.id}">I have this batch</button>
          <button class="btn ghost" data-fb="not_my_batch" data-id="${r.id}">Not my batch</button></div>`}
        ${r.notice_url ? `<a class="small" href="${esc(r.notice_url)}" target="_blank" rel="noopener">View the ${esc(r.recall_source || "FSSAI")} notice</a>` : ""}
      </div></div>`;
  };
  view.innerHTML = `${header("<h1>Alerts</h1>")}
    ${rows.map(card).join("") || `<div class="card empty">${icon.check}<b>No alerts</b><span>You'll see an alert here if something you bought is recalled.</span></div>`}
    <p class="note">RecallIndia passes on official recall notices. A recall applies only to the batches named in the notice.</p>`;
  view.querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => { state.openAlert = Number(b.dataset.open); viewAlerts(); }));
  view.querySelectorAll("[data-fb]").forEach((b) => (b.onclick = async () => {
    try {
      await api(userPath(`/alerts/${b.dataset.id}/feedback`), { method: "POST", json: { answer: b.dataset.fb } });
      toast(b.dataset.fb === "has_batch" ? "Noted. Stop using it and return it to the shop." : "Thanks — we won't count this one.");
      state.openAlert = null; viewAlerts(); refreshBadge();
    } catch (e) { toast(e.message); }
  }));
}

// ---------- ingredient lookup ----------
const GS_TAG = { not_authorised: "red", being_withdrawn: "red", prohibited: "red", restricted: "amber", warning_label: "amber",
  permitted_some_categories: "green", permitted: "green", limit: "amber", no_rule_loaded: "grey", partial_rule_loaded: "grey" };

async function viewLookup(params) {
  const q = params.get("q") || "";
  view.innerHTML = `${header("<h1>Ingredient status</h1>", { back: "#/home" })}
    <form class="search" id="q" role="search" style="border:2px solid var(--accent)">${icon.search}
      <input name="q" value="${esc(q)}" placeholder="Name or E-number, e.g. 124 or titanium dioxide" aria-label="Ingredient or E-number" autocomplete="off"></form>
    <div id="res"></div>
    <div class="stack"><h2 style="font-size:15px">Allowed in India, stricter elsewhere</h2><div class="chips" id="div"></div></div>
    <p class="note">Regulatory status of an ingredient from each regulator's published rules. Not a statement about any product or brand.</p>`;
  view.querySelector("#q").addEventListener("submit", (e) => { e.preventDefault(); location.hash = `#/lookup?q=${encodeURIComponent(new FormData(e.target).get("q").trim())}`; });
  api("/global-status/divergent").then((list) => {
    view.querySelector("#div").innerHTML = list.map((d) => `<a class="chip" style="display:inline-flex;align-items:center;text-decoration:none;color:inherit" href="#/lookup?q=${encodeURIComponent(d.ins || d.name)}">${esc(d.name)}${d.ins ? ` · INS ${esc(d.ins)}` : ""}</a>`).join("");
  }).catch(() => {});
  if (!q) return;
  const res = view.querySelector("#res");
  res.innerHTML = '<div class="spinner"></div>';
  try {
    const out = await api(`/global-status?q=${encodeURIComponent(q)}`);
    res.innerHTML = out.map((s) => `<div class="card pad stack" style="gap:12px">
      <div><div style="font-weight:800;font-size:19px">${esc(s.name)}</div><div class="small muted">${s.ins ? `INS ${esc(s.ins)} · E${esc(s.ins)} · ` : ""}${esc(s.kind || "")}</div></div>
      <div class="todo" style="font-size:14px">${esc(s.summary)}</div>
      <div class="status-table">${s.jurisdictions.map((j) => `<div><b class="small">${esc(j.jurisdiction_name)}</b><div>
        <span class="tag ${GS_TAG[j.status] || "grey"}">${esc(j.status_label)}</span>${j.verified === false ? ' <span class="tiny muted">unverified</span>' : ""}
        ${j.note ? `<div class="tiny muted" style="margin-top:4px;line-height:1.4">${esc(j.note)}</div>` : ""}
        ${j.source_url ? `<a class="tiny" href="${esc(j.source_url)}" target="_blank" rel="noopener">${esc(j.source || "source")}</a>` : ""}</div></div>`).join("")}</div>
      </div>`).join("") || `<div class="card empty">No ingredient found for “${esc(q)}”. Try the E-number, like 124.</div>`;
  } catch (e) { res.innerHTML = errorBox(e); }
}

// ---------- start ----------
try {
  state.user = JSON.parse(localStorage.getItem("ri_user") || "null");
  state.token = localStorage.getItem("ri_token");
} catch { state.user = null; }
window.addEventListener("hashchange", route);
route();
if (state.user) refreshBadge();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/app/sw.js", { scope: "/app/" }).catch(() => {});
