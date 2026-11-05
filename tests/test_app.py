"""Focused tests for the data files, login and request checks. Run: python -m unittest discover tests

No DVR is needed: the parts that would talk to one are replaced with small stand-ins.
"""
import base64
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from dvr import create_app
from dvr.config import Config
from dvr.pruning import adopt_new_shows, run_prune
from dvr.scheduler import Scheduler, low_space_note
from dvr.storage import Storage, StorageError


def make_app(tmp, **kw):
    return create_app(Config(data_dir=tmp, scheduler_enabled=False, **kw))


def basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, text):
        with open(os.path.join(self.tmp, name), "w") as f:
            f.write(text)


class StorageTests(TempDirTest):
    def test_write_then_read(self):
        st = Storage(self.tmp)
        st.save_limits({"S1": {"keep": 2, "delete_watched": False}})
        self.assertEqual(st.limits(), {"S1": {"keep": 2, "delete_watched": False}})
        self.assertFalse(os.path.exists(st.limits_path + ".tmp"))

    def test_damaged_limits_are_moved_aside(self):
        self.write("limits.json", "{not json")
        st = Storage(self.tmp)
        self.assertEqual(st.limits(), {})
        self.assertFalse(os.path.exists(st.limits_path))
        self.assertTrue(any(n.startswith("limits.json.corrupt-") for n in os.listdir(self.tmp)))

    def test_damaged_protected_file_is_kept_and_refused(self):
        self.write("protected.json", "{not json")
        st = Storage(self.tmp)
        with self.assertRaises(StorageError):
            st.protected()
        with self.assertRaises(StorageError):
            st.set_protected("P1@1", None)
        # left where it is for the user to fix, unchanged
        with open(st.protected_path) as f:
            self.assertEqual(f.read(), "{not json")

    def test_protected_file_of_the_wrong_type_is_refused(self):
        self.write("protected.json", "[]")
        with self.assertRaises(StorageError):
            Storage(self.tmp).protected()


class FakeClient:
    """Stands in for DvrClient: one show with three recordings, and a record of deletions."""
    def __init__(self):
        self.deleted = []

    def load_shows(self, series_id=None):
        eps = [{"Key": f"P{i}@{i}", "StartTime": i, "CmdURL": f"http://10.0.0.2/recorded/cmd?id={i}",
                "EpisodeTitle": f"Episode {i}"} for i in (1, 2, 3)]
        return [{"SeriesID": "S1", "Title": "Show", "Episodes": eps}]

    def delete_urls(self, urls, rerecord=0, allowed=None, protected=frozenset()):
        self.deleted += urls
        return [{"cmd_url": u, "ok": True, "attempted": True, **allowed[u]} for u in urls]


class PruneTests(TempDirTest):
    def services(self):
        st = Storage(self.tmp)
        st.save_limits({"S1": {"keep": 1, "delete_watched": False}})
        history = SimpleNamespace(record=lambda *a, **k: None)
        return SimpleNamespace(storage=st, client=FakeClient(), history=history)

    def test_keep_rule_skips_protected(self):
        svc = self.services()
        svc.storage.set_protected("P1@1", {"show": "Show"})
        plan, results = run_prune(svc, source="auto")
        self.assertEqual(svc.client.deleted, ["http://10.0.0.2/recorded/cmd?id=2"])

    def test_nothing_is_deleted_when_protected_file_is_damaged(self):
        svc = self.services()
        self.write("protected.json", "")   # e.g. emptied by a power cut
        with self.assertRaises(StorageError):
            run_prune(svc, source="auto")
        self.assertEqual(svc.client.deleted, [])


def shows(*ids):
    return [{"SeriesID": i, "Title": f"Show {i}", "Episodes": []} for i in ids]


class NewShowRuleTests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.st = Storage(self.tmp)
        self.st.update_settings({"new_show_keep": 5})

    def test_first_list_only_remembers_shows(self):
        self.assertEqual(adopt_new_shows(self.st, shows("A", "B")), [])
        self.assertEqual(self.st.limits(), {})
        self.assertEqual(self.st.known_shows(), ["A", "B"])

    def test_new_show_gets_the_rule(self):
        adopt_new_shows(self.st, shows("A"))
        self.assertEqual(adopt_new_shows(self.st, shows("A", "B")), ["B"])
        self.assertEqual(self.st.limits(), {"B": {"keep": 5, "delete_watched": False}})
        # seen now, so it isn't given the rule again after the user changes it
        self.st.save_limits({})
        self.assertEqual(adopt_new_shows(self.st, shows("A", "B")), [])
        self.assertEqual(self.st.limits(), {})

    def test_existing_rule_is_kept(self):
        adopt_new_shows(self.st, shows("A"))
        self.st.save_limits({"B": {"keep": None, "delete_watched": True}})
        adopt_new_shows(self.st, shows("A", "B"))
        self.assertEqual(self.st.limits()["B"], {"keep": None, "delete_watched": True})

    def test_rule_off_only_remembers(self):
        self.st.update_settings(remove=("new_show_keep",))
        adopt_new_shows(self.st, shows("A"))
        self.assertEqual(adopt_new_shows(self.st, shows("A", "B")), [])
        self.assertEqual(self.st.known_shows(), ["A", "B"])

    def test_cleanup_covers_a_show_first_seen_tonight(self):
        st = self.st
        st.save_known_shows(["OLD"])
        svc = SimpleNamespace(storage=st, client=FakeClient(), history=SimpleNamespace(record=lambda *a, **k: None))
        st.update_settings({"new_show_keep": 1})
        run_prune(svc, source="auto")
        self.assertEqual(st.limits()["S1"], {"keep": 1, "delete_watched": False})
        self.assertEqual(len(svc.client.deleted), 2)   # three recordings, keep the newest one


