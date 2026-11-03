"use strict";
const $ = id => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

let data = [], limits = {}, freeBytes = null, totalBytes = null, autoInfo = null;
let current = null, sortMode = "title", query = "", rerecord = false;
const selected = new Set();   // CmdURLs, always within the current show

const key = s => s.SeriesID || s.Title;
const fmtSize = b => b >= 1e12 ? (b/1e12).toFixed(2)+" TB" : b >= 1e9 ? (b/1e9).toFixed(1)+" GB" : (b/1e6).toFixed(0)+" MB";
const fmtDate = t => t ? new Date(t*1000).toLocaleString([], {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}) : "";
const newest = s => Math.max(0, ...s.Episodes.map(e => e.StartTime || 0));
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const isMobile = () => window.matchMedia("(max-width: 860px)").matches;
const status = t => $("status").textContent = t || "";
const currentShow = () => data.find(x => key(x) === current) || null;
const epLabel = e => [e.EpisodeTitle || e.Title || "(untitled)", e.EpisodeNumber, fmtDate(e.StartTime)].filter(Boolean).join(" · ");

/* ---------- server calls ---------- */
class ApiError extends Error {
  constructor(message, status, body) { super(message); this.status = status; this.body = body; }
}

// fetch wrapper: JSON in/out, and every failure (network, HTTP error, non-JSON reply) becomes an ApiError
async function api(path, {json, ...opts} = {}) {
  if (json !== undefined) {
    opts.method = opts.method || "POST";
    opts.headers = {...(opts.headers || {}), "Content-Type": "application/json"};
    opts.body = JSON.stringify(json);
  }
  let r;
  try { r = await fetch(path, opts); }
  catch (e) { throw new ApiError("Couldn't reach the server. Check your connection and try again.", 0); }
  let body = null;
  try { body = await r.json(); } catch (e) { body = null; }
  if (!r.ok) {
    const msg = (body && body.error) ||
      (r.status === 401 ? "Login required. Reload the page to sign in." : `The server returned an error (HTTP ${r.status}).`);
    throw new ApiError(msg, r.status, body);
  }
  if (body === null) throw new ApiError("The server sent an unexpected response.", r.status);
  return body;
}

/* ---------- confirmation dialog ---------- */
function confirmDialog({title, body, note, okLabel}) {
  const dlg = $("dlg");
  $("dlg-title").textContent = title;
  $("dlg-body").replaceChildren(...(body ? [body] : []));
  $("dlg-note").textContent = note || "";
  $("dlg-ok").textContent = okLabel;
  dlg.returnValue = "";
  return new Promise(resolve => {
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), {once: true});
    dlg.showModal();
    $("dlg-cancel").focus();   // safe default: Enter cancels
  });
}

function tickClock() { $("clock").textContent = new Date().toLocaleTimeString([], {hour: "numeric", minute: "2-digit"}); }
tickClock(); setInterval(tickClock, 15000);

/* ---------- list (right) ---------- */
function visibleShows() {
  const q = query.trim().toLowerCase();
  const rows = data.filter(s => !q || (s.Title || "").toLowerCase().includes(q));
  rows.sort((a, b) =>
    sortMode === "count" ? b.Episodes.length - a.Episodes.length :
    sortMode === "date" ? newest(b) - newest(a) :
    (a.Title || "").localeCompare(b.Title || ""));
  return rows;
}

// switching shows drops the selection, so "Delete selected" only ever covers what is on screen
function setCurrent(k) {
  if (k !== current) selected.clear();
  current = k;
}

function focusRow(k, part) {
  for (const row of $("list").querySelectorAll(".row")) {
    if (row.dataset.key === k) { (part === "cell" ? row.querySelector(".cell") : row).focus(); return; }
  }
}

function fillCell(cell, s) {
  cell.replaceChildren();
  const n = limits[key(s)];
  cell.setAttribute("aria-label", `${s.Title || "Untitled"}: ${n ? "keep " + n : "keep all episodes"}. Press Enter to change.`);
  if (!n) { cell.textContent = "All Episodes"; return; }
  cell.append(document.createTextNode(`Keep ${n}`));
  const extra = Math.max(0, s.Episodes.length - n);
  if (extra) cell.append(el("small", null, `${extra} over limit`));
}

