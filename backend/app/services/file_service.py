"""Filesystem helpers: task directories, safe names, media probing, cleanup."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
import unicodedata
from pathlib import Path
from typing import Any

from ..config import get_settings

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")

# Extension -> logical media kind, used to validate uploads.
VIDEO_EXT = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".wmv", ".flv", ".mpeg", ".mpg"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".flac", ".opus", ".aac", ".ogg", ".oga", ".wma"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif"}


def safe_filename(name: str, fallback: str = "upload") -> str:
    """Strip directories and unsafe characters from a client-supplied filename."""
    stem = Path(name or "").name
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    stem = _SAFE_CHARS.sub("_", stem).strip("._-")
    if not stem:
        return fallback
    # Truncate the stem only, never the extension: a 120-char cut would strip
    # the suffix from a long name and the upload would fail the allowlist.
    path = Path(stem)
    suffix = path.suffix[:16]
    return f"{path.stem[: 120 - len(suffix)]}{suffix}"


def unique_path(directory: Path, filename: str) -> Path:
    """Return a non-colliding path for ``filename`` inside ``directory``.

    Uploads keep their sanitized name, so two inputs named the same would
    overwrite each other and the job would silently process one file twice.
    """
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for index in range(1, 1000):
        alternative = directory / f"{stem}_{index}{suffix}"
        if not alternative.exists():
            return alternative
    raise ValueError("too many files share the same name")


def media_kind(path: str | Path) -> str:
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    return "unknown"


def task_upload_dir(task_id: str) -> Path:
    path = get_settings().upload_path / task_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def task_output_dir(task_id: str) -> Path:
    path = get_settings().output_path / task_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_within(base: Path, candidate: str) -> Path:
    """Resolve ``candidate`` and refuse anything that escapes ``base``."""
    base = base.resolve()
    resolved = (base / candidate).resolve()
    if resolved != base and base not in resolved.parents:
        raise ValueError("path escapes the task directory")
    return resolved


async def probe(path: str | Path) -> dict[str, Any]:
    """Return duration/stream info via ffprobe. Never raises for odd inputs."""
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return {}
        data = json.loads(stdout or b"{}")
    except (OSError, json.JSONDecodeError):
        return {}

    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or 0.0)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {
        "duration": duration,
        "size": int(fmt.get("size") or 0),
        "bit_rate": int(fmt.get("bit_rate") or 0),
        "has_video": video is not None,
        "has_audio": audio is not None,
        "video_codec": (video or {}).get("codec_name"),
        "audio_codec": (audio or {}).get("codec_name"),
        "width": (video or {}).get("width"),
        "height": (video or {}).get("height"),
        "fps": _parse_fps((video or {}).get("avg_frame_rate")),
    }


def _parse_fps(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            den_f = float(den)
            return round(float(num) / den_f, 3) if den_f else None
        except ValueError:
            return None
    try:
        return float(value)
    except ValueError:
        return None


def file_size(path: str | Path) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def human_bytes(size: int | None) -> str:
    if not size:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def cleanup_task(task_id: str) -> None:
    """Remove upload and output directories for a finished/expired task."""
    for base in (get_settings().upload_path, get_settings().output_path):
        target = base / task_id
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


def disk_usage() -> dict[str, int]:
    settings = get_settings()
    return {
        "uploads_bytes": _dir_size(settings.upload_path),
        "outputs_bytes": _dir_size(settings.output_path),
    }


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


def new_task_id() -> str:
    import uuid

    return uuid.uuid4().hex


def is_expired(created_at: str | None, ttl_seconds: int) -> bool:
    if not created_at:
        return False
    from datetime import datetime, timezone

    try:
        created = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (time.time() - created.timestamp()) > ttl_seconds
