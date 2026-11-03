"""Entry point: `gunicorn app:app` or `flask --app app run`. The code lives in the dvr/ package."""
from dvr.routes import app
from dvr.scheduler import start_scheduler

__all__ = ["app", "start_scheduler"]
