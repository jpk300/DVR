// @ts-check
// The page's shared state, and a registry of the functions that draw each part of it.
// Modules call render("list", "panel") instead of importing each other's draw functions,
// which keeps the module graph free of cycles.
/** @typedef {import("./types.js").Show} Show */
/** @typedef {import("./types.js").Rule} Rule */
/** @typedef {import("./types.js").AutoInfo} AutoInfo */

export const state = {
  /** @type {Show[]} */ shows: [],
  /** @type {Record<string, Rule>} */ limits: {},
  /** @type {number | null} */ freeBytes: null,
  /** @type {number | null} */ totalBytes: null,
  /** @type {number | null} */ warnBelow: null,   // free space (bytes) to warn below, from Settings
  /** @type {AutoInfo | null} */ auto: null,
  /** key of the show in the panel @type {string | null} */ current: null,
  /** @type {"title" | "date" | "count" | "hours"} */ sortMode: "title",
  query: "",
  rerecord: false,
  loading: false,
  /** CmdURLs ticked in the panel, always within the current show @type {Set<string>} */
  selected: new Set(),
};

/** @param {Show} s */
export const key = s => s.SeriesID || s.Title;
export const currentShow = () => state.shows.find(s => key(s) === state.current) || null;
/** @param {Show} s @returns {Rule | null} */
export const ruleOf = s => state.limits[key(s)] || null;

/** Switching shows drops the selection, so "Delete selected" only ever covers what is on screen. @param {string | null} k */
export function setCurrent(k) {
  if (k !== state.current) state.selected.clear();
  state.current = k;
}

/** @typedef {"info" | "list" | "panel"} Part */
/** @type {Partial<Record<Part, () => void>>} */
const renderers = {};

/** @param {Part} part @param {() => void} fn */
export function registerRenderer(part, fn) { renderers[part] = fn; }

/** Redraw the given parts, or all of them. @param {...Part} parts */
export function render(...parts) {
  const list = parts.length ? parts : /** @type {Part[]} */ (Object.keys(renderers));
  for (const p of list) renderers[p]?.();
}
