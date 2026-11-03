# HDHomeRun DVR Manager

A small self-hosted web app for tidying an [HDHomeRun](https://www.silicondust.com/hdhomerun.html) DVR. Set a keep rule for each show (keep the newest N, or clear out what you've watched), delete recordings from a browser, and let the cleanup run automatically once a day.

> Unofficial. Not affiliated with or endorsed by Silicondust. Deleting a recording is permanent, so try it on a throwaway recording first.

## Features

- Browse every show and its recordings, with posters, synopses, and used / free disk space
- Delete one or many recordings, with an optional **re-record** flag
- **Per-show keep rules**: keep everything, keep the newest N, or delete watched episodes (optionally keeping only the newest N unwatched)
- **Watched status** shown on each recording, when the DVR reports it
- Rules can be set for shows that are **scheduled but have no recordings yet**
- **Apply all keep rules**, or apply one show's rule from its panel, always with a preview and confirmation first
- **Deletion history**: every deletion, with when, which episode, how it was triggered and whether it will re-record
- **Automatic daily cleanup** at a time you choose, with a safety cap
- **Settings tab** to change the DVR address from the browser, with a connection test
- Dark UI inspired by the HDHomeRun app's own Tasks screen
- Loads each show's recordings in parallel (bounded), with short-lived caching, timeouts, and per-show error handling so one failing show doesn't break the list
- Runs as one small Docker container

## Quick start

Requires Docker with Compose.

```bash
git clone https://github.com/jpk300/DVR.git
cd DVR
docker compose up -d --build
```

Open `http://localhost:8080` (or `http://<server-ip>:8080` if it runs on another machine).

Open the **Settings** tab and enter your DVR's IP address, then use **Test connection** and **Save & apply**.

Your keep rules, settings, deletion history and the result of the last automatic run are stored in `./data` (mounted into the container), so they survive rebuilds.

## Configuration

Set these under `environment:` in `docker-compose.yml`.

| Variable | Default | What it does |
|---|---|---|
| `DVR_HOST` | none | IP or hostname of your DVR. Can also be set from the Settings tab, which takes priority. |
| `DVR_ALLOWED_NETWORKS` | private ranges only | Extra CIDR ranges the DVR may live on, comma-separated (for example `100.64.0.0/10` for Tailscale). By default only `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` and IPv6 `fc00::/7` are allowed. |
| `DVR_STORAGE_URL` | none | Point straight at the record engine's `recorded_files.json` instead of using `DVR_HOST`. |
| `DEVICE_AUTH` | read from the DVR | Override the DeviceAuth used to look up recording rules. Join the strings together if you have several tuners. |
| `APP_USER` / `APP_PASS` | none | Optional HTTP basic auth for the whole app. |
| `DATA_DIR` | `/data` | Where limits, settings and the last automatic run are stored inside the container. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. Routine runs log at `INFO`; skipped or partly failed runs at `WARNING`; failures at `ERROR`. |
| `DVR_SCHEDULER` | `1` | Set to `0` to turn off the automatic-cleanup scheduler entirely (for example in tests). |

Automatic cleanup (on/off, daily time, timezone and safety cap) is configured on the **Settings** tab, not here. The scheduler picks up changes as soon as you save. `AUTO_PRUNE_TIME`, `AUTO_PRUNE_MAX` and `TZ` still work as first-run defaults if you prefer to set them in the compose file; anything saved in the app takes priority.

If no DVR address is set anywhere, the app falls back to Silicondust's cloud discovery, which only works when you share a public IP with the DVR.

## How keep rules work

1. Click a show's **Keep: All** button and pick a rule:
   - **Keep everything**: nothing is deleted automatically (the default).
   - **Keep the newest N**: recordings are ranked by start time; older ones are deleted.
   - **Delete watched episodes**: every watched recording is deleted. Optionally also keep only the newest N unwatched ones.
2. The list shows how many recordings each rule would delete, and the show's panel marks them.
3. Click **Apply all keep rules**, or **Delete N now** in one show's panel, to preview and confirm. Or let the daily run handle it.

"Watched" comes from the DVR's `Resume` field (playback position), which the DVR sets to `4294967295` once an episode has been watched to the end. Recordings whose watched status the DVR doesn't report are treated as unwatched, so a watched rule never deletes them. Check what your DVR reports with `/api/debug`.

Keep-rule cleanups delete **without** re-record, so the DVR remembers those episodes as already recorded and won't record them again. Rules are tied to each show, not to the DVR address, so they carry over if you point the app at a different DVR. Older `limits.json` files that store a plain number still work and mean "keep the newest N".

## Run without Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DATA_DIR=./data DVR_HOST=192.168.1.50 flask --app app run --port 8080
```

Under `flask run` the automatic-cleanup scheduler starts on the first request (open the page once). In the container, gunicorn starts it as soon as the app boots.

## How it talks to the DVR

- `GET http://<dvr>/discover.json`: device info, free and total space, and the record engine URL
- `GET <StorageURL>` and each show's `EpisodesURL`: the recordings list
- `POST <CmdURL>` with `cmd=delete&rerecord=0|1`: delete a recording (documented in Silicondust's [DVR API](https://info.hdhomerun.com/info/dvr_api))
- `GET https://api.hdhomerun.com/api/recording_rules`: your recording rules (cloud), used to show scheduled shows

The DVR doesn't report per-recording file sizes, so "Used on drive" is total space minus free space.

## Security

- The app can delete recordings and change which device it talks to. Keep it on your home network.
- If you expose it beyond your LAN, put it behind a reverse proxy with HTTPS and set `APP_USER` / `APP_PASS`.
- **Deletes are checked on the server.** The browser only names recordings. The server re-reads the DVR's current list and deletes a recording only if its URL appears in that fresh list, and the URL must also be plain `http://`, carry no credentials, point at the same host and port as the DVR, and have the path `/recorded/cmd`. Anything else is refused.
- **The DVR must be on a private network.** Every address the DVR hostname resolves to must be in an allowed range (see `DVR_ALLOWED_NETWORKS`), so the app can't be pointed at the internet or at a loopback/link-local address such as a cloud metadata service.
- **DNS and redirects are handled.** The app resolves the hostname itself, connects to that exact validated IP (so a DNS change between check and use can't redirect it), never follows HTTP redirects, ignores proxy environment variables for DVR traffic, and caps response sizes and request time.
- URLs inside DVR responses (episode lists, delete commands) are validated the same way before they are used, and images are only shown if their URL is `http(s)`.
- **The page runs under a strict Content-Security-Policy.** Scripts and styles load only from the app itself, and the page can't be framed by another site (which blocks clickjacking).
- Your DVR's `DeviceAuth` is a credential for Silicondust's cloud services. `/api/debug` strips it, but don't paste raw DVR data publicly.

## Troubleshooting

- **Empty list or an error banner:** open the Settings tab, enter the DVR's IP, and use **Test connection**.
- **See what your DVR actually returns:** open `/api/debug` (first show and first episode, with credentials removed).
- **Scheduled shows missing:** check `docker logs hdhr-dvr-manager` for a "Could not load recording rules" warning.
- **Check the automatic run:** the Manage page shows the next and last run; details are in `docker logs hdhr-dvr-manager`.
- **Where did an episode go?** The **History** tab lists every deletion this app has made, newest first (the last 1000 are kept in `data/history.jsonl`).

## Project layout

```
app.py               Entry point (gunicorn app:app / flask --app app run)
dvr/                 Backend package
  config.py            environment variables, tunables, logging
  storage.py           JSON files in DATA_DIR
  history.py           the deletion history
  dvr_client.py        all DVR and cloud requests, with the SSRF guard
  pruning.py           keep-limit plans and pruning
  scheduler.py         daily automatic cleanup
  routes.py            Flask app, HTTP API, security headers
static/              Web UI: index.html, app.js, app.css
requirements.txt     Python dependencies
Dockerfile           Container image
gunicorn.conf.py     gunicorn settings (one worker) and the scheduler start-up hook
docker-compose.yml   Example deployment
data/                Created at runtime: limits.json, settings.json, last_run.json, history.jsonl (not committed)
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the branch-and-pull-request workflow and commit message style,
and [CHANGELOG.md](CHANGELOG.md) for what has changed.

## License

[MIT](LICENSE)
