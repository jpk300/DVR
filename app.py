import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, request, Response

app = Flask(__name__, static_folder="static", static_url_path="/static")
TIMEOUT = 10
_storage_cache = {}

APP_USER = os.environ.get("APP_USER")
APP_PASS = os.environ.get("APP_PASS")

DATA_DIR = os.environ.get("DATA_DIR", "/data")
LIMITS_PATH = os.path.join(DATA_DIR, "limits.json")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
AUTO_PRUNE_TIME = os.environ.get("AUTO_PRUNE_TIME", "")  # "HH:MM" once a day, e.g. "03:00"; empty = manual only
AUTO_PRUNE_MAX = int(os.environ.get("AUTO_PRUNE_MAX", "25"))  # safety cap per automatic run
TZNAME = os.environ.get("TZ", "UTC")
_lock = threading.Lock()


@app.before_request
def check_auth():
    if not APP_USER:
        return None
    a = request.authorization
    if a and a.username == APP_USER and a.password == APP_PASS:
        return None
    return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="DVR"'})


# ---------- DVR access ----------

def storage_url(refresh=False):
    """Find the record engine's recorded_files.json URL."""
    if not refresh and "url" in _storage_cache:
        return _storage_cache["url"]

    saved = load_settings().get("dvr_host")
    url = None if saved else os.environ.get("DVR_STORAGE_URL")
    if not url:
        host = saved or os.environ.get("DVR_HOST")
        if host:
            r = requests.get(f"http://{host}/discover.json", timeout=TIMEOUT)
            r.raise_for_status()
            url = r.json().get("StorageURL")
        else:
            r = requests.get("https://api.hdhomerun.com/discover", timeout=TIMEOUT)
            r.raise_for_status()
            for dev in r.json():
                if dev.get("StorageURL"):
                    url = dev["StorageURL"]
                    break
    if not url:
        raise RuntimeError("Could not find a DVR record engine. Set DVR_HOST or DVR_STORAGE_URL.")
    _storage_cache["url"] = url
    return url


def fetch_all():
    series = requests.get(storage_url(refresh=True), timeout=TIMEOUT).json()
    out = []
    for s in series:
        eps_url = s.get("EpisodesURL")
        episodes = requests.get(eps_url, timeout=TIMEOUT).json() if eps_url else []
        out.append({
            "SeriesID": s.get("SeriesID"),
            "Title": s.get("Title"),
            "ImageURL": s.get("ImageURL"),
            "Episodes": episodes,
        })
    # Also include shows that have a recording rule but nothing recorded yet,
    # so keep limits can be set before the first recording finishes.
    try:
        have = {o["SeriesID"] for o in out}
        for r in fetch_rules():
            if r["SeriesID"] not in have:
                out.append({"SeriesID": r["SeriesID"], "Title": r["Title"],
                            "ImageURL": r["ImageURL"], "Episodes": [], "Scheduled": True})
                have.add(r["SeriesID"])
    except Exception as e:
        app.logger.warning("Could not load recording rules: %s", e)
    return out


def device_auth():
    auth = os.environ.get("DEVICE_AUTH")  # optional override; concatenate if you have several tuners
    return auth or _engine_info().get("DeviceAuth")


def fetch_rules():
    """Recording rules from Silicondust's cloud, one entry per series."""
    auth = device_auth()
    if not auth:
        return []
    r = requests.get("https://api.hdhomerun.com/api/recording_rules",
                     params={"DeviceAuth": auth}, timeout=TIMEOUT)
    r.raise_for_status()
    seen, out = set(), []
    for rule in (r.json() or []):  # the API returns null when there are no rules
        sid = rule.get("SeriesID")
        if sid and sid not in seen:
            seen.add(sid)
            out.append({"SeriesID": sid, "Title": rule.get("Title"), "ImageURL": rule.get("ImageURL")})
    return out


def delete_urls(cmd_urls, rerecord=0):
    dvr_host = urlparse(storage_url()).hostname
    results = []
    for cmd_url in cmd_urls:
        # Only talk to the DVR we discovered (prevents use as an open proxy)
        if urlparse(cmd_url).hostname != dvr_host:
            results.append({"cmd_url": cmd_url, "ok": False, "error": "host mismatch"})
            continue
        try:
            # requests merges params into any existing query string on CmdURL
            r = requests.post(cmd_url, params={"cmd": "delete", "rerecord": rerecord}, timeout=TIMEOUT)
            results.append({"cmd_url": cmd_url, "ok": r.ok, "status": r.status_code})
        except Exception as e:
            results.append({"cmd_url": cmd_url, "ok": False, "error": str(e)})
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
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = LIMITS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(limits, f, indent=2)
    os.replace(tmp, LIMITS_PATH)


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
                    "FileSize": e.get("FileSize"),
                    "CmdURL": e.get("CmdURL"),
                } for e in extra],
            })
    return plan


def run_prune(max_deletes=None):
    """Delete recordings beyond each show's limit. Returns (plan, results);
    results is None if the run was skipped for exceeding max_deletes."""
    with _lock:
        plan = build_plan(fetch_all(), load_limits())
        urls = [e["CmdURL"] for g in plan for e in g["Episodes"] if e.get("CmdURL")]
        if max_deletes and len(urls) > max_deletes:
            return plan, None
        results = delete_urls(urls, rerecord=0) if urls else []
        return plan, results


