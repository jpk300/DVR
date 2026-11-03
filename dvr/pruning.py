"""Keep limits: work out which recordings fall beyond each show's limit, and delete them."""
from .dvr_client import delete_urls, fetch_series
from .storage import data_lock, load_limits


def series_key(s):
    return s.get("SeriesID") or s.get("Title")


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
    with data_lock:
        plan = build_plan(fetch_series(), load_limits())
        urls = [e["CmdURL"] for g in plan for e in g["Episodes"] if e.get("CmdURL")]
        if confirmed is not None and set(urls) != set(confirmed):
            raise PlanChanged(plan)
        if max_deletes and len(urls) > max_deletes:
            return plan, None
        # the URLs came from the fresh listing above, so they are the allowed set
        results = delete_urls(urls, rerecord=0, allowed=set(urls)) if urls else []
        return plan, results

