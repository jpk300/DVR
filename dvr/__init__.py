"""HDHomeRun DVR Manager.

Modules:
    config      deployment settings from the environment, tunables, logging setup
    storage     JSON files in the data directory (settings, keep rules, last automatic run)
    history     the deletion history
    dvr_client  every request to the DVR and Silicondust's cloud, behind the SSRF guard
    pruning     keep rules: which recordings they remove, and deleting them
    scheduler   the daily automatic cleanup
    routes      the HTTP API (a Flask blueprint)

create_app() builds everything from a Config, so tests can make an app with their own data
directory without touching environment variables.
"""
import os
from dataclasses import dataclass

from flask import Flask

from .config import HISTORY_MAX, MAX_REQUEST_BYTES, Config, configure_logging
from .dvr_client import DvrClient
from .history import History
from .scheduler import Scheduler
from .storage import Storage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class Services:
    """Everything the routes and the scheduler share, held on app.extensions["dvr"]."""
    config: Config
    storage: Storage
    client: DvrClient
    history: History
    scheduler: Scheduler | None = None


def create_app(config=None):
    config = config or Config.from_env()
    configure_logging(config.log_level)

    storage = Storage(config.data_dir)
    storage.seed_from_env(config.env_defaults)
    svc = Services(
        config=config,
        storage=storage,
        client=DvrClient(storage, config.allowed_networks, config.device_auth),
        history=History(storage.history_path, HISTORY_MAX),
    )
    svc.scheduler = Scheduler(svc, enabled=config.scheduler_enabled)

    app = Flask(__name__, static_folder=os.path.join(ROOT, "static"), static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
    app.extensions["dvr"] = svc

    from .routes import bp
    app.register_blueprint(bp)
    return app
