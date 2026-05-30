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


# ---------------- v0.3 narrate helpers ----------------

KEN_BURNS_STYLES = ("zoom_in", "zoom_out", "pan_left", "pan_right")
"""4 种 Ken Burns 运镜 preset。SceneBuilder 按 line_idx % 4 轮转。

  - zoom_in : 从全景缓推近（揭示细节）
  - zoom_out: 从近景缓拉远（护眼 · 总结）
  - pan_left: 镜头由右向左（追溯过去感）
  - pan_right:镜头由左向右（推进进展感）
"""


def make_kenburns_clip(
    image_path: str | Path,
    duration: float,
    output_path: str | Path,
    *,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    zoom_speed: float = 0.0015,
    style: str | int = "zoom_in",
) -> Path:
    """单帧图片 + Ken Burns 运镜 -> 默片 mp4。

    设计点：
      - 输入是一张 jpg/png，输出是 *无音* 的 H.264 mp4
      - 4 种运镜 preset（style 参数）轮转，避免多句动效一致
      - 统一输出 1280x720@30fps yuv420p，便于 concat 不重编

    Args:
        style: "zoom_in" / "zoom_out" / "pan_left" / "pan_right"。
               也可传 int，自动取模 4 轮转。
    """
    _ensure_binary("ffmpeg")
    image_path = Path(image_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not image_path.exists():
        raise FFmpegError(f"Image not found: {image_path}")
    if duration <= 0:
        raise FFmpegError(f"Invalid duration: {duration}")

    if isinstance(style, int):
        style = KEN_BURNS_STYLES[style % len(KEN_BURNS_STYLES)]
    if style not in KEN_BURNS_STYLES:
        raise FFmpegError(
            f"unknown ken-burns style: {style!r}; expect one of {KEN_BURNS_STYLES}"
        )

    total_frames = max(1, int(round(duration * fps)))
    # 平移运镜时需个固定 zoom，否则画面会同时发生缩放+平移，观感凌乱。
    zoom_max = 1.5
    pan_zoom = 1.25
    # pan 表达式的帧度分母防零（超短 clip 只有1 帧时）
    span = max(total_frames - 1, 1)

    if style == "zoom_in":
        z_expr = f"min(zoom+{zoom_speed},{zoom_max})"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif style == "zoom_out":
        # zoom 从 zoom_max 缓降到 1.0；pzoom 是 zoompan 内置的“上一帧 zoom”。
        z_expr = f"if(eq(on,0),{zoom_max},max(pzoom-{zoom_speed},1.0))"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif style == "pan_left":
        # zoom 固定，x 从最右 (iw-iw/zoom) 线性插值到 0
        z_expr = f"{pan_zoom}"
        x_expr = f"(iw-iw/zoom)*(1-on/{span})"
        y_expr = "ih/2-(ih/zoom/2)"
    else:  # pan_right
        z_expr = f"{pan_zoom}"
        x_expr = f"(iw-iw/zoom)*on/{span}"
        y_expr = "ih/2-(ih/zoom/2)"

    # zoompan 需要先超采样，否则放大后边缘锁定会出骨架。
    vf = (
        f"scale={width * 4}:{height * 4},"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={total_frames}:s={width}x{height}:fps={fps},"
        f"setsar=1"
    )
    # 重要：用 -frames:v 严格限制输出帧数，避免 -loop 1 + zoompan 输出膨胀。
    # zoompan 语义是“每个输入帧输出 d 帧”，而 -loop 1 默认 25fps 反复输入同一张图，
    # 不限制会出现输出时长 ≈ 25× 预期。
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-vf",
        vf,
        "-r",
        str(fps),
        "-frames:v",
        str(total_frames),
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-an",
        "-loglevel",
        "error",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not output_path.exists():
        raise FFmpegError(f"ffmpeg ken-burns failed: {result.stderr.strip()}")
    return output_path


def concat_audio(audio_paths: List[Path], output_path: str | Path) -> Path:
    """拼接多个音频为单个 m4a。用 concat demuxer + aac 重编避免 codec 不一致。"""
    _ensure_binary("ffmpeg")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not audio_paths:
        raise FFmpegError("No audio files to concatenate.")

    list_file = output_path.with_suffix(".audio.concat.txt")
    with list_file.open("w", encoding="utf-8") as f:
        for p in audio_paths:
            escaped = str(Path(p).resolve()).replace("'", r"'\''")
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
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-loglevel",
        "error",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    try:
        list_file.unlink()
    except OSError:
        pass
    if result.returncode != 0 or not output_path.exists():
        raise FFmpegError(f"ffmpeg audio concat failed: {result.stderr.strip()}")
    return output_path


def mux_video_audio(
    video_path: str | Path,
    audio_path: str | Path,
    output_path: str | Path,
) -> Path:
    """记录视频（无音） + 音频轨 -> mp4。-shortest 以短者为准。"""
    _ensure_binary("ffmpeg")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-loglevel",
        "error",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not output_path.exists():
        raise FFmpegError(f"ffmpeg mux failed: {result.stderr.strip()}")
    return output_path
