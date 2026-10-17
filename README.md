# NPlayStudio

**Create. Convert. Compress.**

A free online media toolkit. Nine tools for video, audio and images, built on a
FastAPI backend that drives FFmpeg and Pillow, with Redis/ARQ for background jobs
and SQLite for persistence.

No account, no watermark, no payment wall. Uploaded files are deleted one hour
after processing.

---

## Tools

| Category | Tool | Endpoint |
|---|---|---|
| Video | Compress | `POST /api/video/compress` |
| Video | Convert | `POST /api/video/convert` |
| Video | Merge | `POST /api/video/merge` |
| Video | To GIF | `POST /api/video/to-gif` |
| Audio | Extract | `POST /api/audio/extract` |
| Audio | Convert | `POST /api/audio/convert` |
| Image | Compress | `POST /api/image/compress` |
| Image | Convert | `POST /api/image/convert` |
| Image | Crop | `POST /api/image/crop` |

Supporting endpoints: `GET /api/status/{task_id}`, `GET /api/file/{task_id}`,
`GET /api/history`, `GET /api/stats`, `GET /api/tools`, `GET /api/presets`,
`DELETE /api/task/{task_id}`, `GET /health`.

Interactive API docs are served at `/docs`.

---

## Architecture

```
Browser
  │  HTTPS
  ▼
Nginx ── static files from frontend/
  │      /api/* ──┐
  ▼               │
FastAPI ──────────┘
  │
  ├─ SQLite          tasks, downloads, stats, presets
  ├─ Redis + ARQ     job queue, progress, 1-hour TTL
  ▼
ARQ worker
  ├─ FFmpeg          video + audio
  ├─ Pillow          images
  └─ filesystem      uploads/ and outputs/, cleaned on a cron
```

A job flows as: upload → validate → save to `uploads/{task_id}/` → insert a
`queued` task row → enqueue on Redis → worker flips it to `processing` and
streams progress → output lands in `outputs/{task_id}/` → status becomes `done`
→ the browser polls, then downloads. A cron job deletes anything older than the
TTL.

---

## Quick start (local, no Docker)

Requires Python 3.12+, `ffmpeg`, and Redis.

```bash
# 1. System dependencies (Debian/Ubuntu)
sudo apt-get install -y ffmpeg redis-server

# 2. Python dependencies
python3 -m pip install -r backend/requirements.txt

# 3. Configuration
cp backend/.env.example backend/.env

# 4. Run Redis, then the API and the worker in separate shells
redis-server --daemonize yes
python3 -m uvicorn backend.app.main:app --reload --port 8000
python3 -m arq backend.app.workers.tasks.WorkerSettings
```

Open <http://localhost:8000>.

### Running without Redis

Set `NPLAY_INLINE_WORKER=true` in `backend/.env`. Jobs then execute inside the
API process instead of going through the queue — convenient for development and
used by the test suite, but not suitable for production.

---

## Running with Docker

```bash
cp backend/.env.example backend/.env
docker compose up -d --build
docker compose ps
```

This starts three services: `redis`, `api` (port 8000, bound to loopback only)
and `worker`. Uploads, outputs and the SQLite file live in named volumes so a
rebuild does not lose data.

---

## Configuration

All settings use the `NPLAY_` prefix and can be set in `backend/.env`.

| Variable | Default | Purpose |
|---|---|---|
| `NPLAY_ENV` | `development` | Environment label reported by `/health` |
| `NPLAY_HOST` / `NPLAY_PORT` | `0.0.0.0` / `8000` | Bind address |
| `NPLAY_DB_PATH` | `backend/data/nplaystudio.db` | SQLite file |
| `NPLAY_UPLOAD_DIR` | `uploads` | Incoming files |
| `NPLAY_OUTPUT_DIR` | `outputs` | Finished files |
| `NPLAY_REDIS_URL` | `redis://localhost:6379/0` | Queue backend |
| `NPLAY_QUEUE_NAME` | `nplaystudio` | ARQ queue name — the API and worker must agree |
| `NPLAY_MAX_UPLOAD_MB` | `512` | Per-file upload cap |
| `NPLAY_MAX_MERGE_FILES` | `10` | Files per merge |
| `NPLAY_FILE_TTL_SECONDS` | `3600` | How long files survive |
| `NPLAY_CLEANUP_INTERVAL_SECONDS` | `300` | Cleanup cadence |
| `NPLAY_CORS_ORIGINS` | `*` | Comma-separated allowed origins |
| `NPLAY_INLINE_WORKER` | `false` | Run jobs in-process (development only) |

---

## Tests

```bash
python3 -m pytest backend/tests -q
```

The suite is end-to-end and uses no mocks: it generates real media with FFmpeg,
uploads it through a real HTTP client, lets the real worker process it, and
verifies the downloaded bytes carry the right container signature. `test_units.py`
covers the SQLite layer and the service helpers directly.

---

## Deployment (VPS)

1. **Harden the VPS.** Create a non-root user, disable password SSH login, and
   enable a firewall allowing only 22, 80 and 443.
2. **Install Docker** and the Compose plugin.
3. **Clone and configure.**
   ```bash
   git clone <repo> /srv/nplaystudio && cd /srv/nplaystudio
   cp backend/.env.example backend/.env   # edit it
   ```
4. **Start the stack.**
   ```bash
   docker compose up -d --build
   ```
5. **Put Nginx in front.** Copy `nginx/nginx.conf` to
   `/etc/nginx/sites-available/nplaystudio`, edit `server_name` and the
   certificate paths, then enable it and reload. On the first run, comment out
   the HTTP→HTTPS redirect block so the ACME challenge can complete.
6. **Get a certificate.**
   ```bash
   sudo certbot --nginx -d nplaystudio.example.com
   ```
7. **Verify and back up.**
   ```bash
   curl -fsS https://nplaystudio.example.com/health
   # nightly SQLite backup
   sqlite3 backend/data/nplaystudio.db ".backup '/var/backups/nplaystudio-$(date +%F).db'"
   ```

`.github/workflows/deploy.yml` automates steps 3–4 on every push to `main`,
gated on the test suite. It expects the secrets `VPS_HOST`, `VPS_USER`,
`VPS_SSH_KEY`, `VPS_PORT` and `VPS_APP_DIR`.

### Before going live

- Replace `nplaystudio.example.com` in `frontend/robots.txt`,
  `frontend/sitemap.xml` and the legal pages' contact addresses.
- Set your real AdSense publisher ID in `frontend/index.html` (currently
  `ca-pub-0000000000000000`).
- Serve over HTTPS only — the cookie banner and AdSense both expect it.

---

## Project layout

```
backend/app/
  config.py          settings
  database.py        SQLite schema + queries
  schemas.py         Pydantic request/response models
  routes/            video, audio, image, stats, history, common helpers
  services/          ffmpeg, pillow, file, db facades
  workers/tasks.py   ARQ job execution, progress, cleanup cron
backend/tests/       end-to-end and unit tests
frontend/            dashboard, 9 tool pages, legal pages, assets
nginx/nginx.conf     reverse proxy template
Dockerfile           shared image for the API and the worker
docker-compose.yml   redis + api + worker
```

---

## License

MIT — see [LICENSE](LICENSE).
