"""Task status, file download and history endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from .. import database
from ..config import get_settings
from ..schemas import HistoryItem, TaskStatus
from ..services import db_service, file_service

router = APIRouter(prefix="/api", tags=["tasks"])

_DONE = "done"


def _to_status(task: dict) -> TaskStatus:
    download_url = None
    if task["status"] == _DONE and task.get("output_file"):
        download_url = f"/api/file/{task['id']}"
    return TaskStatus(
        task_id=task["id"], tool=task["tool"], status=task["status"],
        progress=task["progress"], input_size=task.get("input_size"),
        output_size=task.get("output_size"), output_file=task.get("output_file"),
        error=task.get("error"), download_url=download_url,
        created_at=task.get("created_at"), updated_at=task.get("updated_at"),
    )


@router.get("/status/{task_id}", response_model=TaskStatus)
async def status(task_id: str):
    task = await db_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _to_status(task)


@router.get("/file/{task_id}")
async def download(task_id: str, request: Request):
    task = await db_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task["status"] != _DONE or not task.get("output_file"):
        raise HTTPException(status_code=409, detail=f"task is {task['status']}, not ready")

    base = get_settings().output_path
    try:
        path = file_service.resolve_within(base, task["output_file"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=410, detail="file has expired and was removed")

    await db_service.record_download(
        task_id,
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
    )
    return FileResponse(
        path,
        filename=path.name,
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/history")
async def history(limit: int = Query(20, ge=1, le=200), tool: str | None = Query(None)):
    tasks = await db_service.recent_tasks(limit=limit, tool=tool)
    items = [
        HistoryItem(
            task_id=t["id"], tool=t["tool"], status=t["status"],
            input_size=t.get("input_size"), output_size=t.get("output_size"),
            created_at=t.get("created_at"),
        )
        for t in tasks
    ]
    downloads = await asyncio.to_thread(database.get_db().list_downloads, limit)
    return {"tasks": items, "downloads": downloads}


@router.delete("/task/{task_id}")
async def delete_task(task_id: str):
    task = await db_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    file_service.cleanup_task(task_id)
    await database.adelete_task(task_id)
    return {"deleted": task_id}
