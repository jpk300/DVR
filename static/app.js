"use strict";
const $ = id => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const svg = html => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; };
const PENCIL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>';

let data = [], limits = {}, freeBytes = null, totalBytes = null, autoInfo = null;
let current = null, sortMode = "title", query = "", rerecord = false, loading = false;
const selected = new Set();   // CmdURLs, always within the current show

const key = s => s.SeriesID || s.Title;
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const isMobile = () => window.matchMedia("(max-width: 860px)").matches;
const status = t => $("status").textContent = t || "";
const currentShow = () => data.find(x => key(x) === current) || null;

/* ---------- formatting ---------- */
const fmtSize = b => b >= 1e12 ? (b/1e12).toFixed(2)+" TB" : b >= 1e9 ? (b/1e9).toFixed(1)+" GB" : (b/1e6).toFixed(0)+" MB";
const fmtTime = d => d.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
function fmtWhen(d) {
  // Today / Yesterday / weekday for the last week, then the date, with the year when it isn't this year
  if (!(d instanceof Date) || isNaN(d)) return "";
  const now = new Date(), day = 864e5;
  const midnight = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((midnight(now) - midnight(d)) / day);
  if (days === 0) return `Today, ${fmtTime(d)}`;
  if (days === 1) return `Yesterday, ${fmtTime(d)}`;
  if (days > 1 && days < 7) return `${d.toLocaleDateString([], {weekday: "short"})}, ${fmtTime(d)}`;
  const opts = {month: "short", day: "numeric"};
  if (d.getFullYear() !== now.getFullYear()) opts.year = "numeric";
  return `${d.toLocaleDateString([], opts)}, ${fmtTime(d)}`;
}
const fmtEpoch = t => t ? fmtWhen(new Date(t * 1000)) : "";
const durSec = e => (e.RecordStartTime && e.RecordEndTime) ? Math.max(0, e.RecordEndTime - e.RecordStartTime) : 0;
const fmtHours = sec => { const h = sec / 3600; return h >= 10 ? `${Math.round(h)} h` : `${h.toFixed(1)} h`; };
const showHours = s => s.Episodes.reduce((a, e) => a + durSec(e), 0);
const newest = s => Math.max(0, ...s.Episodes.map(e => e.StartTime || 0));
const epLabel = e => [e.EpisodeNumber, e.EpisodeTitle || e.Title || "(untitled)", fmtEpoch(e.StartTime)].filter(Boolean).join(" · ");

/* ---------- keep rules (same logic as dvr/pruning.py, used for counts and previews) ---------- */
const ruleOf = s => limits[key(s)] || null;
function ruleText(rule, short) {
  if (!rule) return short ? "All" : "Keep everything";
  if (rule.delete_watched) return rule.keep ? (short ? `${rule.keep} unwatched` : `Delete watched, keep newest ${rule.keep} unwatched`)
                                            : (short ? "Unwatched" : "Delete watched, keep all unwatched");
  return short ? `${rule.keep}` : `Keep the newest ${rule.keep}`;
}
function toDelete(episodes, rule) {
  if (!rule) return [];
  let eps = [...episodes].sort((a, b) => (b.StartTime || 0) - (a.StartTime || 0));
  const out = [];
  if (rule.delete_watched) {
    eps.filter(e => e.Watched === true).forEach(e => out.push(e));
    eps = eps.filter(e => e.Watched !== true);
  }
  if (rule.keep) eps.slice(rule.keep).forEach(e => out.push(e));
  return out;
}
const reportsWatched = s => s.Episodes.some(e => e.Watched === true || e.Watched === false);

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

// run an async action with a spinner on its button (and the button disabled meanwhile)
async function busy(btn, fn) {
  if (btn) { btn.disabled = true; btn.classList.add("busy"); }
  try { return await fn(); }
  finally { if (btn && btn.isConnected) { btn.disabled = false; btn.classList.remove("busy"); } }
}

