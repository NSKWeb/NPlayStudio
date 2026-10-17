"""End-to-end tests: upload real media, let the worker run, download the result."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from .conftest import assert_signature
from backend.app.config import get_settings

POLL_LIMIT = 90  # seconds


def _submit(client, endpoint: str, files, data=None):
    payload = data or {}
    response = client.post(endpoint, files=files, data=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "queued"
    return body["task_id"]


def _wait(client, task_id: str) -> dict:
    deadline = time.time() + POLL_LIMIT
    last = {}
    while time.time() < deadline:
        response = client.get(f"/api/status/{task_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in ("done", "failed"):
            return last
        time.sleep(0.25)
    pytest.fail(f"task {task_id} did not finish in time; last={last}")


def _run(client, endpoint: str, files, data=None, container: str | None = None) -> dict:
    task_id = _submit(client, endpoint, files, data)
    status = _wait(client, task_id)
    assert status["status"] == "done", status.get("error")

    download = client.get(f"/api/file/{task_id}")
    assert download.status_code == 200, download.text
    assert len(download.content) > 0
    if container:
        # output_file is relative to the configured output directory.
        produced = get_settings().output_path / status["output_file"]
        suffix = produced.suffix.lower().lstrip(".")
        assert suffix == container, f"expected .{container}, got .{suffix}"
        assert_signature(produced, container)
    return status


# ------------------------------------------------------------------ health


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_tools_catalogue(client):
    tools = client.get("/api/tools").json()["tools"]
    assert len(tools) == 9
    assert {t["category"] for t in tools} == {"video", "audio", "image"}


# ------------------------------------------------------------------- video


def test_video_compress(client, sample_mp4):
    with sample_mp4.open("rb") as handle:
        status = _run(
            client, "/api/video/compress",
            {"file": (sample_mp4.name, handle, "video/mp4")},
            {"quality": "small", "format": "mp4", "codec": "h264"},
            container="mp4",
        )
    assert status["output_size"] > 0
    assert status["input_size"] > 0


def test_video_compress_webm_vp9(client, sample_mp4):
    with sample_mp4.open("rb") as handle:
        _run(
            client, "/api/video/compress",
            {"file": (sample_mp4.name, handle, "video/mp4")},
            {"quality": "balanced", "format": "webm", "codec": "vp9", "resolution": "160x120"},
            container="webm",
        )


def test_video_convert_to_mkv(client, sample_mp4):
    with sample_mp4.open("rb") as handle:
        _run(
            client, "/api/video/convert",
            {"file": (sample_mp4.name, handle, "video/mp4")},
            {"format": "mkv"},
            container="mkv",
        )


def test_video_convert_to_avi(client, sample_mp4):
    with sample_mp4.open("rb") as handle:
        _run(
            client, "/api/video/convert",
            {"file": (sample_mp4.name, handle, "video/mp4")},
            {"format": "avi", "resolution": "160x120"},
            container="avi",
        )


def test_video_merge_stream_copy(client, sample_mp4, second_mp4):
    with sample_mp4.open("rb") as a, second_mp4.open("rb") as b:
        status = _run(
            client, "/api/video/merge",
            [("files", (sample_mp4.name, a, "video/mp4")),
             ("files", (second_mp4.name, b, "video/mp4"))],
            {"format": "mp4"},
            container="mp4",
        )
    # Merged output should be at least as large as the larger input.
    assert status["output_size"] >= max(sample_mp4.stat().st_size, second_mp4.stat().st_size) * 0.9


def test_video_merge_reencode(client, sample_mp4, second_mp4):
    with sample_mp4.open("rb") as a, second_mp4.open("rb") as b:
        _run(
            client, "/api/video/merge",
            [("files", (sample_mp4.name, a, "video/mp4")),
             ("files", (second_mp4.name, b, "video/mp4"))],
            {"format": "mp4", "reencode": "true", "resolution": "160x120"},
            container="mp4",
        )


def test_video_merge_reencode_mixed_audio(client, sample_mp4, video_only_mp4):
    """A clip with audio plus a silent clip must still merge."""
    with sample_mp4.open("rb") as a, video_only_mp4.open("rb") as b:
        status = _run(
            client, "/api/video/merge",
            [("files", (sample_mp4.name, a, "video/mp4")),
             ("files", (video_only_mp4.name, b, "video/mp4"))],
            {"format": "mp4", "reencode": "true", "resolution": "160x120"},
            container="mp4",
        )
    assert status["output_size"] > 0


def test_video_merge_reencode_both_silent(client, video_only_mp4, second_mp4):
    """Two clips with no audio at all merge to a video-only file."""
    silent_second = video_only_mp4.parent / "silent2.mp4"
    if not silent_second.exists():
        import subprocess

        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=1",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(silent_second)],
            check=True,
        )
    with video_only_mp4.open("rb") as a, silent_second.open("rb") as b:
        _run(
            client, "/api/video/merge",
            [("files", (video_only_mp4.name, a, "video/mp4")),
             ("files", (silent_second.name, b, "video/mp4"))],
            {"format": "mp4", "reencode": "true"},
            container="mp4",
        )


def test_video_to_gif(client, sample_mp4):
    with sample_mp4.open("rb") as handle:
        _run(
            client, "/api/video/to-gif",
            {"file": (sample_mp4.name, handle, "video/mp4")},
            {"fps": "8", "width": "160", "duration": "1"},
            container="gif",
        )


# ------------------------------------------------------------------- audio


@pytest.mark.parametrize("target", ["mp3", "m4a", "wav", "flac", "opus", "aac", "ogg"])
def test_audio_extract_from_video(client, sample_mp4, target):
    with sample_mp4.open("rb") as handle:
        _run(
            client, "/api/audio/extract",
            {"file": (sample_mp4.name, handle, "video/mp4")},
            {"format": target},
            container=target,
        )


@pytest.mark.parametrize("target", ["mp3", "wav", "flac", "ogg"])
def test_audio_convert(client, sample_mp3, target):
    with sample_mp3.open("rb") as handle:
        _run(
            client, "/api/audio/convert",
            {"file": (sample_mp3.name, handle, "audio/mpeg")},
            {"format": target},
            container=target,
        )


# ------------------------------------------------------------------- image


def test_image_compress_png_quality(client, sample_png):
    with sample_png.open("rb") as handle:
        _run(
            client, "/api/image/compress",
            {"file": (sample_png.name, handle, "image/png")},
            {"quality": "50"},
            container="png",
        )


def test_image_compress_to_webp_with_resize(client, sample_jpg):
    with sample_jpg.open("rb") as handle:
        status = _run(
            client, "/api/image/compress",
            {"file": (sample_jpg.name, handle, "image/jpeg")},
            {"quality": "60", "format": "webp", "max_width": "100"},
            container="webp",
        )
    assert status["output_size"] < status["input_size"]


def test_image_convert(client, sample_png):
    with sample_png.open("rb") as handle:
        _run(
            client, "/api/image/convert",
            {"file": (sample_png.name, handle, "image/png")},
            {"format": "jpg", "quality": "80"},
            container="jpg",
        )


def test_image_crop(client, sample_png):
    with sample_png.open("rb") as handle:
        _run(
            client, "/api/image/crop",
            {"file": (sample_png.name, handle, "image/png")},
            {"x": "10", "y": "10", "width": "120", "height": "90", "format": "png"},
            container="png",
        )


def test_image_crop_clamps_oversized_box(client, sample_png):
    """A box larger than the image is clamped rather than failing."""
    with sample_png.open("rb") as handle:
        _run(
            client, "/api/image/crop",
            {"file": (sample_png.name, handle, "image/png")},
            {"x": "0", "y": "0", "width": "9999", "height": "9999", "format": "png"},
            container="png",
        )


def test_image_crop_outside_bounds_fails(client, sample_png):
    with sample_png.open("rb") as handle:
        task_id = _submit(
            client, "/api/image/crop",
            {"file": (sample_png.name, handle, "image/png")},
            {"x": "99999", "y": "99999", "width": "10", "height": "10"},
        )
    status = _wait(client, task_id)
    assert status["status"] == "failed"
    assert "outside" in (status["error"] or "").lower()


# -------------------------------------------------------------- validation


def test_rejects_unsupported_extension(client, junk_file):
    with junk_file.open("rb") as handle:
        response = client.post(
            "/api/video/compress",
            files={"file": (junk_file.name, handle, "text/plain")},
        )
    assert response.status_code == 415
    assert "unsupported file type" in response.json()["detail"]


def test_rejects_codec_container_mismatch(client, sample_mp4):
    with sample_mp4.open("rb") as handle:
        response = client.post(
            "/api/video/compress",
            files={"file": (sample_mp4.name, handle, "video/mp4")},
            data={"format": "webm", "codec": "h264"},
        )
    assert response.status_code == 422
    assert "not supported" in str(response.json()["detail"])


def test_rejects_oversized_upload(client, sample_mp4, monkeypatch):
    from backend.app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "max_upload_mb", 0, raising=False)
    with sample_mp4.open("rb") as handle:
        response = client.post(
            "/api/video/compress",
            files={"file": (sample_mp4.name, handle, "video/mp4")},
        )
    monkeypatch.undo()
    assert response.status_code == 413


def test_merge_requires_two_files(client, sample_mp4):
    with sample_mp4.open("rb") as handle:
        response = client.post(
            "/api/video/merge",
            files=[("files", (sample_mp4.name, handle, "video/mp4"))],
        )
    assert response.status_code == 422
    assert "at least 2" in str(response.json()["detail"])


def test_unknown_task_returns_404(client):
    assert client.get("/api/status/does-not-exist").status_code == 404
    assert client.get("/api/file/does-not-exist").status_code == 404


def test_download_before_ready_returns_409(client, sample_mp4):
    """A queued-but-unfinished task cannot be downloaded."""
    from backend.app.database import get_db

    db = get_db()
    task = db.create_task("pending-test", "video/compress", {})
    assert task["status"] == "queued"
    response = client.get("/api/file/pending-test")
    assert response.status_code == 409
    db.delete_task("pending-test")


# ------------------------------------------------------- regression guards


def _duration(path) -> float:
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    return float(out.stdout.strip())


def test_video_merge_identical_filenames_keeps_both_inputs(client, sample_mp4, second_mp4):
    """Two uploads sharing a filename must not overwrite each other.

    Parts are written to the same directory, so a naive ``dir / name`` silently
    replaces the first file and the merge returns the second clip twice. The
    merged duration proves whether both inputs survived.
    """
    expected = _duration(sample_mp4) + _duration(second_mp4)
    with sample_mp4.open("rb") as a, second_mp4.open("rb") as b:
        status = _run(
            client, "/api/video/merge",
            [("files", ("clip.mp4", a, "video/mp4")),
             ("files", ("clip.mp4", b, "video/mp4"))],
            {"format": "mp4"},
            container="mp4",
        )

    produced = get_settings().output_path / status["output_file"]
    actual = _duration(produced)
    assert actual >= expected * 0.9, (
        f"merged duration {actual:.2f}s is shorter than the {expected:.2f}s of both "
        "inputs, so an upload was overwritten"
    )


def test_video_compress_accepts_odd_resolution(client, sample_mp4):
    """Odd target dimensions are rounded down instead of failing the encode."""
    with sample_mp4.open("rb") as handle:
        _run(
            client, "/api/video/compress",
            {"file": (sample_mp4.name, handle, "video/mp4")},
            {"resolution": "161x121", "quality": "small", "format": "mp4", "codec": "h264"},
            container="mp4",
        )


def test_video_to_gif_rejects_start_past_end(client, sample_mp4):
    """A start offset beyond the clip fails with a clear message, not an empty GIF."""
    with sample_mp4.open("rb") as handle:
        task_id = _submit(
            client, "/api/video/to-gif",
            {"file": (sample_mp4.name, handle, "video/mp4")},
            {"fps": "10", "width": "160", "start": "3600"},
        )
    status = _wait(client, task_id)
    assert status["status"] == "failed"
    assert "beyond the video length" in (status.get("error") or "")

    from backend.app.database import get_db

    db = get_db()
    task = db.create_task("pending-test", "video/compress", {})
    assert task["status"] == "queued"
    response = client.get("/api/file/pending-test")
    assert response.status_code == 409
    db.delete_task("pending-test")


def test_path_traversal_is_blocked(client):
    from backend.app.database import get_db
    from backend.app.services import file_service

    db = get_db()
    db.create_task("traversal-test", "video/compress", {}, status="done")
    db.update_task("traversal-test", status="done", output_file="../../etc/passwd")
    response = client.get("/api/file/traversal-test")
    assert response.status_code in (400, 410)
    db.delete_task("traversal-test")


# ------------------------------------------------------------ stats/history


def test_stats_reflect_completed_jobs(client, sample_png):
    before = client.get("/api/stats").json()["total_tasks"]
    with sample_png.open("rb") as handle:
        _run(
            client, "/api/image/compress",
            {"file": (sample_png.name, handle, "image/png")},
            {"quality": "40"},
            container="png",
        )
    after = client.get("/api/stats").json()
    assert after["total_tasks"] == before + 1
    assert after["by_tool"].get("image/compress", 0) >= 1


def test_history_lists_tasks_and_downloads(client, sample_png):
    with sample_png.open("rb") as handle:
        task_id = _submit(
            client, "/api/image/convert",
            {"file": (sample_png.name, handle, "image/png")},
            {"format": "webp"},
        )
    assert _wait(client, task_id)["status"] == "done"
    client.get(f"/api/file/{task_id}")

    body = client.get("/api/history?limit=5").json()
    assert any(t["task_id"] == task_id for t in body["tasks"])
    assert any(d["task_id"] == task_id for d in body["downloads"])
    assert body["tasks"][0]["tool"].startswith("image/")


def test_delete_task_removes_files(client, sample_png):
    with sample_png.open("rb") as handle:
        task_id = _submit(
            client, "/api/image/convert",
            {"file": (sample_png.name, handle, "image/png")},
            {"format": "png"},
        )
    assert _wait(client, task_id)["status"] == "done"
    assert client.delete(f"/api/task/{task_id}").status_code == 200
    assert client.get(f"/api/status/{task_id}").status_code == 404