function editCell(cell, s) {
  const input = el("input"); input.type = "number"; input.min = "1"; input.placeholder = "All";
  input.setAttribute("aria-label", `How many recordings of ${s.Title || "this show"} to keep (blank keeps all)`);
  input.value = limits[key(s)] || "";
  cell.replaceChildren(input); input.focus(); input.select();
  let done = false;
  const finish = async (save, refocus) => {
    if (done) return; done = true;
    if (save) await saveLimit(s, input.value === "" ? null : parseInt(input.value, 10));
    renderList(); renderPanel();
    if (refocus) focusRow(key(s), "cell");
  };
  input.onclick = e => e.stopPropagation();
  input.onkeydown = e => {
    e.stopPropagation();
    if (e.key === "Enter") { e.preventDefault(); finish(true, true); }
    if (e.key === "Escape") { e.preventDefault(); finish(false, true); }
  };
  input.onblur = () => finish(true, false);
}

async function saveLimit(s, keep) {
  try {
    const j = await api("api/limits", {json: {series_id: key(s), keep}});
    limits = j.limits; status("");
  } catch (e) { status("Error: " + e.message); }
}

function openShow(s) {
  setCurrent(key(s)); renderList(); renderPanel();
  if (isMobile()) { document.body.classList.add("sheet-open"); $("mclose").focus(); }
  else focusRow(key(s));
}

function renderList() {
  const list = $("list"); list.replaceChildren();
  const rows = visibleShows();
  if (!rows.length) list.append(el("div", "empty", data.length ? "No shows match your search." : "No shows yet."));
  for (const s of rows) {
    const sel = key(s) === current;
    const row = el("div", "row" + (sel ? " sel" : ""));
    row.dataset.key = key(s);
    row.tabIndex = 0; row.setAttribute("role", "button");
    if (sel) row.setAttribute("aria-current", "true");
    const cnt = el("div", "cnt", s.Error ? "!" : s.Episodes.length);
    if (s.Error) cnt.title = s.Error;
    const ttl = el("div", "ttl", s.Title || "(untitled)");
    row.setAttribute("aria-label", `${s.Title || "Untitled"}, ${s.Error ? "couldn't load" : plural(s.Episodes.length, "recording")}`);
    const cell = el("div", "cell"); fillCell(cell, s);
    cell.tabIndex = 0; cell.setAttribute("role", "button");
    cell.title = "Click to set how many recordings to keep";
    const edit = e => { e.stopPropagation(); setCurrent(key(s)); editCell(cell, s); renderPanel(); };
    cell.onclick = edit;
    cell.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); edit(e); } };
    row.append(cnt, ttl, cell);
    row.onclick = () => openShow(s);
    row.onkeydown = e => {
      if (e.target !== row) return;
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openShow(s); }
    };
    list.append(row);
  }
}

/* ---------- panel (left) ---------- */
function globalButtons() {
  const box = el("div", "gbtns");
  const refresh = el("button", "gbtn", "Refresh"); refresh.onclick = () => load(true);
  const prune = el("button", "gbtn", "Apply keep limits"); prune.onclick = () => doPrune();
  box.append(refresh, prune);
  return box;
}

