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
from dvr.pruning import run_prune
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
