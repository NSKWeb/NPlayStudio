# AGENTS.md

Repository knowledge for NPlayStudio. Read this before making changes.

## What this is

A free online media toolkit (video/audio/image convert, compress, merge, extract).
FastAPI backend driving FFmpeg and Pillow, Redis + ARQ for background jobs,
SQLite for persistence, static HTML/CSS/JS frontend, Nginx reverse proxy on a VPS.

## Environment setup

```bash
sudo apt-get install -y ffmpeg redis-server
python3 -m pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
```

Redis is required unless `NPLAY_INLINE_WORKER=true` (which the tests use).

## Commands

```bash
# Tests (end-to-end, real ffmpeg, no mocks) — takes a few minutes
python3 -m pytest backend/tests -q

# Run locally
redis-server --daemonize yes
python3 -m uvicorn backend.app.main:app --reload --port 8000
python3 -m arq backend.app.workers.tasks.WorkerSettings

# JS syntax check
node --check frontend/assets/app.js
```

Run pytest in the background and tail the log; the full suite exceeds the
foreground timeout.

## Conventions

- **Settings** all live in `backend/app/config.py` behind the `NPLAY_` prefix.
  Never read `os.environ` directly elsewhere.
- **Database access** goes through `backend/app/database.py`. Sync methods on
  `Database` are the source of truth; `a*` wrappers push them onto a thread for
  async callers. `services/db_service.py` is the facade routes and workers use.
- **Adding a tool** means: a route in `routes/`, a form schema in `schemas.py`, a
  branch in `workers/tasks.py`, an entry in `TOOLS` in `routes/stats.py`, and an
  HTML page with `data-tool data-endpoint="..."`. The frontend `ToolRunner` in
  `assets/app.js` reads the `data-*` attributes and needs no per-tool script.
- **Uploads are validated twice**: extension allowlist in `file_service`
  (`VIDEO_EXT` / `AUDIO_EXT` / `IMAGE_EXT`) plus size cap in `routes/common.py`.
- **Paths from the database are untrusted.** Always resolve output paths with
  `file_service.resolve_within()` before serving them.

## Gotchas

- The API and the ARQ worker must use the same queue name. `WorkerSettings.queue_name`
  reads `NPLAY_QUEUE_NAME`, and `enqueue()` passes it as `default_queue_name` to
  `create_pool`. If they diverge, jobs sit in Redis unread and tasks stay `queued`.
- ARQ 0.28 accepts `queue_name` on `WorkerSettings` and `default_queue_name` on
  `create_pool()`. The older `enqueue_job(..., _queue_name=...)` kwarg does not work.
- Merging clips where some inputs have no audio track needs generated silence
  padded in; `concat` fails with "Stream specifier ':a' ... matches no streams"
  otherwise. See `ffmpeg_service._merge_reencode`.
- `out_time_us` from `ffmpeg -progress` is microseconds, not milliseconds.
- Progress is capped at 99 until the job actually completes, so a stuck encode
  never looks finished.
- PNG "quality" maps to Pillow's `compress_level` (inverted); JPG/WebP use
  `quality` directly.
- Pillow work runs via `asyncio.to_thread` — it is blocking, and running it on the
  event loop stalls every other request.

## Testing notes

`backend/tests/conftest.py` generates fixtures with ffmpeg (3s testsrc clip with
audio, a silent clip, an MP3, PNG/JPG stills) and sets the env vars before the
app is imported. Tests assert on real container magic bytes
(`conftest.assert_signature`) rather than mocks. `test_tools.py` drives the full
HTTP → queue → worker → download path; `test_units.py` covers SQLite and helpers.

## Bug-hunting notes (verified failures)

These were real defects found by exercising the running app, each now covered
by a regression test that fails on the old code:

- Uploads of the same filename collide. All parts of one job are written to a
  single directory keyed by the client-supplied name, so `dir / name` silently
  replaced the first file and merge concatenated one clip twice. Use
  `file_service.unique_path()` for any per-job input path.
