"""SQLite persistence layer.

Uses the stdlib ``sqlite3`` driver behind a single connection guarded by a lock.
Every public method is sync; async callers use the ``a*`` wrappers which push the
blocking work onto a thread so the event loop is never stalled.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    tool          TEXT NOT NULL,
    status        TEXT NOT NULL,
    progress      INTEGER DEFAULT 0,
    input_file    TEXT,
    output_file   TEXT,
    input_size    INTEGER,
    output_size   INTEGER,
    settings      TEXT,
    error         TEXT,
    client_id     TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_tool ON tasks (tool);

CREATE TABLE IF NOT EXISTS downloads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL,
    ip_address    TEXT,
    user_agent    TEXT,
    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_downloads_task_id ON downloads (task_id);

CREATE TABLE IF NOT EXISTS stats (
    date               TEXT PRIMARY KEY,
    total_tasks        INTEGER DEFAULT 0,
    total_downloads    INTEGER DEFAULT 0,
    total_input_bytes  INTEGER DEFAULT 0,
    total_output_bytes INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS presets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    tool          TEXT NOT NULL,
    settings      TEXT NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Columns callers are allowed to patch on a task row.
_UPDATABLE = {
    "status",
    "progress",
    "input_file",
    "output_file",
    "input_size",
    "output_size",
    "settings",
    "error",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self.init_schema()

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Add columns that older databases predate. Called on every startup.

        The client_id index is created here rather than in SCHEMA because a
        pre-existing tasks table has no such column until the ALTER below runs.
        """
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(tasks)")}
        if "client_id" not in existing:
            self._conn.execute("ALTER TABLE tasks ADD COLUMN client_id TEXT")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_client_id ON tasks (client_id, created_at DESC)"
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- tasks

    def create_task(self, task_id: str, tool: str, settings: dict[str, Any],
                    input_file: str | None = None, input_size: int | None = None,
                    status: str = "queued", client_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._conn.execute(
                """INSERT INTO tasks
                   (id, tool, status, progress, input_file, input_size, settings,
                    client_id, created_at, updated_at)
                   VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?)""",
                (task_id, tool, status, input_file, input_size,
                 json.dumps(settings), client_id, _now(), _now()),
            )
            self._conn.commit()
        return self.get_task(task_id)  # type: ignore[return-value]

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any] | None:
        patch = {k: v for k, v in fields.items() if k in _UPDATABLE}
        if not patch:
            return self.get_task(task_id)
        if "settings" in patch and not isinstance(patch["settings"], str):
            patch["settings"] = json.dumps(patch["settings"])
        assignments = ", ".join(f"{key} = ?" for key in patch)
        values = list(patch.values())
        with self._lock:
            self._conn.execute(
                f"UPDATE tasks SET {assignments}, updated_at = ? WHERE id = ?",
                (*values, _now(), task_id),
            )
            self._conn.commit()
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task_row(row) if row else None

    def list_tasks(self, limit: int = 20, tool: str | None = None,
                   client_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM tasks"
        params: list[Any] = []
        clauses: list[str] = []
        if tool:
            clauses.append("tool = ?")
            params.append(tool)
        if client_id:
            clauses.append("client_id = ?")
            params.append(client_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(max(1, min(limit, 200)))
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_task_row(r) for r in rows]

    def tasks_for_cleanup(self, older_than: datetime) -> list[dict[str, Any]]:
        stamp = older_than.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE created_at < ?", (stamp,)
            ).fetchall()
        return [_task_row(r) for r in rows]

    def delete_task(self, task_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM downloads WHERE task_id = ?", (task_id,))
            self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self._conn.commit()

    # ------------------------------------------------------------ downloads

    def record_download(self, task_id: str, ip_address: str | None = None,
                        user_agent: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO downloads (task_id, ip_address, user_agent) VALUES (?, ?, ?)",
                (task_id, ip_address, user_agent),
            )
            self._conn.commit()
        self.bump_stats(downloads=1)

    def list_downloads(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM downloads ORDER BY downloaded_at DESC, id DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- stats

    def bump_stats(self, tasks: int = 0, downloads: int = 0,
                   input_bytes: int = 0, output_bytes: int = 0) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO stats (date, total_tasks, total_downloads,
                                      total_input_bytes, total_output_bytes)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(date) DO UPDATE SET
                       total_tasks        = total_tasks + excluded.total_tasks,
                       total_downloads    = total_downloads + excluded.total_downloads,
                       total_input_bytes  = total_input_bytes + excluded.total_input_bytes,
                       total_output_bytes = total_output_bytes + excluded.total_output_bytes""",
                (_today(), tasks, downloads, input_bytes, output_bytes),
            )
            self._conn.commit()

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            totals = self._conn.execute(
                """SELECT COALESCE(SUM(total_tasks), 0)        AS total_tasks,
                          COALESCE(SUM(total_downloads), 0)    AS total_downloads,
                          COALESCE(SUM(total_input_bytes), 0)  AS total_input_bytes,
                          COALESCE(SUM(total_output_bytes), 0) AS total_output_bytes
                   FROM stats"""
            ).fetchone()
            by_tool = self._conn.execute(
                "SELECT tool, COUNT(*) AS count FROM tasks GROUP BY tool ORDER BY count DESC"
            ).fetchall()
            by_status = self._conn.execute(
                "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
            ).fetchall()
            today = self._conn.execute(
                "SELECT * FROM stats WHERE date = ?", (_today(),)
            ).fetchone()
        result = dict(totals)
        result["by_tool"] = {r["tool"]: r["count"] for r in by_tool}
        result["by_status"] = {r["status"]: r["count"] for r in by_status}
        result["today"] = dict(today) if today else None
        return result

    # -------------------------------------------------------------- presets

    def create_preset(self, name: str, tool: str, settings: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO presets (name, tool, settings, created_at) VALUES (?, ?, ?, ?)",
                (name, tool, json.dumps(settings), _now()),
            )
            self._conn.commit()
            preset_id = cur.lastrowid
        return self.get_preset(int(preset_id))  # type: ignore[arg-type]

    def get_preset(self, preset_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM presets WHERE id = ?", (preset_id,)).fetchone()
        return _preset_row(row) if row else None

    def list_presets(self, tool: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM presets"
        params: list[Any] = []
        if tool:
            query += " WHERE tool = ?"
            params.append(tool)
        query += " ORDER BY name"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_preset_row(r) for r in rows]


def _task_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    if isinstance(data.get("settings"), str):
        try:
            data["settings"] = json.loads(data["settings"])
        except json.JSONDecodeError:
            data["settings"] = {}
    return data


def _preset_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    if isinstance(data.get("settings"), str):
        try:
            data["settings"] = json.loads(data["settings"])
        except json.JSONDecodeError:
            data["settings"] = {}
    return data


_db: Database | None = None
_db_lock = threading.Lock()


def get_db() -> Database:
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = Database(get_settings().db_file)
    return _db


def reset_db_cache() -> None:
    """Drop the cached connection (used by tests)."""
    global _db
    with _db_lock:
        if _db is not None:
            _db.close()
        _db = None


# ------------------------------------------------------------- async wrappers


async def acreate_task(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return await asyncio.to_thread(get_db().create_task, *args, **kwargs)


async def aupdate_task(task_id: str, **fields: Any) -> dict[str, Any] | None:
    return await asyncio.to_thread(lambda: get_db().update_task(task_id, **fields))


async def aget_task(task_id: str) -> dict[str, Any] | None:
    return await asyncio.to_thread(get_db().get_task, task_id)


async def alist_tasks(limit: int = 20, tool: str | None = None,
                      client_id: str | None = None) -> list[dict[str, Any]]:
    return await asyncio.to_thread(lambda: get_db().list_tasks(limit, tool, client_id))


async def arecord_download(task_id: str, ip_address: str | None = None,
                           user_agent: str | None = None) -> None:
    await asyncio.to_thread(get_db().record_download, task_id, ip_address, user_agent)


async def abump_stats(**kwargs: int) -> None:
    await asyncio.to_thread(lambda: get_db().bump_stats(**kwargs))


async def aget_stats() -> dict[str, Any]:
    return await asyncio.to_thread(get_db().get_stats)


async def alist_presets(tool: str | None = None) -> list[dict[str, Any]]:
    return await asyncio.to_thread(get_db().list_presets, tool)


async def acreate_preset(name: str, tool: str, settings: dict[str, Any]) -> dict[str, Any]:
    return await asyncio.to_thread(get_db().create_preset, name, tool, settings)


async def adelete_task(task_id: str) -> None:
    await asyncio.to_thread(get_db().delete_task, task_id)


def iter_tasks_for_cleanup(older_than: datetime) -> Iterable[dict[str, Any]]:
    return get_db().tasks_for_cleanup(older_than)
