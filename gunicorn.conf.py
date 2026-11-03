# gunicorn settings for the container (see Dockerfile).
bind = "0.0.0.0:8080"

# One worker process, with threads for concurrency. app.py also guards the scheduler with a lock
# file in DATA_DIR, so even with more workers only one of them would run the daily cleanup.
workers = 1
threads = 4


def post_worker_init(worker):
    """Start the auto-prune scheduler as soon as the worker has loaded the app,
    rather than waiting for the first request."""
    from app import start_scheduler

    start_scheduler()