/* ---------- toasts ---------- */
function toast(msg, kind = "ok") {
  const t = el("div", "toast " + kind);
  t.setAttribute("role", kind === "err" ? "alert" : "status");
  t.append(el("span", null, msg));
  const x = el("button", "x", "×"); x.type = "button"; x.setAttribute("aria-label", "Dismiss");
  const close = () => t.remove();
  x.onclick = close;
  t.append(x);
  $("toasts").append(t);
  setTimeout(close, kind === "err" ? 9000 : 4500);
}

/* ---------- confirmation dialog ---------- */
function confirmDialog({title, body, note, okLabel}) {
  const dlg = $("dlg");
  $("dlg-title").textContent = title;
  $("dlg-body").replaceChildren(...(body ? [body] : []));
  $("dlg-note").textContent = note || "";
  $("dlg-ok").textContent = okLabel;
  dlg.returnValue = "";
  // Resolve on the form's submit (fires at once) rather than only on "close", which a browser
  // may deliver late; "close" still covers Esc. Whichever comes first wins.
  return new Promise(resolve => {
    const form = dlg.querySelector("form");
    let done = false;
    const finish = ok => {
      if (done) return; done = true;
      form.removeEventListener("submit", onSubmit); dlg.removeEventListener("close", onClose);
      resolve(ok);
    };
    const onSubmit = e => finish(!!e.submitter && e.submitter.value === "ok");
    const onClose = () => finish(dlg.returnValue === "ok");
    form.addEventListener("submit", onSubmit);
    dlg.addEventListener("close", onClose);
    dlg.showModal();
    $("dlg-cancel").focus();   // safe default: Enter cancels
  });
}

/* ---------- keep-rule dialog ---------- */
let ruleShow = null;
function openRuleDialog(s) {
  ruleShow = s;
  const rule = ruleOf(s);
  const form = $("rule-form");
  $("rule-title").textContent = `Keep rule for ${s.Title || "this show"}`;
  form.mode.value = !rule ? "all" : rule.delete_watched ? "watched" : "newest";
  $("rule-n").value = rule && !rule.delete_watched ? rule.keep : "";
  $("rule-nw").value = rule && rule.delete_watched && rule.keep ? rule.keep : "";
  const noWatched = s.Episodes.length > 0 && !reportsWatched(s);
  $("watched-note").hidden = !noWatched;
  $("watched-note").textContent = noWatched ? "Your DVR hasn't reported watched status for these recordings, so this rule would only delete by count." : "";
  $("rule-err").hidden = true;
  updateRuleEffect();
  const dlg = $("rule-dlg");
  dlg.returnValue = "";
  dlg.showModal();
  const focusTarget = form.mode.value === "newest" ? $("rule-n") : form.querySelector(`input[name=mode][value=${form.mode.value}]`);
  focusTarget.focus();
}

// the rule the dialog currently describes, or {error}
function ruleFromDialog() {
  const mode = $("rule-form").mode.value;
  if (mode === "all") return {rule: null};
  if (mode === "newest") {
    const n = parseInt($("rule-n").value, 10);
    if (!(n >= 1)) return {error: "Enter how many recordings to keep (1 or more)."};
    return {rule: {keep: n, delete_watched: false}};
  }
  const raw = $("rule-nw").value.trim();
  const n = raw === "" ? null : parseInt(raw, 10);
  if (raw !== "" && !(n >= 1)) return {error: "Enter how many unwatched recordings to keep, or leave it blank to keep them all."};
  return {rule: {keep: n, delete_watched: true}};
}

function updateRuleEffect() {
  const s = ruleShow; if (!s) return;
  const {rule, error} = ruleFromDialog();
  const eff = $("rule-effect");
  if (error) { eff.textContent = ""; return; }
  if (!s.Episodes.length) { eff.textContent = "No recordings yet. The rule applies once this show has some."; return; }
  const n = toDelete(s.Episodes, rule).length;
  eff.textContent = n ? `With this rule, ${plural(n, "recording")} of ${s.Episodes.length} would be deleted the next time keep rules are applied.`
                      : `With this rule, nothing would be deleted right now (${plural(s.Episodes.length, "recording")}).`;
}

