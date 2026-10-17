"""Video tool endpoints."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from ..schemas import (
    VIDEO_CODECS,
    VideoCompressForm,
    VideoConvertForm,
    VideoMergeForm,
    VideoToGifForm,
)
from ..services.file_service import VIDEO_EXT
from .common import submit_job

router = APIRouter(prefix="/api/video", tags=["video"])


def _validated(model: type, **values):
    try:
        return model(**values)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


@router.post("/compress")
async def compress(
    file: UploadFile = File(...),
    quality: str = Form("balanced"),
    crf: int | None = Form(None),
    resolution: str | None = Form(None),
    codec: str = Form("h264"),
    format: str = Form("mp4"),
    audio_bitrate: str = Form("128k"),
):
    form = _validated(
        VideoCompressForm, quality=quality, crf=crf, resolution=resolution,
        codec=codec, format=format, audio_bitrate=audio_bitrate,
    )
    if form.codec not in VIDEO_CODECS[form.format]:
        raise HTTPException(
            status_code=422,
            detail=f"codec '{form.codec}' is not supported for {form.format}; "
                   f"use one of {VIDEO_CODECS[form.format]}",
        )
    return await submit_job("video/compress", form.model_dump(), [file], VIDEO_EXT)


@router.post("/convert")
async def convert(
    file: UploadFile = File(...),
    format: str = Form("mp4"),
    codec: str | None = Form(None),
    resolution: str | None = Form(None),
):
    form = _validated(VideoConvertForm, format=format, codec=codec, resolution=resolution)
    return await submit_job("video/convert", form.model_dump(), [file], VIDEO_EXT)


@router.post("/merge")
async def merge(
    files: list[UploadFile] = File(...),
    format: str = Form("mp4"),
    reencode: bool = Form(False),
    resolution: str | None = Form(None),
):
    from ..config import get_settings

    if len(files) < 2:
        raise HTTPException(status_code=422, detail="merge needs at least 2 files")
    if len(files) > get_settings().max_merge_files:
        raise HTTPException(
            status_code=422,
            detail=f"merge accepts at most {get_settings().max_merge_files} files",
        )
    form = _validated(VideoMergeForm, format=format, reencode=reencode, resolution=resolution)
    return await submit_job("video/merge", form.model_dump(), files, VIDEO_EXT)


@router.post("/to-gif")
async def to_gif(
    file: UploadFile = File(...),
    fps: int = Form(10),
    width: int = Form(480),
    duration: float | None = Form(None),
    start: float = Form(0),
):
    form = _validated(VideoToGifForm, fps=fps, width=width, duration=duration, start=start)
    return await submit_job("video/to-gif", form.model_dump(), [file], VIDEO_EXT)
