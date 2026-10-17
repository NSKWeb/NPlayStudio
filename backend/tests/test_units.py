"""Database layer and service unit tests (no HTTP)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.app.database import Database
from backend.app.services import ffmpeg_service, file_service, pillow_service


@pytest.fixture()
def db(tmp_path) -> Database:
    database = Database(tmp_path / "unit.db")
    yield database
    database.close()


# ------------------------------------------------------------------ schema


def test_schema_creates_all_tables(db):
    with db._lock:  # noqa: SLF001 - asserting on real schema
        names = {
            row[0] for row in
            db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"tasks", "downloads", "stats", "presets"} <= names


def test_task_lifecycle_and_settings_roundtrip(db):
    task = db.create_task("t1", "video/compress", {"quality": "high", "crf": 20},
                          input_file="t1/in.mp4", input_size=1000)
    assert task["status"] == "queued"
    assert task["progress"] == 0
    assert task["settings"] == {"quality": "high", "crf": 20}

    db.update_task("t1", status="processing", progress=45)
    mid = db.get_task("t1")
    assert mid["status"] == "processing" and mid["progress"] == 45
    assert mid["updated_at"] >= mid["created_at"]

    db.update_task("t1", status="done", progress=100, output_size=400)
    done = db.get_task("t1")
    assert done["status"] == "done" and done["output_size"] == 400


def test_update_ignores_unknown_columns(db):
    db.create_task("t2", "audio/extract", {})
    db.update_task("t2", not_a_column="nope", status="done")
    assert db.get_task("t2")["status"] == "done"


def test_list_tasks_filters_and_orders(db):
    db.create_task("a", "video/merge", {})
    db.create_task("b", "image/crop", {})
    db.create_task("c", "image/crop", {})
    assert {t["id"] for t in db.list_tasks(tool="image/crop")} == {"b", "c"}
    assert len(db.list_tasks()) == 3


def test_stats_accumulate_and_group(db):
    db.create_task("s1", "video/compress", {})
    db.update_task("s1", status="done")
    db.create_task("s2", "image/crop", {})
    db.bump_stats(tasks=1, input_bytes=500, output_bytes=200)
    db.bump_stats(tasks=1, input_bytes=100, output_bytes=50)

    stats = db.get_stats()
    assert stats["total_tasks"] == 2
    assert stats["total_input_bytes"] == 600
    assert stats["total_output_bytes"] == 250
    assert stats["by_status"] == {"done": 1, "queued": 1}
    assert stats["by_tool"] == {"video/compress": 1, "image/crop": 1}
    assert stats["today"]["total_tasks"] == 2


def test_download_recording_bumps_download_stats(db):
    db.create_task("d1", "video/compress", {})
    db.record_download("d1", "10.0.0.1", "pytest-agent")
    downloads = db.list_downloads()
    assert downloads[0]["task_id"] == "d1"
    assert downloads[0]["ip_address"] == "10.0.0.1"
    assert db.get_stats()["total_downloads"] == 1


def test_presets_crud(db):
    preset = db.create_preset("Podcast", "audio/convert", {"format": "mp3", "bitrate": "128k"})
    assert preset["id"] > 0
    assert preset["settings"]["bitrate"] == "128k"
    assert [p["name"] for p in db.list_presets(tool="audio/convert")] == ["Podcast"]
    assert db.list_presets(tool="video/compress") == []


def test_delete_task_also_removes_downloads(db):
    db.create_task("x", "video/compress", {})
    db.record_download("x")
    db.delete_task("x")
    assert db.get_task("x") is None
    assert db.list_downloads() == []


def test_tasks_for_cleanup_uses_cutoff(db):
    db.create_task("fresh", "video/compress", {})
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    with db._lock:  # noqa: SLF001 - backdate the row directly
        db._conn.execute(
            "UPDATE tasks SET created_at = ? WHERE id = ?",
            (old.strftime("%Y-%m-%d %H:%M:%S"), "fresh"),
        )
        db._conn.commit()
    found = db.tasks_for_cleanup(datetime.now(timezone.utc) - timedelta(hours=1))
    assert [t["id"] for t in found] == ["fresh"]


# --------------------------------------------------------------- services


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("My Video (final).MP4", "My_Video_final_.MP4"),
        ("../../etc/passwd", "passwd"),
        ("", "upload"),
        ("   ", "upload"),
        ("cl\u00e9.mp4", "cle.mp4"),
    ],
)
def test_safe_filename(raw, expected):
    assert file_service.safe_filename(raw) == expected


def test_safe_filename_keeps_extension_when_truncating():
    """A long name is cut on the stem, so the extension survives the allowlist."""
    result = file_service.safe_filename("x" * 200 + ".mp4")
    assert result.endswith(".mp4")
    assert len(result) <= 120


def test_unique_path_avoids_overwriting_same_name(tmp_path):
    first = file_service.unique_path(tmp_path, "clip.mp4")
    first.write_bytes(b"a")
    second = file_service.unique_path(tmp_path, "clip.mp4")
    assert second != first
    assert second.name == "clip_1.mp4"


@pytest.mark.parametrize(
    "name,kind",
    [("a.mp4", "video"), ("a.mkv", "video"), ("a.mp3", "audio"),
     ("a.flac", "audio"), ("a.png", "image"), ("a.webp", "image"), ("a.txt", "unknown")],
)
def test_media_kind(name, kind):
    assert file_service.media_kind(name) == kind


def test_resolve_within_blocks_escape(tmp_path):
    base = tmp_path / "out"
    base.mkdir()
    (base / "ok.mp4").write_bytes(b"x")
    assert file_service.resolve_within(base, "ok.mp4") == (base / "ok.mp4").resolve()
    with pytest.raises(ValueError):
        file_service.resolve_within(base, "../secret.txt")


def test_human_bytes_formatting():
    assert file_service.human_bytes(0) == "0 B"
    assert file_service.human_bytes(512) == "512 B"
    assert file_service.human_bytes(2048) == "2.0 KB"
    assert file_service.human_bytes(5 * 1024 * 1024) == "5.0 MB"


def test_is_expired():
    now = datetime.now(timezone.utc)
    fresh = now.strftime("%Y-%m-%d %H:%M:%S")
    assert file_service.is_expired(fresh, 3600) is False
    assert file_service.is_expired(fresh, -1) is True
    assert file_service.is_expired(None, 3600) is False
    assert file_service.is_expired("not-a-date", 3600) is False


def test_crf_presets_ordering():
    assert ffmpeg_service.QUALITY_CRF["high"] < ffmpeg_service.QUALITY_CRF["balanced"]
    assert ffmpeg_service.QUALITY_CRF["balanced"] < ffmpeg_service.QUALITY_CRF["small"]


def test_scale_filter_uses_even_dimensions():
    assert ffmpeg_service._scale_filter(None) == []
    assert ffmpeg_service._scale_filter("1280x720") == ["-vf", "scale=1280:720"]
    # Odd dimensions are rounded down; libx264 rejects them outright.
    assert ffmpeg_service._scale_filter("641x361") == ["-vf", "scale=640:360"]
    assert ffmpeg_service._scale_filter("3x3") == ["-vf", "scale=2:2"]


def test_audio_args_lossless_ignores_bitrate():
    args = ffmpeg_service._audio_args("flac", "320k")
    assert "flac" in args and "-b:a" not in args
    lossy = ffmpeg_service._audio_args("mp3", "192k")
    assert "-b:a" in lossy and "192k" in lossy


def test_extract_percentage():
    assert ffmpeg_service.extract_percentage("frame= 42 time=00:00:01.5 55%") == 55
    assert ffmpeg_service.extract_percentage("no percent here") is None


def test_clamp_progress():
    assert ffmpeg_service.clamp_progress(-5) == 0
    assert ffmpeg_service.clamp_progress(150) == 100


def test_pillow_compress_reduces_size(tmp_path, sample_png):
    out = tmp_path / "small.webp"
    pillow_service.compress(sample_png, out, quality=40, container="webp", max_width=150)
    assert out.exists() and out.stat().st_size > 0
    width, _ = pillow_service.dimensions(out)
    assert width == 150


def test_pillow_crop_dimensions(tmp_path, sample_png):
    out = tmp_path / "crop.png"
    pillow_service.crop(sample_png, out, x=5, y=5, width=100, height=80, container="png")
    assert pillow_service.dimensions(out) == (100, 80)


def test_pillow_crop_rejects_box_that_misses_the_image(tmp_path, sample_png):
    with pytest.raises(ValueError):
        pillow_service.crop(sample_png, tmp_path / "x.png",
                            x=9999, y=9999, width=10, height=10, container="png")


def test_pillow_crop_clamps_box_to_image_bounds(tmp_path, sample_png):
    """An oversized box is clamped, not rejected."""
    out = tmp_path / "clamped.png"
    pillow_service.crop(sample_png, out, x=350, y=250, width=9999, height=9999, container="png")
    assert pillow_service.dimensions(out) == (50, 50)


def test_pillow_convert_jpg_flattens_alpha(tmp_path, sample_png):
    out = tmp_path / "flat.jpg"
    pillow_service.convert(sample_png, out, container="jpg", quality=70)
    assert out.read_bytes()[:3] == b"\xff\xd8\xff"
