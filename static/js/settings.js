// @ts-check
// The Settings tab: DVR address, the automatic-cleanup schedule, the new-show rule and the free-space warning.
import {load} from "./actions.js";
import {api, busy} from "./api.js";
import {$, $button, $input} from "./dom.js";
import {fmtSize, fmtWhen} from "./format.js";
import {render, state} from "./state.js";

/** @param {string} id @param {string} [text] @param {string} [cls] */
const setMsg = (id, text, cls) => { const m = $(id); m.textContent = text || ""; m.className = cls || ""; };
/** @param {{name?: string, model?: string, free?: number}} d */
const deviceLine = d => `${d.name || "DVR"}${d.model ? " (" + d.model + ")" : ""}${d.free != null ? " · " + fmtSize(d.free) + " free" : ""}`;
export const browserTz = () => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch { return ""; } };

async function loadTimezones() {
  const dl = $("tzlist");
  if (dl.children.length) return;   // loaded once per page; the browser also caches the response
  try {
    for (const z of await api("api/timezones")) {
      const o = document.createElement("option");
      o.value = z;
      dl.append(o);
    }
  } catch { /* the field still accepts typed names */ }
}

export async function loadSettings() {
  let j;
  try { j = await api("api/settings"); }
  catch (e) { setMsg("hostMsg", "✗ Couldn't load settings: " + /** @type {Error} */ (e).message, "bad"); return; }
  $input("host").value = j.dvr_host || "";
  $("hostSrc").textContent = j.dvr_host ? "Saved."
    : "No address saved. The app will try to find your DVR through Silicondust's cloud.";
  $button("host-reset").disabled = !j.dvr_host;
  setMsg("hostMsg");
  const sc = j.schedule;
  $input("auto-on").checked = sc.enabled;
  $input("auto-time").value = sc.time;
  $input("auto-max").value = String(sc.max);
  const btz = browserTz();
  if (!j.tz_saved && btz && btz !== sc.tz) {
    $input("auto-tz").value = btz;
    $("tz-note").textContent = `Filled in from your browser (it was ${sc.tz}). Press Save schedule to use it.`;
    $("tz-note").hidden = false;
  } else {
    $input("auto-tz").value = sc.tz;
    $("tz-note").hidden = true;
  }
  setMsg("autoMsg");
  $input("new-on").checked = j.new_show_keep != null;
  $input("new-keep").value = String(j.new_show_keep ?? DEFAULT_NEW_KEEP);
  setMsg("newMsg");
  $input("space-on").checked = j.low_space_gb != null;
  $input("space-gb").value = String(j.low_space_gb ?? DEFAULT_WARN_GB);
  $("space-now").textContent = state.freeBytes != null ? `The DVR has ${fmtSize(state.freeBytes)} free now.` : "";
  setMsg("spaceMsg");
  loadTimezones();
}

const DEFAULT_NEW_KEEP = 5;    // suggested when the new-show rule is first turned on
const DEFAULT_WARN_GB = 50;

/** A whole number of 1 or more from a field, or null. @param {string} id */
function wholeNumber(id) {
  const n = Number($input(id).value);
  return Number.isInteger(n) && n >= 1 ? n : null;
}

/** @param {HTMLButtonElement} btn */
export async function saveNewShows(btn) {
  setMsg("newMsg");
  const on = $input("new-on").checked, n = wholeNumber("new-keep");
  if (on && n == null) { setMsg("newMsg", "✗ Enter how many recordings new shows keep (1 or more).", "bad"); return; }
  try {
    await busy(btn, () => api("api/settings/new-shows", {json: {keep: on ? n : null}}));
  } catch (e) { setMsg("newMsg", "✗ " + /** @type {Error} */ (e).message, "bad"); return; }
  setMsg("newMsg", on ? `✓ Saved. New shows will keep the newest ${n}.` : "✓ Saved. New shows keep everything.", "good");
}

/** @param {HTMLButtonElement} btn */
export async function saveSpaceWarning(btn) {
  setMsg("spaceMsg");
  const on = $input("space-on").checked, gb = wholeNumber("space-gb");
  if (on && gb == null) { setMsg("spaceMsg", "✗ Enter the free space to warn below, in GB (1 or more).", "bad"); return; }
  try {
    await busy(btn, () => api("api/settings/space", {json: {warn_gb: on ? gb : null}}));
  } catch (e) { setMsg("spaceMsg", "✗ " + /** @type {Error} */ (e).message, "bad"); return; }
  state.warnBelow = on && gb ? gb * 1e9 : null;
  render("info");
  setMsg("spaceMsg", on ? `✓ Saved. You'll be warned below ${gb} GB free.` : "✓ Saved. No free space warning.", "good");
}

/** @param {"test" | "save" | "reset"} mode @param {HTMLButtonElement} btn */
export async function submitHost(mode, btn) {
  setMsg("hostMsg");
  let j;
  try {
    j = await busy(btn, () => api(mode === "test" ? "api/settings/test" : "api/settings",
                                 {json: {dvr_host: mode === "reset" ? "" : $input("host").value}}));
  } catch (e) { setMsg("hostMsg", "✗ " + /** @type {Error} */ (e).message, "bad"); return; }
  if (mode === "test") { setMsg("hostMsg", "✓ Connected: " + deviceLine(j.device), "good"); return; }
  await loadSettings();
  setMsg("hostMsg", mode === "reset" ? "✓ Saved address cleared." : "✓ Saved and applied: " + deviceLine(j.device), "good");
  state.shows = [];
  load(true);
}

/** @param {HTMLButtonElement} btn */
export async function saveSchedule(btn) {
  setMsg("autoMsg");
  let j;
  try {
    j = await busy(btn, () => api("api/settings/schedule", {json: {
      enabled: $input("auto-on").checked, time: $input("auto-time").value,
      tz: $input("auto-tz").value.trim(), max: parseInt($input("auto-max").value, 10)}}));
  } catch (e) { setMsg("autoMsg", "✗ " + /** @type {Error} */ (e).message, "bad"); return; }
  $("tz-note").hidden = true;
  setMsg("autoMsg", j.schedule.enabled ? "✓ Saved. Next run: " + fmtWhen(new Date(j.next)) : "✓ Saved. Automatic cleanup is off.", "good");
  try { state.auto = (await api("api/limits")).auto || state.auto; render("info"); } catch { /* shown next load */ }
}

/** Settings controls that need wiring once: − / + beside the numbers, the section bar, and the
 * one-field forms (they only group a field for on-screen keyboards, so they never submit). */
export function initSettings() {
  for (const b of document.querySelectorAll("#settings-view .step")) {
    const btn = /** @type {HTMLButtonElement} */ (b);
    btn.addEventListener("click", () => {
      const input = $input(btn.dataset.for || "");
      const cur = parseInt(input.value, 10);
      input.value = String(Math.max(1, (cur >= 1 ? cur : 0) + Number(btn.dataset.step)));
      if (btn.dataset.check) $input(btn.dataset.check).checked = true;   // changing the number means "use it"
    });
  }
  for (const b of document.querySelectorAll("#settings-view .jump button")) {
    const btn = /** @type {HTMLButtonElement} */ (b);
    btn.addEventListener("click", () => {
      const card = $(btn.dataset.to || "");
      card.scrollIntoView({block: "start"});
      const first = /** @type {HTMLElement | null} */ (card.querySelector("input:not([tabindex='-1']), button"));
      first?.focus({preventScroll: true});
    });
  }
  for (const f of document.querySelectorAll("#settings-view form.solo")) f.addEventListener("submit", e => e.preventDefault());
}
