"""Task status, file download and history endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse

from .. import database
from ..config import get_settings
from ..schemas import HistoryItem, TaskStatus
from ..services import db_service, file_service
from .common import client_id_from

router = APIRouter(prefix="/api", tags=["tasks"])

_DONE = "done"


def _to_status(task: dict) -> TaskStatus:
    download_url = None
    if task["status"] == _DONE and task.get("output_file"):
        download_url = f"/api/file/{task['id']}"
        # The download is owner-scoped, so carry the id the browser must present.
        if task.get("client_id"):
            download_url += f"?client={task['client_id']}"
    return TaskStatus(
        task_id=task["id"], tool=task["tool"], status=task["status"],
        progress=task["progress"], input_size=task.get("input_size"),
        output_size=task.get("output_size"), output_file=task.get("output_file"),
        error=task.get("error"), download_url=download_url,
        created_at=task.get("created_at"), updated_at=task.get("updated_at"),
    )


@router.get("/status/{task_id}", response_model=TaskStatus)
async def status(
    task_id: str,
    x_client_id: str | None = Header(None),
    client: str | None = Query(None),
):
    """Poll one job, but only for the browser that created it.

    Same ownership rule as download and delete: a task with a stored client id
    is visible only to that browser; a task with no owner (queued straight to
    the API) is visible to anyone holding the id.
    """
    task = await db_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    owner = task.get("client_id")
    if owner is not None and client_id_from(x_client_id) != owner and client_id_from(client) != owner:
        raise HTTPException(status_code=403, detail="this task belongs to another browser")
    return _to_status(task)


@router.get("/file/{task_id}")
async def download(
    task_id: str,
    request: Request,
    x_client_id: str | None = Header(None),
    client: str | None = Query(None),
):
    """Serve a finished output, but only to the browser that created the job.

    Downloads used to be open to anyone holding the task id, which the history
    endpoint was handing out. A task now carries the client id of the browser
    that queued it, and the download must present the same id — either the
    ``X-Client-Id`` header or the ``client`` query parameter (the download link
    is a plain anchor, which cannot set headers). Rows created before the
    client id existed, and jobs submitted straight to the API without one, have
    no owner to check against and stay downloadable.
    """
    task = await db_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task["status"] != _DONE or not task.get("output_file"):
        raise HTTPException(status_code=409, detail=f"task is {task['status']}, not ready")

    owner = task.get("client_id")
    if owner is not None and client_id_from(x_client_id) != owner and client_id_from(client) != owner:
        raise HTTPException(status_code=403, detail="this file belongs to another browser")

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
async def history(
    limit: int = Query(20, ge=1, le=200),
    tool: str | None = Query(None),
    x_client_id: str | None = Header(None),
):
    """Recent jobs, scoped to the caller's own browser.

    The dashboard reads this. It used to return every task on the server plus a
    raw download log (including IP address and user agent), which both leaked
    personal data and handed out other visitors' task ids. Now a caller only
    sees jobs carrying its own client id, and no download log is exposed.
    """
    client_id = client_id_from(x_client_id)
    if client_id is None:
        # Without a client id there is nothing this caller may see. Never fall
        # back to "all tasks" — that was the leak.
        return {"tasks": []}
    tasks = await db_service.recent_tasks(
        limit=limit, tool=tool, client_id=client_id,
    )
    items = [
        HistoryItem(
            task_id=t["id"], tool=t["tool"], status=t["status"],
            input_size=t.get("input_size"), output_size=t.get("output_size"),
            created_at=t.get("created_at"),
        )
        for t in tasks
    ]
    return {"tasks": items}


@router.delete("/task/{task_id}")
async def delete_task(
    task_id: str,
    x_client_id: str | None = Header(None),
    client: str | None = Query(None),
):
    """Delete a task and its files, but only for the browser that created it.

    Same ownership rule as the download: a task with a stored client id may only
    be deleted by that browser; a task with no owner (queued straight to the API)
    stays deletable by anyone holding the id.
    """
    task = await db_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    owner = task.get("client_id")
    if owner is not None and client_id_from(x_client_id) != owner and client_id_from(client) != owner:
        raise HTTPException(status_code=403, detail="this task belongs to another browser")
    file_service.cleanup_task(task_id)
    await database.adelete_task(task_id)
    return {"deleted": task_id}