class SpaceWarningTests(TempDirTest):
    def test_note(self):
        self.assertEqual(low_space_note(60e9, 50e9), "")
        self.assertIn("40.0 GB left", low_space_note(40e9, 50e9))
        self.assertEqual(low_space_note(None, 50e9), "")

    def test_cleanup_summary_mentions_low_space(self):
        st = Storage(self.tmp)
        st.update_settings({"low_space_gb": 50})
        client = FakeClient()
        client.engine_info = lambda: {"FreeSpace": 12e9}
        svc = SimpleNamespace(storage=st, client=client, history=SimpleNamespace(record=lambda *a, **k: None))
        msg = Scheduler(svc, enabled=False).run_once(25, "2026-11-05T03:00")
        self.assertIn("Free space is low: 12.0 GB left", msg)
        self.assertIn("Free space is low", st.last_run()["summary"])


class LoginTests(TempDirTest):
    def test_no_login_configured(self):
        self.assertEqual(make_app(self.tmp).test_client().get("/api/settings").status_code, 200)

    def test_login_required_and_checked(self):
        c = make_app(self.tmp, app_user="family", app_pass="s3cret").test_client()
        self.assertEqual(c.get("/api/settings").status_code, 401)
        self.assertEqual(c.get("/api/settings", headers=basic("family", "wrong")).status_code, 401)
        self.assertEqual(c.get("/api/settings", headers=basic("other", "s3cret")).status_code, 401)
        self.assertEqual(c.get("/api/settings", headers=basic("family", "s3cret")).status_code, 200)

    def test_user_without_password_locks_everyone_out(self):
        c = make_app(self.tmp, app_user="family", app_pass=None).test_client()
        self.assertEqual(c.get("/api/settings", headers=basic("family", "")).status_code, 401)


class RequestCheckTests(TempDirTest):
    def setUp(self):
        super().setUp()
        self.c = make_app(self.tmp).test_client()

    def test_non_string_fields_are_rejected_not_crashing(self):
        r = self.c.post("/api/settings", json={"dvr_host": 1234})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/settings/test", json={"dvr_host": ["10.0.0.2"]})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/settings/schedule", json={"time": 300, "tz": "UTC", "max": 5})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/settings/schedule", json={"time": "03:00", "tz": "UTC", "max": True})
        self.assertEqual(r.status_code, 400)

    def test_schedule_saves(self):
        r = self.c.post("/api/settings/schedule", json={"enabled": True, "time": "03:00", "tz": "UTC", "max": 5})
        self.assertEqual(r.status_code, 200, r.get_json())
        with open(os.path.join(self.tmp, "settings.json")) as f:
            self.assertEqual(json.load(f)["auto_prune_max"], 5)

    def test_protect_reports_a_damaged_file(self):
        self.write("protected.json", "{")
        r = self.c.post("/api/protect", json={"key": "P1@1", "protected": False})
        self.assertEqual(r.status_code, 500)
        self.assertIn("damaged", r.get_json()["error"])

    def test_new_show_setting(self):
        self.assertIsNone(self.c.get("/api/settings").get_json()["new_show_keep"])
        r = self.c.post("/api/settings/new-shows", json={"keep": 5})
        self.assertEqual(r.get_json()["new_show_keep"], 5)
        self.assertEqual(self.c.get("/api/settings").get_json()["new_show_keep"], 5)
        for bad in (0, -1, 2.5, "5", True):
            self.assertEqual(self.c.post("/api/settings/new-shows", json={"keep": bad}).status_code, 400, bad)
        self.assertIsNone(self.c.post("/api/settings/new-shows", json={"keep": None}).get_json()["new_show_keep"])

    def test_space_setting_and_status(self):
        self.assertEqual(self.c.post("/api/settings/space", json={"warn_gb": 50}).status_code, 200)
        self.assertEqual(self.c.get("/api/settings").get_json()["low_space_gb"], 50)
        self.c.application.extensions["dvr"].client.engine_info = lambda: {"FreeSpace": 1, "TotalSpace": 2}
        self.assertEqual(self.c.get("/api/status").get_json()["warn_below"], 50 * 10**9)
        self.assertEqual(self.c.post("/api/settings/space", json={"warn_gb": "lots"}).status_code, 400)
        self.c.post("/api/settings/space", json={"warn_gb": None})
        self.assertIsNone(self.c.get("/api/status").get_json()["warn_below"])

    def test_damaged_file_is_reported_as_json(self):
        self.write("protected.json", "{")
        self.c.application.extensions["dvr"].client.fetch_one_series = lambda sid: {"Title": "T", "Episodes": []}
        r = self.c.get("/api/show?series_id=S1")
        self.assertEqual(r.status_code, 500)
        self.assertIn("damaged", r.get_json()["error"])

    def test_form_posts_are_not_accepted(self):
        # a page on another site can only send forms, not JSON; those must change nothing
        r = self.c.post("/api/limits", data={"series_id": "S1", "keep": "1"})
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/settings/schedule", data={"enabled": "1", "time": "03:00", "tz": "UTC", "max": "5"})
        self.assertEqual(r.status_code, 400)

    def test_security_headers(self):
        with self.c.get("/") as r:
            self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
            self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")


if __name__ == "__main__":
    unittest.main()
