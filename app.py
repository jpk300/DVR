import ipaddress
import json
import logging
import os
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo, available_timezones

import requests
from flask import Flask, jsonify, request, Response
from requests.adapters import HTTPAdapter

try:
    import fcntl  # Unix only; used so just one process per data directory runs the scheduler
except ImportError:  # pragma: no cover
    fcntl = None

# ---------- logging ----------
# LOG_LEVEL=DEBUG|INFO|WARNING|ERROR (default INFO). Logs go to stderr, so `docker logs` shows them.
_level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").strip().upper(), None)
logging.basicConfig(level=_level if isinstance(_level, int) else logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("dvr")

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # request bodies are small JSON; refuse anything bigger

APP_USER = os.environ.get("APP_USER")
APP_PASS = os.environ.get("APP_PASS")

DATA_DIR = os.environ.get("DATA_DIR", "/data")
LIMITS_PATH = os.path.join(DATA_DIR, "limits.json")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
LAST_RUN_PATH = os.path.join(DATA_DIR, "last_run.json")
_lock = threading.Lock()

# ---------- tunables ----------
DVR_TIMEOUT = (3.05, 10)          # (connect, read) seconds for every DVR request
MAX_WORKERS = 6                   # bounded concurrency when loading each show's episodes
MAX_BODY_BYTES = 8 * 1024 * 1024  # refuse DVR responses bigger than this
MAX_DELETE_BATCH = 500            # most recordings one delete request may name
LIST_TTL = 10                     # seconds the recordings list is cached
RULES_TTL = 300                   # seconds the cloud recording rules are cached


@app.before_request
def check_auth():
    if not APP_USER:
        return None
    a = request.authorization
    if a and a.username == APP_USER and a.password == APP_PASS:
        return None
    return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="DVR"'})


# ---------- settings storage ----------

HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:\d{1,5})?$")


def load_settings():
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def write_json(path, obj):
    """Write JSON atomically: a crash mid-write leaves the old file, never a half-written one."""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def save_settings(settings):
    write_json(SETTINGS_PATH, settings)


def clean_host(raw):
    h = (raw or "").strip()
    for prefix in ("http://", "https://"):
        if h.lower().startswith(prefix):
            h = h[len(prefix):]
    h = h.split("/")[0]
    return h if HOST_RE.match(h) else None


# ---------- safe DVR access ----------
#
# Every request to the DVR goes through dvr_fetch(), which:
#   * only speaks plain http:// with no credentials in the URL
#   * resolves the hostname itself and requires EVERY address to be on an allowed
#     private network (default: RFC1918 + IPv6 ULA; extend with DVR_ALLOWED_NETWORKS)
#   * connects to that exact validated IP (so DNS can't change between check and use)
#   * never follows redirects, caps response size, and uses short timeouts

class UnsafeURL(ValueError):
    pass


def _load_allowed_networks():
    entries = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"]
    entries += [x.strip() for x in os.environ.get("DVR_ALLOWED_NETWORKS", "").split(",") if x.strip()]
    nets = []
    for e in entries:
        try:
            nets.append(ipaddress.ip_network(e, strict=False))
        except ValueError:
            log.warning("Ignoring invalid DVR_ALLOWED_NETWORKS entry: %s", e)
    return nets


_NETS = _load_allowed_networks()

_session = requests.Session()
_session.trust_env = False  # ignore HTTP(S)_PROXY and .netrc for LAN traffic
_session.mount("http://", HTTPAdapter(pool_connections=4, pool_maxsize=MAX_WORKERS + 2))


def resolve_allowed(hostname, port):
    """Resolve hostname and return one IP, but only if all its addresses are on allowed networks."""
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise UnsafeURL(f"could not resolve {hostname}") from e
    ips = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        ips.append(ip)
    if not ips:
        raise UnsafeURL(f"{hostname} did not resolve to any address")
    for ip in ips:
        if not any(ip in net for net in _NETS):
            raise UnsafeURL(f"{hostname} resolves to {ip}, which is outside the allowed networks "
                            "(set DVR_ALLOWED_NETWORKS to permit it)")
    ips.sort(key=lambda i: i.version)  # prefer IPv4
    return ips[0]


