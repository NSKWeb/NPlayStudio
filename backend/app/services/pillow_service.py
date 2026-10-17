"""Pillow-backed image operations."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 200_000_000

PIL_FORMAT = {
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "png": "PNG",
    "webp": "WEBP",
    "gif": "GIF",
    "bmp": "BMP",
    "tiff": "TIFF",
}

_LOSSY = {"JPEG", "WEBP"}


def _load(path: Path) -> Image.Image:
    image = Image.open(path)
    # Honour EXIF rotation so cropped/resized output matches what users see.
    image = ImageOps.exif_transpose(image)
    return image


def _normalize_mode(image: Image.Image, fmt: str) -> Image.Image:
    if fmt == "JPEG" and image.mode not in ("RGB", "L"):
        return image.convert("RGB")
    if fmt in ("PNG", "WEBP", "GIF", "TIFF") and image.mode == "P":
        return image.convert("RGBA")
    return image


def compress(
    src: Path, dst: Path, *, quality: int = 75, container: str | None = None,
    max_width: int | None = None,
) -> None:
    with _load(src) as image:
        fmt = PIL_FORMAT[(container or src.suffix.lstrip(".") or "jpg").lower()]
        if max_width and image.width > max_width:
            ratio = max_width / image.width
            image = image.resize((max_width, max(1, round(image.height * ratio))), Image.LANCZOS)
        image = _normalize_mode(image, fmt)
        save_kwargs: dict[str, object] = {"optimize": True}
        if fmt in _LOSSY:
            save_kwargs["quality"] = quality
        elif fmt == "PNG":
            save_kwargs["compress_level"] = max(1, min(9, round((100 - quality) / 11)))
        image.save(dst, format=fmt, **save_kwargs)


def convert(src: Path, dst: Path, *, container: str, quality: int = 85) -> None:
    with _load(src) as image:
        fmt = PIL_FORMAT[container.lower()]
        image = _normalize_mode(image, fmt)
        save_kwargs: dict[str, object] = {}
        if fmt in _LOSSY:
            save_kwargs["quality"] = quality
        image.save(dst, format=fmt, **save_kwargs)


def crop(
    src: Path, dst: Path, *, x: int, y: int, width: int, height: int,
    container: str | None = None, quality: int = 90,
) -> None:
    with _load(src) as image:
        fmt = PIL_FORMAT[(container or src.suffix.lstrip(".") or "png").lower()]
        if x >= image.width or y >= image.height:
            raise ValueError("crop rectangle is outside the image bounds")
        left = max(0, x)
        top = max(0, y)
        right = min(image.width, left + width)
        bottom = min(image.height, top + height)
        if right <= left or bottom <= top:
            raise ValueError("crop rectangle is outside the image bounds")
        box = image.crop((left, top, right, bottom))
        box = _normalize_mode(box, fmt)
        save_kwargs: dict[str, object] = {}
        if fmt in _LOSSY:
            save_kwargs["quality"] = quality
        box.save(dst, format=fmt, **save_kwargs)


def dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size
