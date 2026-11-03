"""Settings read from the environment, tunables, and logging setup."""
import logging
import os

# ---------- logging ----------
# LOG_LEVEL=DEBUG|INFO|WARNING|ERROR (default INFO). Logs go to stderr, so `docker logs` shows them.
_level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").strip().upper(), None)
logging.basicConfig(level=_level if isinstance(_level, int) else logging.INFO,
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

# ---------- environment ----------
APP_USER = os.environ.get("APP_USER")
APP_PASS = os.environ.get("APP_PASS")

DATA_DIR = os.environ.get("DATA_DIR", "/data")
LIMITS_PATH = os.path.join(DATA_DIR, "limits.json")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
LAST_RUN_PATH = os.path.join(DATA_DIR, "last_run.json")

# ---------- tunables ----------
DVR_TIMEOUT = (3.05, 10)          # (connect, read) seconds for every DVR request
MAX_WORKERS = 6                   # bounded concurrency when loading each show's episodes
MAX_BODY_BYTES = 8 * 1024 * 1024  # refuse DVR responses bigger than this
MAX_DELETE_BATCH = 500            # most recordings one delete request may name
LIST_TTL = 10                     # seconds the recordings list is cached
RULES_TTL = 300                   # seconds the cloud recording rules are cached
MAX_REQUEST_BYTES = 1024 * 1024   # request bodies are small JSON; refuse anything bigger
