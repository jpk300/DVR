# Changelog

Notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
There are no tagged releases yet, so everything is under **Unreleased**. When you tag a release,
move these entries under a heading like `## [0.1.0] - YYYY-MM-DD`.

## [Unreleased]

### Added
- `LICENSE` (MIT), this changelog, and `CONTRIBUTING.md` describing the branch-and-PR workflow.
- A Content-Security-Policy, `X-Content-Type-Options: nosniff` and `X-Frame-Options: DENY` on every response.
  The page can't be framed, which blocks clickjacking of the delete buttons.
- `gunicorn.conf.py`, which starts the automatic-cleanup scheduler when the worker boots.
- `LOG_LEVEL` and `DVR_SCHEDULER` environment variables.
- The result of the last automatic run is saved to `data/last_run.json` and survives restarts.
- `/api/timezones`, cached by the browser for a day.
- A confirmation dialog that lists every recording before it is deleted.
- Keyboard access: sort tabs, show rows and keep-limit cells work with Tab, Enter and Space.

### Changed
- The backend is now the `dvr/` package (`config`, `storage`, `dvr_client`, `pruning`, `scheduler`,
  `routes`). `app.py` is a thin entry point, so `gunicorn app:app` and `flask --app app run` still work.
- The page's script and styles moved to `static/app.js` and `static/app.css`.
- `POST /api/prune` needs the exact list of recordings from the preview. If the DVR's recordings changed
  in the meantime, nothing is deleted and it returns HTTP 409 with the new plan.
- The scheduler no longer starts when the module is imported. Under gunicorn it starts at boot, under
  other servers on the first request, and a lock file keeps it to one process per data directory.
- Logging uses proper levels: routine runs at INFO, skipped or partly failed runs at WARNING,
  failures at ERROR.

### Fixed
- Selecting episodes and then switching shows no longer leaves hidden recordings selected for deletion.
- Network failures and server errors show a message instead of leaving the page stuck on "Loading…".
- The "automatic cleanup is off" hint pointed at an environment variable; it now links to Settings.
- `flask run --debug` no longer starts two schedulers.

### Earlier
- 2026-11-02 to 2026-11-03: first version, with show browsing, deleting recordings, per-show keep limits,
  automatic daily cleanup, the Settings tab, and the DVR access safeguards. See the git history for details.