function renderPanel() {
  const p = $("panel"); p.replaceChildren();
  const s = currentShow();
  if (!s) { p.append(el("div", "empty", data.length ? "Select a show" : "No shows yet.")); return; }

  if (s.ImageURL) {
    const img = el("img", "poster"); img.src = s.ImageURL; img.alt = ""; img.onerror = () => { img.hidden = true; };
    p.append(img);
  }
  p.append(el("h2", null, s.Title || "(untitled)"));

  const eps = [...s.Episodes].sort((a, b) => (b.StartTime || 0) - (a.StartTime || 0));
  const syn = eps.find(e => e.Synopsis);
  p.append(el("p", "syn", s.Error ? `Couldn't load this show's recordings (${s.Error}). Try Refresh.` :
    syn ? syn.Synopsis : (eps.length ? "" : "Scheduled to record. No recordings yet.")));

  const hd = el("div", "hd"); hd.append(el("span", null, plural(eps.length, "recording")));
  if (eps.length) {
    const all = el("button", "link");
    all.onclick = () => {
      const allSel = eps.every(e => selected.has(e.CmdURL));
      eps.forEach(e => allSel ? selected.delete(e.CmdURL) : selected.add(e.CmdURL));
      renderPanel(); $("panel").querySelector(".hd .link").focus();
    };
    hd.append(all);
  }
  p.append(hd);

  if (eps.length) {
    const box = el("div", "eps");
    for (const e of eps) {
      const row = el("label", "ep");
      const cb = el("input"); cb.type = "checkbox"; cb.checked = selected.has(e.CmdURL);
      cb.onchange = () => { cb.checked ? selected.add(e.CmdURL) : selected.delete(e.CmdURL); updateDeleteButton(); updateSelectAll(eps); };
      const t = el("div", "t"); t.append(el("div", null, e.EpisodeTitle || e.Title || ""));
      const dur = (e.RecordStartTime && e.RecordEndTime) ? Math.round((e.RecordEndTime - e.RecordStartTime) / 60) + " min" : "";
      t.append(el("div", "sub", [e.EpisodeNumber, fmtDate(e.StartTime), dur].filter(Boolean).join(" · ")));
      row.append(cb, t); box.append(row);
    }
    p.append(box);
  }

  const actions = el("div", "actions");
  const lbl = el("label"); const rr = el("input"); rr.type = "checkbox"; rr.checked = rerecord;
  rr.onchange = () => rerecord = rr.checked;
  lbl.append(rr, document.createTextNode("Re-record deleted episodes"));
  const del = el("button", "btn danger"); del.id = "del-btn"; del.onclick = doDelete;
  actions.append(lbl, del); p.append(actions);
  updateDeleteButton(); updateSelectAll(eps);
}

function updateDeleteButton() {
  const del = $("del-btn"); if (!del) return;
  del.textContent = `Delete selected (${selected.size})`;
  del.disabled = selected.size === 0;
}

function updateSelectAll(eps) {
  const link = $("panel").querySelector(".hd .link");
  if (link) link.textContent = eps.length && eps.every(e => selected.has(e.CmdURL)) ? "Deselect all" : "Select all";
}

/* ---------- info line ---------- */
function renderInfo() {
  const box = $("info"); box.replaceChildren();
  const parts = [plural(data.length, "show"), plural(data.reduce((a, s) => a + s.Episodes.length, 0), "recording")];
  if (totalBytes != null && freeBytes != null) parts.push(`${fmtSize(totalBytes - freeBytes)} used`, `${fmtSize(freeBytes)} free`);
  box.append(el("div", null, parts.join(" · ")));
  box.append(el("div", "mhint", "Tap a show to see its recordings. Tap its right-hand cell to set how many to keep."));
  const a = autoInfo, line = el("div", "auto");
  if (a && a.enabled) {
    line.append(el("span", "ok", "✓ Automatic cleanup on"));
    line.append(document.createTextNode(
      (a.next ? ` · next run ${new Date(a.next).toLocaleString()}` : ` · daily at ${a.time}`) +
      (a.last ? ` · last run (${new Date(a.last.when).toLocaleString()}): ${a.last.summary}` : "")));
    box.append(line);
  } else if (a) {
    line.append(document.createTextNode("Automatic cleanup is off. "));
    const go = el("button", "link", "Turn it on in Settings"); go.onclick = () => showView("settings");
    line.append(go);
    box.append(line);
  }
}

