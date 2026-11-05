# Contributing

## Workflow

1. **Branch from `main`** for every change. Name the branch after what it does:
   `fix/…` for bug fixes, `feat/…` for new features, `refactor/…` for internal changes, `docs/…` for docs.
2. **Commit in small, focused steps** with descriptive messages (see below).
3. **Open a pull request** into `main`, even when you're working alone. The PR description is where you
   record what changed, why, and how you tested it.
4. **Update `CHANGELOG.md`** under `## [Unreleased]` for anything a user would notice.
5. **Merge with a merge commit** (`gh pr merge --merge`), then delete the branch.

## Commit messages

- First line: what the commit does, in the imperative, under about 72 characters.
  "Fix hidden selections when switching shows", not "Update #2" or "Fixes".
- Leave a blank line, then explain *why* when it isn't obvious: the bug, the trade-off, what you tested.
- One logical change per commit. A refactor and a behaviour change go in separate commits.

## Running locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DATA_DIR=./data DVR_HOST=192.168.1.50 flask --app app run --debug --port 8080
```

Deleting a recording is permanent, so try changes on a throwaway recording first.

## Checks

CI runs these on every pull request. Run them locally before you push:

```bash
pip install ruff==0.8.4 && ruff check .
python -m unittest discover -s tests   # tests/: data files, login, request checks (no DVR needed)
npm ci && npm run check      # ESLint and the JSDoc type check for static/js
```

`create_app()` takes a `Config`, so you can build an app for a test with its own data folder and no environment variables (see `tests/test_app.py`):

```python
from dvr import create_app
from dvr.config import Config
app = create_app(Config(data_dir=tmp_path, scheduler_enabled=False))
client = app.test_client()
```

## Code layout

| Path | What's in it |
|---|---|
| `app.py` | Entry point only: `app = create_app()` |
| `dvr/__init__.py` | `create_app(config)` and `Services` (storage, client, history, scheduler), held on `app.extensions["dvr"]` |
| `dvr/config.py` | `Config` (deployment settings from the environment), tunables, logging setup |
| `dvr/storage.py` | `Storage`: settings, keep rules and the last run, with corrupt-file handling. `settings.json` is the only source of user settings |
| `dvr/history.py` | `History`: the deletion log |
| `dvr/dvr_client.py` | `DvrClient`: all DVR and cloud requests, and the SSRF guard. Any new DVR request must go through `DvrClient.fetch()` |
| `dvr/pruning.py` | Keep rules (pure functions) and `run_prune()` |
| `dvr/scheduler.py` | `Scheduler`: the daily automatic cleanup |
| `dvr/routes.py` | The HTTP API as a blueprint; it gets shared objects from `current_app`, never from module globals |
| `static/js/` | ES modules: `main.js` (wiring), `state.js` (shared state and the render registry), `actions.js`, `list.js`, `panel.js`, `ruledialog.js`, `history.js`, `settings.js`, `views.js`, plus `api.js`, `dom.js`, `format.js`, `rules.js`, `ui.js`, `types.js` (JSDoc types). Every file starts with `// @ts-check` |
| `static/` | `index.html`, `app.css`. No inline scripts or styles: the Content-Security-Policy blocks them |