function initRuleDialog() {
  const form = $("rule-form");
  form.addEventListener("change", updateRuleEffect);
  form.addEventListener("input", updateRuleEffect);
  // typing a number selects its option
  $("rule-n").addEventListener("focus", () => { form.mode.value = "newest"; updateRuleEffect(); });
  $("rule-nw").addEventListener("focus", () => { form.mode.value = "watched"; updateRuleEffect(); });
  // Enter saves (the first button in the form is Cancel, which a plain Enter would press)
  form.addEventListener("keydown", e => {
    if (e.key === "Enter" && e.target.tagName === "INPUT") { e.preventDefault(); $("rule-ok").click(); }
  });
  // save on submit (fires at once; "close" may arrive late), and forget the show on any close
  form.addEventListener("submit", e => {
    const s = ruleShow;
    if (!e.submitter || e.submitter.value !== "ok" || !s) { ruleShow = null; return; }
    const {rule, error} = ruleFromDialog();
    if (error) { e.preventDefault(); $("rule-err").textContent = error; $("rule-err").hidden = false; return; }
    ruleShow = null;
    saveRule(s, rule);
  });
  $("rule-dlg").addEventListener("close", () => { ruleShow = null; });
}

async function saveRule(s, rule) {
  try {
    const j = await api("api/limits", {json: {series_id: key(s), keep: rule ? rule.keep : null,
                                              delete_watched: rule ? rule.delete_watched : false}});
    limits = j.limits || {};
    const n = toDelete(s.Episodes, ruleOf(s)).length;
    toast(`${s.Title}: ${ruleText(ruleOf(s))}.` + (n ? ` ${plural(n, "recording")} can be deleted now.` : ""), "info");
  } catch (e) { toast("Couldn't save the rule: " + e.message, "err"); }
  renderList(); renderPanel();
  focusRow(key(s), "pill");
}

/* ---------- list (right) ---------- */
function visibleShows() {
  const q = query.trim().toLowerCase();
  const rows = data.filter(s => !q || (s.Title || "").toLowerCase().includes(q));
  rows.sort((a, b) =>
    sortMode === "count" ? b.Episodes.length - a.Episodes.length :
    sortMode === "hours" ? showHours(b) - showHours(a) :
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
    if (row.dataset.key === k) { row.querySelector(part === "pill" ? ".keep-pill" : ".row-main").focus(); return; }
  }
}

function keepPill(s) {
  const rule = ruleOf(s);
  const b = el("button", "keep-pill" + (rule ? " set" : ""));
  b.type = "button";
  b.append(el("span", null, `Keep: ${ruleText(rule, true)}`), svg(PENCIL));
  b.setAttribute("aria-label", `${s.Title || "Untitled"}: ${ruleText(rule)}. Change keep rule`);
  b.title = "Change what to keep";
  b.onclick = e => { e.stopPropagation(); setCurrent(key(s)); renderList(); renderPanel(); openRuleDialog(s); };
  return b;
}