/* ---------- actions ---------- */
// returns true if the list loaded; on failure the error is shown and the old list stays on screen
async function load(fresh = true) {
  status("Loading…");
  let j, lj;
  try {
    [j, lj] = await Promise.all([api(fresh ? "api/recordings?fresh=1" : "api/recordings"), api("api/limits")]);
  } catch (e) { status("Error: " + e.message); return false; }
  limits = lj.limits || {}; autoInfo = lj.auto || null; data = Array.isArray(j) ? j : [];
  selected.clear();
  try { const sj = await api("api/status"); freeBytes = sj.free; totalBytes = sj.total; }
  catch (e) { freeBytes = totalBytes = null; }
  if (!data.some(s => key(s) === current)) current = visibleShows().length ? key(visibleShows()[0]) : null;
  const bad = data.filter(s => s.Error).length;
  status(bad ? `${bad} show${bad === 1 ? "" : "s"} couldn't be loaded. Try Refresh.` : "");
  renderInfo(); renderList(); renderPanel();
  return true;
}

async function doDelete() {
  const s = currentShow();
  const eps = s ? s.Episodes.filter(e => selected.has(e.CmdURL)) : [];
  if (!eps.length) return;
  const n = eps.length;
  const ul = el("ul");
  eps.sort((a, b) => (b.StartTime || 0) - (a.StartTime || 0)).forEach(e => ul.append(el("li", null, epLabel(e))));
  const ok = await confirmDialog({
    title: `Delete ${plural(n, "recording")} of ${s.Title || "this show"}?`,
    body: ul,
    note: rerecord ? "The DVR will record these episodes again if they air. This can't be undone."
                   : "The DVR will treat these episodes as already recorded. This can't be undone.",
    okLabel: `Delete ${n}`,
  });
  if (!ok) return;
  status("Deleting…");
  let res;
  try { res = await api("api/delete", {json: {cmd_urls: eps.map(e => e.CmdURL), rerecord}}); }
  catch (e) { status("Error: " + e.message); return; }
  const failed = res.filter(x => !x.ok).length;
  const loaded = await load();
  status((failed ? `${failed} of ${n} failed.` : `Deleted ${n}.`) + (loaded ? "" : " Couldn't refresh the list; press Refresh."));
}

function planList(plan) {
  const ul = el("ul");
  for (const g of plan) {
    ul.append(el("li", "g", `${g.Title}: delete ${g.Episodes.length} oldest, keeping ${g.Keep}`));
    for (const e of g.Episodes) ul.append(el("li", "sub", epLabel(e)));
  }
  return ul;
}

async function doPrune(plan = null, notice = "") {
  if (!plan) {
    status("Checking limits…");
    try { plan = await api("api/prune/preview"); }
    catch (e) { status("Error: " + e.message); return; }
  }
  if (!plan.length) {
    status(notice ? "The recordings changed, and every show is now within its limit." : "Nothing to prune: every show is within its limit.");
    return;
  }
  status("");
  const urls = plan.flatMap(g => g.Episodes.map(e => e.CmdURL)).filter(Boolean);
  const ok = await confirmDialog({
    title: `Delete ${plural(urls.length, "recording")} to meet your keep limits?`,
    body: planList(plan),
    note: (notice ? notice + " " : "") + "Recordings are deleted without re-record. This can't be undone.",
    okLabel: `Delete ${urls.length}`,
  });
  if (!ok) return;
  status("Pruning…");
  let pj;
  try { pj = await api("api/prune", {json: {cmd_urls: urls}}); }
  catch (e) {
    if (e.status === 409 && e.body && Array.isArray(e.body.plan))
      return doPrune(e.body.plan, "The recordings changed since you opened this, so this is the updated list.");
    status("Error: " + e.message); return;
  }
  const failed = pj.results.filter(x => !x.ok).length;
  const loaded = await load();
  status((failed ? `${failed} of ${pj.results.length} failed.` : `Pruned ${pj.results.length}.`) +
         (loaded ? "" : " Couldn't refresh the list; press Refresh."));
}

