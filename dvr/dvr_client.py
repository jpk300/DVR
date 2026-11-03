"""Every request to the DVR and to Silicondust's cloud.

Every request to the DVR goes through dvr_fetch(), which:
  * only speaks plain http:// with no credentials in the URL
  * resolves the hostname itself and requires EVERY address to be on an allowed
    private network (default: RFC1918 + IPv6 ULA; extend with DVR_ALLOWED_NETWORKS)
  * connects to that exact validated IP (so DNS can't change between check and use)
  * never follows redirects, caps response size, and uses short timeouts
URLs found inside DVR responses are checked with check_dvr_url() before they are used.
"""
import ipaddress
import json
import logging
import os
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter

from .config import DVR_TIMEOUT, LIST_TTL, MAX_BODY_BYTES, MAX_WORKERS, RULES_TTL
from .storage import load_settings

log = logging.getLogger(__name__)

HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:\d{1,5})?$")


def clean_host(raw):
    h = (raw or "").strip()
    for prefix in ("http://", "https://"):
        if h.lower().startswith(prefix):
            h = h[len(prefix):]
    h = h.split("/")[0]
    return h if HOST_RE.match(h) else None


# ---------- safe DVR access ----------

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


def probe(host):
    """Check that a DVR record engine answers at this address (and is on an allowed network)."""
    info = dvr_json(f"http://{host}/discover.json", dict)
    su = info.get("StorageURL")
    if not su:
        raise RuntimeError("the device answered, but it has no DVR record engine (no StorageURL)")
    check_storage_url(su)
    _pin(su)  # the record engine's address must be on an allowed network too
    return {"name": info.get("FriendlyName"), "model": info.get("ModelNumber"), "free": info.get("FreeSpace")}
