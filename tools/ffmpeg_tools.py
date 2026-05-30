"""ffmpeg / ffprobe wrappers used by the video composer agent."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List


class FFmpegError(RuntimeError):
    pass


def _ensure_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise FFmpegError(f"`{name}` not found in PATH. Please install ffmpeg first.")


@dataclass(frozen=True)
class VideoMeta:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: str | None


def probe(input_path: str | Path) -> VideoMeta:
    """Run ffprobe and return basic metadata."""
    _ensure_binary("ffprobe")
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(path)

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {result.stderr.strip()}")

    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video_stream is None:
        raise FFmpegError(f"No video stream found in {path}")

    duration = float(data.get("format", {}).get("duration", 0.0))
    fps_str = video_stream.get("avg_frame_rate", "0/1")
    num, _, den = fps_str.partition("/")
    fps = float(num) / float(den) if den and float(den) != 0 else 0.0

    return VideoMeta(
        path=path,
        duration=duration,
        width=int(video_stream.get("width", 0)),
        height=int(video_stream.get("height", 0)),
        fps=fps,
        video_codec=video_stream.get("codec_name", ""),
        audio_codec=audio_stream.get("codec_name") if audio_stream else None,
    )


def cut(
    input_path: str | Path,
    start: float,
    end: float,
    output_path: str | Path,
    *,
    reencode: bool = False,
) -> Path:
    """Extract a sub-clip [start, end] (seconds). Defaults to stream-copy."""
    _ensure_binary("ffmpeg")
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.0, end - start)

    base_cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(input_path),
        "-t",
        f"{duration:.3f}",
        "-avoid_negative_ts",
        "make_zero",
    ]

    if reencode:
        cmd = base_cmd + [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output_path),
        ]
    else:
        cmd = base_cmd + ["-c", "copy", str(output_path)]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        if not reencode:
            # Fallback to re-encode if stream-copy fails.
            return cut(input_path, start, end, output_path, reencode=True)
        raise FFmpegError(f"ffmpeg cut failed: {result.stderr.strip()}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        if not reencode:
            return cut(input_path, start, end, output_path, reencode=True)
        raise FFmpegError(f"ffmpeg produced empty output: {output_path}")

    return output_path


def concat(segment_paths: List[Path], output_path: str | Path) -> Path:
    """Concatenate clips using the demuxer concat protocol."""
    _ensure_binary("ffmpeg")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not segment_paths:
        raise FFmpegError("No segments to concatenate.")

    list_file = output_path.with_suffix(".concat.txt")
    with list_file.open("w", encoding="utf-8") as f:
        for seg in segment_paths:
            # ffmpeg concat demuxer requires escaped single quotes.
            escaped = str(seg.resolve()).replace("'", r"'\''")
            f.write(f"file '{escaped}'\n")

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        # Re-encode fallback when codecs/timebases mismatch across segments.
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise FFmpegError(f"ffmpeg concat failed: {result.stderr.strip()}")

    try:
        list_file.unlink()
    except OSError:
        pass
    return output_path