- `scale=WxH` with an odd dimension dies in libx264/libx265 ("width not
  divisible by 2"). Round odd values down; `_scale_filter` and the merge
  filtergraph both do this now.
- Seeking at or past a clip's end makes ffmpeg emit an empty GIF and
  "Error writing trailer: Invalid argument". Check `start` against the probed
  duration before running.
- `safe_filename` truncated the entire name, which strips the extension from a
  long filename and then fails the extension allowlist. Truncate the stem only.
- `/api/history` returned every task on the server plus the raw `downloads`
  table (IP address and user agent). Any visitor could read everyone's task ids
  and then `GET /api/file/{id}` any output. Tasks now carry a `client_id` (a
  random per-browser value the frontend sends as `X-Client-Id`); history is
  scoped to it, and download/delete refuse a task whose stored id does not
  match. A missing client id on history returns an empty list, never "all".
  Tasks with no owner (queued straight to the API) stay open — do not "fix"
  that by inventing an owner.
- AdSense was loaded unconditionally in `index.html` while the cookie banner
  claimed ads ran "with consent". The loader now lives in `assets/app.js`
  (`loadAds()`) and only runs after the visitor chooses "Accept". Keep the
  publisher id there, not in the HTML.

Quick manual probe pattern (no test suite needed): set the `NPLAY_*` env vars to
a temp dir, import `create_app`, drive it with `TestClient`, and poll
`/api/status/{id}`. `NPLAY_INLINE_WORKER=true` runs jobs in-process, so Redis is
not required.

## CI

`.github/workflows/deploy.yml` runs on every push to `main`: a `test` job
installs ffmpeg + `backend/requirements.txt` on Python 3.12 and runs
`pytest backend/tests -q`, then a `deploy` job rebuilds the stack on the VPS
over SSH. The deploy step checks for `VPS_HOST` first, so it is skipped with a
notice until the `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY` (and optional
`VPS_PORT`, `VPS_APP_DIR`) secrets are set. Pushing this file needs a token
with the `workflow` scope; a `repo`-only token is rejected by GitHub.

## Deployment helpers

- `scripts/configure-domain.sh <domain> [email]` rewrites the
  `nplaystudio.example.com` placeholder across nginx, robots.txt, sitemap.xml
  and the legal pages. Without an email it derives `support@`, `dmca@` and
  `privacy@` from the domain; with one it points all three at that address.
- `scripts/vps-bootstrap.sh <domain> [email]` does a full first-time VPS setup
  (Docker + nginx + certbot, clone, compose up, certificate). It is idempotent.
- The first certbot run needs `nginx/nplaystudio.pre-certbot.conf`: the main
  `nginx/nginx.conf` references `ssl_certificate` paths that do not exist yet,
  so nginx refuses to start and certbot has nothing to rewrite. Bootstrap
  installs the HTTP-only config, runs certbot, then swaps in the hardened one.
- The frontend is served by nginx from `/srv/nplaystudio/frontend`, not by the
  container. The API binds `127.0.0.1:8000` only.
- `scripts/smoke-test.sh <domain> [--local]` verifies a live deployment end to
  end (DNS, cert, redirect, static pages, `/docs`, security headers, and a real
  upload → worker → download). `--local` skips DNS/cert and targets
  `127.0.0.1` with `-k`. Both configs were validated with `nginx -t` inside
  `nginx:1.26-alpine` with a self-signed cert.
- README images live in `docs/` (hand-written SVG diagrams) and
  `docs/screenshots/` (captured PNGs). Regenerate the PNGs with
  `python3 scripts/capture_screenshots.py <base_url>`; it needs Playwright and
  the system Chromium. It presets `nplay.theme` and `nplay.consent` in
  localStorage, because the app reads both from there and a default headless run
  otherwise captures the light theme with the cookie dialog over the page. Keep
  `device_scale_factor` at 1: GitHub renders README images at about 880px, and
  2x screenshots were both larger on disk and no sharper.
