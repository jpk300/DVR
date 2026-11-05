"""The daily automatic cleanup.

The scheduler is started explicitly, never at import, so tests and the `flask run --debug`
reloader's watcher process don't get a stray copy. Under gunicorn, gunicorn.conf.py starts it
as soon as the worker boots; under any other server routes.py starts it on the first request.
A lock file in the data directory makes sure only one process per data directory runs it.
Set DVR_SCHEDULER=0 to turn it off entirely (for example in tests).
"""
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

try:
    import fcntl  # Unix only; used so just one process per data directory runs the scheduler
except ImportError:  # pragma: no cover
    fcntl = None

from .dvr_client import friendly
from .pruning import run_prune
from .storage import TIME_RE, valid_tz

log = logging.getLogger(__name__)


def schedule_config(settings):
    """The schedule saved in settings.json, with defaults for anything missing."""
    try:
        mx = int(settings.get("auto_prune_max") or 25)
    except (TypeError, ValueError):
        mx = 25
    return {
        "enabled": bool(settings.get("auto_prune_enabled", False)),
        "time": settings.get("auto_prune_time") or "03:00",
        "tz": settings.get("timezone") or "UTC",
        "max": mx,
    }


def low_space_bytes(settings):
    """The free space (in bytes) below which to warn (Settings > Free space warning), or None."""
    gb = settings.get("low_space_gb")
    return gb * 10**9 if isinstance(gb, int) and not isinstance(gb, bool) and gb >= 1 else None


def low_space_note(free, warn_below):
    """A warning sentence if free bytes are below the warning level, else ""."""
    if not (isinstance(free, (int, float)) and warn_below and free < warn_below):
        return ""
    return f"Free space is low: {free / 1e9:.1f} GB left (warning below {warn_below / 1e9:.0f} GB)."


def next_run(now, hh, mm):
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def next_run_iso(cfg):
    if not (cfg["enabled"] and TIME_RE.match(cfg["time"]) and valid_tz(cfg["tz"])):
        return None
    hh, mm = (int(x) for x in cfg["time"].split(":"))
    return next_run(datetime.now(ZoneInfo(cfg["tz"])), hh, mm).isoformat()


class Scheduler:
    def __init__(self, svc, enabled=True):
        self.svc = svc
        self.enabled = enabled
        self.state = None  # None (not tried yet) | "running" | "off"
        self._wake = threading.Event()  # set when the schedule is saved, to re-read it right away
        self._lock = threading.Lock()
        self._lockfile = None

    def config(self):
        return schedule_config(self.svc.storage.settings())

    def wake(self):
        """Tell the scheduler the schedule was saved, so it re-reads it right away."""
        self._wake.set()

    def ensure_started(self):
        """Start the scheduler if no attempt has been made yet in this process (cheap to call often)."""
        if self.state is None:
            self.start()

    def start(self):
        """Start the auto-prune thread once. Returns True if this process runs it."""
        with self._lock:
            if self.state is not None:
                return self.state == "running"
            self.state = "off"
            if not self.enabled:
                log.info("Auto-prune scheduler disabled by DVR_SCHEDULER=0")
                return False
            data_dir = self.svc.storage.data_dir
            if fcntl is not None:
                try:
                    os.makedirs(data_dir, exist_ok=True)
                    f = open(os.path.join(data_dir, ".scheduler.lock"), "w")
                except OSError as e:
                    log.error("Auto-prune scheduler not started: can't write to %s (%s)", data_dir, e)
                    return False
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    f.close()
                    log.info("Another process already runs the auto-prune scheduler for %s", data_dir)
                    return False
                self._lockfile = f  # keep it open: the lock lasts as long as this process
            threading.Thread(target=self._loop, name="auto-prune", daemon=True).start()
            self.state = "running"
            cfg = self.config()
            log.info("Auto-prune scheduler started (%s)",
                     f"next run {next_run_iso(cfg)}" if cfg["enabled"] else "automatic cleanup is off")
            return True

    def _loop(self):
        """Sleeps until the next scheduled run, waking at least hourly to re-read the schedule."""
        while True:
            try:
                cfg = self.config()
                nxt = tz = None
                wait = 3600
                if cfg["enabled"] and TIME_RE.match(cfg["time"]) and valid_tz(cfg["tz"]):
                    tz = ZoneInfo(cfg["tz"])
                    hh, mm = (int(x) for x in cfg["time"].split(":"))
                    now = datetime.now(tz)
                    nxt = next_run(now, hh, mm)
                    wait = min(3600, max(1, (nxt - now).total_seconds()))
                if self._wake.wait(timeout=wait):  # schedule changed: start over with the new one
                    self._wake.clear()
                    continue
                if nxt is None:
                    continue
                cfg2 = self.config()
                if (cfg2["enabled"], cfg2["time"], cfg2["tz"]) != (cfg["enabled"], cfg["time"], cfg["tz"]):
                    continue
                if datetime.now(tz) < nxt:
                    continue
                self.run_once(cfg2["max"], datetime.now(tz).isoformat(timespec="minutes"))
            except Exception:
                log.exception("Auto-prune scheduler error; retrying in 60 seconds")
                time.sleep(60)

    def run_once(self, max_deletes, when):
        """One automatic cleanup: apply every keep rule, unless that exceeds the safety cap."""
        level = logging.INFO
        try:
            plan, results = run_prune(self.svc, max_deletes=max_deletes, source="auto")
            if results is None:
                n = sum(len(g["Episodes"]) for g in plan)
                msg = (f"Skipped: {n} recordings exceeds the safety cap of {max_deletes}. "
                       "Review them and use Apply all keep rules.")
                level = logging.WARNING
            else:
                ok = sum(1 for r in results if r["ok"])
                msg = f"Deleted {ok} of {len(results)} recordings."
                if ok < len(results):
                    level = logging.WARNING
        except Exception as e:
            msg = f"Failed: {friendly(e)}"
            level = logging.ERROR
        warn_below = low_space_bytes(self.svc.storage.settings())
        if warn_below:
            try:
                note = low_space_note(self.svc.client.engine_info().get("FreeSpace"), warn_below)
            except Exception:
                note = ""   # the DVR didn't answer; the run's own message already says so
            if note:
                msg += " " + note
                level = max(level, logging.WARNING)
        log.log(level, "Auto-prune %s: %s", when, msg)
        self.svc.storage.save_last_run({"when": when, "summary": msg})
        return msg
