// @ts-check
// The History tab: every deletion the app has made.
import {api} from "./api.js";
import {$, $input, el} from "./dom.js";
import {fmtEpoch, fmtWhen} from "./format.js";
/** @typedef {import("./types.js").HistoryEntry} HistoryEntry */

/** @type {HistoryEntry[]} */
let entries = [];
let max = 1000;
const SOURCE_LABEL = {manual: "Deleted by hand", "keep-rules": "Keep rules", auto: "Automatic cleanup"};

export async function loadHistory() {
  const box = $("history-list");
  if (!entries.length) box.replaceChildren(...Array.from({length: 4}, () => el("div", "skel")));
  try {
    const j = await api("api/history");
    entries = j.entries || [];
    max = j.max || max;
  } catch (e) {
    box.replaceChildren(el("div", "empty bad", "Couldn't load history: " + /** @type {Error} */ (e).message));
    return;
  }
  $("history-help").textContent = `Every recording this app has deleted, newest first. The last ${max} are kept.`;
  renderHistory();
}

export function renderHistory() {
  const box = $("history-list");
  box.replaceChildren();
  const q = $input("hq").value.trim().toLowerCase();
  const rows = entries.filter(h => !q || (h.show || "").toLowerCase().includes(q));
  if (!rows.length) {
    box.append(el("div", "empty", entries.length ? "No deletions match that show." : "Nothing has been deleted yet."));
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
