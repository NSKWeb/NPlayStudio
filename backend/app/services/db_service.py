"""Thin facade so routes and workers share one persistence entry point."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .. import database
from ..config import get_settings


async def create_task(task_id: str, tool: str, settings: dict[str, Any],
                      input_file: str | None = None, input_size: int | None = None,
                      status: str = "queued", client_id: str | None = None) -> dict[str, Any]:
    return await database.acreate_task(
        task_id, tool, settings, input_file=input_file, input_size=input_size,
        status=status, client_id=client_id,
    )


async def mark_processing(task_id: str) -> None:
    await database.aupdate_task(task_id, status="processing", progress=1, error=None)


async def set_progress(task_id: str, progress: int) -> None:
    await database.aupdate_task(task_id, progress=max(0, min(99, progress)))


async def mark_done(task_id: str, *, output_file: str, output_size: int,
                    input_size: int) -> None:
    await database.aupdate_task(
        task_id, status="done", progress=100,
        output_file=output_file, output_size=output_size,
    )
    await database.abump_stats(
        tasks=1, input_bytes=input_size or 0, output_bytes=output_size or 0
    )


async def mark_failed(task_id: str, error: str) -> None:
    await database.aupdate_task(task_id, status="failed", error=error[:1000])


async def get_task(task_id: str) -> dict[str, Any] | None:
    return await database.aget_task(task_id)


async def recent_tasks(limit: int = 20, tool: str | None = None,
                       client_id: str | None = None) -> list[dict[str, Any]]:
    return await database.alist_tasks(limit=limit, tool=tool, client_id=client_id)


async def stats() -> dict[str, Any]:
    return await database.aget_stats()


async def record_download(task_id: str, ip: str | None, user_agent: str | None) -> None:
    await database.arecord_download(task_id, ip, user_agent)


async def list_presets(tool: str | None = None) -> list[dict[str, Any]]:
    return await database.alist_presets(tool)


async def create_preset(name: str, tool: str, settings: dict[str, Any]) -> dict[str, Any]:
    return await database.acreate_preset(name, tool, settings)


async def expired_tasks() -> list[dict[str, Any]]:
    ttl = get_settings().file_ttl_seconds
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=ttl)
    return list(database.iter_tasks_for_cleanup(cutoff))
