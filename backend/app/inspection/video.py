"""Frame sampling with FFmpeg, preserving each frame's presentation timestamp."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings

_SHOWINFO_RE = re.compile(r"\bn:\s*(\d+)\b.*?\bpts_time:\s*([0-9.]+)")


@dataclass
class SampledFrame:
    seq: int
    timestamp_s: float
    path: Path


def sample_frames(video_path: Path, out_dir: Path, interval_s: float, max_frames: int) -> list[SampledFrame]:
    """Select the first frame, then the first frame at least ``interval_s`` after the previously selected one.

    Timestamps come from FFmpeg's showinfo filter (pts_time of each *selected* frame), so they are the
    real presentation times of the extracted frames rather than computed approximations.
    """
    s = get_settings()
    interval = max(0.1, float(interval_s))
    vf = f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{interval})',showinfo"
    cmd = [s.ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "info", "-i", str(video_path), "-an", "-sn",
           "-vf", vf, "-fps_mode", "vfr", "-frames:v", str(max_frames), "-q:v", "3", str(out_dir / "frame_%06d.jpg")]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=3600, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg is not installed on the server.") from exc
    stderr = proc.stderr.decode("utf-8", "replace")
    if proc.returncode != 0:
        tail = stderr.strip().splitlines()[-3:]
        raise RuntimeError("FFmpeg failed to extract frames: " + " | ".join(tail)[:500])
    times = [float(m.group(2)) for line in stderr.splitlines() if "Parsed_showinfo" in line and (m := _SHOWINFO_RE.search(line))]
    files = sorted(out_dir.glob("frame_*.jpg"))
    if len(times) < len(files):
        raise RuntimeError("Frame timestamps could not be matched to extracted frames.")
    return [SampledFrame(i, round(times[i], 3), f) for i, f in enumerate(files)]


def expected_samples(duration_s: float | None, interval_s: float) -> int | None:
    if not duration_s:
        return None
    return int(duration_s // max(0.1, interval_s)) + 1