$("q").oninput = e => { query = e.target.value; renderList(); };
$("tabs").onclick = e => {
  const b = e.target.closest("button"); if (!b) return;
  sortMode = b.dataset.sort;
  document.querySelectorAll("#tabs button").forEach(x => {
    x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b ? "true" : "false");
  });
  renderList();
};
/* ---------- settings view ---------- */
function showView(v) {
  const settings = v === "settings";
  $("main-view").hidden = settings;
  $("settings-view").hidden = !settings;
  $("tab-manage").classList.toggle("on", !settings);
  $("tab-settings").classList.toggle("on", settings);
  $("tab-manage").setAttribute("aria-pressed", String(!settings));
  $("tab-settings").setAttribute("aria-pressed", String(settings));
  if (settings) loadSettings();
}
const setMsg = (t, cls) => { const m = $("hostMsg"); m.textContent = t || ""; m.className = cls || ""; };
const deviceLine = d => `${d.name || "DVR"}${d.model ? " (" + d.model + ")" : ""}${d.free != null ? " · " + fmtSize(d.free) + " free" : ""}`;

async function loadTimezones() {
  const dl = $("tzlist");
  if (dl.children.length) return;   // loaded once per page; the browser also caches the response
  try {
    for (const z of await api("api/timezones")) { const o = document.createElement("option"); o.value = z; dl.append(o); }
  } catch (e) { /* the field still accepts typed names */ }
}

async function loadSettings() {
  let j;
  try { j = await api("api/settings"); }
  catch (e) { setMsg("✗ Couldn't load settings: " + e.message, "bad"); return; }
  $("host").value = j.dvr_host || "";
  $("hostSrc").textContent =
    j.source === "ui" ? "Saved from this page." :
    j.source === "env" ? `Using the server default (${j.env_dvr_host}).` :
    "No address set. The app will try Silicondust cloud discovery.";
  setMsg("");
  const sc = j.schedule;
  $("auto-on").checked = sc.enabled; $("auto-time").value = sc.time; $("auto-tz").value = sc.tz; $("auto-max").value = sc.max;
  setAutoMsg("");
  loadTimezones();
}

async function submitHost(mode) {   // "test" | "save" | "reset"
  setMsg(mode === "test" ? "Testing…" : "Applying…");
  let j;
  try {
    j = await api(mode === "test" ? "api/settings/test" : "api/settings",
                  {json: {dvr_host: mode === "reset" ? "" : $("host").value}});
  } catch (e) { setMsg("✗ " + e.message, "bad"); return; }
  if (mode === "test") { setMsg("✓ Connected: " + deviceLine(j.device), "good"); return; }
  await loadSettings();
  setMsg(mode === "reset" ? "✓ Back to the server default." : "✓ Saved and applied: " + deviceLine(j.device), "good");
  load();
}
const setAutoMsg = (t, cls) => { const m = $("autoMsg"); m.textContent = t || ""; m.className = cls || ""; };
async function saveSchedule() {
  setAutoMsg("Saving…");
  let j;
  try {
    j = await api("api/settings/schedule", {json: {
      enabled: $("auto-on").checked, time: $("auto-time").value,
      tz: $("auto-tz").value.trim(), max: parseInt($("auto-max").value, 10)}});
  } catch (e) { setAutoMsg("✗ " + e.message, "bad"); return; }
  setAutoMsg(j.schedule.enabled ? "✓ Saved. Next run: " + new Date(j.next).toLocaleString() : "✓ Saved. Automatic cleanup is off.", "good");
  load();
}
$("auto-save").onclick = saveSchedule;
$("tz-browser").onclick = () => { $("auto-tz").value = Intl.DateTimeFormat().resolvedOptions().timeZone; };
$("tab-manage").onclick = () => showView("main");
$("tab-settings").onclick = () => showView("settings");
$("host-test").onclick = () => submitHost("test");
$("host-save").onclick = () => submitHost("save");
$("host-reset").onclick = () => submitHost("reset");
$("host").onkeydown = e => { if (e.key === "Enter") submitHost("save"); };

$("sidebtns").append(globalButtons());
$("topbtns").append(globalButtons());
$("mclose").onclick = () => { document.body.classList.remove("sheet-open"); if (current) focusRow(current); };
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && document.body.classList.contains("sheet-open") && !$("dlg").open) $("mclose").click();
});
load(false);
