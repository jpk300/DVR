"""A log of every deletion: when, which show and episode, how it was triggered, and the result.

Stored as one JSON object per line (oldest first), trimmed to the newest `max_entries`.
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone

log = logging.getLogger(__name__)

SOURCES = {
    "manual": "Deleted by hand",
    "keep-rules": "Applied keep rules",
    "auto": "Automatic cleanup",
}


class History:
    def __init__(self, path, max_entries):
        self.path = path
        self.max_entries = max_entries
        self._lock = threading.Lock()

    def record(self, results, source, rerecord=False):
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
        with self._lock:
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                with open(self.path, "a") as f:
                    for e in entries:
                        f.write(json.dumps(e) + "\n")
                self._trim()
            except OSError as e:
                log.error("Could not write deletion history to %s: %s", self.path, e)

    def _trim(self):
        """Keep the file to the newest max_entries lines (rewritten only once it is 20% over)."""
        with open(self.path) as f:
            lines = f.readlines()
        if len(lines) <= self.max_entries * 1.2:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            f.writelines(lines[-self.max_entries:])
        os.replace(tmp, self.path)

    def load(self, limit=None):
        """Newest first. Lines that aren't valid JSON objects are skipped."""
        limit = limit or self.max_entries
        try:
            with open(self.path) as f:
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
