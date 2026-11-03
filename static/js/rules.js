// @ts-check
// Keep rules. toDelete() is the same logic as dvr/pruning.py, used for counts and previews;
// the server's plan is what is actually deleted.
/** @typedef {import("./types.js").Episode} Episode */
/** @typedef {import("./types.js").Rule} Rule */
/** @typedef {import("./types.js").Show} Show */

/**
 * @param {Rule | null} rule
 * @param {boolean} [short]  the few words shown on the "Keep: …" button
 */
export function ruleText(rule, short = false) {
  if (!rule) return short ? "All" : "Keep everything";
  if (rule.delete_watched) {
    if (rule.keep) return short ? `${rule.keep} unwatched` : `Delete watched, keep newest ${rule.keep} unwatched`;
    return short ? "Unwatched" : "Delete watched, keep all unwatched";
  }
  return short ? `${rule.keep}` : `Keep the newest ${rule.keep}`;
}

/**
 * The recordings a rule removes.
 * @param {Episode[]} episodes
 * @param {Rule | null} rule
 * @returns {Episode[]}
 */
export function toDelete(episodes, rule) {
  if (!rule) return [];
  let eps = [...episodes].sort((a, b) => (b.StartTime || 0) - (a.StartTime || 0));
  /** @type {Episode[]} */
  const out = [];
  if (rule.delete_watched) {
    eps.filter(e => e.Watched === true).forEach(e => out.push(e));
    eps = eps.filter(e => e.Watched !== true);
  }
  if (rule.keep) eps.slice(rule.keep).forEach(e => out.push(e));
  return out;
}

/** Does the DVR report watched status for any of this show's recordings? @param {Show} s */
export const reportsWatched = s => s.Episodes.some(e => e.Watched === true || e.Watched === false);
