// @ts-check
// Turning numbers and dates into text.
/** @typedef {import("./types.js").Episode} Episode */
/** @typedef {import("./types.js").Show} Show */

/** @param {number} b bytes */
export const fmtSize = b => b >= 1e12 ? (b / 1e12).toFixed(2) + " TB" : b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : (b / 1e6).toFixed(0) + " MB";

/** @param {Date} d */
const fmtTime = d => d.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});

/**
 * Today / Yesterday / weekday for the last week, then the date, with the year when it isn't this year.
 * @param {Date} d
 */
export function fmtWhen(d) {
  if (!(d instanceof Date) || isNaN(d.getTime())) return "";
  const now = new Date();
  /** @param {Date} x */
  const midnight = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((midnight(now) - midnight(d)) / 864e5);
  if (days === 0) return `Today, ${fmtTime(d)}`;
  if (days === 1) return `Yesterday, ${fmtTime(d)}`;
  if (days > 1 && days < 7) return `${d.toLocaleDateString([], {weekday: "short"})}, ${fmtTime(d)}`;
  /** @type {Intl.DateTimeFormatOptions} */
  const opts = {month: "short", day: "numeric"};
  if (d.getFullYear() !== now.getFullYear()) opts.year = "numeric";
  return `${d.toLocaleDateString([], opts)}, ${fmtTime(d)}`;
}

/** @param {number | undefined} t seconds since 1970 */
export const fmtEpoch = t => t ? fmtWhen(new Date(t * 1000)) : "";

/** Recorded length in seconds. @param {Episode} e */
export const durSec = e => (e.RecordStartTime && e.RecordEndTime) ? Math.max(0, e.RecordEndTime - e.RecordStartTime) : 0;

/** @param {number} sec */
export const fmtHours = sec => { const h = sec / 3600; return h >= 10 ? `${Math.round(h)} h` : `${h.toFixed(1)} h`; };

/** @param {Show} s */
export const showHours = s => s.Episodes.reduce((a, e) => a + durSec(e), 0);

/** @param {Show} s */
export const newest = s => Math.max(0, ...s.Episodes.map(e => e.StartTime || 0));

/** "S01E02 · Title · Today, 4:54 PM" @param {{EpisodeNumber?: string, EpisodeTitle?: string, Title?: string, StartTime?: number}} e */
export const epLabel = e => [e.EpisodeNumber, e.EpisodeTitle || e.Title || "(untitled)", fmtEpoch(e.StartTime)].filter(Boolean).join(" · ");
