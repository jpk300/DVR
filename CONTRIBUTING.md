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

## Code layout

| Path | What's in it |
|---|---|
| `app.py` | Entry point only (`app:app`) |
| `dvr/config.py` | Environment variables, tunables, logging setup |
| `dvr/storage.py` | JSON files in `DATA_DIR` |
| `dvr/dvr_client.py` | All DVR and cloud requests, and the SSRF guard. Any new DVR request must go through `dvr_fetch()` |
| `dvr/pruning.py` | Keep-limit plans and pruning |
| `dvr/scheduler.py` | The daily automatic cleanup |
| `dvr/routes.py` | Flask app, HTTP API, security headers |
| `static/` | `index.html`, `app.js`, `app.css`. No inline scripts or styles: the Content-Security-Policy blocks them |
