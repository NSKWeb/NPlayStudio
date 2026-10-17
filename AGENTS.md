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
