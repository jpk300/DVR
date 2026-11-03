"""The Flask app and its HTTP API."""
import os
from functools import lru_cache
from zoneinfo import available_timezones

from flask import Flask, Response, jsonify, request

from . import history, scheduler
from .config import APP_PASS, APP_USER, HISTORY_MAX, MAX_DELETE_BATCH, MAX_REQUEST_BYTES
from .dvr_client import (check_dvr_url, clean_host, delete_urls, dvr_json, engine_info, episode_index,
                         fetch_all, fetch_one_series, friendly, probe, reset_dvr_caches, storage_url)
from .pruning import PlanChanged, build_plan, load_shows, normalize_rule, normalized_limits, run_prune
from .scheduler import TIME_RE, next_run_iso, schedule_config, valid_tz
from .storage import data_lock, load_last_run, load_limits, load_settings, save_limits, save_settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__, static_folder=os.path.join(ROOT, "static"), static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES

# The page loads its script and styles from /static only, so it can run under a strict policy.
# Posters come from the DVR or Silicondust's image servers, so images may load from any http(s) host.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' http: https: data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


@app.before_request
def check_auth():
    if not APP_USER:
        return None
    a = request.authorization
    if a and a.username == APP_USER and a.password == APP_PASS:
        return None
    return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="DVR"'})


@app.before_request
def ensure_scheduler():
    scheduler.ensure_started()


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")  # older browsers that ignore frame-ancestors
    return resp


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


@app.get("/api/show")
def show():
    """One show with its recordings, so the page can refresh it without reloading every show."""
    sid = request.args.get("series_id") or ""
    try:
        s = fetch_one_series(sid)
    except Exception as e:
        return jsonify({"error": friendly(e)}), 502
    if s is None:
        return jsonify({"error": "The DVR has no recordings of this show"}), 404
    return jsonify(s)


@app.post("/api/delete")
def delete():
    """Delete recordings the user picked. With series_id, only that show is re-read from the DVR
    to check the URLs (two requests instead of one per show), and the updated show is returned."""
    body = request.get_json(silent=True) or {}
    urls = body.get("cmd_urls")
    sid = body.get("series_id")
    if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
        return jsonify({"error": "cmd_urls must be a list of strings"}), 400
    if sid is not None and not isinstance(sid, str):
        return jsonify({"error": "series_id must be a string"}), 400
    urls = list(dict.fromkeys(urls))
    if len(urls) > MAX_DELETE_BATCH:
        return jsonify({"error": f"Too many recordings in one request (max {MAX_DELETE_BATCH})"}), 400
    rerecord = 1 if body.get("rerecord") else 0
    try:
        allowed = episode_index(load_shows(sid)) if sid else None
        results = delete_urls(urls, rerecord, allowed)
        history.record(results, "manual", rerecord=bool(rerecord))
        out = {"results": results}
        if sid:
            out["show"] = fetch_one_series(sid)
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": friendly(e)}), 502


@app.get("/api/limits")
def get_limits():
    cfg = schedule_config()
    return jsonify({
        "limits": normalized_limits(),
        "auto": {**cfg, "next": next_run_iso(cfg), "last": load_last_run()},
    })


@app.post("/api/limits")
def set_limit():
    """Set one show's keep rule: {series_id, keep: N or null, delete_watched: bool}.
    A rule that keeps everything removes the show's entry."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Send a JSON object"}), 400
    key = body.get("series_id")
    if not key or not isinstance(key, str):
        return jsonify({"error": "series_id required"}), 400
    keep = body.get("keep")
    if keep in ("", 0):
        keep = None
    if keep is not None:
        try:
            if isinstance(keep, bool):
                raise ValueError
            keep = int(keep)
            if keep < 1:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "Keep must be a whole number, 1 or more"}), 400
    rule = normalize_rule({"keep": keep, "delete_watched": bool(body.get("delete_watched"))})
    with data_lock:
        limits = load_limits()
        if rule:
            limits[key] = rule
        else:
            limits.pop(key, None)
        save_limits(limits)
    return jsonify({"limits": normalized_limits(limits)})


@app.get("/api/prune/preview")
def prune_preview():
    """What the keep rules would delete now, for every show or only ?series_id=."""
    sid = request.args.get("series_id") or None
    try:
        return jsonify(build_plan(load_shows(sid), load_limits(), only=sid))
    except Exception as e:
        return jsonify({"error": friendly(e)}), 502


@app.post("/api/prune")
def prune_now():
    """Run the plan the user reviewed. The body must list the exact CmdURLs from the preview
    (and the same series_id, if the preview was for one show); if the DVR's recordings changed
    since then, nothing is deleted and the new plan is returned."""
    body = request.get_json(silent=True)
    urls = body.get("cmd_urls") if isinstance(body, dict) else None
    sid = body.get("series_id") if isinstance(body, dict) else None
    if not isinstance(urls, list) or not urls or not all(isinstance(u, str) for u in urls):
        return jsonify({"error": "Preview the keep rules first, then confirm the list"}), 400
    if sid is not None and not isinstance(sid, str):
        return jsonify({"error": "series_id must be a string"}), 400
    try:
        plan, results = run_prune(confirmed=urls, series_id=sid)
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

@app.get("/api/settings")
def get_settings():
    saved = load_settings().get("dvr_host")
    env = os.environ.get("DVR_HOST") or None
    return jsonify({
        "dvr_host": saved or env or "",
        "source": "ui" if saved else "env" if env else "none",
        "env_dvr_host": env,
        "schedule": schedule_config(),
        "tz_saved": bool(load_settings().get("timezone")),
    })


@app.get("/api/history")
def get_history():
    """Recent deletions, newest first (?limit=, default and max HISTORY_MAX)."""
    try:
        limit = max(1, min(HISTORY_MAX, int(request.args.get("limit", HISTORY_MAX))))
    except ValueError:
        limit = HISTORY_MAX
    return jsonify({"entries": history.load(limit), "max": HISTORY_MAX})


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
    scheduler.wake()
    cfg = schedule_config()
    return jsonify({"ok": True, "schedule": cfg, "next": next_run_iso(cfg)})
