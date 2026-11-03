# Changelog

Notable changes to this project. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
There are no tagged releases yet, so everything is under **Unreleased**. When you tag a release,
move these entries under a heading like `## [0.1.0] - YYYY-MM-DD`.

## [Unreleased]

### Added
- CI (GitHub Actions): ruff, an app start-up check, ESLint, a JSDoc type check, and a Docker build.
- A damaged `settings.json`, `limits.json` or `last_run.json` no longer breaks the app: it is renamed to
  `<name>.corrupt-<timestamp>`, an error is logged, and defaults are used.
- **Keep rules** replace keep limits: keep everything, keep the newest N, or delete watched episodes
  (optionally keeping the newest N unwatched). Edited in a dialog that previews what the rule would delete.
- Watched and partly-watched markers on recordings, from the DVR's `Resume` field.
- **History** tab listing every deletion (when, show, episode, manual / keep rules / automatic, re-record),
  stored in `data/history.jsonl`. Deletions are also logged.
- "Delete N now to match this rule" in each show's panel, and recordings the rule would delete are marked.
- Pop-up messages for results and errors, spinners on busy buttons, and placeholder rows while loading.
- "Hours" sort and total hours per show; a "Scheduled" tag on shows with no recordings yet.
- `GET /api/show`, `GET /api/history`, and an optional `series_id` on delete, preview and prune.
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
- `settings.json` is the only source of the DVR address and schedule. `DVR_HOST`, `DVR_STORAGE_URL`, `TZ`
  and `AUTO_PRUNE_*` are copied into it once, on first start, and ignored after that. The Settings tab no
  longer shows a "server default".
- Backend: `create_app(config)` builds the app; the HTTP session and caches live in `DvrClient`, and storage,
  history and the scheduler are objects too, so nothing is read from the environment at import time.
- Front end: `app.js` is split into ES modules under `static/js/`, each checked with `// @ts-check`.
- The keep-limit cell is now a clearly clickable "Keep: …" button.
- Refresh and Apply all keep rules moved into the toolbar above the list (they were under one show's panel).
- Smaller top bar without the clock; it no longer wraps on phones. Sort labels are shorter ("Count").
- Dates show "Today" / "Yesterday" / the weekday for the last week, and the year when it isn't this year.
- Settings: "Use server default" is now "Clear saved address"; the timezone is filled in from your browser
  until one is saved. The re-record checkbox reads "Let the DVR record these episodes again".
- Deleting from one show, or applying one show's rule, re-reads only that show from the DVR instead of every show.
- `limits.json` now stores `{"keep": N, "delete_watched": bool}` per show. Plain numbers from older files still work.
- The Settings page is more compact: smaller type, inputs and buttons, and tighter spacing.
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
