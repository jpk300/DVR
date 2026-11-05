// @ts-check
// The dialog for editing one show's keep rule.
import {api} from "./api.js";
import {$, $button, $dialog, $input, focusRow, plural} from "./dom.js";
import {reportsWatched, ruleText, toDelete} from "./rules.js";
import {key, render, ruleOf, state} from "./state.js";
import {toast} from "./ui.js";
/** @typedef {import("./types.js").Show} Show */
/** @typedef {import("./types.js").Rule} Rule */

/** @type {Show | null} */
let ruleShow = null;

const radios = () => /** @type {NodeListOf<HTMLInputElement>} */ (document.querySelectorAll("#rule-form input[name=mode]"));
const getMode = () => [...radios()].find(r => r.checked)?.value || "all";
/** @param {string} v */
const setMode = v => radios().forEach(r => { r.checked = r.value === v; });

/** @param {Show} s */
export function openRuleDialog(s) {
  ruleShow = s;
  const rule = ruleOf(s);
  $("rule-title").textContent = `Keep rule for ${s.Title || "this show"}`;
  setMode(!rule ? "all" : rule.delete_watched ? "watched" : "newest");
  $input("rule-n").value = rule && !rule.delete_watched && rule.keep ? String(rule.keep) : "";
  $input("rule-nw").value = rule && rule.delete_watched && rule.keep ? String(rule.keep) : "";
  const noWatched = s.Episodes.length > 0 && !reportsWatched(s);
  $("watched-note").hidden = !noWatched;
  $("watched-note").textContent = noWatched
    ? "Your DVR hasn't reported watched status for these recordings, so this rule would only delete by count." : "";
  $("rule-err").hidden = true;
  updateEffect();
  const dlg = $dialog("rule-dlg");
  dlg.returnValue = "";
  dlg.showModal();
  const mode = getMode();
  const radio = /** @type {HTMLInputElement} */ ([...radios()].find(r => r.value === mode));
  // with a remote, start on the chosen option; with a keyboard, on its number
  (mode === "newest" && !isTvMode() ? $input("rule-n") : radio).focus();
}

const isTvMode = () => document.documentElement.classList.contains("tv");

/** Pick an option. Choosing "keep the newest" with no number yet fills in how many there are now,
 * so the rule starts out deleting nothing. @param {string} mode */
function pick(mode) {
  setMode(mode);
  const n = $input("rule-n");
  if (mode === "newest" && !n.value) n.value = String(Math.max(1, ruleShow ? ruleShow.Episodes.length : 1));
  $("rule-err").hidden = true;
  updateEffect();
}

/** The − and + buttons: change the number by one, and choose that number's option.
 * For "unwatched to keep", going below 1 means blank, which keeps them all. @param {HTMLButtonElement} b */
function step(b) {
  const input = $input(b.dataset.for || "");
  const blankMeansAll = input.id === "rule-nw";
  const cur = parseInt(input.value, 10);
  const next = (cur >= 1 ? cur : 0) + Number(b.dataset.step);
  input.value = next >= 1 ? String(next) : blankMeansAll ? "" : "1";
  pick(input.id === "rule-n" ? "newest" : "watched");
}

/** The rule the dialog currently describes. @returns {{rule: Rule | null, error?: undefined} | {error: string, rule?: undefined}} */
function ruleFromDialog() {
  const mode = getMode();
  if (mode === "all") return {rule: null};
  if (mode === "newest") {
    const n = parseInt($input("rule-n").value, 10);
    if (!(n >= 1)) return {error: "Enter how many recordings to keep (1 or more)."};
    return {rule: {keep: n, delete_watched: false}};
  }
  const raw = $input("rule-nw").value.trim();
  const n = raw === "" ? null : parseInt(raw, 10);
  if (n !== null && !(n >= 1)) return {error: "Enter how many unwatched recordings to keep, or leave it blank to keep them all."};
  return {rule: {keep: n, delete_watched: true}};
}

function updateEffect() {
  const s = ruleShow;
  if (!s) return;
  const {rule, error} = ruleFromDialog();
  const eff = $("rule-effect");
  if (error) { eff.textContent = ""; return; }
  if (!s.Episodes.length) { eff.textContent = "No recordings yet. The rule applies once this show has some."; return; }
  const n = toDelete(s.Episodes, rule ?? null).length;
  const prot = s.Episodes.filter(e => e.Protected).length;
  eff.textContent = (n
    ? `With this rule, ${plural(n, "recording")} of ${s.Episodes.length} would be deleted the next time keep rules are applied.`
    : `With this rule, nothing would be deleted right now (${plural(s.Episodes.length, "recording")}).`) +
    (prot ? ` ${prot} protected ${prot === 1 ? "recording is" : "recordings are"} never deleted and ${prot === 1 ? "doesn't" : "don't"} count toward the rule.` : "");
}

/** @param {Show} s @param {Rule | null} rule */
async function saveRule(s, rule) {
  try {
    const j = await api("api/limits", {json: {series_id: key(s), keep: rule ? rule.keep : null,
                                              delete_watched: rule ? rule.delete_watched : false}});
    state.limits = j.limits || {};
    const n = toDelete(s.Episodes, ruleOf(s)).length;
    toast(`${s.Title}: ${ruleText(ruleOf(s))}.` + (n ? ` ${plural(n, "recording")} can be deleted now.` : ""), "info");
  } catch (e) {
    toast("Couldn't save the rule: " + /** @type {Error} */ (e).message, "err");
  }
  render("list", "panel");
  focusRow(key(s), "pill");
}

export function initRuleDialog() {
  const form = /** @type {HTMLFormElement} */ ($("rule-form"));
  form.addEventListener("change", e => {
    const t = /** @type {HTMLInputElement} */ (e.target);
    if (t.name === "mode") pick(t.value); else updateEffect();
  });
  form.addEventListener("input", updateEffect);
  // clicking or typing in a number selects its option (just moving past it with the remote doesn't)
  for (const [id, mode] of [["rule-n", "newest"], ["rule-nw", "watched"]]) {
    for (const ev of ["click", "input"]) $input(id).addEventListener(ev, () => { if (getMode() !== mode) pick(mode); });
  }
  for (const b of form.querySelectorAll(".step")) b.addEventListener("click", () => step(/** @type {HTMLButtonElement} */ (b)));
  // Enter in a number saves (the first button in the form is Cancel, which a plain Enter would
  // press). Not on the options: with a remote, OK on an option only chooses it.
  form.addEventListener("keydown", e => {
    const t = /** @type {HTMLInputElement} */ (e.target);
    if (e.key !== "Enter" || t.tagName !== "INPUT" || t.type !== "number") return;
    e.preventDefault();
    if (isTvMode()) t.click();   // with a remote, OK on a number opens the keyboard rather than saving
    else $button("rule-ok").click();
  });
  // save on submit (fires at once; "close" may arrive late), and forget the show on any close
  form.addEventListener("submit", e => {
    const s = ruleShow;
    const submitter = /** @type {HTMLButtonElement | null} */ (e.submitter);
    if (!submitter || submitter.value !== "ok" || !s) { ruleShow = null; return; }
    const {rule, error} = ruleFromDialog();
    if (error) {
      e.preventDefault();
      $("rule-err").textContent = error;
      $("rule-err").hidden = false;
      return;
    }
    ruleShow = null;
    saveRule(s, rule ?? null);
  });
  $dialog("rule-dlg").addEventListener("close", () => { ruleShow = null; });
}