function renderList() {
  const list = $("list"); list.replaceChildren();
  if (loading && !data.length) {
    for (let i = 0; i < 6; i++) list.append(el("div", "skel"));
    return;
  }
  const rows = visibleShows();
  if (!rows.length) list.append(el("div", "empty", data.length ? "No shows match your search." : "No shows yet."));
  for (const s of rows) {
    const sel = key(s) === current;
    const row = el("div", "row" + (sel ? " sel" : ""));
    row.dataset.key = key(s);
    const main = el("button", "row-main"); main.type = "button";
    if (sel) main.setAttribute("aria-current", "true");
    const cntText = s.Error ? "!" : s.Scheduled && !s.Episodes.length ? "–" :
                    sortMode === "hours" ? fmtHours(showHours(s)) : s.Episodes.length;
    const cnt = el("span", "cnt", cntText);
    const ttl = el("span", "ttl");
    ttl.append(el("span", "name", s.Title || "(untitled)"));
    if (s.Scheduled && !s.Episodes.length) ttl.append(el("span", "tag", "Scheduled"));
    if (s.Error) { const t = el("span", "tag err", "Couldn't load"); t.title = s.Error; ttl.append(t); }
    main.append(cnt, ttl);
    main.setAttribute("aria-label", `${s.Title || "Untitled"}, ${s.Error ? "couldn't load" : s.Scheduled && !s.Episodes.length ? "scheduled, no recordings yet" : plural(s.Episodes.length, "recording")}`);
    main.onclick = e => { e.stopPropagation(); openShow(s); };
    const cell = el("div", "cell");
    cell.append(keepPill(s));
    const n = toDelete(s.Episodes, ruleOf(s)).length;
    if (n) cell.append(el("small", null, `${n} to delete`));
    row.append(main, cell);
    row.onclick = () => openShow(s);   // clicking the empty part of the rule cell opens the show too
    list.append(row);
  }
}

function openShow(s) {
  setCurrent(key(s)); renderList(); renderPanel();
  if (isMobile()) { document.body.classList.add("sheet-open"); $("mclose").focus(); }
  else focusRow(key(s));
}

/* ---------- panel (left) ---------- */
function renderPanel() {
  const p = $("panel"); p.replaceChildren();
  const s = currentShow();
  if (!s) { p.append(el("div", "empty", loading ? "Loading…" : data.length ? "Select a show" : "No shows yet.")); return; }

  if (s.ImageURL) {
    const img = el("img", "poster"); img.src = s.ImageURL; img.alt = ""; img.onerror = () => { img.hidden = true; };
    p.append(img);
  }
  p.append(el("h2", null, s.Title || "(untitled)"));

  const eps = [...s.Episodes].sort((a, b) => (b.StartTime || 0) - (a.StartTime || 0));
  const syn = eps.find(e => e.Synopsis);
  p.append(el("p", "syn", s.Error ? `Couldn't load this show's recordings (${s.Error}). Try Refresh.` :
    syn ? syn.Synopsis : (eps.length ? "" : "Scheduled to record. No recordings yet.")));

  // keep rule, and a shortcut to apply it to this show only
  const rule = ruleOf(s);
  const doomed = new Set(toDelete(eps, rule).map(e => e.CmdURL));
  const box = el("div", "rulebox");
  const line = el("div", "line");
  const lt = el("span"); lt.append("Keep rule: ", el("b", null, ruleText(rule)));
  const change = el("button", "link", "Change"); change.type = "button"; change.onclick = () => openRuleDialog(s);
  line.append(lt, change); box.append(line);
  if (doomed.size) {
    const go = el("button", "btn outline-danger");
    go.type = "button";
    go.append(el("span", "spin"), document.createTextNode(`Delete ${doomed.size} now to match this rule`));
    go.onclick = () => busy(go, () => doPrune({seriesId: key(s)}));
    box.append(go);
  }
  p.append(box);

  const hd = el("div", "hd");
  hd.append(el("span", null, plural(eps.length, "recording") + (eps.length ? ` · ${fmtHours(showHours(s))}` : "")));
  if (eps.length) {
    const all = el("button", "link"); all.type = "button";
    all.onclick = () => {
      const allSel = eps.every(e => selected.has(e.CmdURL));
      eps.forEach(e => allSel ? selected.delete(e.CmdURL) : selected.add(e.CmdURL));
      renderPanel(); $("panel").querySelector(".hd .link").focus();
    };
    hd.append(all);
  }
  p.append(hd);

  if (eps.length) {
    const list = el("div", "eps");
    for (const e of eps) {
      const row = el("label", "ep");
      const cb = el("input"); cb.type = "checkbox"; cb.checked = selected.has(e.CmdURL);
      cb.onchange = () => { cb.checked ? selected.add(e.CmdURL) : selected.delete(e.CmdURL); updateDeleteButton(); updateSelectAll(eps); };
      const t = el("div", "t"); t.append(el("div", null, e.EpisodeTitle || e.Title || ""));
      const dur = durSec(e) ? Math.round(durSec(e) / 60) + " min" : "";
      t.append(el("div", "sub", [e.EpisodeNumber, fmtEpoch(e.StartTime), dur].filter(Boolean).join(" · ")));
      const marks = el("div", "marks");
      if (e.Watched === true) marks.append(el("span", "mark watched", "✓ Watched"));
      else if (e.ResumeSeconds) marks.append(el("span", "mark", `▶ Watched ${Math.round(e.ResumeSeconds / 60)} min`));
      if (doomed.has(e.CmdURL)) marks.append(el("span", "mark doomed", "Rule deletes this"));
      if (marks.children.length) t.append(marks);
      row.append(cb, t); list.append(row);
    }
    p.append(list);
  }

  const actions = el("div", "actions");
  const lbl = el("label"); const rr = el("input"); rr.type = "checkbox"; rr.checked = rerecord;
  rr.onchange = () => rerecord = rr.checked;
  lbl.append(rr, document.createTextNode("Let the DVR record these episodes again"));
  const del = el("button", "btn danger"); del.type = "button"; del.id = "del-btn";
  del.onclick = () => busy(del, doDelete);
  actions.append(lbl, del); p.append(actions);
  updateDeleteButton(); updateSelectAll(eps);
}

