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
from .dvr_client import episode_index


def series_key(s):
    return s.get("SeriesID") or s.get("Title")


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
