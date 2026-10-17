"""Stats and health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..services import db_service, file_service

router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats")
async def stats():
    data = await db_service.stats()
    data["disk"] = file_service.disk_usage()
    data["total_input_human"] = file_service.human_bytes(data.get("total_input_bytes"))
    data["total_output_human"] = file_service.human_bytes(data.get("total_output_bytes"))
    return data


@router.get("/tools")
async def tools():
    """Tool catalogue used by the dashboard to render cards."""
    return {"tools": TOOLS}


@router.get("/presets")
async def presets(tool: str | None = Query(None)):
    return {"presets": await db_service.list_presets(tool)}


TOOLS = [
    {"id": "video-compress", "name": "Video Compress", "category": "video",
     "path": "/video-compress.html", "icon": "compress",
     "description": "Shrink video size with quality presets or a target CRF."},
    {"id": "video-convert", "name": "Video Convert", "category": "video",
     "path": "/video-convert.html", "icon": "convert",
     "description": "Convert between MP4, MKV, WebM, AVI and MOV."},
    {"id": "video-merge", "name": "Video Merge", "category": "video",
     "path": "/video-merge.html", "icon": "merge",
     "description": "Join multiple clips into one file."},
    {"id": "video-to-gif", "name": "Video to GIF", "category": "video",
     "path": "/video-to-gif.html", "icon": "gif",
     "description": "Turn any clip into an optimised GIF."},
    {"id": "audio-extract", "name": "Audio Extract", "category": "audio",
     "path": "/audio-extract.html", "icon": "extract",
     "description": "Pull the audio track out of a video."},
    {"id": "audio-convert", "name": "Audio Convert", "category": "audio",
     "path": "/audio-convert.html", "icon": "convert",
     "description": "Convert MP3, M4A, WAV, FLAC, OPUS, AAC and OGG."},
    {"id": "image-compress", "name": "Image Compress", "category": "image",
     "path": "/image-compress.html", "icon": "compress",
     "description": "Reduce image weight without visible quality loss."},
    {"id": "image-convert", "name": "Image Convert", "category": "image",
     "path": "/image-convert.html", "icon": "convert",
     "description": "Convert JPG, PNG, WebP, GIF, BMP and TIFF."},
    {"id": "image-crop", "name": "Image Crop", "category": "image",
     "path": "/image-crop.html", "icon": "crop",
     "description": "Crop images to exact dimensions."},
]
