"""Shared request-handling helpers for the tool routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from ..config import get_settings
from ..services import db_service, file_service
from ..workers import tasks as worker_tasks

_CHUNK = 1024 * 1024


async def save_upload(upload: UploadFile, task_id: str, allowed_ext: set[str]) -> tuple[Path, int]:
    """Stream an upload to disk, enforcing the extension allowlist and size cap."""
    settings = get_settings()
    filename = file_service.safe_filename(upload.filename or "upload")
    extension = Path(filename).suffix.lower()
    if extension not in allowed_ext:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported file type '{extension or filename}'. Allowed: "
                   + ", ".join(sorted(allowed_ext)),
        )

    destination = file_service.unique_path(file_service.task_upload_dir(task_id), filename)
    size = 0
    try:
        with destination.open("wb") as handle:
            while chunk := await upload.read(_CHUNK):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"file exceeds the {settings.max_upload_mb} MB limit",
                    )
                handle.write(chunk)
    except HTTPException:
        file_service.cleanup_task(task_id)
        raise
    finally:
        await upload.close()

    if size == 0:
        file_service.cleanup_task(task_id)
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    return destination, size


async def submit_job(tool: str, options: dict[str, Any], uploads: list[UploadFile],
                     allowed_ext: set[str]) -> dict[str, Any]:
    """Persist uploads, record the task, and hand it to the worker."""
    task_id = file_service.new_task_id()
    saved: list[Path] = []
    total_size = 0
    for upload in uploads:
        path, size = await save_upload(upload, task_id, allowed_ext)
        saved.append(path)
        total_size += size

    await db_service.create_task(
        task_id, tool, options,
        input_file=str(saved[0].relative_to(get_settings().upload_path)),
        input_size=total_size,
    )

    payload = {"task_id": task_id, "tool": tool, "options": options,
               "inputs": [str(p) for p in saved]}
    try:
        await worker_tasks.enqueue(payload)
    except Exception as exc:  # noqa: BLE001 - Redis may be unavailable
        await db_service.mark_failed(task_id, f"queue unavailable: {exc}")
        raise HTTPException(
            status_code=503,
            detail="job queue is unavailable; please retry shortly",
        ) from exc

    return {"task_id": task_id, "status": "queued", "tool": tool}
