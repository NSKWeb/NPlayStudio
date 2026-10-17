"""Audio tool endpoints."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from pydantic import ValidationError

from ..schemas import AudioConvertForm, AudioExtractForm
from ..services.file_service import AUDIO_EXT, VIDEO_EXT
from .common import client_id_from, submit_job

router = APIRouter(prefix="/api/audio", tags=["audio"])

# Audio can be pulled from a video file too, so both sets are accepted.
_ACCEPTED = AUDIO_EXT | VIDEO_EXT


def _validated(model: type, **values):
    try:
        return model(**values)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


@router.post("/extract")
async def extract(
    file: UploadFile = File(...),
    format: str = Form("mp3"),
    bitrate: str = Form("192k"),
    sample_rate: int | None = Form(None),
    x_client_id: str | None = Header(None),
):
    form = _validated(AudioExtractForm, format=format, bitrate=bitrate, sample_rate=sample_rate)
    return await submit_job(
        "audio/extract", form.model_dump(), [file], _ACCEPTED,
        client_id_from(x_client_id),
    )


@router.post("/convert")
async def convert(
    file: UploadFile = File(...),
    format: str = Form("mp3"),
    bitrate: str = Form("192k"),
    sample_rate: int | None = Form(None),
    x_client_id: str | None = Header(None),
):
    form = _validated(AudioConvertForm, format=format, bitrate=bitrate, sample_rate=sample_rate)
    return await submit_job(
        "audio/convert", form.model_dump(), [file], _ACCEPTED,
        client_id_from(x_client_id),
    )
