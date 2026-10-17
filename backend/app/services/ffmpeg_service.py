"""FFmpeg command construction and execution with real progress reporting.

Progress is read from ffmpeg's ``-progress`` stream rather than guessed from
timings, so the UI percentage reflects actual encoded duration.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from ..config import get_settings
from . import file_service

ProgressCb = Callable[[int], Awaitable[None]]

# CRF per quality preset; lower CRF = better quality / larger file.
QUALITY_CRF = {"high": 20, "balanced": 26, "small": 32}

AUDIO_CODECS = {
    "mp3": "libmp3lame",
    "m4a": "aac",
    "aac": "aac",
    "wav": "pcm_s16le",
    "flac": "flac",
    "opus": "libopus",
    "ogg": "libvorbis",
}

_LOSSLESS_AUDIO = {"wav", "flac"}

VIDEO_ENCODERS = {
    "h264": "libx264",
    "h265": "libx265",
    "vp9": "libvpx-vp9",
}

# Container -> (default video encoder, default audio encoder)
CONTAINER_DEFAULTS = {
    "mp4": ("h264", "aac"),
    "mkv": ("h264", "aac"),
    "webm": ("vp9", "libopus"),
    "avi": ("h264", "libmp3lame"),
    "mov": ("h264", "aac"),
}


class FFmpegError(RuntimeError):
    """Raised when an ffmpeg/ffprobe invocation fails."""


async def run_ffmpeg(
    args: Sequence[str],
    *,
    duration: float | None = None,
    on_progress: ProgressCb | None = None,
    timeout: int = 3600,
) -> None:
    """Run ffmpeg with ``args`` (excluding the binary name), streaming progress.

    ``duration`` is the input duration in seconds; when known the reported
    percentage is the ratio of encoded output time to it.
    """
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", *args, "-progress", "pipe:1", "-nostats"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:  # ffmpeg missing from PATH
        raise FFmpegError(f"unable to start ffmpeg: {exc}") from exc

    stderr_chunks: list[bytes] = []

    async def _drain_stderr() -> None:
        assert proc.stderr is not None
        async for chunk in proc.stderr:
            stderr_chunks.append(chunk)
            # Keep memory bounded on very chatty encodes.
            if len(stderr_chunks) > 200:
                del stderr_chunks[:100]

    async def _read_progress() -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key in ("out_time_us", "out_time_ms") and duration and on_progress:
                try:
                    seconds = int(value) / 1_000_000
                except ValueError:
                    continue
                pct = max(0, min(99, int(seconds / duration * 100)))
                await on_progress(pct)
            elif key == "progress" and value == "end" and on_progress:
                await on_progress(99)

    drain = asyncio.create_task(_drain_stderr())
    watch = asyncio.create_task(_read_progress())

    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise FFmpegError(f"ffmpeg timed out after {timeout}s") from exc
    finally:
        await asyncio.gather(drain, watch, return_exceptions=True)

    if proc.returncode != 0:
        tail = b"".join(stderr_chunks).decode("utf-8", "replace").strip().splitlines()[-12:]
        raise FFmpegError("ffmpeg failed: " + (" | ".join(tail) or f"exit {proc.returncode}"))


def _scale_filter(resolution: str | None) -> list[str]:
    if not resolution:
        return []
    width, _, height = resolution.partition("x")
    # -2 keeps aspect ratio and guarantees an even dimension for the encoder.
    return ["-vf", f"scale={int(width)}:{int(height)}"]


def _codec_args(codec: str, container: str, crf: int | None, preset: str = "medium") -> list[str]:
    encoder = VIDEO_ENCODERS[codec]
    args = ["-c:v", encoder]
    if codec == "vp9":
        args += ["-crf", str(crf if crf is not None else 32), "-b:v", "0", "-row-mt", "1"]
    elif codec == "h265":
        args += ["-crf", str(crf if crf is not None else 28), "-preset", preset, "-tag:v", "hvc1"]
    else:
        args += ["-crf", str(crf if crf is not None else 26), "-preset", preset, "-pix_fmt", "yuv420p"]
    if container == "mp4":
        args += ["-movflags", "+faststart"]
    return args


def _audio_args(audio_format: str, bitrate: str, sample_rate: int | None = None) -> list[str]:
    codec = AUDIO_CODECS.get(audio_format, "aac")
    args = ["-c:a", codec]
    if audio_format in _LOSSLESS_AUDIO:
        args += ["-ar", str(sample_rate or 48000)]
    else:
        args += ["-b:a", bitrate]
        if sample_rate:
            args += ["-ar", str(sample_rate)]
    return args


# ------------------------------------------------------------------- video


async def video_compress(
    src: Path, dst: Path, *, quality: str = "balanced", crf: int | None = None,
    resolution: str | None = None, codec: str = "h264", container: str = "mp4",
    audio_bitrate: str = "128k", duration: float | None = None,
    on_progress: ProgressCb | None = None,
) -> None:
    effective_crf = crf if crf is not None else QUALITY_CRF.get(quality, 26)
    args: list[str] = ["-i", str(src), *_scale_filter(resolution)]
    args += _codec_args(codec, container, effective_crf)
    args += _audio_args("aac" if container != "webm" else "opus", audio_bitrate)
    args.append(str(dst))
    await run_ffmpeg(args, duration=duration, on_progress=on_progress)


async def video_convert(
    src: Path, dst: Path, *, container: str = "mp4", codec: str | None = None,
    resolution: str | None = None, duration: float | None = None,
    on_progress: ProgressCb | None = None,
) -> None:
    default_video, default_audio = CONTAINER_DEFAULTS.get(container, ("h264", "aac"))
    chosen = codec or default_video
    args: list[str] = ["-i", str(src), *_scale_filter(resolution)]
    args += _codec_args(chosen, container, None)
    args += ["-c:a", default_audio]
    args.append(str(dst))
    await run_ffmpeg(args, duration=duration, on_progress=on_progress)


async def video_merge(
    sources: Sequence[Path], dst: Path, *, container: str = "mp4",
    reencode: bool = False, resolution: str | None = None,
    duration: float | None = None, on_progress: ProgressCb | None = None,
) -> None:
    """Concatenate videos.

    Stream-copy (``reencode=False``) is instant but only works when every input
    shares codecs and parameters; otherwise we re-encode to normalise them.
    """
    if reencode:
        await _merge_reencode(
            sources, dst, container=container, resolution=resolution,
            duration=duration, on_progress=on_progress,
        )
        return

    listing = sources[0].parent / "concat.txt"
    listing.write_text(
        "\n".join(f"file '{source.as_posix()}'" for source in sources), encoding="utf-8"
    )
    args = ["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(dst)]
    try:
        await run_ffmpeg(args, duration=duration, on_progress=on_progress)
    except FFmpegError:
        # Codecs differ; fall back to a normalising re-encode.
        await _merge_reencode(
            sources, dst, container=container, resolution=resolution,
            duration=duration, on_progress=on_progress,
        )


async def _merge_reencode(
    sources: Sequence[Path], dst: Path, *, container: str,
    resolution: str | None, duration: float | None, on_progress: ProgressCb | None,
) -> None:
    """Normalising concat.

    Inputs without an audio track are padded with generated silence, otherwise
    ``concat`` rejects the filtergraph because the ``:a`` labels match nothing.
    """
    probes = [await file_service.probe(source) for source in sources]
    has_any_audio = any(info.get("has_audio") for info in probes)

    args: list[str] = []
    for source in sources:
        args += ["-i", str(source)]

    silent_inputs: dict[int, int] = {}
    if has_any_audio:
        for index, info in enumerate(probes):
            if info.get("has_audio"):
                continue
            seconds = info.get("duration") or duration or 1.0
            args += ["-f", "lavfi", "-t", f"{float(seconds):.3f}",
                     "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
            silent_inputs[index] = len(sources) + len(silent_inputs)

    chains: list[str] = []
    for index in range(len(sources)):
        if resolution:
            width, _, height = resolution.partition("x")
            chains.append(f"[{index}:v]scale={int(width)}:{int(height)},setsar=1[v{index}]")
        else:
            chains.append(f"[{index}:v]scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1[v{index}]")

    if has_any_audio:
        labels = []
        for index, info in enumerate(probes):
            audio_ref = f"[{index}:a]" if info.get("has_audio") else f"[{silent_inputs[index]}:a]"
            labels.append(f"[v{index}]{audio_ref}")
        filter_complex = ";".join(chains) + (
            f";{''.join(labels)}concat=n={len(sources)}:v=1:a=1[v][a]"
        )
        args += ["-filter_complex", filter_complex, "-map", "[v]", "-map", "[a]"]
    else:
        filter_complex = ";".join(chains) + (
            f";{''.join(f'[v{i}]' for i in range(len(sources)))}concat=n={len(sources)}:v=1:a=0[v]"
        )
        args += ["-filter_complex", filter_complex, "-map", "[v]", "-an"]

    args += _codec_args("h264", container, None)
    if has_any_audio:
        args += ["-c:a", "aac"]
    args.append(str(dst))
    await run_ffmpeg(args, duration=duration, on_progress=on_progress)


async def video_to_gif(
    src: Path, dst: Path, *, fps: int = 10, width: int = 480,
    start: float = 0, duration: float | None = None,
    on_progress: ProgressCb | None = None,
) -> None:
    args: list[str] = []
    if start:
        args += ["-ss", str(start)]
    args += ["-i", str(src)]
    if duration:
        args += ["-t", str(duration)]
    args += [
        "-vf", f"fps={fps},scale={width}:-1:flags=lanczos,split[s0][s1];"
               f"[s0]palettegen=max_colors=256[p];[s1][p]paletteuse=dither=bayer",
        "-loop", "0",
        str(dst),
    ]
    await run_ffmpeg(args, duration=duration, on_progress=on_progress)


# ------------------------------------------------------------------- audio


async def audio_extract(
    src: Path, dst: Path, *, container: str = "mp3", bitrate: str = "192k",
    sample_rate: int | None = None, duration: float | None = None,
    on_progress: ProgressCb | None = None,
) -> None:
    args = ["-i", str(src), "-vn", *_audio_args(container, bitrate, sample_rate), str(dst)]
    await run_ffmpeg(args, duration=duration, on_progress=on_progress)


async def audio_convert(
    src: Path, dst: Path, *, container: str = "mp3", bitrate: str = "192k",
    sample_rate: int | None = None, duration: float | None = None,
    on_progress: ProgressCb | None = None,
) -> None:
    args = ["-i", str(src), "-vn", *_audio_args(container, bitrate, sample_rate), str(dst)]
    await run_ffmpeg(args, duration=duration, on_progress=on_progress)


# ------------------------------------------------------------------ helper


async def measure_duration(path: Path) -> float | None:
    info = await file_service.probe(path)
    duration = info.get("duration")
    return float(duration) if duration else None


def clamp_progress(value: int) -> int:
    return max(0, min(100, int(value)))


def extract_percentage(text: str) -> int | None:
    match = re.search(r"(\d{1,3})%", text)
    return int(match.group(1)) if match else None


def max_upload_bytes() -> int:
    return get_settings().max_upload_bytes