function updateDeleteButton() {
  const del = $("del-btn"); if (!del) return;
  del.replaceChildren(el("span", "spin"), document.createTextNode(`Delete selected (${selected.size})`));
  del.disabled = selected.size === 0;
}

function updateSelectAll(eps) {
  const link = $("panel").querySelector(".hd .link");
  if (link) link.textContent = eps.length && eps.every(e => selected.has(e.CmdURL)) ? "Deselect all" : "Select all";
}

/* ---------- info line ---------- */
function renderInfo() {
  const box = $("info"); box.replaceChildren();
  if (loading && !data.length) { box.append(el("div", null, "Loading your recordings…")); return; }
  const recs = data.reduce((a, s) => a + s.Episodes.length, 0);
  const parts = [plural(data.length, "show"), plural(recs, "recording")];
  const hrs = data.reduce((a, s) => a + showHours(s), 0);
  if (hrs) parts.push(fmtHours(hrs));
  if (totalBytes != null && freeBytes != null) parts.push(`${fmtSize(totalBytes - freeBytes)} used`, `${fmtSize(freeBytes)} free`);
  box.append(el("div", null, parts.join(" · ")));
  const a = autoInfo, line = el("div", "auto");
  if (a && a.enabled) {
    line.append(el("span", "ok", "✓ Automatic cleanup on"));
    line.append(document.createTextNode(
      (a.next ? ` · next run ${fmtWhen(new Date(a.next))}` : ` · daily at ${a.time}`) +
      (a.last ? ` · last run ${fmtWhen(new Date(a.last.when))}: ${a.last.summary}` : "")));
  } else if (a) {
    line.append(document.createTextNode("Automatic cleanup is off. "));
    const go = el("button", "link", "Turn it on in Settings"); go.type = "button"; go.onclick = () => showView("settings");
    line.append(go);
  }
  if (line.childNodes.length) box.append(line);
}

/* ---------- loading ---------- */
async function loadStatus() {
  try { const sj = await api("api/status"); freeBytes = sj.free; totalBytes = sj.total; }
  catch (e) { freeBytes = totalBytes = null; }
}

