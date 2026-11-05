// @ts-check
// The selected show's details and recordings (left, or a full-screen sheet on phones).
import {doDelete, doPrune, setProtected} from "./actions.js";
import {busy} from "./api.js";
import {$, LOCK_CLOSED, LOCK_OPEN, button, el, plural, svg} from "./dom.js";
import {durSec, fmtEpoch, fmtHours, showHours} from "./format.js";
import {openRuleDialog} from "./ruledialog.js";
import {ruleText, toDelete} from "./rules.js";
import {currentShow, key, registerRenderer, ruleOf, state} from "./state.js";
/** @typedef {import("./types.js").Episode} Episode */

function renderPanel() {
  const p = $("panel");
  p.replaceChildren();
  const s = currentShow();
  if (!s) { p.append(el("div", "empty", state.loading ? "Loading…" : state.shows.length ? "Select a show" : "No shows yet.")); return; }

  if (s.ImageURL) {
    const img = el("img", "poster");
    img.src = s.ImageURL; img.alt = "";
    img.onerror = () => { img.hidden = true; };
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
  const lt = el("span");
  lt.append("Keep rule: ", el("b", null, ruleText(rule)));
  const change = button("link", "Change");
  change.onclick = () => openRuleDialog(s);
  line.append(lt, change);
  box.append(line);
  if (doomed.size) {
    const go = button("btn outline-danger");
    go.append(el("span", "spin"), document.createTextNode(`Delete ${doomed.size} now to match this rule`));
    go.onclick = () => busy(go, () => doPrune({seriesId: key(s)}));
    box.append(go);
  }
  p.append(box);

  const hd = el("div", "hd");
  const nProtected = eps.filter(e => e.Protected).length;
  hd.append(el("span", null, plural(eps.length, "recording") + (eps.length ? ` · ${fmtHours(showHours(s))}` : "") +
                             (nProtected ? ` · ${nProtected} protected` : "")));
  const deletable = eps.filter(e => !e.Protected);
  if (deletable.length) {
    const all = button("link");
    all.onclick = () => {
      const allSel = deletable.every(e => state.selected.has(e.CmdURL));
      deletable.forEach(e => allSel ? state.selected.delete(e.CmdURL) : state.selected.add(e.CmdURL));
      renderPanel();
      /** @type {HTMLElement | null} */ ($("panel").querySelector(".hd .link"))?.focus();
    };
    hd.append(all);
  }
  p.append(hd);

  if (eps.length) {
    const list = el("div", "eps");
    for (const e of eps) {
      const row = el("label", "ep" + (e.Protected ? " protected" : ""));
      row.dataset.cmd = e.CmdURL;
      const cb = el("input");
      cb.type = "checkbox"; cb.checked = !e.Protected && state.selected.has(e.CmdURL);
      cb.disabled = !!e.Protected;
      if (e.Protected) cb.title = "Protected recordings can't be deleted. Unprotect it first.";
      cb.onchange = () => {
        cb.checked ? state.selected.add(e.CmdURL) : state.selected.delete(e.CmdURL);
        updateDeleteButton(); updateSelectAll(deletable);
      };
      const t = el("div", "t");
      t.append(el("div", null, e.EpisodeTitle || e.Title || ""));
      const dur = durSec(e) ? Math.round(durSec(e) / 60) + " min" : "";
      t.append(el("div", "sub", [e.EpisodeNumber, fmtEpoch(e.StartTime), dur].filter(Boolean).join(" · ")));
      const marks = el("div", "marks");
      if (e.Watched === true) marks.append(el("span", "mark watched", "✓ Watched"));
      else if (e.ResumeSeconds) marks.append(el("span", "mark", `▶ Watched ${Math.round(e.ResumeSeconds / 60)} min`));
      if (e.Protected) marks.append(el("span", "mark protected", "Protected"));
      if (doomed.has(e.CmdURL)) marks.append(el("span", "mark doomed", "Rule deletes this"));
      if (marks.children.length) t.append(marks);
      row.append(cb, t, lockButton(s, e));
      list.append(row);
    }
    p.append(list);
  }

  const actions = el("div", "actions");
  const lbl = el("label");
  const rr = el("input");
  rr.type = "checkbox"; rr.checked = state.rerecord;
  rr.onchange = () => { state.rerecord = rr.checked; };
  lbl.append(rr, document.createTextNode("Let the DVR record these episodes again"));
  const del = button("btn danger");
  del.id = "del-btn";
  del.onclick = () => busy(del, doDelete);
  actions.append(lbl, del);
  p.append(actions);
  updateDeleteButton(); updateSelectAll(deletable);
}

/**
 * The lock toggle on one recording.
 * @param {import("./types.js").Show} s @param {Episode} e
 */
function lockButton(s, e) {
  const name = e.EpisodeTitle || e.Title || "this recording";
  const b = button("lock" + (e.Protected ? " on" : ""));
  b.append(svg(e.Protected ? LOCK_CLOSED : LOCK_OPEN));
  b.setAttribute("aria-pressed", String(!!e.Protected));
  b.setAttribute("aria-label", e.Protected ? `${name} is protected. Remove protection` : `Protect ${name} from deletion`);
  b.title = e.Protected ? "Protected: never deleted. Click to remove protection." : "Protect from deletion";
  // the button sits inside the row's <label>; don't let the click tick the checkbox too
  b.onclick = ev => {
    ev.preventDefault(); ev.stopPropagation();
    busy(b, () => setProtected(s, e, !e.Protected));
  };
  return b;
}

function updateDeleteButton() {
  const del = /** @type {HTMLButtonElement | null} */ (document.getElementById("del-btn"));
  if (!del) return;
  del.replaceChildren(el("span", "spin"), document.createTextNode(`Delete selected (${state.selected.size})`));
  del.disabled = state.selected.size === 0;
}

/** @param {Episode[]} eps */
function updateSelectAll(eps) {
  const link = $("panel").querySelector(".hd .link");
  if (link) link.textContent = eps.length && eps.every(e => state.selected.has(e.CmdURL)) ? "Deselect all" : "Select all";
}

registerRenderer("panel", renderPanel);
