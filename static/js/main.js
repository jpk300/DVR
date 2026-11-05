// @ts-check
// Entry point: wires the page's fixed controls to the modules and loads the list.
import {doPrune, load} from "./actions.js";
import {busy} from "./api.js";
import {$, $button, $dialog, $input, focusRow} from "./dom.js";
import {renderHistory} from "./history.js";
import "./list.js";    // registers the list and info renderers
import "./panel.js";   // registers the panel renderer
import {initRuleDialog} from "./ruledialog.js";
import {browserTz, saveSchedule, submitHost} from "./settings.js";
import {render, state} from "./state.js";
import {focusFirstShow, initTv} from "./tv.js";
import {showView} from "./views.js";

const tv = initTv();   // TV mode (Fire TV app, or ?tv=1): remote navigation and 10-foot styling

$input("q").oninput = () => { state.query = $input("q").value; render("list"); };
$input("hq").oninput = renderHistory;
$("tabs").onclick = e => {
  const b = /** @type {HTMLElement} */ (e.target).closest("button");
  if (!b) return;
  state.sortMode = /** @type {typeof state.sortMode} */ (b.dataset.sort);
  document.querySelectorAll("#tabs button").forEach(x => {
    x.classList.toggle("on", x === b);
    x.setAttribute("aria-pressed", x === b ? "true" : "false");
  });
  render("list");
};
$("refresh").onclick = () => busy($button("refresh"), () => load(true));
$("apply-all").onclick = () => busy($button("apply-all"), () => doPrune());
$("auto-save").onclick = () => saveSchedule($button("auto-save"));
$("tz-browser").onclick = () => { $input("auto-tz").value = browserTz(); $("tz-note").hidden = true; };
$("tab-manage").onclick = () => showView("main");
$("tab-history").onclick = () => showView("history");
$("tab-settings").onclick = () => showView("settings");
$("host-test").onclick = () => submitHost("test", $button("host-test"));
$("host-save").onclick = () => submitHost("save", $button("host-save"));
$("host-reset").onclick = () => submitHost("reset", $button("host-reset"));
$input("host").onkeydown = e => { if (e.key === "Enter") submitHost("save", $button("host-save")); };
$("mclose").onclick = () => {
  document.body.classList.remove("sheet-open");
  if (state.current) focusRow(state.current);
};
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && document.body.classList.contains("sheet-open") &&
      !$dialog("dlg").open && !$dialog("rule-dlg").open) $("mclose").click();
});

initRuleDialog();
load(false).then(() => { if (tv) focusFirstShow(); });
