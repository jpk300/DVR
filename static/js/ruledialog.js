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
  (mode === "newest" ? $input("rule-n") : /** @type {HTMLInputElement} */ ([...radios()].find(r => r.value === mode))).focus();
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
  eff.textContent = n
    ? `With this rule, ${plural(n, "recording")} of ${s.Episodes.length} would be deleted the next time keep rules are applied.`
    : `With this rule, nothing would be deleted right now (${plural(s.Episodes.length, "recording")}).`;
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
  form.addEventListener("change", updateEffect);
  form.addEventListener("input", updateEffect);
  // typing a number selects its option
  $input("rule-n").addEventListener("focus", () => { setMode("newest"); updateEffect(); });
  $input("rule-nw").addEventListener("focus", () => { setMode("watched"); updateEffect(); });
  // Enter saves (the first button in the form is Cancel, which a plain Enter would press)
  form.addEventListener("keydown", e => {
    if (e.key === "Enter" && /** @type {HTMLElement} */ (e.target).tagName === "INPUT") {
      e.preventDefault();
      $button("rule-ok").click();
    }
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
