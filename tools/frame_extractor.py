"""Extract evenly-spaced keyframes from a video and encode as base64 JPEGs.

The OpenAI-compatible Coding Plan endpoint does not accept a ``video`` field;
we therefore down-sample the input video to a small set of representative
frames and feed them as ``image_url`` parts to the multimodal chat call.
"""
from __future__ import annotations

import base64
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List

from tools.ffmpeg_tools import FFmpegError, _ensure_binary, probe


@dataclass(frozen=True)
class Frame:
    timestamp: float        # seconds into the source video
    b64_jpeg: str           # base64-encoded JPEG, no data: prefix


def extract_frames(
    video_path: str | Path,
    *,
    num_frames: int = 16,
    max_side: int = 768,
    quality: int = 4,
) -> List[Frame]:
    """Uniformly sample ``num_frames`` frames across the video.

    Args:
        video_path: source video
        num_frames: how many keyframes to sample
        max_side: cap on the longer image side (px), scales down only
        quality: ffmpeg ``-q:v`` JPEG quality (2 best, 31 worst); 4 is a good tradeoff
    """
    _ensure_binary("ffmpeg")
    video_path = Path(video_path)
    meta = probe(video_path)
    if meta.duration <= 0:
        raise FFmpegError(f"Cannot extract frames: invalid duration for {video_path}")

    num_frames = max(1, num_frames)
    # Sample at the centers of (num_frames + 1) equal-width buckets so we avoid
    # the very beginning / very end which are often title cards or fade-outs.
    timestamps = [
        round(meta.duration * (i + 1) / (num_frames + 1), 3)
        for i in range(num_frames)
    ]

    frames: List[Frame] = []
    with tempfile.TemporaryDirectory(prefix="vae_frames_") as tmp:
        tmp_dir = Path(tmp)
        for idx, ts in enumerate(timestamps):
            out = tmp_dir / f"f_{idx:03d}.jpg"
            # NOTE: -ss before -i is much faster (input seek) and accurate enough
            # for sub-second timestamps in modern containers.
            scale_expr = (
                f"scale='if(gt(iw,ih),min({max_side},iw),-2)':"
                f"'if(gt(iw,ih),-2,min({max_side},ih))'"
            )
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                f"{ts:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                scale_expr,
                "-q:v",
                str(quality),
                "-loglevel",
                "error",
                str(out),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode != 0 or not out.exists():
                # Skip individual failed frames; continue extracting the rest.
                continue
            b64 = base64.b64encode(out.read_bytes()).decode("ascii")
            frames.append(Frame(timestamp=ts, b64_jpeg=b64))

    if not frames:
        raise FFmpegError(f"Failed to extract any frame from {video_path}")
    return frames
