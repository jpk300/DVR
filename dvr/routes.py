"""The HTTP API, as a Flask blueprint. Shared objects come from current_app.extensions["dvr"]
(see Services in __init__.py), never from module globals."""
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import available_timezones

from flask import Blueprint, Response, current_app, jsonify, request

from .config import HISTORY_MAX, MAX_DELETE_BATCH
from .dvr_client import episode_index, friendly
from .pruning import PlanChanged, build_plan, normalize_rule, normalized_limits, run_prune
from .scheduler import next_run_iso, schedule_config
from .storage import TIME_RE, clean_host, valid_tz

bp = Blueprint("dvr", __name__)

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


def svc():
    return current_app.extensions["dvr"]


def error(msg, status):
    return jsonify({"error": msg}), status


def with_protection(shows):
    """Copies of the shows with Protected set on each recording. Copies, because the client
    caches the list it returns and protection can change between requests."""
    keys = svc().storage.protected()
    return [{**s, "Episodes": [{**e, "Protected": e.get("Key") in keys} for e in s["Episodes"]]}
            for s in shows]


def json_body():
    """The request's JSON object, or None if the body isn't one (sent as application/json)."""
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


@bp.before_app_request
def check_auth():
    cfg = svc().config
    if not cfg.app_user:
        return None
    a = request.authorization
    if a and a.username == cfg.app_user and a.password == cfg.app_pass:
        return None
    return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="DVR"'})


@bp.before_app_request
def ensure_scheduler():
    svc().scheduler.ensure_started()


@bp.after_app_request
def security_headers(resp):
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")  # older browsers that ignore frame-ancestors
    return resp


# ---------- page ----------

@bp.get("/")
def index():
    return current_app.send_static_file("index.html")


# ---------- recordings ----------

@bp.get("/api/recordings")
def recordings():
    try:
        return jsonify(with_protection(svc().client.fetch_all(fresh=request.args.get("fresh") == "1")))
    except Exception as e:
        return error(friendly(e), 502)


@bp.get("/api/show")
def show():
    """One show with its recordings, so the page can refresh it without reloading every show."""
    try:
        s = svc().client.fetch_one_series(request.args.get("series_id") or "")
    except Exception as e:
        return error(friendly(e), 502)
    if s is None:
        return error("The DVR has no recordings of this show", 404)
    return jsonify(with_protection([s])[0])


@bp.post("/api/delete")
def delete():
    """Delete recordings the user picked. With series_id, only that show is re-read from the DVR
    to check the URLs (two requests instead of one per show), and the updated show is returned."""
    body = json_body() or {}
    urls, sid = body.get("cmd_urls"), body.get("series_id")
    if not isinstance(urls, list) or not all(isinstance(u, str) for u in urls):
        return error("cmd_urls must be a list of strings", 400)
    if sid is not None and not isinstance(sid, str):
        return error("series_id must be a string", 400)
    urls = list(dict.fromkeys(urls))
    if len(urls) > MAX_DELETE_BATCH:
        return error(f"Too many recordings in one request (max {MAX_DELETE_BATCH})", 400)
    rerecord = 1 if body.get("rerecord") else 0
    s = svc()
    try:
        allowed = episode_index(s.client.load_shows(sid)) if sid else None
        results = s.client.delete_urls(urls, rerecord, allowed, protected=set(s.storage.protected()))
        s.history.record(results, "manual", rerecord=bool(rerecord))
        out = {"results": results}
        if sid:
            fresh = s.client.fetch_one_series(sid)
            out["show"] = with_protection([fresh])[0] if fresh else None
        return jsonify(out)
    except Exception as e:
        return error(friendly(e), 502)


@bp.get("/api/status")
def status():
    try:
        info = svc().client.engine_info()
        return jsonify({"free": info.get("FreeSpace"), "total": info.get("TotalSpace")})
    except Exception as e:
        return error(friendly(e), 502)


