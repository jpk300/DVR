// @ts-check
// The show list (right) and the summary line above it.
import {$, PENCIL, button, el, focusRow, isMobile, plural, svg} from "./dom.js";
import {fmtHours, fmtSize, fmtWhen, newest, showHours} from "./format.js";
import {openRuleDialog} from "./ruledialog.js";
import {ruleText, toDelete} from "./rules.js";
import {key, registerRenderer, render, ruleOf, setCurrent, state} from "./state.js";
import {showView} from "./views.js";
/** @typedef {import("./types.js").Show} Show */

function visibleShows() {
  const q = state.query.trim().toLowerCase();
  const rows = state.shows.filter(s => !q || (s.Title || "").toLowerCase().includes(q));
  const m = state.sortMode;
  rows.sort((a, b) =>
    m === "count" ? b.Episodes.length - a.Episodes.length :
    m === "hours" ? showHours(b) - showHours(a) :
    m === "date" ? newest(b) - newest(a) :
    (a.Title || "").localeCompare(b.Title || ""));
  return rows;
}

/** Show a show's recordings in the panel (a full-screen sheet on phones). @param {Show} s */
export function openShow(s) {
  setCurrent(key(s));
  render("list", "panel");
  if (isMobile()) { document.body.classList.add("sheet-open"); $("mclose").focus(); }
  else focusRow(key(s));
}

/** @param {Show} s */
function keepPill(s) {
  const rule = ruleOf(s);
  const b = button("keep-pill" + (rule ? " set" : ""));
  b.append(el("span", null, `Keep: ${ruleText(rule, true)}`), svg(PENCIL));
  b.setAttribute("aria-label", `${s.Title || "Untitled"}: ${ruleText(rule)}. Change keep rule`);
  b.title = "Change what to keep";
  b.onclick = e => { e.stopPropagation(); setCurrent(key(s)); render("list", "panel"); openRuleDialog(s); };
  return b;
}

function renderList() {
  const list = $("list");
  list.replaceChildren();
  if (state.loading && !state.shows.length) {
    for (let i = 0; i < 6; i++) list.append(el("div", "skel"));
    return;
  }
  const rows = visibleShows();
  if (!rows.length) list.append(el("div", "empty", state.shows.length ? "No shows match your search." : "No shows yet."));
  for (const s of rows) {
    const sel = key(s) === state.current;
    const scheduledOnly = !!s.Scheduled && !s.Episodes.length;
    const row = el("div", "row" + (sel ? " sel" : ""));
    row.dataset.key = key(s);
    const main = button("row-main");
    if (sel) main.setAttribute("aria-current", "true");
    const cntText = s.Error ? "!" : scheduledOnly ? "–" : state.sortMode === "hours" ? fmtHours(showHours(s)) : s.Episodes.length;
    const ttl = el("span", "ttl");
    ttl.append(el("span", "name", s.Title || "(untitled)"));
    if (scheduledOnly) ttl.append(el("span", "tag", "Scheduled"));
    if (s.Error) { const t = el("span", "tag err", "Couldn't load"); t.title = s.Error; ttl.append(t); }
    main.append(el("span", "cnt", cntText), ttl);
    main.setAttribute("aria-label", `${s.Title || "Untitled"}, ${s.Error ? "couldn't load" :
      scheduledOnly ? "scheduled, no recordings yet" : plural(s.Episodes.length, "recording")}`);
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

function renderInfo() {
  const box = $("info");
  box.replaceChildren();
  if (state.loading && !state.shows.length) { box.append(el("div", null, "Loading your recordings…")); return; }
  const recs = state.shows.reduce((a, s) => a + s.Episodes.length, 0);
  const parts = [plural(state.shows.length, "show"), plural(recs, "recording")];
  const hrs = state.shows.reduce((a, s) => a + showHours(s), 0);
  if (hrs) parts.push(fmtHours(hrs));
  if (state.totalBytes != null && state.freeBytes != null)
    parts.push(`${fmtSize(state.totalBytes - state.freeBytes)} used`, `${fmtSize(state.freeBytes)} free`);
  box.append(el("div", null, parts.join(" · ")));
  if (state.freeBytes != null && state.warnBelow && state.freeBytes < state.warnBelow) {
    box.append(el("div", "low", `⚠ Free space is low: ${fmtSize(state.freeBytes)} left (warning below ` +
      `${fmtSize(state.warnBelow)}). Delete recordings or tighten keep rules to make room.`));
  }
  const a = state.auto, line = el("div", "auto");
  if (a && a.enabled) {
    line.append(el("span", "ok", "✓ Automatic cleanup on"));
    line.append(document.createTextNode(
      (a.next ? ` · next run ${fmtWhen(new Date(a.next))}` : ` · daily at ${a.time}`) +
      (a.last ? ` · last run ${fmtWhen(new Date(a.last.when))}: ${a.last.summary}` : "")));
  } else if (a) {
    line.append(document.createTextNode("Automatic cleanup is off. "));
    const go = button("link", "Turn it on in Settings");
    go.onclick = () => showView("settings");
    line.append(go);
  }
  if (line.childNodes.length) box.append(line);
}

registerRenderer("list", renderList);
registerRenderer("info", renderInfo);
