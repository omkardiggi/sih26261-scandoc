"""Validation and probing of inspection photographs and videos."""

from __future__ import annotations

import io
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from PIL import ExifTags, Image

from ..config import get_settings
from ..documents.upload import VIDEO_EXTENSIONS, UploadRejected, extension_of, sniff, validate_image_upload


@dataclass
class MediaInfo:
    kind: str
    detected_type: str
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None
    fps: float | None = None
    codec: str | None = None
    frame_count_est: int | None = None
    captured_at: str | None = None
    exif: dict = field(default_factory=dict)


def _exif(data: bytes) -> tuple[str | None, dict]:
    try:
        with Image.open(io.BytesIO(data)) as im:
            raw = im.getexif()
            if not raw:
                return None, {}
            tags = {ExifTags.TAGS.get(k, str(k)): v for k, v in raw.items()}
            ifd = raw.get_ifd(0x8769)  # Exif sub-IFD
            tags.update({ExifTags.TAGS.get(k, str(k)): v for k, v in ifd.items()})
            gps = raw.get_ifd(0x8825)
            out = {k: str(tags[k])[:100] for k in ("Make", "Model", "Software", "DateTimeOriginal", "DateTime") if k in tags}
            if gps:
                def deg(v):
                    d, m, s = (float(x) for x in v)
                    return d + m / 60 + s / 3600
                try:
                    lat = deg(gps[2]) * (-1 if gps.get(1) == "S" else 1)
                    lon = deg(gps[4]) * (-1 if gps.get(3) == "W" else 1)
                    out["gps"] = {"lat": round(lat, 6), "lon": round(lon, 6)}
                except Exception:  # noqa: BLE001
                    pass
            return out.get("DateTimeOriginal") or out.get("DateTime"), out
    except Exception:  # noqa: BLE001
        return None, {}


def validate_image(filename: str, data: bytes) -> MediaInfo:
    info = validate_image_upload(filename, data)
    captured, exif = _exif(data)
    return MediaInfo("image", info.detected_type, info.width, info.height, captured_at=captured, exif=exif)


def ffprobe(path: Path) -> dict:
    s = get_settings()
    try:
        out = subprocess.run(
            [s.ffprobe_path, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, timeout=60, check=False,
        )
    except FileNotFoundError as exc:
        raise UploadRejected("Video processing is unavailable: FFprobe is not installed on the server.") from exc
    except subprocess.TimeoutExpired as exc:
        raise UploadRejected("The video could not be analysed in time.") from exc
    if out.returncode != 0:
        raise UploadRejected("The video could not be read; it appears to be corrupted or in an unsupported format.")
    return json.loads(out.stdout or b"{}")


def _rate(r: str | None) -> float | None:
    if not r or r in ("0/0",):
        return None
    try:
        n, d = r.split("/")
        return float(n) / float(d) if float(d) else None
    except ValueError:
        return None


def validate_video(filename: str, data: bytes, path: Path) -> MediaInfo:
    s = get_settings()
    if not data:
        raise UploadRejected("The file is empty.")
    if len(data) > s.max_video_mb * 1024 * 1024:
        raise UploadRejected(f"The video exceeds the {s.max_video_mb} MB limit.")
    ext = extension_of(filename)
    if ext not in VIDEO_EXTENSIONS:
        raise UploadRejected("Unsupported video type. Upload MP4, MOV, M4V, AVI, MKV or WebM.")
    if sniff(data) != "video":
        raise UploadRejected("The file content does not match a supported video container.")
    probe = ffprobe(path)
    video = next((st for st in probe.get("streams", []) if st.get("codec_type") == "video"), None)
    if video is None:
        raise UploadRejected("The file contains no video stream.")
    duration = None
    for src in (video.get("duration"), (probe.get("format") or {}).get("duration")):
        try:
            duration = float(src)
            break
        except (TypeError, ValueError):
            continue
    if not duration or duration <= 0:
        raise UploadRejected("The video duration could not be determined.")
    fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate"))
    nb = video.get("nb_frames")
    return MediaInfo("video", ext.lstrip("."), int(video.get("width") or 0) or None, int(video.get("height") or 0) or None,
                     duration_s=round(duration, 3), fps=round(fps, 3) if fps else None, codec=video.get("codec_name"),
                     frame_count_est=int(nb) if nb and str(nb).isdigit() else (int(duration * fps) if fps else None))
