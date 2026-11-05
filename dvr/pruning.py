"""Keep rules: work out which recordings each show's rule says to remove, and delete them.

A rule is {"keep": N or None, "delete_watched": bool}:
  * keep N                    -> keep the newest N recordings, delete older ones
  * delete_watched            -> delete every watched recording, keep all unwatched ones
  * delete_watched + keep N   -> delete watched recordings, and keep only the newest N unwatched
Shows with no rule keep everything. Recordings whose watched status the DVR doesn't report
are treated as unwatched, so a watched rule never deletes them.
Protected recordings are set aside before a rule is applied: they are never deleted and don't
count toward N, so "keep the newest 2" keeps the 2 newest unprotected recordings plus every
protected one.
"""
import logging

from .dvr_client import episode_index

log = logging.getLogger(__name__)


def series_key(s):
    return s.get("SeriesID") or s.get("Title")


def new_show_keep(settings):
    """The "keep the newest N" rule new shows get (Settings > New shows), or None if it's off."""
    n = settings.get("new_show_keep")
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 1 else None


def adopt_new_shows(storage, shows):
    """Give shows the app hasn't seen before the new-show rule, and remember every show seen.
    Call with storage.data_lock held. The first time (no known_shows.json yet) every show is only
    remembered, so turning the rule on never changes shows you already had. A show that already
    has a rule keeps it. Returns the ids that were given the rule."""
    ids = {series_key(s) for s in shows if series_key(s)}
    known = storage.known_shows()
    if known is not None and ids <= set(known):
        return []
    adopted = []
    keep = new_show_keep(storage.settings())
    if known is not None and keep:
        limits = storage.limits()
        for sid in sorted(ids - set(known)):
            if sid not in limits:
                limits[sid] = {"keep": keep, "delete_watched": False}
                adopted.append(sid)
        if adopted:
            storage.save_limits(limits)
            titles = {series_key(s): s.get("Title") for s in shows}
            log.info("New shows given the rule \"keep the newest %d\": %s", keep,
                     ", ".join(repr(titles.get(sid) or sid) for sid in adopted))
    storage.save_known_shows(set(known or []) | ids)
    return adopted


def normalize_rule(value):
    """A stored rule as {"keep": int | None, "delete_watched": bool}, or None for "keep everything".
    Older limits.json files store a bare number, which means "keep the newest N"."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return {"keep": value, "delete_watched": False} if value >= 1 else None
    if isinstance(value, dict):
        keep = value.get("keep")
        if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
            keep = None
        watched = bool(value.get("delete_watched"))
        if keep is None and not watched:
            return None
        return {"keep": keep, "delete_watched": watched}
    return None


def normalized_limits(limits):
    """Every stored rule in the current format (rules that mean "keep everything" are dropped)."""
    out = {}
    for k, v in limits.items():
        rule = normalize_rule(v)
        if rule:
            out[k] = rule
    return out


def to_delete(episodes, rule, protected=frozenset()):
    """(episode, reason) pairs the rule removes, oldest first. Protected recordings are skipped."""
    eps = sorted((e for e in episodes if e.get("Key") not in protected),
                 key=lambda e: e.get("StartTime") or 0, reverse=True)
    doomed = []
    if rule["delete_watched"]:
        doomed += [(e, "watched") for e in eps if e.get("Watched") is True]
        eps = [e for e in eps if e.get("Watched") is not True]
    if rule["keep"]:
        doomed += [(e, "older than the newest %d" % rule["keep"]) for e in eps[rule["keep"]:]]
    return sorted(doomed, key=lambda d: d[0].get("StartTime") or 0)


def build_plan(series, limits, only=None, protected=frozenset()):
    """For each show with a rule (or just the show `only`), list the recordings it removes.
    Recordings whose Key is in `protected` are never included."""
    plan = []
    for s in series:
        if only is not None and series_key(s) != only:
            continue
        rule = normalize_rule(limits.get(series_key(s)))
        if not rule:
            continue
        doomed = to_delete(s["Episodes"], rule, protected)
        if doomed:
            plan.append({
                "SeriesID": series_key(s),
                "Title": s["Title"],
                "Rule": rule,
                "Keep": rule["keep"],
                "Episodes": [{
                    "EpisodeTitle": e.get("EpisodeTitle") or e.get("Title"),
                    "EpisodeNumber": e.get("EpisodeNumber"),
                    "StartTime": e.get("StartTime"),
                    "CmdURL": e.get("CmdURL"),
                    "Reason": reason,
                } for e, reason in doomed],
            })
    return plan


class PlanChanged(Exception):
    """The fresh plan no longer matches the one the user confirmed."""
    def __init__(self, plan):
        super().__init__("plan changed")
        self.plan = plan


def run_prune(svc, max_deletes=None, confirmed=None, series_id=None, source="keep-rules"):
    """Delete what the keep rules remove, for every show or only `series_id`. `svc` is the app's
    Services (client, storage, history).
    Returns (plan, results); results is None if the run was skipped for exceeding max_deletes.
    If `confirmed` is given (the CmdURLs the user reviewed), the fresh plan must match it
    exactly, otherwise PlanChanged is raised and nothing is deleted."""
    with svc.storage.data_lock:
        series = svc.client.load_shows(series_id)
        if series_id is None:
            adopt_new_shows(svc.storage, series)   # so a show recorded today is covered tonight
        protected = set(svc.storage.protected())
        plan = build_plan(series, svc.storage.limits(), only=series_id, protected=protected)
        urls = [e["CmdURL"] for g in plan for e in g["Episodes"] if e.get("CmdURL")]
        if confirmed is not None and set(urls) != set(confirmed):
            raise PlanChanged(plan)
        if max_deletes and len(urls) > max_deletes:
            return plan, None
        if not urls:
            return plan, []
        # the URLs came from the fresh listing above, so they are the allowed set
        index = episode_index(series)
        results = svc.client.delete_urls(urls, rerecord=0, allowed={u: index[u] for u in urls},
                                         protected=protected)
        svc.history.record(results, source)
        return plan, results
