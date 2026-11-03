// @ts-check
// The Settings tab: DVR address and the automatic-cleanup schedule.
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
  loadTimezones();
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