// returns true if the list loaded; on failure the error is shown and the old list stays on screen
async function load(fresh = true) {
  loading = true;
  if (!data.length) { renderInfo(); renderList(); renderPanel(); }
  try {
    let j, lj;
    try {
      [j, lj] = await Promise.all([api(fresh ? "api/recordings?fresh=1" : "api/recordings"), api("api/limits")]);
    } catch (e) { status("Couldn't load your recordings: " + e.message); return false; }
    limits = lj.limits || {}; autoInfo = lj.auto || null; data = Array.isArray(j) ? j : [];
    selected.clear();
    await loadStatus();
    if (!data.some(s => key(s) === current)) current = visibleShows().length ? key(visibleShows()[0]) : null;
    const bad = data.filter(s => s.Error).length;
    status(bad ? `${plural(bad, "show")} couldn't be loaded. Try Refresh.` : "");
    return true;
  } finally {
    loading = false;
    renderInfo(); renderList(); renderPanel();
  }
}

// replace one show in the list with a fresh copy (null = the DVR no longer has recordings of it)
async function replaceShow(sid, fresh) {
  const i = data.findIndex(s => key(s) === sid);
  if (i < 0) return;
  if (fresh) data[i] = {...fresh, Scheduled: data[i].Scheduled};
  else if (data[i].Scheduled) data[i] = {...data[i], Episodes: []};
  else { data.splice(i, 1); if (current === sid) current = null; }
  selected.clear();
  await loadStatus();
  renderInfo(); renderList(); renderPanel();
}

async function refreshShow(sid) {
  try { await replaceShow(sid, await api("api/show?series_id=" + encodeURIComponent(sid))); }
  catch (e) {
    if (e.status === 404) await replaceShow(sid, null);
    else await load(true);
  }
}

/* ---------- deleting ---------- */
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
  let res;
  try { res = await api("api/delete", {json: {cmd_urls: eps.map(e => e.CmdURL), rerecord, series_id: key(s)}}); }
  catch (e) { toast("Nothing was deleted: " + e.message, "err"); return; }
  const failed = res.results.filter(x => !x.ok).length;
  await replaceShow(key(s), res.show || null);
  if (failed) toast(`Deleted ${n - failed} of ${n}. ${failed} couldn't be deleted; see History for details.`, "err");
  else toast(`Deleted ${plural(n, "recording")} of ${s.Title}.`);
}

function planList(plan, oneShow) {
  const ul = el("ul");
  for (const g of plan) {
    if (!oneShow) ul.append(el("li", "g", `${g.Title}: ${ruleText(g.Rule)}`));
    for (const e of g.Episodes) {
      const li = el("li", "sub", epLabel(e));
      if (e.Reason) li.append(el("span", "why", e.Reason));
      ul.append(li);
    }
  }
  return ul;
}

// apply keep rules: to every show, or just {seriesId}
async function doPrune({seriesId = null} = {}, plan = null, notice = "") {
  const q = seriesId ? "?series_id=" + encodeURIComponent(seriesId) : "";
  if (!plan) {
    try { plan = await api("api/prune/preview" + q); }
    catch (e) { toast("Couldn't check the keep rules: " + e.message, "err"); return; }
  }
  if (!plan.length) {
    toast(notice ? "The recordings changed, and nothing needs deleting now." : seriesId ? "Nothing to delete: this show already matches its rule." : "Nothing to delete: every show matches its keep rule.", "info");
    if (seriesId) await refreshShow(seriesId);
    return;
  }
  const urls = plan.flatMap(g => g.Episodes.map(e => e.CmdURL)).filter(Boolean);
  const title = seriesId ? `Delete ${plural(urls.length, "recording")} of ${plan[0].Title}?`
                         : `Delete ${plural(urls.length, "recording")} across ${plural(plan.length, "show")}?`;
  const ok = await confirmDialog({
    title,
    body: planList(plan, !!seriesId),
    note: (notice ? notice + " " : "") + (seriesId ? `Rule: ${ruleText(plan[0].Rule)}. ` : "") +
          "Recordings are deleted without re-recording. This can't be undone.",
    okLabel: `Delete ${urls.length}`,
  });
  if (!ok) return;
  let pj;
  try { pj = await api("api/prune", {json: {cmd_urls: urls, series_id: seriesId}}); }
  catch (e) {
    if (e.status === 409 && e.body && Array.isArray(e.body.plan))
      return doPrune({seriesId}, e.body.plan, "The recordings changed since you opened this, so this is the updated list.");
    toast("Nothing was deleted: " + e.message, "err"); return;
  }
  const failed = pj.results.filter(x => !x.ok).length;
  if (seriesId) await refreshShow(seriesId);
  else if (!await load(true)) toast("Couldn't refresh the list; press Refresh.", "err");
  const n = pj.results.length;
  if (failed) toast(`Deleted ${n - failed} of ${n}. ${failed} couldn't be deleted; see History for details.`, "err");
  else toast(`Deleted ${plural(n, "recording")} to match ${seriesId ? "the keep rule" : "your keep rules"}.`);
}

