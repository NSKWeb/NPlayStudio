# Single image used by both the API and the ARQ worker; the command differs.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg brings the encoders (libx264, libx265, libvpx-vp9, lame, opus, vorbis)
# and ffprobe used for duration probing.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/nplaystudio

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/

RUN mkdir -p backend/data uploads outputs \
    && useradd --create-home --uid 10001 app \
    && chown -R app:app /srv/nplaystudio
USER app

EXPOSE 8000

# The API overrides this with an HTTP check; the worker keeps this process
# check, because it serves no HTTP. Avoids procps/curl in the base image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os,pathlib,sys; me=str(os.getpid()); sys.exit(0 if any(b'backend.app' in p.read_bytes() for p in pathlib.Path('/proc').glob('[0-9]*/cmdline') if p.parent.name != me) else 1)"]

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
