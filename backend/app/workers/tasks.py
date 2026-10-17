"""ARQ worker: executes queued media jobs and reports progress.

The same coroutine (``execute_job``) is used by the API in inline mode so
development does not require Redis.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from arq import cron
from arq.connections import RedisSettings

from ..config import get_settings
from ..services import db_service, ffmpeg_service, file_service, pillow_service

logger = logging.getLogger("nplaystudio.worker")


async def _progress_reporter(task_id: str):
    last = -1

    async def report(progress: int) -> None:
        nonlocal last
        progress = max(0, min(99, progress))
        # Only write when the integer percentage actually moves.
        if progress != last:
            last = progress
            await db_service.set_progress(task_id, progress)

    return report


def _output_path(task_id: str, stem: str, extension: str) -> Path:
    return file_service.task_output_dir(task_id) / f"{stem}.{extension}"


async def execute_job(ctx: dict[str, Any] | None, payload: dict[str, Any]) -> dict[str, Any]:
    """Run one media job. ``payload`` comes from the API request handler."""
    task_id: str = payload["task_id"]
    tool: str = payload["tool"]
    options: dict[str, Any] = payload.get("options") or {}
    inputs: list[str] = payload.get("inputs") or []
    source = Path(inputs[0]) if inputs else None

    await db_service.mark_processing(task_id)
    report = await _progress_reporter(task_id)

    try:
        if tool.startswith("image/"):
            # Pillow work is CPU-bound and blocking; keep the loop responsive.
            await asyncio.to_thread(_run_image, task_id, tool, options, inputs)
        else:
            await _run_media(task_id, tool, options, inputs, source, report)

        output_dir = file_service.task_output_dir(task_id)
        produced = sorted(output_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not produced:
            raise RuntimeError("processing produced no output file")
        output = produced[0]

        input_size = file_service.file_size(source) if source else 0
        await db_service.mark_done(
            task_id,
            output_file=str(output.relative_to(get_settings().output_path)),
            output_size=file_service.file_size(output),
            input_size=input_size,
        )
        return {"task_id": task_id, "status": "done", "output": str(output)}
    except Exception as exc:  # noqa: BLE001 - surfaced to the client verbatim
        logger.exception("job %s (%s) failed", task_id, tool)
        await db_service.mark_failed(task_id, str(exc))
        return {"task_id": task_id, "status": "failed", "error": str(exc)}


async def _run_media(task_id: str, tool: str, options: dict[str, Any],
                     inputs: list[str], source: Path | None, report) -> None:
    if tool == "video/merge":
        sources = [Path(p) for p in inputs]
        duration = 0.0
        for item in sources:
            duration += await ffmpeg_service.measure_duration(item) or 0.0
        await ffmpeg_service.video_merge(
            sources, _output_path(task_id, "merged", options["format"]),
            container=options["format"], reencode=options.get("reencode", False),
            resolution=options.get("resolution"),
            duration=duration or None, on_progress=report,
        )
        return

    assert source is not None
    duration = await ffmpeg_service.measure_duration(source)

    if tool == "video/compress":
        await ffmpeg_service.video_compress(
            source, _output_path(task_id, "compressed", options["format"]),
            quality=options["quality"], crf=options.get("crf"),
            resolution=options.get("resolution"), codec=options["codec"],
            container=options["format"], audio_bitrate=options.get("audio_bitrate", "128k"),
            duration=duration, on_progress=report,
        )
    elif tool == "video/convert":
        await ffmpeg_service.video_convert(
            source, _output_path(task_id, "converted", options["format"]),
            container=options["format"], codec=options.get("codec"),
            resolution=options.get("resolution"), duration=duration, on_progress=report,
        )
    elif tool == "video/to-gif":
        clip = options.get("duration")
        await ffmpeg_service.video_to_gif(
            source, _output_path(task_id, "animation", "gif"),
            fps=options["fps"], width=options["width"], start=options.get("start", 0),
            duration=clip, on_progress=report,
        )
    elif tool == "audio/extract":
        await ffmpeg_service.audio_extract(
            source, _output_path(task_id, "audio", options["format"]),
            container=options["format"], bitrate=options.get("bitrate", "192k"),
            sample_rate=options.get("sample_rate"), duration=duration, on_progress=report,
        )
    elif tool == "audio/convert":
        await ffmpeg_service.audio_convert(
            source, _output_path(task_id, "converted", options["format"]),
            container=options["format"], bitrate=options.get("bitrate", "192k"),
            sample_rate=options.get("sample_rate"), duration=duration, on_progress=report,
        )
    else:
        raise ValueError(f"unsupported tool: {tool}")


def _run_image(task_id: str, tool: str, options: dict[str, Any], inputs: list[str]) -> None:
    source = Path(inputs[0])
    if tool == "image/compress":
        target_format = options.get("format") or source.suffix.lstrip(".").lower()
        pillow_service.compress(
            source, _output_path(task_id, "compressed", target_format),
            quality=options["quality"], container=options.get("format"),
            max_width=options.get("max_width"),
        )
    elif tool == "image/convert":
        pillow_service.convert(
            source, _output_path(task_id, "converted", options["format"]),
            container=options["format"], quality=options.get("quality", 85),
        )
    elif tool == "image/crop":
        target_format = options.get("format") or source.suffix.lstrip(".").lower()
        pillow_service.crop(
            source, _output_path(task_id, "cropped", target_format),
            x=options["x"], y=options["y"], width=options["width"],
            height=options["height"], container=options.get("format"),
        )
    else:
        raise ValueError(f"unsupported tool: {tool}")


# ----------------------------------------------------------------- cleanup


async def cleanup_expired(ctx: dict[str, Any] | None = None) -> int:
    """Delete files (and task rows) past the TTL."""
    expired = await db_service.expired_tasks()
    removed = 0
    for task in expired:
        file_service.cleanup_task(task["id"])
        await db_service.database.adelete_task(task["id"])
        removed += 1
    if removed:
        logger.info("cleanup removed %d expired task(s)", removed)
    return removed


async def startup(ctx: dict[str, Any]) -> None:
    get_settings().upload_path.mkdir(parents=True, exist_ok=True)
    get_settings().output_path.mkdir(parents=True, exist_ok=True)
    logger.info("worker ready")


async def shutdown(ctx: dict[str, Any]) -> None:
    logger.info("worker stopped")


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


class WorkerSettings:
    """ARQ entry point: ``arq backend.app.workers.tasks.WorkerSettings``."""

    functions = [execute_job, cleanup_expired]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings()
    # Must match the queue the API enqueues onto, otherwise jobs sit unread.
    queue_name = get_settings().queue_name
    max_jobs = 4
    job_timeout = 3600
    keep_result = 3600
    cron_jobs = [
        cron(cleanup_expired, second={0, 30}),
    ]


async def enqueue(payload: dict[str, Any]) -> None:
    """Push a job onto the ARQ queue (or run inline when configured)."""
    settings = get_settings()
    if settings.inline_worker:
        asyncio.create_task(execute_job({}, payload))
        return

    from arq import create_pool

    redis = await create_pool(_redis_settings(), default_queue_name=settings.queue_name)
    try:
        await redis.enqueue_job("execute_job", payload)
    finally:
        await redis.close()


async def purge_outputs() -> None:
    """Best-effort removal of stray temp dirs (used on shutdown)."""
    for base in (get_settings().upload_path, get_settings().output_path):
        if base.exists():
            for child in base.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _cutoff(seconds: int) -> datetime:
    return _utcnow() - timedelta(seconds=seconds)