/* ---------- history view ---------- */
let historyEntries = [], historyMax = 1000;
const SOURCE_LABEL = {manual: "Deleted by hand", "keep-rules": "Keep rules", auto: "Automatic cleanup"};

async function loadHistory() {
  const box = $("history-list");
  if (!historyEntries.length) box.replaceChildren(...Array.from({length: 4}, () => el("div", "skel")));
  try {
    const j = await api("api/history");
    historyEntries = j.entries || []; historyMax = j.max || historyMax;
  } catch (e) { box.replaceChildren(el("div", "empty bad", "Couldn't load history: " + e.message)); return; }
  $("history-help").textContent = `Every recording this app has deleted, newest first. The last ${historyMax} are kept.`;
  renderHistory();
}

function renderHistory() {
  const box = $("history-list"); box.replaceChildren();
  const q = $("hq").value.trim().toLowerCase();
  const rows = historyEntries.filter(h => !q || (h.show || "").toLowerCase().includes(q));
  if (!rows.length) {
    box.append(el("div", "empty", historyEntries.length ? "No deletions match that show." : "Nothing has been deleted yet."));
    return;
  }
  for (const h of rows) {
    const r = el("div", "hrow" + (h.ok ? "" : " failed"));
    r.append(el("div", "when", fmtWhen(new Date(h.when))));
    const what = el("div", "what");
    what.append(el("div", null, h.show || "(unknown show)"));
    what.append(el("div", "sub", [h.number, h.episode, h.start ? "recorded " + fmtEpoch(h.start) : ""].filter(Boolean).join(" · ")));
    if (!h.ok) what.append(el("div", "sub bad", "Not deleted: " + (h.error || "unknown error")));
    const how = el("div", "how");
    how.append(el("span", "mark", SOURCE_LABEL[h.source] || h.source));
    if (h.rerecord) how.append(el("span", "mark", "Will re-record"));
    r.append(what, how);
    box.append(r);
  }
}

/* ---------- views ---------- */
const VIEWS = ["main", "history", "settings"];
function showView(v) {
  for (const name of VIEWS) $(name + "-view").hidden = name !== v;
  const tabs = {main: "tab-manage", history: "tab-history", settings: "tab-settings"};
  for (const [name, id] of Object.entries(tabs)) {
    $(id).classList.toggle("on", name === v); $(id).setAttribute("aria-pressed", String(name === v));
  }
  document.body.classList.remove("sheet-open");
  if (v === "settings") loadSettings();
  if (v === "history") loadHistory();
}