def _pin(url, params=None):
    """Validate url and return (url rewritten to the validated IP, original Host header)."""
    p = urlparse(url)
    if p.scheme != "http":
        raise UnsafeURL("only http:// DVR URLs are allowed")
    if p.username or p.password:
        raise UnsafeURL("credentials in a DVR URL are not allowed")
    if not p.hostname:
        raise UnsafeURL("DVR URL has no host")
    try:
        port = p.port
    except ValueError:
        raise UnsafeURL("DVR URL has an invalid port") from None
    ip = resolve_allowed(p.hostname, port or 80)
    netloc = f"[{ip}]" if ip.version == 6 else str(ip)
    if port:
        netloc += f":{port}"
    query = p.query
    if params:
        query = urlencode(parse_qsl(p.query, keep_blank_values=True) + list(params.items()))
    return urlunparse(("http", netloc, p.path, "", query, "")), p.netloc


def _read_limited(r):
    chunks, total = [], 0
    for chunk in r.iter_content(65536):
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise RuntimeError("the DVR sent a response that is too large")
        chunks.append(chunk)
    return b"".join(chunks)


def dvr_fetch(url, method="GET", params=None):
    pinned, host_header = _pin(url, params)
    with _session.request(method, pinned, headers={"Host": host_header, "User-Agent": "hdhr-dvr-manager"},
                          timeout=DVR_TIMEOUT, allow_redirects=False, stream=True) as r:
        if 300 <= r.status_code < 400:
            raise UnsafeURL("the DVR tried to redirect; refusing to follow")
        return r.status_code, _read_limited(r)


def dvr_json(url, expect=None):
    status, body = dvr_fetch(url)
    if status >= 400:
        raise RuntimeError(f"the DVR returned HTTP {status}")
    try:
        data = json.loads(body)
    except ValueError:
        raise RuntimeError("the DVR returned something that isn't JSON") from None
    if expect and not isinstance(data, expect):
        raise RuntimeError("the DVR returned an unexpected response")
    return data


def friendly(e):
    if isinstance(e, requests.exceptions.Timeout):
        return "the DVR took too long to respond"
    if isinstance(e, requests.exceptions.ConnectionError):
        return "could not connect to the DVR"
    return str(e) or e.__class__.__name__


def safe_image(url):
    return url if isinstance(url, str) and url.lower().startswith(("http://", "https://")) else None


# ---------- DVR discovery ----------

_storage_cache = {}
_list_cache = {"t": 0.0, "data": None}
_rules_cache = {"t": 0.0, "data": None}
_cache_lock = threading.Lock()


def reset_dvr_caches():
    _storage_cache.clear()
    _list_cache["t"] = 0.0
    _rules_cache["data"] = None


def invalidate_list_cache():
    _list_cache["t"] = 0.0


def configured_host():
    return load_settings().get("dvr_host") or os.environ.get("DVR_HOST") or None


def check_storage_url(url):
    p = urlparse(url)
    if p.scheme != "http" or p.username or p.password or not p.hostname:
        raise UnsafeURL("the DVR reported an unsafe storage URL")
    if p.path != "/recorded_files.json":
        raise UnsafeURL(f"the DVR reported an unexpected storage path {p.path!r}")
    return url


def storage_url(refresh=False):
    """Find the record engine's recorded_files.json URL (validated)."""
    if not refresh and "url" in _storage_cache:
        return _storage_cache["url"]

    saved = load_settings().get("dvr_host")
    url = None if saved else os.environ.get("DVR_STORAGE_URL")
    if not url:
        host = saved or os.environ.get("DVR_HOST")
        if host:
            host = clean_host(host)
            if not host:
                raise RuntimeError("The configured DVR address is not valid. Fix it in Settings.")
            url = dvr_json(f"http://{host}/discover.json", dict).get("StorageURL")
        else:
            r = requests.get("https://api.hdhomerun.com/discover", timeout=DVR_TIMEOUT, allow_redirects=False)
            r.raise_for_status()
            for dev in r.json():
                if dev.get("StorageURL"):
                    url = dev["StorageURL"]
                    break
    if not url:
        raise RuntimeError("Could not find a DVR record engine. Set the DVR address in Settings.")
    check_storage_url(url)
    _storage_cache["url"] = url
    return url