@bp.get("/api/debug")
def debug():
    """Raw DVR data (first series + first episode) to see which fields your DVR really reports."""
    c = svc().client
    try:
        series = c.json(c.storage_url(refresh=True), list)
        first_series = series[0] if series else None
        first_episode = None
        if first_series and first_series.get("EpisodesURL"):
            c.check_dvr_url(first_series["EpisodesURL"], "/recorded_files.json")
            eps = c.json(first_series["EpisodesURL"], list)
            first_episode = eps[0] if eps else None
        disc = c.engine_info()
        disc.pop("DeviceAuth", None)  # keep credentials out of the debug output
        return jsonify({"discover": disc, "first_series": first_series, "first_episode": first_episode})
    except Exception as e:
        return error(friendly(e), 502)


# ---------- keep rules ----------

@bp.get("/api/limits")
def get_limits():
    s = svc()
    cfg = schedule_config(s.storage.settings())
    return jsonify({
        "limits": normalized_limits(s.storage.limits()),
        "auto": {**cfg, "next": next_run_iso(cfg), "last": s.storage.last_run()},
    })


@bp.post("/api/limits")
def set_limit():
    """Set one show's keep rule: {series_id, keep: N or null, delete_watched: bool}.
    A rule that keeps everything removes the show's entry."""
    body = json_body()
    if body is None:
        return error("Send a JSON object", 400)
    key = body.get("series_id")
    if not key or not isinstance(key, str):
        return error("series_id required", 400)
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
            return error("Keep must be a whole number, 1 or more", 400)
    rule = normalize_rule({"keep": keep, "delete_watched": bool(body.get("delete_watched"))})
    st = svc().storage
    with st.data_lock:
        limits = st.limits()
        if rule:
            limits[key] = rule
        else:
            limits.pop(key, None)
        st.save_limits(limits)
    return jsonify({"limits": normalized_limits(limits)})


@bp.get("/api/prune/preview")
def prune_preview():
    """What the keep rules would delete now, for every show or only ?series_id=."""
    sid = request.args.get("series_id") or None
    s = svc()
    try:
        return jsonify(build_plan(s.client.load_shows(sid), s.storage.limits(), only=sid,
                                  protected=set(s.storage.protected())))
    except Exception as e:
        return error(friendly(e), 502)


@bp.post("/api/prune")
def prune_now():
    """Run the plan the user reviewed. The body must list the exact CmdURLs from the preview
    (and the same series_id, if the preview was for one show); if the DVR's recordings changed
    since then, nothing is deleted and the new plan is returned."""
    body = json_body() or {}
    urls, sid = body.get("cmd_urls"), body.get("series_id")
    if not isinstance(urls, list) or not urls or not all(isinstance(u, str) for u in urls):
        return error("Preview the keep rules first, then confirm the list", 400)
    if sid is not None and not isinstance(sid, str):
        return error("series_id must be a string", 400)
    try:
        plan, results = run_prune(svc(), confirmed=urls, series_id=sid)
        return jsonify({"plan": plan, "results": results})
    except PlanChanged as e:
        return jsonify({"error": "The recordings changed since you reviewed them. Review the updated list.",
                        "plan": e.plan}), 409
    except Exception as e:
        return error(friendly(e), 502)


