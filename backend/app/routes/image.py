"""Image tool endpoints."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from pydantic import ValidationError

from ..schemas import ImageCompressForm, ImageConvertForm, ImageCropForm
from ..services.file_service import IMAGE_EXT
from .common import client_id_from, submit_job

router = APIRouter(prefix="/api/image", tags=["image"])


def _validated(model: type, **values):
    try:
        return model(**values)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


@router.post("/compress")
async def compress(
    file: UploadFile = File(...),
    quality: int = Form(75),
    format: str | None = Form(None),
    max_width: int | None = Form(None),
    x_client_id: str | None = Header(None),
):
    form = _validated(ImageCompressForm, quality=quality, format=format, max_width=max_width)
    return await submit_job(
        "image/compress", form.model_dump(), [file], IMAGE_EXT,
        client_id_from(x_client_id),
    )


@router.post("/convert")
async def convert(
    file: UploadFile = File(...),
    format: str = Form("webp"),
    quality: int = Form(85),
    x_client_id: str | None = Header(None),
):
    form = _validated(ImageConvertForm, format=format, quality=quality)
    return await submit_job(
        "image/convert", form.model_dump(), [file], IMAGE_EXT,
        client_id_from(x_client_id),
    )


@router.post("/crop")
async def crop(
    file: UploadFile = File(...),
    x: int = Form(0),
    y: int = Form(0),
    width: int = Form(...),
    height: int = Form(...),
    format: str | None = Form(None),
    x_client_id: str | None = Header(None),
):
    form = _validated(ImageCropForm, x=x, y=y, width=width, height=height, format=format)
    return await submit_job(
        "image/crop", form.model_dump(), [file], IMAGE_EXT,
        client_id_from(x_client_id),
    )