/* ---------- settings view ---------- */
const setMsg = (t, cls) => { const m = $("hostMsg"); m.textContent = t || ""; m.className = cls || ""; };
const setAutoMsg = (t, cls) => { const m = $("autoMsg"); m.textContent = t || ""; m.className = cls || ""; };
const deviceLine = d => `${d.name || "DVR"}${d.model ? " (" + d.model + ")" : ""}${d.free != null ? " · " + fmtSize(d.free) + " free" : ""}`;
const browserTz = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { return ""; } };

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
    j.source === "env" ? `Using ${j.env_dvr_host}, from the server's DVR_HOST setting.` :
    "No address saved. The app will try to find your DVR through Silicondust's cloud.";
  $("host-reset").disabled = j.source !== "ui";
  setMsg("");
  const sc = j.schedule;
  $("auto-on").checked = sc.enabled; $("auto-time").value = sc.time; $("auto-max").value = sc.max;
  const btz = browserTz();
  if (!j.tz_saved && btz && btz !== sc.tz) {
    $("auto-tz").value = btz;
    $("tz-note").textContent = `Filled in from your browser (it was ${sc.tz}). Press Save schedule to use it.`;
    $("tz-note").hidden = false;
  } else {
    $("auto-tz").value = sc.tz;
    $("tz-note").hidden = true;
  }
  setAutoMsg("");
  loadTimezones();
}

async function submitHost(mode, btn) {   // "test" | "save" | "reset"
  setMsg("");
  let j;
  try {
    j = await busy(btn, () => api(mode === "test" ? "api/settings/test" : "api/settings",
                                 {json: {dvr_host: mode === "reset" ? "" : $("host").value}}));
  } catch (e) { setMsg("✗ " + e.message, "bad"); return; }
  if (mode === "test") { setMsg("✓ Connected: " + deviceLine(j.device), "good"); return; }
  await loadSettings();
  setMsg(mode === "reset" ? "✓ Saved address cleared." : "✓ Saved and applied: " + deviceLine(j.device), "good");
  data = []; load(true);
}

async function saveSchedule(btn) {
  setAutoMsg("");
  let j;
  try {
    j = await busy(btn, () => api("api/settings/schedule", {json: {
      enabled: $("auto-on").checked, time: $("auto-time").value,
      tz: $("auto-tz").value.trim(), max: parseInt($("auto-max").value, 10)}}));
  } catch (e) { setAutoMsg("✗ " + e.message, "bad"); return; }
  $("tz-note").hidden = true;
  setAutoMsg(j.schedule.enabled ? "✓ Saved. Next run: " + fmtWhen(new Date(j.next)) : "✓ Saved. Automatic cleanup is off.", "good");
  try { autoInfo = (await api("api/limits")).auto || autoInfo; renderInfo(); } catch (e) { /* shown next load */ }
}

/* ---------- wiring ---------- */
$("q").oninput = e => { query = e.target.value; renderList(); };
$("hq").oninput = renderHistory;
$("tabs").onclick = e => {
  const b = e.target.closest("button"); if (!b) return;
  sortMode = b.dataset.sort;
  document.querySelectorAll("#tabs button").forEach(x => {
    x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b ? "true" : "false");
  });
  renderList();
};
$("refresh").onclick = () => busy($("refresh"), () => load(true));
$("apply-all").onclick = () => busy($("apply-all"), () => doPrune());
$("auto-save").onclick = () => saveSchedule($("auto-save"));
$("tz-browser").onclick = () => { $("auto-tz").value = browserTz(); $("tz-note").hidden = true; };
$("tab-manage").onclick = () => showView("main");
$("tab-history").onclick = () => showView("history");
$("tab-settings").onclick = () => showView("settings");
$("host-test").onclick = () => submitHost("test", $("host-test"));
$("host-save").onclick = () => submitHost("save", $("host-save"));
$("host-reset").onclick = () => submitHost("reset", $("host-reset"));
$("host").onkeydown = e => { if (e.key === "Enter") submitHost("save", $("host-save")); };
$("mclose").onclick = () => { document.body.classList.remove("sheet-open"); if (current) focusRow(current); };
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && document.body.classList.contains("sheet-open") && !$("dlg").open && !$("rule-dlg").open) $("mclose").click();
});
initRuleDialog();
load(false);