@bp.post("/api/protect")
def protect():
    """Protect one recording from deletion, or remove its protection:
    {series_id, key, protected: bool}. Protecting checks the recording is on the DVR (two requests)
    and stores what it is, so the list stays readable; unprotecting works even if it is gone."""
    body = json_body()
    if body is None:
        return error("Send a JSON object", 400)
    key, sid, on = body.get("key"), body.get("series_id"), body.get("protected")
    if not key or not isinstance(key, str) or not isinstance(on, bool):
        return error("key (a string) and protected (true or false) are required", 400)
    s = svc()
    if not on:
        s.storage.set_protected(key, None)
        return jsonify({"key": key, "protected": False})
    if not sid or not isinstance(sid, str):
        return error("series_id is required to protect a recording", 400)
    try:
        show = s.client.fetch_one_series(sid)
    except Exception as e:
        return error(friendly(e), 502)
    ep = next((e for e in (show or {}).get("Episodes", []) if e.get("Key") == key), None)
    if ep is None:
        return error("That recording isn't on the DVR any more", 404)
    s.storage.set_protected(key, {
        "show": show.get("Title"), "series_id": sid, "episode": ep.get("EpisodeTitle") or ep.get("Title"),
        "number": ep.get("EpisodeNumber"), "start": ep.get("StartTime"),
        "since": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    return jsonify({"key": key, "protected": True})


@bp.get("/api/history")
def get_history():
    """Recent deletions, newest first (?limit=, default and max HISTORY_MAX)."""
    try:
        limit = max(1, min(HISTORY_MAX, int(request.args.get("limit", HISTORY_MAX))))
    except ValueError:
        limit = HISTORY_MAX
    return jsonify({"entries": svc().history.load(limit), "max": HISTORY_MAX})


# ---------- settings (DVR address, schedule) ----------

@bp.get("/api/settings")
def get_settings():
    st = svc().storage.settings()
    return jsonify({
        "dvr_host": st.get("dvr_host") or "",
        "schedule": schedule_config(st),
        "tz_saved": bool(st.get("timezone")),
    })


@lru_cache(maxsize=1)
def timezone_names():
    return sorted(available_timezones())


@bp.get("/api/timezones")
def timezones():
    """The timezone list never changes while the app runs, so browsers may cache it for a day."""
    resp = jsonify(timezone_names())
    resp.cache_control.private = True
    resp.cache_control.max_age = 86400
    return resp


def _check_host(body):
    """(host, device info) for the address in the request, or (None, error response)."""
    host = clean_host((body or {}).get("dvr_host"))
    if not host:
        return None, error("Enter an IP address or hostname, like 192.168.1.50", 400)
    try:
        info = svc().client.probe(host)
    except Exception as e:
        return None, error(f"Could not reach a DVR at {host}: {friendly(e)}", 400)
    return (host, info), None


@bp.post("/api/settings/test")
def test_settings():
    ok, err = _check_host(json_body())
    if err:
        return err
    return jsonify({"ok": True, "dvr_host": ok[0], "device": ok[1]})


@bp.post("/api/settings")
def update_settings():
    body = json_body() or {}
    s = svc()
    if not (body.get("dvr_host") or "").strip():
        # empty = forget the saved address (the app falls back to cloud discovery)
        s.storage.update_settings(remove=("dvr_host",))
        s.client.reset_caches()
        return jsonify({"ok": True, "dvr_host": None})
    ok, err = _check_host(body)
    if err:
        return err
    s.storage.update_settings({"dvr_host": ok[0]})
    s.client.reset_caches()
    return jsonify({"ok": True, "dvr_host": ok[0], "device": ok[1]})


@bp.post("/api/settings/schedule")
def update_schedule():
    b = json_body() or {}
    t = (b.get("time") or "").strip()
    tz = (b.get("tz") or "").strip()
    try:
        mx = int(b.get("max"))
    except (TypeError, ValueError):
        mx = 0
    if not TIME_RE.match(t):
        return error("Enter a time like 03:00", 400)
    if not valid_tz(tz):
        return error("Unknown timezone. Pick one from the list, like America/Chicago", 400)
    if mx < 1:
        return error("The safety cap must be a whole number, 1 or more", 400)
    s = svc()
    st = s.storage.update_settings({"auto_prune_enabled": bool(b.get("enabled")), "auto_prune_time": t,
                                    "timezone": tz, "auto_prune_max": mx})
    s.scheduler.wake()
    cfg = schedule_config(st)
    return jsonify({"ok": True, "schedule": cfg, "next": next_run_iso(cfg)})
