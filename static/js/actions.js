// @ts-check
// Loading recordings, deleting, and applying keep rules.
import {api} from "./api.js";
import {el, plural} from "./dom.js";
import {epLabel} from "./format.js";
import {ruleText} from "./rules.js";
import {currentShow, key, render, state} from "./state.js";
import {confirmDialog, toast} from "./ui.js";
/** @typedef {import("./types.js").Show} Show */
/** @typedef {import("./types.js").PlanGroup} PlanGroup */

const statusLine = (/** @type {string} */ t) => { document.getElementById("status")?.replaceChildren(t || ""); };

export async function loadStatus() {
  try {
    const sj = await api("api/status");
    state.freeBytes = sj.free; state.totalBytes = sj.total; state.warnBelow = sj.warn_below ?? null;
  } catch {
    state.freeBytes = state.totalBytes = null;
  }
}

/** Load every show. Returns true if it worked; on failure the old list stays on screen. */
export async function load(fresh = true) {
  state.loading = true;
  if (!state.shows.length) render();
  try {
    let shows, lim;
    try {
      // one after the other: loading the list can give new shows the new-show rule
      shows = await api(fresh ? "api/recordings?fresh=1" : "api/recordings");
      lim = await api("api/limits");
    } catch (e) {
      statusLine("Couldn't load your recordings: " + /** @type {Error} */ (e).message);
      return false;
    }
    state.limits = lim.limits || {};
    state.auto = lim.auto || null;
    state.shows = Array.isArray(shows) ? shows : [];
    state.selected.clear();
    await loadStatus();
    if (!state.shows.some(s => key(s) === state.current)) state.current = state.shows.length ? key(state.shows[0]) : null;
    const bad = state.shows.filter(s => s.Error).length;
    statusLine(bad ? `${plural(bad, "show")} couldn't be loaded. Try Refresh.` : "");
    return true;
  } finally {
    state.loading = false;
    render();
  }
}

/**
 * Put a fresh copy of one show in the list (null = the DVR no longer has recordings of it).
 * @param {string} sid @param {Show | null} fresh
 */
async function replaceShow(sid, fresh) {
  const i = state.shows.findIndex(s => key(s) === sid);
  if (i < 0) return;
  const old = state.shows[i];
  if (fresh) state.shows[i] = {...fresh, Scheduled: old.Scheduled};
  else if (old.Scheduled) state.shows[i] = {...old, Episodes: []};
  else { state.shows.splice(i, 1); if (state.current === sid) state.current = null; }
  state.selected.clear();
  await loadStatus();
  render();
}

/** Re-read one show from the DVR (two requests). @param {string} sid */
export async function refreshShow(sid) {
  try {
    await replaceShow(sid, await api("api/show?series_id=" + encodeURIComponent(sid)));
  } catch (e) {
    if (/** @type {any} */ (e).status === 404) await replaceShow(sid, null);
    else await load(true);
  }
}

/**
 * Protect a recording from deletion, or remove its protection.
 * @param {Show} s @param {import("./types.js").Episode} e @param {boolean} on
 */
export async function setProtected(s, e, on) {
  try {
    await api("api/protect", {json: {series_id: key(s), key: e.Key, protected: on}});
  } catch (err) {
    toast(`Couldn't ${on ? "protect" : "unprotect"} it: ` + /** @type {Error} */ (err).message, "err");
    return;
  }
  e.Protected = on;
  if (on) state.selected.delete(e.CmdURL);
  render();
  const name = e.EpisodeTitle || e.Title || "this recording";
  toast(on ? `Protected ${name}. Deletes and keep rules will skip it.` : `${name} is no longer protected.`, "info");
}

/** Delete the recordings ticked in the panel, after confirming. */
export async function doDelete() {
  const s = currentShow();
  const eps = s ? s.Episodes.filter(e => state.selected.has(e.CmdURL)) : [];
  if (!s || !eps.length) return;
  const n = eps.length;
  const ul = el("ul");
  eps.sort((a, b) => (b.StartTime || 0) - (a.StartTime || 0)).forEach(e => ul.append(el("li", null, epLabel(e))));
  const ok = await confirmDialog({
    title: `Delete ${plural(n, "recording")} of ${s.Title || "this show"}?`,
    body: ul,
    note: state.rerecord ? "The DVR will record these episodes again if they air. This can't be undone."
                         : "The DVR will treat these episodes as already recorded. This can't be undone.",
    okLabel: `Delete ${n}`,
  });
  if (!ok) return;
  let res;
  try {
    res = await api("api/delete", {json: {cmd_urls: eps.map(e => e.CmdURL), rerecord: state.rerecord, series_id: key(s)}});
  } catch (e) {
    toast("Nothing was deleted: " + /** @type {Error} */ (e).message, "err");
    return;
  }
  const failed = res.results.filter((/** @type {{ok: boolean}} */ x) => !x.ok).length;
  await replaceShow(key(s), res.show || null);
  if (failed) toast(`Deleted ${n - failed} of ${n}. ${failed} couldn't be deleted; see History for details.`, "err");
  else toast(`Deleted ${plural(n, "recording")} of ${s.Title}.`);
}

/** @param {PlanGroup[]} plan @param {boolean} oneShow */
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

/**
 * Apply keep rules to every show, or just one, after confirming the server's plan.
 * @param {{seriesId?: string | null}} [opts]
 * @param {PlanGroup[] | null} [plan]  a plan already in hand (after the recordings changed)
 * @param {string} [notice]
 * @returns {Promise<void>}
 */
export async function doPrune({seriesId = null} = {}, plan = null, notice = "") {
  const q = seriesId ? "?series_id=" + encodeURIComponent(seriesId) : "";
  if (!plan) {
    try { plan = /** @type {PlanGroup[]} */ (await api("api/prune/preview" + q)); }
    catch (e) { toast("Couldn't check the keep rules: " + /** @type {Error} */ (e).message, "err"); return; }
  }
  if (!plan.length) {
    toast(notice ? "The recordings changed, and nothing needs deleting now."
      : seriesId ? "Nothing to delete: this show already matches its rule."
      : "Nothing to delete: every show matches its keep rule.", "info");
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
  try {
    pj = await api("api/prune", {json: {cmd_urls: urls, series_id: seriesId}});
  } catch (e) {
    const err = /** @type {any} */ (e);
    if (err.status === 409 && err.body && Array.isArray(err.body.plan))
      return doPrune({seriesId}, err.body.plan, "The recordings changed since you opened this, so this is the updated list.");
    toast("Nothing was deleted: " + err.message, "err");
    return;
  }
  const failed = pj.results.filter((/** @type {{ok: boolean}} */ x) => !x.ok).length;
  if (seriesId) await refreshShow(seriesId);
  else if (!await load(true)) toast("Couldn't refresh the list; press Refresh.", "err");
  const n = pj.results.length;
  if (failed) toast(`Deleted ${n - failed} of ${n}. ${failed} couldn't be deleted; see History for details.`, "err");
  else toast(`Deleted ${plural(n, "recording")} to match ${seriesId ? "the keep rule" : "your keep rules"}.`);
}