def check_dvr_url(url, path):
    """A URL taken from a DVR response must point at the same host:port as the storage URL,
    use plain http, carry no credentials, and have the expected path."""
    p = urlparse(url)
    try:
        port = p.port or 80
    except ValueError:
        raise UnsafeURL("invalid port") from None
    base = urlparse(storage_url())
    if p.scheme != "http" or p.username or p.password:
        raise UnsafeURL("URL must be plain http without credentials")
    if (p.hostname or "").lower() != (base.hostname or "").lower() or port != (base.port or 80):
        raise UnsafeURL("URL does not point at the DVR")
    if p.path != path:
        raise UnsafeURL(f"unexpected path {p.path!r}")
    return url


def engine_info():
    host = configured_host()
    if host:
        host = clean_host(host)
        if not host:
            raise RuntimeError("The configured DVR address is not valid. Fix it in Settings.")
        base = f"http://{host}"
    else:
        base = f"http://{urlparse(storage_url()).netloc}"
    return dvr_json(base + "/discover.json", dict)


# ---------- recordings ----------

EP_FIELDS = ("ProgramID", "Title", "EpisodeTitle", "EpisodeNumber", "Synopsis",
             "StartTime", "EndTime", "RecordStartTime", "RecordEndTime", "CmdURL")


def _load_series(s):
    entry = {"SeriesID": s.get("SeriesID"), "Title": s.get("Title"),
             "ImageURL": safe_image(s.get("ImageURL")), "Episodes": []}
    eps_url = s.get("EpisodesURL")
    if not eps_url:
        return entry
    try:
        check_dvr_url(eps_url, "/recorded_files.json")
        raw = dvr_json(eps_url, list)
    except Exception as e:
        entry["Error"] = friendly(e)
        log.warning("Could not load episodes for %r: %s", s.get("Title"), entry["Error"])
        return entry
    skipped = 0
    for ep in raw:
        if not isinstance(ep, dict):
            continue
        try:
            check_dvr_url(ep.get("CmdURL") or "", "/recorded/cmd")
        except Exception:
            skipped += 1
            continue
        entry["Episodes"].append({k: ep.get(k) for k in EP_FIELDS})
    if skipped:
        log.warning("Hid %d recording(s) of %r with unexpected URLs", skipped, s.get("Title"))
    return entry


def fetch_series():
    """Every show with its recordings, loaded with bounded concurrency. A show that fails
    to load comes back with an Error and no recordings instead of failing the whole list."""
    series = dvr_json(storage_url(refresh=True), list)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        return list(ex.map(_load_series, [s for s in series if isinstance(s, dict)]))


def device_auth():
    auth = os.environ.get("DEVICE_AUTH")  # optional override; concatenate if you have several tuners
    return auth or engine_info().get("DeviceAuth")


def fetch_rules(fresh=False):
    """Recording rules from Silicondust's cloud, one entry per series (cached)."""
    now = time.time()
    if not fresh and _rules_cache["data"] is not None and now - _rules_cache["t"] < RULES_TTL:
        return _rules_cache["data"]
    out, auth = [], device_auth()
    if auth:
        r = requests.get("https://api.hdhomerun.com/api/recording_rules", params={"DeviceAuth": auth},
                         timeout=DVR_TIMEOUT, allow_redirects=False)
        r.raise_for_status()
        seen = set()
        for rule in (r.json() or []):  # the API returns null when there are no rules
            sid = rule.get("SeriesID")
            if sid and sid not in seen:
                seen.add(sid)
                out.append({"SeriesID": sid, "Title": rule.get("Title"), "ImageURL": safe_image(rule.get("ImageURL"))})
    _rules_cache.update(t=now, data=out)
    return out


