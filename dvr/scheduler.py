"""The daily automatic cleanup.

The scheduler is started explicitly, never at import, so tests and the `flask run --debug`
reloader's watcher process don't get a stray copy. Under gunicorn, gunicorn.conf.py starts it
as soon as the worker boots; under any other server routes.py starts it on the first request.
A lock file in DATA_DIR makes sure only one process per data directory runs it.
Set DVR_SCHEDULER=0 to turn it off entirely (for example in tests).
"""
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

try:
    import fcntl  # Unix only; used so just one process per data directory runs the scheduler
except ImportError:  # pragma: no cover
    fcntl = None

from .config import DATA_DIR
from .dvr_client import friendly
from .pruning import run_prune
from .storage import load_settings, save_last_run

log = logging.getLogger(__name__)

_wake = threading.Event()  # set when the schedule is saved so the scheduler re-reads it right away
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def valid_tz(name):
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


def schedule_config():
    """Schedule from the Settings tab; environment variables act as first-run defaults."""
    st = load_settings()
    env_time = os.environ.get("AUTO_PRUNE_TIME", "")
    return {
        "enabled": bool(st["auto_prune_enabled"]) if "auto_prune_enabled" in st else bool(env_time),
        "time": st.get("auto_prune_time") or env_time or "03:00",
        "tz": st.get("timezone") or os.environ.get("TZ") or "UTC",
        "max": int(st.get("auto_prune_max") or os.environ.get("AUTO_PRUNE_MAX") or 25),
    }


def next_run(now, hh, mm):
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def next_run_iso(cfg):
    if not (cfg["enabled"] and TIME_RE.match(cfg["time"]) and valid_tz(cfg["tz"])):
        return None
    hh, mm = (int(x) for x in cfg["time"].split(":"))
    return next_run(datetime.now(ZoneInfo(cfg["tz"])), hh, mm).isoformat()


def auto_loop():
    """Sleeps until the next scheduled run, waking at least hourly to re-read the schedule."""
    while True:
        try:
            cfg = schedule_config()
            nxt = tz = None
            wait = 3600
            if cfg["enabled"] and TIME_RE.match(cfg["time"]) and valid_tz(cfg["tz"]):
                tz = ZoneInfo(cfg["tz"])
                hh, mm = (int(x) for x in cfg["time"].split(":"))
                now = datetime.now(tz)
                nxt = next_run(now, hh, mm)
                wait = min(3600, max(1, (nxt - now).total_seconds()))
            if _wake.wait(timeout=wait):  # schedule changed: start over with the new one
                _wake.clear()
                continue
            if nxt is None:
                continue
            cfg2 = schedule_config()
            if (cfg2["enabled"], cfg2["time"], cfg2["tz"]) != (cfg["enabled"], cfg["time"], cfg["tz"]):
                continue
            if datetime.now(tz) < nxt:
                continue
            when = datetime.now(tz).isoformat(timespec="minutes")
            level = logging.INFO
            try:
                plan, results = run_prune(max_deletes=cfg2["max"], source="auto")
                if results is None:
                    n = sum(len(g["Episodes"]) for g in plan)
                    msg = f"Skipped: {n} recordings exceeds the safety cap of {cfg2['max']}. Review them and use Apply all keep rules."
                    level = logging.WARNING
                else:
                    ok = sum(1 for r in results if r["ok"])
                    msg = f"Deleted {ok} of {len(results)} recordings."
                    if ok < len(results):
                        level = logging.WARNING
            except Exception as e:
                msg = f"Failed: {friendly(e)}"
                level = logging.ERROR
            log.log(level, "Auto-prune %s: %s", when, msg)
            save_last_run({"when": when, "summary": msg})
        except Exception:
            log.exception("Auto-prune scheduler error; retrying in 60 seconds")
            time.sleep(60)


def wake():
    """Tell the scheduler the schedule was saved, so it re-reads it right away."""
    _wake.set()


_sched = {"state": None}  # None (not tried yet) | "running" | "off"
_sched_lock = threading.Lock()
_sched_lockfile = None


def start_scheduler():
    """Start the auto-prune thread once. Returns True if this process runs it."""
    global _sched_lockfile
    with _sched_lock:
        if _sched["state"] is not None:
            return _sched["state"] == "running"
        _sched["state"] = "off"
        if os.environ.get("DVR_SCHEDULER", "1").strip() == "0":
            log.info("Auto-prune scheduler disabled by DVR_SCHEDULER=0")
            return False
        if fcntl is not None:
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                f = open(os.path.join(DATA_DIR, ".scheduler.lock"), "w")
            except OSError as e:
                log.error("Auto-prune scheduler not started: can't write to %s (%s)", DATA_DIR, e)
                return False
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                f.close()
                log.info("Another process already runs the auto-prune scheduler for %s", DATA_DIR)
                return False
            _sched_lockfile = f  # keep it open: the lock lasts as long as this process
        threading.Thread(target=auto_loop, name="auto-prune", daemon=True).start()
        _sched["state"] = "running"
        cfg = schedule_config()
        log.info("Auto-prune scheduler started (%s)",
                 f"next run {next_run_iso(cfg)}" if cfg["enabled"] else "automatic cleanup is off")
        return True


def ensure_started():
    """Start the scheduler if no attempt has been made yet in this process (cheap to call often)."""
    if _sched["state"] is None:
        start_scheduler()
