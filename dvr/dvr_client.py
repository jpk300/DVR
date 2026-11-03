"""Every request to the DVR and to Silicondust's cloud.

Every request to the DVR goes through DvrClient.fetch(), which:
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
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter

from .config import DVR_TIMEOUT, LIST_TTL, MAX_BODY_BYTES, MAX_WORKERS, RULES_TTL
from .storage import clean_host

log = logging.getLogger(__name__)

EP_FIELDS = ("ProgramID", "Title", "EpisodeTitle", "EpisodeNumber", "Synopsis",
             "StartTime", "EndTime", "RecordStartTime", "RecordEndTime", "CmdURL")

# The DVR reports playback progress as Resume (seconds into the recording). It sets Resume to
# this value once an episode has been watched to the end.
RESUME_WATCHED = 4294967295


class UnsafeURL(ValueError):
    pass


# ---------- helpers with no state ----------

def friendly(e):
    if isinstance(e, requests.exceptions.Timeout):
        return "the DVR took too long to respond"
    if isinstance(e, requests.exceptions.ConnectionError):
        return "could not connect to the DVR"
    return str(e) or e.__class__.__name__


def safe_image(url):
    return url if isinstance(url, str) and url.lower().startswith(("http://", "https://")) else None


def check_storage_url(url):
    p = urlparse(url)
    if p.scheme != "http" or p.username or p.password or not p.hostname:
        raise UnsafeURL("the DVR reported an unsafe storage URL")
    if p.path != "/recorded_files.json":
        raise UnsafeURL(f"the DVR reported an unexpected storage path {p.path!r}")
    return url


def watched_fields(ep):
    """Watched: True / False, or None when the DVR doesn't report progress for this recording.
    ResumeSeconds: how far into a partly watched recording playback got (else None)."""
    r = ep.get("Resume")
    if isinstance(r, bool) or not isinstance(r, int):
        return {"Watched": None, "ResumeSeconds": None}
    if r == RESUME_WATCHED:
        return {"Watched": True, "ResumeSeconds": None}
    return {"Watched": False, "ResumeSeconds": r if r > 0 else None}


def recording_key(ep):
    """A stable id for one recording that doesn't depend on the DVR's address (so protection
    survives the DVR changing IP): the program plus when it aired, or else the DVR's own id."""
    pid, start = ep.get("ProgramID"), ep.get("StartTime")
    if pid and start:
        return f"{pid}@{start}"
    ids = parse_qs(urlparse(ep.get("CmdURL") or "").query).get("id")
    return f"id:{ids[0]}" if ids else (ep.get("CmdURL") or "")


def episode_index(series):
    """Map each recording's CmdURL to what history needs to describe it, and its Key."""
    return {e["CmdURL"]: {"Show": s.get("Title"), "SeriesID": s.get("SeriesID"), "Key": e.get("Key"),
                          "EpisodeTitle": e.get("EpisodeTitle") or e.get("Title"),
                          "EpisodeNumber": e.get("EpisodeNumber"), "StartTime": e.get("StartTime")}
            for s in series for e in s["Episodes"] if e.get("CmdURL")}