def _safe_rules(fresh):
    try:
        return fetch_rules(fresh)
    except Exception as e:
        log.warning("Could not load recording rules: %s", friendly(e))
        return []


def fetch_all(fresh=False):
    """Recorded shows plus shows that only have a recording rule so far (cached for LIST_TTL)."""
    with _cache_lock:
        now = time.time()
        if not fresh and _list_cache["data"] is not None and now - _list_cache["t"] < LIST_TTL:
            return _list_cache["data"]
        with ThreadPoolExecutor(max_workers=1) as rex:
            rules_future = rex.submit(_safe_rules, fresh)
            out = fetch_series()
            rules = rules_future.result()
        have = {o["SeriesID"] for o in out}
        for r in rules:
            if r["SeriesID"] not in have:
                out.append({"SeriesID": r["SeriesID"], "Title": r["Title"],
                            "ImageURL": r["ImageURL"], "Episodes": [], "Scheduled": True})
                have.add(r["SeriesID"])
        _list_cache.update(t=time.time(), data=out)
        return out


def delete_urls(cmd_urls, rerecord=0, allowed=None):
    """Delete recordings. Only URLs that appear in a fresh server-side DVR listing are accepted,
    and each must still pass the scheme / host / port / path checks."""
    if allowed is None:
        allowed = {e["CmdURL"] for s in fetch_series() for e in s["Episodes"] if e.get("CmdURL")}
    results = []
    for u in cmd_urls:
        if u not in allowed:
            results.append({"cmd_url": u, "ok": False, "error": "not a current recording on the DVR"})
            continue
        try:
            check_dvr_url(u, "/recorded/cmd")
            status, _ = dvr_fetch(u, method="POST", params={"cmd": "delete", "rerecord": rerecord})
            results.append({"cmd_url": u, "ok": status < 400, "status": status})
        except Exception as e:
            results.append({"cmd_url": u, "ok": False, "error": friendly(e)})
    invalidate_list_cache()
    return results


# ---------- keep-limits ----------

def series_key(s):
    return s.get("SeriesID") or s.get("Title")


