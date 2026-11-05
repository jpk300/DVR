// @ts-check
// TV mode: for the Fire TV app (and any browser opened with ?tv=1).
//  * The remote's arrow keys move focus to the nearest control in that direction.
//  * OK (Enter) presses buttons and ticks checkboxes.
//  * Back (handled by the Android app, which calls window.tvBack) closes a dialog,
//    then returns to the Manage tab, and only then leaves the app.
//  * Focus survives redraws, and larger type and a bold focus ring suit a TV across the room.
// Nothing here runs unless TV mode is on, so normal browsers are unaffected.
import {renderHooks} from "./state.js";
import {showView} from "./views.js";

const FOCUSABLE = 'button, input:not([type=hidden]), select, textarea, a[href], [tabindex]:not([tabindex="-1"])';
/** @typedef {"up" | "down" | "left" | "right"} Dir */
/** @type {Record<string, Dir>} */
const KEYS = {ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right"};

/** TV mode is on when the page was opened with ?tv=1 (the app does this, and it lasts for the
 * browser tab, so reloads keep it) or on a Fire TV browser. ?tv=0 turns it off again. */
export function isTv() {
  try {
    const p = new URLSearchParams(location.search).get("tv");
    if (p === "1") sessionStorage.setItem("tv", "1");
    if (p === "0") { sessionStorage.removeItem("tv"); return false; }
    return sessionStorage.getItem("tv") === "1" || /\bAFT\w*\b/.test(navigator.userAgent);
  } catch {
    return new URLSearchParams(location.search).get("tv") === "1";
  }
}

/** @param {Element} e */
function usable(e) {
  const h = /** @type {HTMLElement} */ (e);
  if (/** @type {HTMLButtonElement} */ (h).disabled || h.closest("[hidden]")) return false;
  const r = h.getBoundingClientRect();
  if (!r.width || !r.height) return false;
  const s = getComputedStyle(h);
  return s.visibility !== "hidden" && s.display !== "none";
}

/** Controls the remote can reach: inside the open dialog if there is one. */
function candidates() {
  const root = document.querySelector("dialog[open]") || document.body;
  return /** @type {HTMLElement[]} */ ([...root.querySelectorAll(FOCUSABLE)].filter(usable));
}

/**
 * The best control in a direction: mostly ahead, as little off to the side as possible.
 * @param {HTMLElement} from @param {Dir} dir @param {HTMLElement[]} all
 */
function nearest(from, dir, all) {
  const vertical = dir === "up" || dir === "down";
  // Up and Down stay in the same area (the episode list, then the recordings panel, the show
  // list, ...) while it has anything further that way; only then do they move on.
  if (vertical) {
    let outer = null;
    for (let area = from.closest(AREAS); area; area = area.parentElement?.closest(AREAS) || null) {
      const box = outer = area;
      const mine = nearestIn(from, dir, all.filter(el => box.contains(el)), vertical);
      if (mine) return mine;
    }
    // leaving an area, go to what's above or below it (like the top bar), never to what's beside it
    if (outer) {
      const o = outer.getBoundingClientRect();
      all = all.filter(el => { const r = el.getBoundingClientRect(); return overlaps(o.left, o.right, r.left, r.right); });
    }
  }
  // Left and Right: within an area only along the same line; diagonally only into an area
  // beside this one (the show list next to the panel), never one above or below it
  if (!vertical) {
    let outer = from.closest(AREAS);
    while (outer?.parentElement?.closest(AREAS)) outer = outer.parentElement.closest(AREAS);
    if (outer) {
      const o = outer.getBoundingClientRect(), box = outer;
      all = all.filter(el => {
        const r = el.getBoundingClientRect();
        return box.contains(el) || overlaps(o.top, o.bottom, r.top, r.bottom);
      });
    }
    return nearestIn(from, dir, all, vertical, outer);
  }
  return nearestIn(from, dir, all, vertical);
}

/** Areas of the page that Up and Down keep to, smallest first. */
const AREAS = ".eps, dialog, header.top, #side, #main-view section, #history-view, #settings-view";

/**
 * @param {HTMLElement} from @param {Dir} dir @param {HTMLElement[]} all @param {boolean} vertical
 * @param {Element | null} [ownArea] for Left and Right: the area where only same-line moves count
 */
function nearestIn(from, dir, all, vertical, ownArea = null) {
  const a = from.getBoundingClientRect();
  const ax = a.left + a.width / 2, ay = a.top + a.height / 2;
  let best = null, bestScore = Infinity;
  for (const el of all) {
    if (el === from || el.contains(from) || from.contains(el)) continue;
    const b = el.getBoundingClientRect();
    const bx = b.left + b.width / 2, by = b.top + b.height / 2;
    // must lie ahead in that direction (by its centre, and not starting behind our leading edge)
    if (dir === "down" && (by <= ay + 1 || b.top < a.top + 1)) continue;
    if (dir === "up" && (by >= ay - 1 || b.bottom > a.bottom - 1)) continue;
    if (dir === "right" && (bx <= ax + 1 || b.left < a.left + 1)) continue;
    if (dir === "left" && (bx >= ax - 1 || b.right > a.right - 1)) continue;
    // how far ahead (edge to edge), and how far off to the side (0 if on the same line)
    const ahead = vertical ? Math.max(0, dir === "down" ? b.top - a.bottom : a.top - b.bottom)
                           : Math.max(0, dir === "right" ? b.left - a.right : a.left - b.right);
    const sameLine = vertical ? overlaps(a.left, a.right, b.left, b.right) : overlaps(a.top, a.bottom, b.top, b.bottom);
    if (!sameLine && ownArea && ownArea.contains(el)) continue;
    const side = sameLine ? 0 : vertical ? Math.abs(bx - ax) : Math.abs(by - ay);
    // Left and Right go to something on the same line if there is one, however far
    const score = ahead + side * 3 + (!vertical && !sameLine ? 1e6 : 0);
    if (score < bestScore) { bestScore = score; best = el; }
  }
  return best;
}

/** Do two ranges on one axis overlap? @param {number} a1 @param {number} a2 @param {number} b1 @param {number} b2 */
const overlaps = (a1, a2, b1, b2) => b1 < a2 && b2 > a1;

/** Move the focus ring (see initTv) to el. @param {Element} el */
function markFocus(el) {
  document.querySelector(".tv-focus")?.classList.remove("tv-focus");
  el.classList.add("tv-focus");
}

/** The scrolling boxes around el, innermost first, ending with the page itself. @param {Element} el */
function scrollers(el) {
  const out = [];
  for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(p).overflowY) && p.scrollHeight > p.clientHeight) out.push(p);
  }
  out.push(/** @type {HTMLElement} */ (document.scrollingElement || document.documentElement));
  return out;
}

