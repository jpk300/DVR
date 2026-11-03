"""JSON files in the data directory: settings, keep rules, and the last automatic run.

A file that isn't valid JSON (hand-edited, or cut short by a full disk) doesn't take the app
down: it is renamed to <name>.corrupt-<timestamp>, an error is logged, and defaults are used.
"""
import json
import logging
import os
import re
import threading
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:\d{1,5})?$")


def clean_host(raw):
    """An address typed by a user, without scheme or path, or None if it isn't a valid host[:port]."""
    h = (raw or "").strip()
    for prefix in ("http://", "https://"):
        if h.lower().startswith(prefix):
            h = h[len(prefix):]
    h = h.split("/")[0]
    return h if HOST_RE.match(h) else None


def valid_tz(name):
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


class Storage:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.settings_path = os.path.join(data_dir, "settings.json")
        self.limits_path = os.path.join(data_dir, "limits.json")
        self.last_run_path = os.path.join(data_dir, "last_run.json")
        self.history_path = os.path.join(data_dir, "history.jsonl")
        self.protected_path = os.path.join(data_dir, "protected.json")
        # Held while changing keep rules and while pruning, so the two never interleave.
        self.data_lock = threading.Lock()
        # Held for read-modify-write of settings.json, so two saves can't overwrite each other.
        self._settings_lock = threading.Lock()
        self._file_lock = threading.Lock()
        self._protected_lock = threading.Lock()

    # ---------- low level ----------
    def write_json(self, path, obj):
        """Write JSON atomically: a crash mid-write leaves the old file, never a half-written one."""
        os.makedirs(self.data_dir, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f, indent=2)
        os.replace(tmp, path)

    def read_json(self, path, expect=dict):
        """The file's contents, or None if it doesn't exist. A file that isn't valid JSON of the
        expected type is moved aside (see module docstring) and None is returned."""
        with self._file_lock:
            try:
                with open(path) as f:
                    data = json.load(f)
            except FileNotFoundError:
                return None
            except (ValueError, UnicodeDecodeError) as e:
                self._quarantine(path, f"it isn't valid JSON ({e})")
                return None
            if not isinstance(data, expect):
                names = {dict: "object", list: "array"}
                self._quarantine(path, f"it should hold a JSON {names.get(expect, expect.__name__)}, "
                                       f"not {names.get(type(data), type(data).__name__)}")
                return None
            return data

    def _quarantine(self, path, why):
        dest = f"{path}.corrupt-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        try:
            os.replace(path, dest)
            log.error("Could not read %s because %s. Moved it to %s and using defaults; "
                      "fix that file and copy it back to restore it.", path, why, dest)
        except OSError as e:
            log.error("Could not read %s because %s, and could not move it aside (%s). Using defaults.",
                      path, why, e)

    # ---------- settings ----------
    def settings(self):
        return self.read_json(self.settings_path) or {}

    def update_settings(self, changes=None, remove=()):
        """Apply changes to settings.json under a lock and return the new settings."""
        with self._settings_lock:
            st = self.settings()
            st.update(changes or {})
            for k in remove:
                st.pop(k, None)
            self.write_json(self.settings_path, st)
            return st

    def seed_from_env(self, env):
        """Copy first-run values from the environment into settings.json, once. After that the
        file is the only source, so changing DVR_HOST etc. later has no effect (use Settings)."""
        with self._settings_lock:
            st = self.settings()
            if st.get("env_imported"):
                return
            seeded, used = {}, set()
            host = clean_host(env.get("DVR_HOST") or "")
            if host:
                used.add("DVR_HOST")
            elif env.get("DVR_STORAGE_URL"):
                host = clean_host(urlparse(env["DVR_STORAGE_URL"]).netloc)
                if host:
                    used.add("DVR_STORAGE_URL")
            if host:
                seeded["dvr_host"] = host
            if env.get("TZ") and valid_tz(env["TZ"]):
                seeded["timezone"] = env["TZ"]
                used.add("TZ")
            t = env.get("AUTO_PRUNE_TIME")
            if t and TIME_RE.match(t):
                seeded.update(auto_prune_time=t, auto_prune_enabled=True)
                used.add("AUTO_PRUNE_TIME")
            try:
                mx = int(env.get("AUTO_PRUNE_MAX") or 0)
                if mx >= 1:
                    seeded["auto_prune_max"] = mx
                    used.add("AUTO_PRUNE_MAX")
            except ValueError:
                pass
            # DVR_STORAGE_URL is unused, not invalid, when DVR_HOST is also set
            ignored = sorted(set(env) - used - ({"DVR_STORAGE_URL"} if "DVR_HOST" in used else set()))
            new = {k: v for k, v in seeded.items() if k not in st}
            st.update(new)
            st["env_imported"] = True
            try:
                self.write_json(self.settings_path, st)
            except OSError as e:
                log.error("Could not write %s: %s", self.settings_path, e)
                return
            if new:
                log.info("Copied first-run settings from the environment into %s: %s",
                         self.settings_path, ", ".join(sorted(new)))
            if ignored:
                log.warning("Ignored environment values that aren't valid: %s", ", ".join(ignored))

    # ---------- keep rules ----------
    def limits(self):
        return self.read_json(self.limits_path) or {}

    def save_limits(self, limits):
        self.write_json(self.limits_path, limits)

    # ---------- protected recordings ----------
    def protected(self):
        """{recording key: details} for every recording the user has protected from deletion."""
        return self.read_json(self.protected_path) or {}

    def set_protected(self, key, details=None):
        """Protect a recording (details given) or unprotect it (details None). Returns all of them."""
        with self._protected_lock:
            p = self.protected()
            if details is None:
                p.pop(key, None)
            else:
                p[key] = details
            self.write_json(self.protected_path, p)
            return p

    # ---------- last automatic run ----------
    def last_run(self):
        """The most recent automatic run, saved so it survives restarts (None if there hasn't been one)."""
        return self.read_json(self.last_run_path)

    def save_last_run(self, entry):
        self.write_json(self.last_run_path, entry)