def load_limits():
    try:
        with open(LIMITS_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_limits(limits):
    write_json(LIMITS_PATH, limits)


def build_plan(series, limits):
    """For each series with a limit, list the oldest episodes beyond the newest N."""
    plan = []
    for s in series:
        n = limits.get(series_key(s))
        if not n:
            continue
        eps = sorted(s["Episodes"], key=lambda e: e.get("StartTime") or 0, reverse=True)
        extra = eps[n:]
        if extra:
            plan.append({
                "SeriesID": series_key(s),
                "Title": s["Title"],
                "Keep": n,
                "Episodes": [{
                    "EpisodeTitle": e.get("EpisodeTitle") or e.get("Title"),
                    "StartTime": e.get("StartTime"),
                    "CmdURL": e.get("CmdURL"),
                } for e in extra],
            })
    return plan


class PlanChanged(Exception):
    """The fresh prune plan no longer matches the one the user confirmed."""
    def __init__(self, plan):
        super().__init__("plan changed")
        self.plan = plan


def run_prune(max_deletes=None, confirmed=None):
    """Delete recordings beyond each show's limit. Returns (plan, results);
    results is None if the run was skipped for exceeding max_deletes.
    If `confirmed` is given (the CmdURLs the user reviewed), the fresh plan must match it
    exactly, otherwise PlanChanged is raised and nothing is deleted."""
    with _lock:
        plan = build_plan(fetch_series(), load_limits())
        urls = [e["CmdURL"] for g in plan for e in g["Episodes"] if e.get("CmdURL")]
        if confirmed is not None and set(urls) != set(confirmed):
            raise PlanChanged(plan)
        if max_deletes and len(urls) > max_deletes:
            return plan, None
        # the URLs came from the fresh listing above, so they are the allowed set
        results = delete_urls(urls, rerecord=0, allowed=set(urls)) if urls else []
        return plan, results


def load_last_run():
    """The most recent automatic run, saved so it survives restarts (None if there hasn't been one)."""
    try:
        with open(LAST_RUN_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return None
    return data if isinstance(data, dict) else None


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
                plan, results = run_prune(max_deletes=cfg2["max"])
                if results is None:
                    n = sum(len(g["Episodes"]) for g in plan)
                    msg = f"Skipped: {n} recordings exceeds the safety cap of {cfg2['max']}. Review and use Apply keep limits."
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
            write_json(LAST_RUN_PATH, {"when": when, "summary": msg})
        except Exception:
            log.exception("Auto-prune scheduler error; retrying in 60 seconds")
            time.sleep(60)


# The scheduler is started explicitly, never at import, so tests and the `flask run --debug`
# reloader's watcher process don't get a stray copy. Under gunicorn, gunicorn.conf.py starts it
# as soon as the worker boots; under any other server it starts on the first request.
# A lock file in DATA_DIR makes sure only one process per data directory runs it.
# Set DVR_SCHEDULER=0 to turn it off entirely (for example in tests).
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


@app.before_request
def ensure_scheduler():
    if _sched["state"] is None:
        start_scheduler()


# ---------- routes ----------

@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/recordings")
def recordings():
    try:
        return jsonify(fetch_all(fresh=request.args.get("fresh") == "1"))
    except Exception as e:
        return jsonify({"error": friendly(e)}), 502


@app.post("/api/delete")
def delete():
    body = request.get_json(silent=True) or {}
    urls = body.get("cmd_urls")
    if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
        return jsonify({"error": "cmd_urls must be a list of strings"}), 400
    urls = list(dict.fromkeys(urls))
    if len(urls) > MAX_DELETE_BATCH:
        return jsonify({"error": f"Too many recordings in one request (max {MAX_DELETE_BATCH})"}), 400
    try:
        return jsonify(delete_urls(urls, 1 if body.get("rerecord") else 0))
    except Exception as e:
        return jsonify({"error": friendly(e)}), 502


@app.get("/api/limits")
def get_limits():
    cfg = schedule_config()
    return jsonify({
        "limits": load_limits(),
        "auto": {**cfg, "next": next_run_iso(cfg), "last": load_last_run()},
    })


@app.post("/api/limits")
def set_limit():
    body = request.get_json(force=True)
    key = body.get("series_id")
    keep = body.get("keep")
    if not key:
        return jsonify({"error": "series_id required"}), 400
    with _lock:
        limits = load_limits()
        if keep in (None, "", 0):
            limits.pop(key, None)
        else:
            try:
                keep = int(keep)
                if keep < 1:
                    raise ValueError
            except (TypeError, ValueError):
                return jsonify({"error": "keep must be a whole number >= 1"}), 400
            limits[key] = keep
        save_limits(limits)
    return jsonify({"limits": limits})


@app.get("/api/prune/preview")
def prune_preview():
    try:
        return jsonify(build_plan(fetch_series(), load_limits()))
    except Exception as e:
        return jsonify({"error": friendly(e)}), 502


@app.post("/api/prune")
def prune_now():
    """Run the prune the user reviewed. The body must list the exact CmdURLs from the preview;
    if the DVR's recordings changed since then, nothing is deleted and the new plan is returned."""
    body = request.get_json(silent=True)
    urls = body.get("cmd_urls") if isinstance(body, dict) else None
    if not isinstance(urls, list) or not urls or not all(isinstance(u, str) for u in urls):
        return jsonify({"error": "Preview the keep limits first, then confirm the list"}), 400
    try:
        plan, results = run_prune(confirmed=urls)
        return jsonify({"plan": plan, "results": results})
    except PlanChanged as e:
        return jsonify({"error": "The recordings changed since you reviewed them. Review the updated list.",
                        "plan": e.plan}), 409
    except Exception as e:
        return jsonify({"error": friendly(e)}), 502


@app.get("/api/status")
def status():
    try:
        info = engine_info()
        return jsonify({"free": info.get("FreeSpace"), "total": info.get("TotalSpace")})
    except Exception as e:
        return jsonify({"error": friendly(e)}), 502


@app.get("/api/debug")
def debug():
    """Raw DVR data (first series + first episode) to see which fields your DVR really reports."""
    try:
        series = dvr_json(storage_url(refresh=True), list)
        first_series = series[0] if series else None
        first_episode = None
        if first_series and first_series.get("EpisodesURL"):
            check_dvr_url(first_series["EpisodesURL"], "/recorded_files.json")
            eps = dvr_json(first_series["EpisodesURL"], list)
            first_episode = eps[0] if eps else None
        disc = engine_info()
        disc.pop("DeviceAuth", None)  # keep credentials out of the debug output
        return jsonify({"discover": disc, "first_series": first_series, "first_episode": first_episode})
    except Exception as e:
        return jsonify({"error": friendly(e)}), 502


# ---------- settings (DVR address, schedule) ----------

def probe(host):
    """Check that a DVR record engine answers at this address (and is on an allowed network)."""
    info = dvr_json(f"http://{host}/discover.json", dict)
    su = info.get("StorageURL")
    if not su:
        raise RuntimeError("the device answered, but it has no DVR record engine (no StorageURL)")
    check_storage_url(su)
    _pin(su)  # the record engine's address must be on an allowed network too
    return {"name": info.get("FriendlyName"), "model": info.get("ModelNumber"), "free": info.get("FreeSpace")}


@app.get("/api/settings")
def get_settings():
    saved = load_settings().get("dvr_host")
    env = os.environ.get("DVR_HOST") or None
    return jsonify({
        "dvr_host": saved or env or "",
        "source": "ui" if saved else "env" if env else "none",
        "env_dvr_host": env,
        "schedule": schedule_config(),
    })


@lru_cache(maxsize=1)
def timezone_names():
    return sorted(available_timezones())


@app.get("/api/timezones")
def timezones():
    """The timezone list never changes while the app runs, so browsers may cache it for a day."""
    resp = jsonify(timezone_names())
    resp.cache_control.private = True
    resp.cache_control.max_age = 86400
    return resp


def _check_host_from_request():
    host = clean_host((request.get_json(force=True) or {}).get("dvr_host"))
    if not host:
        return None, (jsonify({"error": "Enter an IP address or hostname, like 192.168.1.50"}), 400)
    try:
        return (host, probe(host)), None
    except Exception as e:
        return None, (jsonify({"error": f"Could not reach a DVR at {host}: {friendly(e)}"}), 400)


@app.post("/api/settings/test")
def test_settings():
    ok, err = _check_host_from_request()
    if err:
        return err
    return jsonify({"ok": True, "dvr_host": ok[0], "device": ok[1]})


@app.post("/api/settings")
def update_settings():
    body = request.get_json(force=True) or {}
    settings = load_settings()
    if not (body.get("dvr_host") or "").strip():
        # empty = drop the saved address and go back to the server default
        settings.pop("dvr_host", None)
        save_settings(settings)
        reset_dvr_caches()
        return jsonify({"ok": True, "dvr_host": None})
    ok, err = _check_host_from_request()
    if err:
        return err
    settings["dvr_host"] = ok[0]
    save_settings(settings)
    reset_dvr_caches()
    return jsonify({"ok": True, "dvr_host": ok[0], "device": ok[1]})


@app.post("/api/settings/schedule")
def update_schedule():
    b = request.get_json(force=True) or {}
    t = (b.get("time") or "").strip()
    tz = (b.get("tz") or "").strip()
    try:
        mx = int(b.get("max"))
    except (TypeError, ValueError):
        mx = 0
    if not TIME_RE.match(t):
        return jsonify({"error": "Enter a time like 03:00"}), 400
    if not valid_tz(tz):
        return jsonify({"error": "Unknown timezone. Pick one from the list, like America/Chicago"}), 400
    if mx < 1:
        return jsonify({"error": "The safety cap must be a whole number, 1 or more"}), 400
    st = load_settings()
    st.update({"auto_prune_enabled": bool(b.get("enabled")), "auto_prune_time": t,
               "timezone": tz, "auto_prune_max": mx})
    save_settings(st)
    _wake.set()
    cfg = schedule_config()
    return jsonify({"ok": True, "schedule": cfg, "next": next_run_iso(cfg)})

