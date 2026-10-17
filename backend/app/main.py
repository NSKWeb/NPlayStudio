"""FastAPI application factory for NPlayStudio."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, database
from .config import REPO_ROOT, get_settings
from .routes import audio, history, image, stats, video
from .services.ffmpeg_service import FFmpegError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("nplaystudio")

FRONTEND_DIR = REPO_ROOT / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    settings.output_path.mkdir(parents=True, exist_ok=True)
    database.get_db()  # create schema / fail fast on a bad path
    logger.info("NPlayStudio %s ready (env=%s)", __version__, settings.env)
    yield
    database.reset_db_cache()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="NPlayStudio",
        description="Create. Convert. Compress. — free online media toolkit.",
        version=__version__,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for module in (video, audio, image, stats, history):
        app.include_router(module.router)

    @app.get("/health", tags=["system"])
    async def health():
        return {"status": "ok", "version": __version__, "env": settings.env}

    @app.exception_handler(FFmpegError)
    async def ffmpeg_error_handler(request: Request, exc: FFmpegError):
        logger.warning("ffmpeg error on %s: %s", request.url.path, exc)
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    if FRONTEND_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

    return app


app = create_app()


def _index_exists() -> bool:
    return (Path(FRONTEND_DIR) / "index.html").is_file()
