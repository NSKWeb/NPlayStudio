"""Shared pytest fixtures.

Every fixture produces *real* media with ffmpeg, and the API is exercised through
a real HTTP client against the real app with the real worker (inline mode), so
nothing here is mocked.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _set_env(tmp_root: Path) -> None:
    os.environ.update(
        {
            "NPLAY_ENV": "test",
            "NPLAY_DB_PATH": str(tmp_root / "data" / "test.db"),
            "NPLAY_UPLOAD_DIR": str(tmp_root / "uploads"),
            "NPLAY_OUTPUT_DIR": str(tmp_root / "outputs"),
            "NPLAY_INLINE_WORKER": "true",
            "NPLAY_CORS_ORIGINS": "*",
            "NPLAY_MAX_UPLOAD_MB": "64",
        }
    )


@pytest.fixture(scope="session")
def tmp_root(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("nplay")


@pytest.fixture(scope="session")
def settings(tmp_root):
    _set_env(tmp_root)
    from backend.app.config import get_settings

    get_settings.cache_clear()
    return get_settings()


@pytest.fixture(scope="session")
def client(settings):
    from backend.app.database import reset_db_cache
    from backend.app.main import create_app
    from fastapi.testclient import TestClient

    reset_db_cache()
    with TestClient(create_app()) as test_client:
        yield test_client
    reset_db_cache()


# ------------------------------------------------------------------ media


def _ffmpeg(args: list[str]) -> None:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg fixture failed: {result.stderr[-800:]}")


@pytest.fixture(scope="session")
def sample_mp4(tmp_root) -> Path:
    """3s H.264 + AAC clip, 320x240."""
    path = tmp_root / "sample.mp4"
    if not path.exists():
        _ffmpeg([
            "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(path),
        ])
    return path


@pytest.fixture(scope="session")
def second_mp4(tmp_root) -> Path:
    """A different 2s clip used for merge tests."""
    path = tmp_root / "sample2.mp4"
    if not path.exists():
        _ffmpeg([
            "-f", "lavfi", "-i", "smptebars=size=320x240:rate=25:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=660:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(path),
        ])
    return path


@pytest.fixture(scope="session")
def video_only_mp4(tmp_root) -> Path:
    """A 2s clip with *no* audio track, used to test merge normalisation."""
    path = tmp_root / "silent.mp4"
    if not path.exists():
        _ffmpeg([
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(path),
        ])
    return path


@pytest.fixture(scope="session")
def sample_mp3(tmp_root) -> Path:
    path = tmp_root / "sample.mp3"
    if not path.exists():
        _ffmpeg(["-f", "lavfi", "-i", "sine=frequency=330:duration=2",
                 "-c:a", "libmp3lame", "-b:a", "128k", str(path)])
    return path


@pytest.fixture(scope="session")
def sample_png(tmp_root) -> Path:
    path = tmp_root / "sample.png"
    if not path.exists():
        _ffmpeg(["-f", "lavfi", "-i", "testsrc=size=400x300:duration=1",
                 "-frames:v", "1", str(path)])
    return path


@pytest.fixture(scope="session")
def sample_jpg(tmp_root) -> Path:
    path = tmp_root / "sample.jpg"
    if not path.exists():
        _ffmpeg(["-f", "lavfi", "-i", "testsrc=size=400x300:duration=1",
                 "-frames:v", "1", "-q:v", "3", str(path)])
    return path


@pytest.fixture(scope="session")
def junk_file(tmp_root) -> Path:
    path = tmp_root / "notes.txt"
    path.write_text("not media", encoding="utf-8")
    return path


# ------------------------------------------------------------- assertions

SIGNATURES = {
    "mp4": lambda head: head[4:8] == b"ftyp",
    "mov": lambda head: head[4:8] == b"ftyp",
    "mkv": lambda head: head[:4] == b"\x1a\x45\xdf\xa3",
    "webm": lambda head: head[:4] == b"\x1a\x45\xdf\xa3",
    "avi": lambda head: head[:4] == b"RIFF" and head[8:12] == b"AVI ",
    "gif": lambda head: head[:4] == b"GIF8",
    "png": lambda head: head[:4] == b"\x89PNG",
    "jpg": lambda head: head[:3] == b"\xff\xd8\xff",
    "jpeg": lambda head: head[:3] == b"\xff\xd8\xff",
    "webp": lambda head: head[:4] == b"RIFF" and head[8:12] == b"WEBP",
    "bmp": lambda head: head[:2] == b"BM",
    "tiff": lambda head: head[:4] in (b"II*\x00", b"MM\x00*"),
    "mp3": lambda head: head[:3] == b"ID3" or head[0] == 0xFF,
    "m4a": lambda head: head[4:8] == b"ftyp",
    "aac": lambda head: head[0] == 0xFF,
    "wav": lambda head: head[:4] == b"RIFF" and head[8:12] == b"WAVE",
    "flac": lambda head: head[:4] == b"fLaC",
    "opus": lambda head: head[:4] == b"OggS",
    "ogg": lambda head: head[:4] == b"OggS",
}


def assert_signature(path: Path, container: str) -> None:
    head = path.read_bytes()[:16]
    check = SIGNATURES[container]
    assert check(head), f"{path.name} does not look like {container}: {head!r}"