/** @param {HTMLElement} el */
function focusEl(el) {
  const prev = document.activeElement;
  el.focus({preventScroll: true});
  markFocus(el);   // also set here: focus events can be late or missing while the window isn't focused
  // leaving the recordings panel puts it (and the episode list inside it) back at the top, so it
  // isn't left half-scrolled; moving within the panel leaves its scrolling alone
  if (prev && prev !== document.body) {
    const boxes = scrollers(prev).slice(0, -1);
    const outer = boxes[boxes.length - 1];
    if (outer && !outer.contains(el)) {
      for (const box of [outer, ...outer.querySelectorAll("*")]) if (box.scrollTop) box.scrollTop = 0;
    }
  }
  el.scrollIntoView({block: "nearest", inline: "nearest"});
  // "nearest" stops as soon as el shows, which can leave a picture or heading above it cut off:
  // on the first or last control in a box, scroll the box all the way
  const all = candidates();
  for (const box of scrollers(el)) {
    const inside = all.filter(c => box.contains(c));
    if (inside[0] === el) box.scrollTop = 0;
    else if (inside[inside.length - 1] === el) box.scrollTop = box.scrollHeight;
  }
}

/** Should an arrow key stay inside this field (moving the text cursor) instead of moving focus? */
function arrowStaysInField(/** @type {KeyboardEvent} */ e) {
  const t = /** @type {HTMLElement} */ (e.target);
  if (t.tagName !== "INPUT" && t.tagName !== "TEXTAREA") return false;
  const input = /** @type {HTMLInputElement} */ (t);
  if (input.type === "time") return e.key === "ArrowLeft" || e.key === "ArrowRight";
  if (input.type === "checkbox" || input.type === "radio") return false;
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return false;
  try {
    const at = input.selectionStart ?? 0, len = input.value.length;
    return e.key === "ArrowLeft" ? at > 0 : at < len;   // leave the field only from its ends
  } catch {
    return false;   // number inputs have no selection: arrows always move focus
  }
}

/** The first thing to focus on the current screen. */
function startFocus() {
  /** @param {ParentNode | null} root @returns {HTMLElement[]} */
  const usableIn = root => root ? /** @type {HTMLElement[]} */ ([...root.querySelectorAll(FOCUSABLE)].filter(usable)) : [];
  const dlg = document.querySelector("dialog[open]");
  if (dlg) {
    const safe = /** @type {HTMLElement | null} */ (dlg.querySelector("#dlg-cancel, #rule-cancel"));
    const el = safe && usable(safe) ? safe : usableIn(dlg)[0];
    if (el) focusEl(el);
    return;
  }
  // on Manage: the selected show, else the first show; elsewhere: the first control on that screen
  const show = document.querySelector("#main-view:not([hidden]) .row.sel .row-main") ||
               document.querySelector("#main-view:not([hidden]) .row-main");
  const el = show && usable(show) ? /** @type {HTMLElement} */ (show)
    : usableIn(document.querySelector("main:not([hidden])"))[0] || candidates()[0];
  if (el) focusEl(el);
}

