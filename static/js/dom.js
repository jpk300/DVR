// @ts-check
// Small DOM helpers.

/** @param {string} id @returns {HTMLElement} */
export function $(id) {
  const e = document.getElementById(id);
  if (!e) throw new Error(`#${id} is missing from the page`);
  return e;
}
/** @param {string} id */
export const $input = id => /** @type {HTMLInputElement} */ ($(id));
/** @param {string} id */
export const $button = id => /** @type {HTMLButtonElement} */ ($(id));
/** @param {string} id */
export const $dialog = id => /** @type {HTMLDialogElement} */ ($(id));

/**
 * Create an element with an optional class and text.
 * @template {keyof HTMLElementTagNameMap} K
 * @param {K} tag
 * @param {string | null} [cls]
 * @param {string | number | null} [text]
 * @returns {HTMLElementTagNameMap[K]}
 */
export function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = String(text);
  return e;
}

/** A button that doesn't submit forms. @param {string | null} [cls] @param {string | null} [text] */
export function button(cls, text) {
  const b = el("button", cls, text);
  b.type = "button";
  return b;
}

/** Parse a fixed SVG snippet (never user data). @param {string} html */
export function svg(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return /** @type {Node} */ (t.content.firstChild);
}

export const PENCIL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>';

/** @param {number} n @param {string} word */
export const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

export const isMobile = () => window.matchMedia("(max-width: 860px)").matches;

/**
 * Focus a show's row (its main button, or its keep-rule button).
 * @param {string} key @param {"main" | "pill"} [part]
 */
export function focusRow(key, part = "main") {
  for (const row of $("list").querySelectorAll(".row")) {
    if (/** @type {HTMLElement} */ (row).dataset.key === key) {
      /** @type {HTMLElement | null} */ (row.querySelector(part === "pill" ? ".keep-pill" : ".row-main"))?.focus();
      return;
    }
  }
}
