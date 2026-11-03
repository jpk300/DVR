// @ts-check
// Switching between the Manage, History and Settings tabs.
import {$} from "./dom.js";
import {loadHistory} from "./history.js";
import {loadSettings} from "./settings.js";

/** @typedef {"main" | "history" | "settings"} View */
/** @type {Record<View, string>} */
const TABS = {main: "tab-manage", history: "tab-history", settings: "tab-settings"};

/** @param {View} v */
export function showView(v) {
  for (const [name, tab] of /** @type {[View, string][]} */ (Object.entries(TABS))) {
    $(name + "-view").hidden = name !== v;
    $(tab).classList.toggle("on", name === v);
    $(tab).setAttribute("aria-pressed", String(name === v));
  }
  document.body.classList.remove("sheet-open");
  if (v === "settings") loadSettings();
  if (v === "history") loadHistory();
}