/** Remember what had focus before a redraw, by what it is rather than which element it was. */
function describeFocus() {
  const a = /** @type {HTMLElement | null} */ (document.activeElement);
  if (!a || a === document.body) return null;
  const row = /** @type {HTMLElement | null} */ (a.closest(".row"));
  if (row) return {row: row.dataset.key, pill: a.classList.contains("keep-pill")};
  const ep = /** @type {HTMLElement | null} */ (a.closest(".ep"));
  if (ep) return {ep: ep.dataset.cmd, lock: a.classList.contains("lock")};
  if (a.id) return {id: a.id};
  if (a.closest("#panel .hd")) return {selectAll: true};
  if (a.closest(".rulebox")) return {rulebox: true};
  return null;
}

/** After a redraw, put focus back on the same thing if the redraw dropped it. @param {any} saved */
function restoreFocus(saved) {
  if (document.activeElement && document.activeElement !== document.body) return;
  if (!saved) return;
  /** @type {Element | null} */
  let el = null;
  const byData = (/** @type {string} */ sel, /** @type {string} */ attr, /** @type {string} */ val) =>
    [...document.querySelectorAll(sel)].find(x => /** @type {HTMLElement} */ (x).dataset[attr] === val) || null;
  if (saved.row) el = byData(".row", "key", saved.row)?.querySelector(saved.pill ? ".keep-pill" : ".row-main") || null;
  else if (saved.ep) el = byData(".ep", "cmd", saved.ep)?.querySelector(saved.lock ? ".lock" : "input") || null;
  else if (saved.id) el = document.getElementById(saved.id);
  else if (saved.selectAll) el = document.querySelector("#panel .hd .link");
  else if (saved.rulebox) el = document.querySelector(".rulebox .btn, .rulebox .link");
  if (el && usable(el)) focusEl(/** @type {HTMLElement} */ (el));
  else startFocus();
}

/**
 * Called by the Android app when the remote's Back button is pressed.
 * Returns true if the page handled it, false to let the app close.
 */
function tvBack() {
  const dlg = /** @type {HTMLDialogElement | null} */ (document.querySelector("dialog[open]"));
  if (dlg) { dlg.close(); setTimeout(startFocus, 0); return true; }
  const main = document.getElementById("main-view");
  if (main && main.hidden) { showView("main"); setTimeout(startFocus, 0); return true; }
  return false;
}

export function initTv() {
  if (!isTv()) return false;
  document.documentElement.classList.add("tv");
  /** @type {any} */ (window).tvBack = tvBack;
  renderHooks.before = describeFocus;
  renderHooks.after = restoreFocus;

  document.addEventListener("keydown", e => {
    const dir = KEYS[e.key];
    if (dir) {
      if (arrowStaysInField(e)) return;
      e.preventDefault();
      const all = candidates();
      const cur = /** @type {HTMLElement | null} */ (document.activeElement);
      if (!cur || cur === document.body || !all.includes(cur)) { startFocus(); return; }
      const next = nearest(cur, dir, all);
      if (next) focusEl(next);
      return;
    }
    // OK on a checkbox or radio ticks it (buttons already respond to Enter)
    const t = /** @type {HTMLInputElement} */ (e.target);
    if (e.key === "Enter" && t.tagName === "INPUT" && (t.type === "checkbox" || t.type === "radio")) {
      e.preventDefault();
      t.click();
    }
    // a browser's Back key (when testing outside the app)
    if (e.key === "GoBack" || e.key === "BrowserBack") { if (tvBack()) e.preventDefault(); }
  }, true);

  // The focus ring follows a class rather than :focus alone, so it shows even when the page
  // doesn't have window focus (some TV launchers, and browsers in the background).
  document.addEventListener("focusin", e => markFocus(/** @type {Element} */ (e.target)));
  document.addEventListener("focusout", e => { /** @type {HTMLElement} */ (e.target).classList.remove("tv-focus"); });

  // a dialog opening moves focus into it; when it closes, start from the screen again
  for (const dlg of document.querySelectorAll("dialog")) dlg.addEventListener("close", () => setTimeout(() => {
    if (!document.activeElement || document.activeElement === document.body) startFocus();
  }, 0));
  // switching tabs: focus the new screen
  for (const id of ["tab-manage", "tab-history", "tab-settings"]) {
    document.getElementById(id)?.addEventListener("click", () => setTimeout(() => {
      /** @type {HTMLElement | null} */ (document.getElementById(id))?.focus();
    }, 0));
  }
  return true;
}

/** Give the remote somewhere to start once the list has loaded. */
export function focusFirstShow() {
  if (document.documentElement.classList.contains("tv")) startFocus();
}
