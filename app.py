"""Entry point: `gunicorn app:app` or `flask --app app run`. The code lives in the dvr/ package."""
from dvr import create_app

app = create_app()


def start_scheduler():
    """Called by gunicorn.conf.py once the worker has loaded the app."""
    return app.extensions["dvr"].scheduler.start()