_auto = {"next": None, "last": None}


def next_run(now, hh, mm):
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def auto_loop(hh, mm):
    tz = ZoneInfo(TZNAME)
    while True:
        now = datetime.now(tz)
        nxt = next_run(now, hh, mm)
        _auto["next"] = nxt.isoformat()
        time.sleep(max(1, (nxt - now).total_seconds()))
        when = datetime.now(tz).isoformat(timespec="minutes")
        try:
            plan, results = run_prune(max_deletes=AUTO_PRUNE_MAX)
            if results is None:
                n = sum(len(g["Episodes"]) for g in plan)
                msg = f"Skipped: {n} recordings exceeds the safety cap of {AUTO_PRUNE_MAX}. Review and use Apply keep limits."
            else:
                ok = sum(1 for r in results if r["ok"])
                msg = f"Deleted {ok} of {len(results)} recordings."
        except Exception as e:
            msg = f"Failed: {e}"
        _auto["last"] = {"when": when, "summary": msg}
        app.logger.warning("Auto-prune %s: %s", when, msg)


if AUTO_PRUNE_TIME:
    try:
        _hh, _mm = (int(x) for x in AUTO_PRUNE_TIME.split(":"))
        ZoneInfo(TZNAME)
        threading.Thread(target=auto_loop, args=(_hh, _mm), daemon=True).start()
    except Exception as e:
        app.logger.warning("Auto-prune disabled (bad AUTO_PRUNE_TIME or TZ): %s", e)


# ---------- routes ----------

@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/recordings")
def recordings():
    try:
        return jsonify(fetch_all())
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.post("/api/delete")
def delete():
    body = request.get_json(force=True)
    rerecord = 1 if body.get("rerecord") else 0
    return jsonify(delete_urls(body.get("cmd_urls", []), rerecord))


@app.get("/api/limits")
def get_limits():
    return jsonify({
        "limits": load_limits(),
        "auto": {
            "enabled": bool(AUTO_PRUNE_TIME), "time": AUTO_PRUNE_TIME, "tz": TZNAME,
            "next": _auto["next"], "last": _auto["last"], "max": AUTO_PRUNE_MAX,
        },
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
        return jsonify(build_plan(fetch_all(), load_limits()))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.post("/api/prune")
def prune_now():
    try:
        plan, results = run_prune()
        return jsonify({"plan": plan, "results": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


def _engine_info():
    p = urlparse(storage_url())
    return requests.get(f"{p.scheme}://{p.netloc}/discover.json", timeout=TIMEOUT).json()


@app.get("/api/status")
def status():
    try:
        info = _engine_info()
        return jsonify({"free": info.get("FreeSpace"), "total": info.get("TotalSpace")})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.get("/api/debug")
def debug():
    """Raw DVR data (first series + first episode) to see which fields your DVR really reports."""
    try:
        series = requests.get(storage_url(refresh=True), timeout=TIMEOUT).json()
        first_series = series[0] if series else None
        first_episode = None
        if first_series and first_series.get("EpisodesURL"):
            eps = requests.get(first_series["EpisodesURL"], timeout=TIMEOUT).json()
            first_episode = eps[0] if eps else None
        disc = _engine_info()
        disc.pop("DeviceAuth", None)  # keep credentials out of the debug output
        return jsonify({"discover": disc, "first_series": first_series, "first_episode": first_episode})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


# ---------- settings (DVR address) ----------

HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:\d{1,5})?$")


def load_settings():
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_settings(settings):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(settings, f, indent=2)
    os.replace(tmp, SETTINGS_PATH)


def clean_host(raw):
    h = (raw or "").strip()
    for prefix in ("http://", "https://"):
        if h.lower().startswith(prefix):
            h = h[len(prefix):]
    h = h.split("/")[0]
    return h if HOST_RE.match(h) else None


def probe(host):
    """Check that a DVR record engine answers at this address."""
    r = requests.get(f"http://{host}/discover.json", timeout=5)
    r.raise_for_status()
    info = r.json()
    if not info.get("StorageURL"):
        raise RuntimeError("the device answered, but it has no DVR record engine (no StorageURL)")
    return {"name": info.get("FriendlyName"), "model": info.get("ModelNumber"), "free": info.get("FreeSpace")}


@app.get("/api/settings")
def get_settings():
    saved = load_settings().get("dvr_host")
    env = os.environ.get("DVR_HOST") or None
    return jsonify({
        "dvr_host": saved or env or "",
        "source": "ui" if saved else "env" if env else "none",
        "env_dvr_host": env,
    })


def _check_host_from_request():
    host = clean_host((request.get_json(force=True) or {}).get("dvr_host"))
    if not host:
        return None, (jsonify({"error": "Enter an IP address or hostname, like 192.168.1.130"}), 400)
    try:
        return (host, probe(host)), None
    except Exception as e:
        return None, (jsonify({"error": f"Could not reach a DVR at {host}: {e}"}), 400)


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
        _storage_cache.clear()
        return jsonify({"ok": True, "dvr_host": None})
    ok, err = _check_host_from_request()
    if err:
        return err
    settings["dvr_host"] = ok[0]
    save_settings(settings)
    _storage_cache.clear()
    return jsonify({"ok": True, "dvr_host": ok[0], "device": ok[1]})
