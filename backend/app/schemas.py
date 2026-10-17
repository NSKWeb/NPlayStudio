"""Pydantic request/response models for the public API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# ------------------------------------------------------------------ enums

VideoFormat = Literal["mp4", "mkv", "webm", "avi", "mov"]
AudioFormat = Literal["mp3", "m4a", "wav", "flac", "opus", "aac", "ogg"]
ImageFormat = Literal["jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff"]

QualityPreset = Literal["small", "balanced", "high"]
CodecChoice = Literal["h264", "h265", "vp9"]

VIDEO_CODECS: dict[str, list[str]] = {
    "mp4": ["h264", "h265"],
    "mkv": ["h264", "h265", "vp9"],
    "webm": ["vp9"],
    "avi": ["h264"],
    "mov": ["h264", "h265"],
}


class _TaskAccepted(BaseModel):
    task_id: str
    status: str = "queued"
    tool: str


class TaskStatus(BaseModel):
    task_id: str
    tool: str
    status: str
    progress: int
    input_size: int | None = None
    output_size: int | None = None
    output_file: str | None = None
    error: str | None = None
    download_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class HistoryItem(BaseModel):
    task_id: str
    tool: str
    status: str
    input_size: int | None = None
    output_size: int | None = None
    created_at: str | None = None


class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    tool: str = Field(min_length=1, max_length=64)
    settings: dict[str, Any]


# ------------------------------------------------------------ form schemas


class VideoCompressForm(BaseModel):
    quality: QualityPreset = "balanced"
    crf: int | None = Field(default=None, ge=0, le=51)
    resolution: str | None = None
    codec: CodecChoice = "h264"
    format: VideoFormat = "mp4"
    audio_bitrate: str = "128k"

    @field_validator("resolution")
    @classmethod
    def _check_resolution(cls, value: str | None) -> str | None:
        if value in (None, "", "original"):
            return None
        if "x" not in value:
            raise ValueError("resolution must look like WIDTHxHEIGHT")
        width, height = value.lower().split("x", 1)
        if not width.isdigit() or not height.isdigit():
            raise ValueError("resolution must look like WIDTHxHEIGHT")
        return f"{int(width)}x{int(height)}"


class VideoConvertForm(BaseModel):
    format: VideoFormat = "mp4"
    codec: CodecChoice | None = None
    resolution: str | None = None
    audio_format: str | None = None

    @field_validator("resolution")
    @classmethod
    def _check_resolution(cls, value: str | None) -> str | None:
        if value in (None, "", "original"):
            return None
        if "x" not in value:
            raise ValueError("resolution must look like WIDTHxHEIGHT")
        width, height = value.lower().split("x", 1)
        if not width.isdigit() or not height.isdigit():
            raise ValueError("resolution must look like WIDTHxHEIGHT")
        return f"{int(width)}x{int(height)}"


class VideoMergeForm(BaseModel):
    format: VideoFormat = "mp4"
    reencode: bool = False
    resolution: str | None = None


class VideoToGifForm(BaseModel):
    fps: int = Field(default=10, ge=1, le=50)
    width: int = Field(default=480, ge=64, le=1920)
    duration: float | None = Field(default=None, gt=0, le=120)
    start: float = Field(default=0, ge=0)


class AudioExtractForm(BaseModel):
    format: AudioFormat = "mp3"
    bitrate: str = "192k"
    sample_rate: int | None = Field(default=None, ge=8000, le=192000)


class AudioConvertForm(BaseModel):
    format: AudioFormat = "mp3"
    bitrate: str = "192k"
    sample_rate: int | None = Field(default=None, ge=8000, le=192000)


class ImageCompressForm(BaseModel):
    quality: int = Field(default=75, ge=1, le=100)
    format: ImageFormat | None = None
    max_width: int | None = Field(default=None, ge=16, le=20000)


class ImageConvertForm(BaseModel):
    format: ImageFormat = "webp"
    quality: int = Field(default=85, ge=1, le=100)


class ImageCropForm(BaseModel):
    x: int = Field(default=0, ge=0)
    y: int = Field(default=0, ge=0)
    width: int = Field(ge=1, le=20000)
    height: int = Field(ge=1, le=20000)
    format: ImageFormat | None = None