class DvrClient:
    """Talks to one DVR (the address in settings.json) and holds the HTTP session and caches."""

    def __init__(self, storage, allowed_networks, device_auth_override=None):
        self.storage = storage
        self.networks = tuple(allowed_networks)
        self.device_auth_override = device_auth_override
        self.session = requests.Session()
        self.session.trust_env = False  # ignore HTTP(S)_PROXY and .netrc for LAN traffic
        self.session.mount("http://", HTTPAdapter(pool_connections=4, pool_maxsize=MAX_WORKERS + 2))
        self._storage_url = None
        self._list_cache = {"t": 0.0, "data": None}
        self._rules_cache = {"t": 0.0, "data": None}
        self._cache_lock = threading.Lock()

    # ---------- safe DVR access ----------
    def resolve_allowed(self, hostname, port):
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
            if not any(ip in net for net in self.networks):
                raise UnsafeURL(f"{hostname} resolves to {ip}, which is outside the allowed networks "
                                "(set DVR_ALLOWED_NETWORKS to permit it)")
        ips.sort(key=lambda i: i.version)  # prefer IPv4
        return ips[0]

    def pin(self, url, params=None):
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
        ip = self.resolve_allowed(p.hostname, port or 80)
        netloc = f"[{ip}]" if ip.version == 6 else str(ip)
        if port:
            netloc += f":{port}"
        query = p.query
        if params:
            query = urlencode(parse_qsl(p.query, keep_blank_values=True) + list(params.items()))
        return urlunparse(("http", netloc, p.path, "", query, "")), p.netloc

    @staticmethod
    def _read_limited(r):
        chunks, total = [], 0
        for chunk in r.iter_content(65536):
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                raise RuntimeError("the DVR sent a response that is too large")
            chunks.append(chunk)
        return b"".join(chunks)

    def fetch(self, url, method="GET", params=None):
        pinned, host_header = self.pin(url, params)
        with self.session.request(method, pinned, headers={"Host": host_header, "User-Agent": "hdhr-dvr-manager"},
                                  timeout=DVR_TIMEOUT, allow_redirects=False, stream=True) as r:
            if 300 <= r.status_code < 400:
                raise UnsafeURL("the DVR tried to redirect; refusing to follow")
            return r.status_code, self._read_limited(r)

    def json(self, url, expect=None):
        status, body = self.fetch(url)
        if status >= 400:
            raise RuntimeError(f"the DVR returned HTTP {status}")
        try:
            data = json.loads(body)
        except ValueError:
            raise RuntimeError("the DVR returned something that isn't JSON") from None
        if expect and not isinstance(data, expect):
            raise RuntimeError("the DVR returned an unexpected response")
        return data

    # ---------- discovery ----------
    def reset_caches(self):
        """Forget everything about the current DVR (after its address changes)."""
        with self._cache_lock:
            self._storage_url = None
            self._list_cache.update(t=0.0, data=None)
            self._rules_cache.update(t=0.0, data=None)

    def invalidate_list(self):
        self._list_cache["t"] = 0.0

    def configured_host(self):
        """The DVR address from settings.json, validated (None if none is saved)."""
        raw = self.storage.settings().get("dvr_host")
        if not raw:
            return None
        host = clean_host(raw)
        if not host:
            raise RuntimeError("The saved DVR address is not valid. Fix it in Settings.")
        return host

    def storage_url(self, refresh=False):
        """The record engine's recorded_files.json URL (validated, cached until refresh)."""
        if not refresh and self._storage_url:
            return self._storage_url
        host = self.configured_host()
        url = None
        if host:
            url = self.json(f"http://{host}/discover.json", dict).get("StorageURL")
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
        self._storage_url = url
        return url

    def check_dvr_url(self, url, path):
        """A URL taken from a DVR response must point at the same host:port as the storage URL,
        use plain http, carry no credentials, and have the expected path."""
        p = urlparse(url)
        try:
            port = p.port or 80
        except ValueError:
            raise UnsafeURL("invalid port") from None
        base = urlparse(self.storage_url())
        if p.scheme != "http" or p.username or p.password:
            raise UnsafeURL("URL must be plain http without credentials")
        if (p.hostname or "").lower() != (base.hostname or "").lower() or port != (base.port or 80):
            raise UnsafeURL("URL does not point at the DVR")
        if p.path != path:
            raise UnsafeURL(f"unexpected path {p.path!r}")
        return url

    def engine_info(self):
        host = self.configured_host()
        base = f"http://{host}" if host else f"http://{urlparse(self.storage_url()).netloc}"
        return self.json(base + "/discover.json", dict)

    def probe(self, host):
        """Check that a DVR record engine answers at this address (and is on an allowed network)."""
        info = self.json(f"http://{host}/discover.json", dict)
        su = info.get("StorageURL")
        if not su:
            raise RuntimeError("the device answered, but it has no DVR record engine (no StorageURL)")
        check_storage_url(su)
        self.pin(su)  # the record engine's address must be on an allowed network too
        return {"name": info.get("FriendlyName"), "model": info.get("ModelNumber"), "free": info.get("FreeSpace")}

    # ---------- recordings ----------
    def load_series(self, s):
        """One show from the DVR's list, with its recordings (one request)."""
        entry = {"SeriesID": s.get("SeriesID"), "Title": s.get("Title"),
                 "ImageURL": safe_image(s.get("ImageURL")), "Episodes": []}
        eps_url = s.get("EpisodesURL")
        if not eps_url:
            return entry
        try:
            self.check_dvr_url(eps_url, "/recorded_files.json")
            raw = self.json(eps_url, list)
        except Exception as e:
            entry["Error"] = friendly(e)
            log.warning("Could not load episodes for %r: %s", s.get("Title"), entry["Error"])
            return entry
        skipped = 0
        for ep in raw:
            if not isinstance(ep, dict):
                continue
            try:
                self.check_dvr_url(ep.get("CmdURL") or "", "/recorded/cmd")
            except Exception:
                skipped += 1
                continue
            entry["Episodes"].append({**{k: ep.get(k) for k in EP_FIELDS}, **watched_fields(ep),
                                      "Key": recording_key(ep)})
        if skipped:
            log.warning("Hid %d recording(s) of %r with unexpected URLs", skipped, s.get("Title"))
        return entry

    def series_list(self):
        """The DVR's list of shows. Uses the cached record-engine URL, and only re-discovers it
        (one extra request) if that fails, for example because the DVR got a new IP address."""
        if self._storage_url:
            try:
                return [s for s in self.json(self.storage_url(), list) if isinstance(s, dict)]
            except Exception as e:
                log.info("Cached record engine URL failed (%s); discovering it again", friendly(e))
        return [s for s in self.json(self.storage_url(refresh=True), list) if isinstance(s, dict)]

    def fetch_series(self):
        """Every show with its recordings, loaded with bounded concurrency. A show that fails
        to load comes back with an Error and no recordings instead of failing the whole list."""
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            return list(ex.map(self.load_series, self.series_list()))

    def fetch_one_series(self, series_id):
        """One show with its recordings (two DVR requests), or None if the DVR has no recordings of it."""
        for s in self.series_list():
            if s.get("SeriesID") == series_id:
                return self.load_series(s)
        return None

    def load_shows(self, series_id=None):
        """All shows, or just one (two DVR requests instead of one per show)."""
        if series_id is None:
            return self.fetch_series()
        s = self.fetch_one_series(series_id)
        return [s] if s else []

    def device_auth(self):
        return self.device_auth_override or self.engine_info().get("DeviceAuth")

    def fetch_rules(self, fresh=False):
        """Recording rules from Silicondust's cloud, one entry per series (cached)."""
        now, rc = time.time(), self._rules_cache
        if not fresh and rc["data"] is not None and now - rc["t"] < RULES_TTL:
            return rc["data"]
        out, auth = [], self.device_auth()
        if auth:
            r = requests.get("https://api.hdhomerun.com/api/recording_rules", params={"DeviceAuth": auth},
                             timeout=DVR_TIMEOUT, allow_redirects=False)
            r.raise_for_status()
            seen = set()
            for rule in (r.json() or []):  # the API returns null when there are no rules
                sid = rule.get("SeriesID")
                if sid and sid not in seen:
                    seen.add(sid)
                    out.append({"SeriesID": sid, "Title": rule.get("Title"),
                                "ImageURL": safe_image(rule.get("ImageURL"))})
        rc.update(t=now, data=out)
        return out

    def _safe_rules(self, fresh):
        try:
            return self.fetch_rules(fresh)
        except Exception as e:
            log.warning("Could not load recording rules: %s", friendly(e))
            return []

    def fetch_all(self, fresh=False):
        """Recorded shows plus shows that only have a recording rule so far (cached for LIST_TTL)."""
        with self._cache_lock:
            now, lc = time.time(), self._list_cache
            if not fresh and lc["data"] is not None and now - lc["t"] < LIST_TTL:
                return lc["data"]
            with ThreadPoolExecutor(max_workers=1) as rex:
                rules_future = rex.submit(self._safe_rules, fresh)
                out = self.fetch_series()
                rules = rules_future.result()
            have = {o["SeriesID"] for o in out}
            for r in rules:
                if r["SeriesID"] not in have:
                    out.append({"SeriesID": r["SeriesID"], "Title": r["Title"],
                                "ImageURL": r["ImageURL"], "Episodes": [], "Scheduled": True})
                    have.add(r["SeriesID"])
            lc.update(t=time.time(), data=out)
            return out

    def delete_urls(self, cmd_urls, rerecord=0, allowed=None, protected=frozenset()):
        """Delete recordings. `allowed` maps each deletable CmdURL to its details (episode_index());
        it must come from a fresh server-side DVR listing, and is built from all shows if not given.
        URLs not in it are refused, as are recordings whose Key is in `protected`, and each must
        still pass the scheme / host / port / path checks.
        Each result carries the recording's details, and attempted=False if it was refused."""
        if allowed is None:
            allowed = episode_index(self.fetch_series())
        results = []
        for u in cmd_urls:
            if u not in allowed:
                results.append({"cmd_url": u, "ok": False, "attempted": False,
                                "error": "not a current recording on the DVR"})
                continue
            meta = allowed[u]
            if meta.get("Key") in protected:
                results.append({"cmd_url": u, "ok": False, "attempted": False, "protected": True,
                                "error": "this recording is protected", **meta})
                continue
            try:
                self.check_dvr_url(u, "/recorded/cmd")
                status, _ = self.fetch(u, method="POST", params={"cmd": "delete", "rerecord": rerecord})
                results.append({"cmd_url": u, "ok": status < 400, "attempted": True, "status": status,
                                **({} if status < 400 else {"error": f"the DVR returned HTTP {status}"}), **meta})
            except Exception as e:
                results.append({"cmd_url": u, "ok": False, "attempted": True, "error": friendly(e), **meta})
        self.invalidate_list()
        return results
