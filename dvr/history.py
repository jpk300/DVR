"""A log of every deletion: when, which show and episode, how it was triggered, and the result.

Stored as one JSON object per line in DATA_DIR/history.jsonl, oldest first, trimmed to the
newest HISTORY_MAX entries.
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone

from .config import DATA_DIR, HISTORY_MAX, HISTORY_PATH

log = logging.getLogger(__name__)
_lock = threading.Lock()

SOURCES = {
    "manual": "Deleted by hand",
    "keep-rules": "Applied keep rules",
    "auto": "Automatic cleanup",
}


def record(results, source, rerecord=False):
    """Add one entry per attempted deletion. Refused URLs (never sent to the DVR) are skipped."""
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entries = []
    for r in results:
        if not r.get("attempted"):
            continue
        entry = {"when": when, "source": source, "show": r.get("Show"), "series_id": r.get("SeriesID"),
                 "episode": r.get("EpisodeTitle"), "number": r.get("EpisodeNumber"),
                 "start": r.get("StartTime"), "rerecord": bool(rerecord), "ok": bool(r.get("ok"))}
        if not r.get("ok"):
            entry["error"] = r.get("error")
        entries.append(entry)
        label = " ".join(x for x in (entry["number"], entry["episode"]) if x)
        if entry["ok"]:
            log.info("%s: deleted %r %s%s", SOURCES.get(source, source), entry["show"], label,
                     " (will re-record)" if rerecord else "")
        else:
            log.warning("%s: could not delete %r %s: %s", SOURCES.get(source, source), entry["show"],
                        label, entry.get("error"))
    if not entries:
        return
    with _lock:
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(HISTORY_PATH, "a") as f:
                for e in entries:
                    f.write(json.dumps(e) + "\n")
            _trim()
        except OSError as e:
            log.error("Could not write deletion history to %s: %s", HISTORY_PATH, e)


def _trim():
    """Keep the file to the newest HISTORY_MAX lines (rewritten only once it is 20% over)."""
    with open(HISTORY_PATH) as f:
        lines = f.readlines()
    if len(lines) <= HISTORY_MAX * 1.2:
        return
    tmp = HISTORY_PATH + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(lines[-HISTORY_MAX:])
    os.replace(tmp, HISTORY_PATH)


def load(limit=HISTORY_MAX):
    """Newest first. Lines that aren't valid JSON objects are skipped."""
    try:
        with open(HISTORY_PATH) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    out = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
            if len(out) >= limit:
                break
    return out
